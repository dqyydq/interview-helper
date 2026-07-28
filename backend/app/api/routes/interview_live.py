import asyncio
import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.errors import AppError
from app.context.segmentation import close_current_segment
from app.context.snapshot import finalize_context_snapshot
from app.core.config import settings
from app.core.security import InFlightAnswerRegistry, SlidingWindowRateLimiter
from app.db.models.common import MessageRole, SessionStatus, utc_now
from app.db.models.interview import InterviewPlan, InterviewSession
from app.db.session import async_session_factory
from app.providers.base import ProviderError
from app.providers.types import StreamEventType, Usage
from app.realtime.connection_manager import connection_manager
from app.realtime.event_store import append_event, find_client_event, replay_events
from app.realtime.events import ClientEvent, ServerEvent
from app.schemas.attachments import AnswerCommitPayload
from app.services import interview_sessions
from app.services.interview_orchestrator import (
    decide_turn,
    pending_answer_for_retry,
    prepare_turn,
    save_assistant_message,
    save_restatement,
    save_user_answer,
)
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["interview-live"])
MAX_MESSAGE_BYTES = 64 * 1024
MAX_INVALID_EVENTS = 3
IDLE_TIMEOUT_SECONDS = 15 * 60
PROVIDER_STREAM_TIMEOUT_SECONDS = 60
connection_rate_limiter = SlidingWindowRateLimiter(
    max_events=settings.websocket_connections_per_minute,
    window_seconds=60,
    max_keys=settings.websocket_rate_limiter_max_keys,
)
in_flight_answers = InFlightAnswerRegistry()


def _status_value(value: SessionStatus | str) -> str:
    """Normalize SQLAlchemy String-backed enum fields for realtime payloads."""

    return SessionStatus(value).value


def _message_role_value(value: MessageRole | str) -> str:
    """Normalize SQLAlchemy String-backed role fields for realtime payloads."""

    return MessageRole(value).value


async def _send(websocket: WebSocket, event: ServerEvent) -> None:
    await websocket.send_json(event.model_dump(mode="json"))


async def _transient(websocket: WebSocket, session_id: uuid.UUID, text: str) -> None:
    await _send(
        websocket,
        ServerEvent(
            event_id=uuid.uuid4(),
            session_id=session_id,
            type="assistant.delta",
            sequence=0,
            timestamp=utc_now(),
            payload={"text": text},
            transient=True,
        ),
    )


async def _turn_status(
    websocket: WebSocket,
    session_id: uuid.UUID,
    stage: str,
    message: str,
) -> None:
    """Tell the client what durable/recoverable work is happening next."""

    await _send(
        websocket,
        ServerEvent(
            event_id=uuid.uuid4(),
            session_id=session_id,
            type="turn.status",
            sequence=0,
            timestamp=utc_now(),
            payload={"stage": stage, "message": message},
            transient=True,
        ),
    )


async def _send_protocol_error(
    websocket: WebSocket,
    session_id: uuid.UUID,
    code: str,
    message: str,
) -> None:
    await _send(
        websocket,
        ServerEvent(
            event_id=uuid.uuid4(),
            session_id=session_id,
            type="error",
            sequence=0,
            timestamp=utc_now(),
            payload={"code": code, "message": message, "retryable": False},
            transient=True,
        ),
    )


async def _send_timer(
    websocket: WebSocket,
    session,
    interview: InterviewSession,
) -> None:
    plan = await session.get(InterviewPlan, interview.plan_id)
    total_seconds = (plan.total_minutes if plan else 0) * 60
    if interview.started_at:
        deadline = interview.started_at + timedelta(seconds=total_seconds)
        remaining_seconds = max(0, int((deadline - utc_now()).total_seconds()))
    else:
        remaining_seconds = total_seconds
    await _send(
        websocket,
        ServerEvent(
            event_id=uuid.uuid4(),
            session_id=interview.id,
            type="timer.update",
            sequence=0,
            timestamp=utc_now(),
            payload={
                "remaining_seconds": remaining_seconds,
                "total_seconds": total_seconds,
            },
            transient=True,
        ),
    )


