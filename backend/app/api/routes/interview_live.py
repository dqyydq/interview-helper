import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.errors import AppError
from app.context.snapshot import finalize_context_snapshot
from app.db.models.common import SessionStatus, utc_now
from app.db.session import async_session_factory
from app.providers.types import StreamEventType, Usage
from app.realtime.event_store import append_event, find_client_event, replay_events
from app.realtime.events import ClientEvent, ServerEvent
from app.services import interview_sessions
from app.services.interview_orchestrator import (
    prepare_turn,
    save_assistant_message,
    save_user_answer,
)
from app.services.model_connections import ensure_local_profile

router = APIRouter(tags=["interview-live"])


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


@router.websocket("/interviews/{session_id}/live")
async def interview_live(websocket: WebSocket, session_id: uuid.UUID) -> None:
    await websocket.accept()
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
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > 64 * 1024:
                await websocket.close(code=1009, reason="message too large")
                return
            try:
                incoming = ClientEvent.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError):
                await websocket.send_json(
                    {"type": "error", "payload": {"code": "event_invalid"}}
                )
                continue
            async with async_session_factory() as session:
                profile = await ensure_local_profile(session)
                interview = await interview_sessions.get_session(session, profile.id, session_id)
                duplicate = await find_client_event(session, session_id, str(incoming.event_id))
                if duplicate:
                    await _send(websocket, duplicate)
                    continue
                if incoming.type == "session.resume":
                    after = max(0, int(incoming.payload.get("last_sequence", 0)))
                    for event in await replay_events(session, session_id, after):
                        await _send(websocket, event)
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
                if incoming.type != "user.text.submit":
                    continue
                if interview.status == SessionStatus.READY:
                    interview = await interview_sessions.start_session(session, interview)
                answer = await save_user_answer(
                    session, interview, str(incoming.payload.get("text", ""))
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
                            async for chunk in turn.provider.stream_chat(turn.request):
                                if chunk.type == StreamEventType.TEXT_DELTA and chunk.text:
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
