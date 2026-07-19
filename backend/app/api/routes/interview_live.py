import asyncio
import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.errors import AppError
from app.context.snapshot import finalize_context_snapshot
from app.db.models.common import SessionStatus, utc_now
from app.db.models.interview import InterviewPlan, InterviewSession
from app.db.session import async_session_factory
from app.providers.types import StreamEventType, Usage
from app.realtime.connection_manager import connection_manager
from app.realtime.event_store import append_event, find_client_event, replay_events
from app.realtime.events import ClientEvent, ServerEvent
from app.schemas.attachments import AnswerCommitPayload
from app.services import interview_sessions
from app.services.interview_orchestrator import (
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


@router.websocket("/interviews/{session_id}/live")
async def interview_live(websocket: WebSocket, session_id: uuid.UUID) -> None:
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
                    payload={"status": interview.status.value},
                ),
            )
            await _send_timer(websocket, session, interview)
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS
                )
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
                            payload={"status": interview.status.value},
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
                                    "role": message.role.value,
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
                        payload={"status": interview.status.value},
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
                        payload={"status": interview.status.value},
                        client_event_id=str(incoming.event_id),
                    )
                    await _send(websocket, event)
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
                            "role": answer.role.value,
                            "content": answer.content,
                            "sequence": answer.sequence,
                        },
                    },
                    client_event_id=str(incoming.event_id),
                )
                await _send(websocket, ack)
                try:
                    turn = await prepare_turn(session, interview)
                    content = ""
                    usage: Usage | None = None
                    if turn.static_prompt:
                        content = turn.static_prompt
                        await _transient(websocket, session_id, content)
                    else:
                        assert turn.provider and turn.request
                        try:
                            try:
                                async with asyncio.timeout(PROVIDER_STREAM_TIMEOUT_SECONDS):
                                    async for chunk in turn.provider.stream_chat(turn.request):
                                        if (
                                            chunk.type == StreamEventType.TEXT_DELTA
                                            and chunk.text
                                        ):
                                            content += chunk.text
                                            await _transient(websocket, session_id, chunk.text)
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
                            message="面试官未返回内容",
                            status_code=502,
                        )
                    message = await save_assistant_message(session, interview, turn, content)
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
                                "role": message.role.value,
                                "content": message.content,
                                "sequence": message.sequence,
                            }
                        },
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
                                payload={"status": interview.status.value},
                            ),
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
                    )
                    await _send(websocket, error)
    except WebSocketDisconnect:
        return
    except (AppError, ValueError):
        await websocket.close(code=1008, reason="session unavailable")
    finally:
        await connection_manager.disconnect(session_id, websocket)