async def _generate_turn_from_saved_answer(
    websocket: WebSocket,
    session,
    interview: InterviewSession,
    answer,
    *,
    retry_client_event_id: str | None = None,
) -> None:
    """Turn an already-persisted answer into one next interviewer message.

    A retry starts here, never at answer submission.  That makes the durable
    user message the single source of truth while allowing provider failures to
    be retried without duplicate transcript rows.
    """

    try:
        await _turn_status(websocket, interview.id, "choosing_follow_up", "正在判断下一步节奏")
        decision_result = await decide_turn(session, interview, answer)
        stage = decision_result.decision.action
        if stage == "advance":
            await _turn_status(websocket, interview.id, "advancing", "正在切换到下一道主问题")
        elif stage == "finish":
            await _turn_status(websocket, interview.id, "advancing", "正在收束本场面试")
        await _turn_status(websocket, interview.id, "generating_question", "正在生成下一步问题")
        turn = await prepare_turn(
            session,
            interview,
            decision=decision_result.decision,
            decision_source=decision_result.source,
        )
        content = ""
        usage: Usage | None = None
        if turn.static_prompt:
            content = turn.static_prompt
            await _transient(websocket, interview.id, content)
        else:
            assert turn.provider and turn.request
            try:
                try:
                    async with asyncio.timeout(PROVIDER_STREAM_TIMEOUT_SECONDS):
                        async for chunk in turn.provider.stream_chat(turn.request):
                            if chunk.type == StreamEventType.TEXT_DELTA and chunk.text:
                                content += chunk.text
                                await _transient(websocket, interview.id, chunk.text)
                            elif chunk.type == StreamEventType.USAGE and chunk.usage:
                                usage = chunk.usage
                            elif chunk.type == StreamEventType.FAILED:
                                raise AppError(
                                    code=chunk.error_code or "provider_failed",
                                    message="面试官模型暂时无法回答",
                                    status_code=503,
                                    retryable=chunk.retryable,
                                )
                except TimeoutError as exc:
                    raise AppError(
                        code="provider_timeout",
                        message="面试官模型响应超时，可以重试当前回答",
                        status_code=504,
                        retryable=True,
                    ) from exc
            finally:
                close = getattr(turn.provider, "aclose", None)
                if close:
                    await close()
        if not content.strip():
            raise AppError(
                code="provider_empty_response",
                message="面试官未返回内容，可以重试当前回答",
                status_code=502,
                retryable=True,
            )
        message = await save_assistant_message(session, interview, turn, content)
        # Persist the closing message before sealing the segment so its evidence
        # remains attached to the final question and the summary job can include it.
        if turn.should_finish:
            await close_current_segment(session, interview, turn.plan_question.id)
        await finalize_context_snapshot(
            session,
            turn.context_snapshot_id,
            usage if not turn.static_prompt else None,
        )
        final = await append_event(
            session,
            interview,
            event_type="assistant.message",
            payload={
                "message": {
                    "id": str(message.id),
                    "role": _message_role_value(message.role),
                    "content": message.content,
                    "sequence": message.sequence,
                }
            },
            # New-answer acknowledgements already use the client id. A retry
            # has no acknowledgement, so it can use its id to become idempotent.
            client_event_id=retry_client_event_id,
        )
        await _send(websocket, final)
        await _send_timer(websocket, session, interview)
        if turn.should_finish:
            interview = await interview_sessions.finish_session(session, interview)
            await _send(
                websocket,
                await append_event(
                    session,
                    interview,
                    event_type="session.state",
                    payload={"status": _status_value(interview.status)},
                ),
            )
    except ProviderError as exc:
        error = await append_event(
            session,
            interview,
            event_type="error",
            payload={"code": exc.code, "message": exc.message, "retryable": True},
            client_event_id=retry_client_event_id,
        )
        await _send(websocket, error)
    except AppError as exc:
        error = await append_event(
            session,
            interview,
            event_type="error",
            payload={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
            client_event_id=retry_client_event_id,
        )
        await _send(websocket, error)


async def _pending_turn_available(session, interview: InterviewSession) -> bool:
    try:
        await pending_answer_for_retry(session, interview)
    except AppError:
        return False
    return True


@router.websocket("/interviews/{session_id}/live")
async def interview_live(websocket: WebSocket, session_id: uuid.UUID) -> None:
    client_host = websocket.client.host if websocket.client else "unknown"
    # Limit by the remote peer, not by session ID.  A client can otherwise mint
    # arbitrary IDs to bypass the connection-frequency window before database lookup.
    if not connection_rate_limiter.allow(client_host):
        await websocket.close(code=1013, reason="connection rate limit")
        return
    if not await connection_manager.connect(session_id, websocket):
        return
    invalid_events = 0
    last_client_sequence = 0
    try:
        async with async_session_factory() as session:
            profile = await ensure_local_profile(session)
            interview = await interview_sessions.get_session(session, profile.id, session_id)
            last_sequence = max(0, int(websocket.query_params.get("last_sequence", "0")))
            for event in await replay_events(session, session_id, last_sequence):
                await _send(websocket, event)
            await _send(
                websocket,
                await append_event(
                    session,
                    interview,
                    event_type="session.state",
                    payload={
                        "status": _status_value(interview.status),
                        "pending_turn": await _pending_turn_available(session, interview),
                    },
                ),
            )
            await _send_timer(websocket, session, interview)
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
            except TimeoutError:
                await websocket.close(code=1001, reason="session idle timeout")
                return
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="message too large")
                return
            try:
                incoming = ClientEvent.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError):
                invalid_events += 1
                await _send_protocol_error(
                    websocket,
                    session_id,
                    "event_invalid",
                    "事件格式不符合实时协议",
                )
                if invalid_events >= MAX_INVALID_EVENTS:
                    await websocket.close(code=1008, reason="too many invalid events")
                    return
                continue
            async with async_session_factory() as session:
                profile = await ensure_local_profile(session)
                interview = await interview_sessions.get_session(session, profile.id, session_id)
                duplicate = await find_client_event(session, session_id, str(incoming.event_id))
                if duplicate:
                    await _send(websocket, duplicate)
                    continue
                if incoming.sequence <= last_client_sequence:
                    invalid_events += 1
                    await _send_protocol_error(
                        websocket,
                        session_id,
                        "event_sequence_invalid",
                        "客户端事件序号必须严格递增",
                    )
                    if invalid_events >= MAX_INVALID_EVENTS:
                        await websocket.close(code=1008, reason="invalid event sequence")
                        return
                    continue
                last_client_sequence = incoming.sequence
                if incoming.type == "session.resume":
                    after = max(0, int(incoming.payload.get("last_sequence", 0)))
                    for event in await replay_events(session, session_id, after):
                        await _send(websocket, event)
                    if interview.status == SessionStatus.PAUSED:
                        interview = await interview_sessions.resume_session(session, interview)
                    await _send(
                        websocket,
                        await append_event(
                            session,
                            interview,
                            event_type="session.state",
                            payload={
                                "status": _status_value(interview.status),
                                "pending_turn": await _pending_turn_available(session, interview),
                            },
                            client_event_id=str(incoming.event_id),
                        ),
                    )
                    await _send_timer(websocket, session, interview)
                    continue
                if incoming.type == "user.transcript.partial":
                    await _send(
                        websocket,
                        ServerEvent(
                            event_id=uuid.uuid4(),
                            session_id=session_id,
                            type="input.ack",
                            sequence=0,
                            timestamp=utc_now(),
                            payload={
                                "client_event_id": str(incoming.event_id),
                                "committed": False,
                            },
                            transient=True,
                        ),
                    )
                    continue
                if incoming.type == "session.restate":
                    message = await save_restatement(session, interview)
                    await _send(
                        websocket,
                        await append_event(
                            session,
                            interview,
                            event_type="assistant.message",
                            payload={
                                "message": {
                                    "id": str(message.id),
                                    "role": _message_role_value(message.role),
                                    "content": message.content,
                                    "sequence": message.sequence,
                                }
                            },
                            client_event_id=str(incoming.event_id),
                        ),
                    )
                    continue
                if incoming.type == "session.pause":
                    interview = await interview_sessions.pause_session(session, interview)
                    event = await append_event(
                        session,
                        interview,
                        event_type="session.state",
                        payload={"status": _status_value(interview.status)},
                        client_event_id=str(incoming.event_id),
                    )
                    await _send(websocket, event)
                    await _send_timer(websocket, session, interview)
                    continue
                if incoming.type == "session.finish":
                    interview = await interview_sessions.finish_session(session, interview)
                    event = await append_event(
                        session,
                        interview,
                        event_type="session.state",
                        payload={"status": _status_value(interview.status)},
                        client_event_id=str(incoming.event_id),
                    )
                    await _send(websocket, event)
                    continue
                if incoming.type == "turn.retry":
                    if not await in_flight_answers.acquire(session_id):
                        await _send_protocol_error(
                            websocket,
                            session_id,
                            "answer_pending",
                            "上一轮仍在处理中，请稍候再试",
                        )
                        continue
                    try:
                        answer = await pending_answer_for_retry(session, interview)
                        await _turn_status(
                            websocket,
                            session_id,
                            "retrying",
                            "回答已保存，正在重新生成下一步",
                        )
                        await _generate_turn_from_saved_answer(
                            websocket,
                            session,
                            interview,
                            answer,
                            retry_client_event_id=str(incoming.event_id),
                        )
                    except AppError as exc:
                        error = await append_event(
                            session,
                            interview,
                            event_type="error",
                            payload={
                                "code": exc.code,
                                "message": exc.message,
                                "retryable": exc.retryable,
                            },
                            client_event_id=str(incoming.event_id),
                        )
                        await _send(websocket, error)
                    finally:
                        await in_flight_answers.release(session_id)
                    continue
                if incoming.type not in {"user.text.submit", "user.answer.commit"}:
                    continue
                if interview.status == SessionStatus.READY:
                    interview = await interview_sessions.start_session(session, interview)
                try:
                    answer_payload = AnswerCommitPayload.model_validate(incoming.payload)
                except ValidationError:
                    await _send_protocol_error(
                        websocket,
                        session_id,
                        "answer_payload_invalid",
                        "回答或代码附件格式不符合要求",
                    )
                    continue
                if not await in_flight_answers.acquire(session_id):
                    await _send_protocol_error(
                        websocket,
                        session_id,
                        "answer_pending",
                        "上一条回答尚未确认，请等待确认后再提交",
                    )
                    continue
                try:
                    answer = await save_user_answer(
                        session,
                        interview,
                        answer_payload.text,
                        client_event_id=str(incoming.event_id),
                        attachments=answer_payload.attachments,
                    )
                    ack = await append_event(
                        session,
                        interview,
                        event_type="input.ack",
                        payload={
                            "client_event_id": str(incoming.event_id),
                            "message": {
                                "id": str(answer.id),
                                "role": _message_role_value(answer.role),
                                "content": answer.content,
                                "sequence": answer.sequence,
                            },
                        },
                        client_event_id=str(incoming.event_id),
                    )
                    await _send(websocket, ack)
                    await _turn_status(
                        websocket,
                        session_id,
                        "answer_saved",
                        "回答已保存，正在准备下一步",
                    )
                    await _generate_turn_from_saved_answer(
                        websocket,
                        session,
                        interview,
                        answer,
                    )
                    await in_flight_answers.release(session_id)
                    continue
                except BaseException:
                    await in_flight_answers.release(session_id)
                    raise
    except WebSocketDisconnect:
        return
    except (AppError, ValueError):
        await websocket.close(code=1008, reason="session unavailable")
    finally:
        await connection_manager.disconnect(session_id, websocket)
