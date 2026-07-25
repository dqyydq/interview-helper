"""Offline, bounded OpenAI-compatible SenseVoice transcription service."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MODEL_ID = "sensevoice-small"
REQUIRED_MODEL_FILES = (
    "am.mvn",
    "chn_jpn_yue_eng_ko_spectok.bpe.model",
    "config.yaml",
    "configuration.json",
    "model.pt",
    "tokens.json",
)
ALLOWED_RESPONSE_FORMATS = {"json", "verbose_json"}
MODEL_TAG_PATTERN = re.compile(r"<\|[^|]*\|>")
UPLOAD_CHUNK_BYTES = 1024 * 1024
# Multipart fields add a small fixed amount above the raw audio payload.  This
# cap protects the tmpfs before Starlette's multipart parser can spool a file.
MAX_MULTIPART_OVERHEAD_BYTES = 128 * 1024
NORMALIZED_BYTES_PER_SECOND = 16_000 * 2  # mono, signed 16-bit WAV


class ServiceConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    model_dir: Path
    active_marker: Path
    revision: str
    device: str
    max_audio_bytes: int
    max_audio_seconds: int
    max_concurrent_requests: int

    @classmethod
    def from_environment(cls) -> RuntimeConfig:
        return cls(
            model_dir=Path(os.environ["LOCAL_ASR_MODEL_DIR"]),
            active_marker=Path(os.environ["LOCAL_ASR_ACTIVE_MARKER"]),
            revision=os.environ["LOCAL_ASR_MODEL_REVISION"],
            device=os.environ["LOCAL_ASR_DEVICE"],
            max_audio_bytes=_positive_int("LOCAL_ASR_MAX_AUDIO_BYTES"),
            max_audio_seconds=_positive_int("LOCAL_ASR_MAX_AUDIO_SECONDS"),
            max_concurrent_requests=_positive_int("LOCAL_ASR_MAX_CONCURRENT_REQUESTS"),
        )


def _positive_int(name: str) -> int:
    try:
        value = int(os.environ[name])
    except (KeyError, ValueError) as exc:
        raise ServiceConfigurationError("invalid_runtime_configuration") from exc
    if value < 1:
        raise ServiceConfigurationError("invalid_runtime_configuration")
    return value


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def validate_model_installation(config: RuntimeConfig) -> None:
    """Reject missing, non-active, or symlinked artifacts before model import."""

    try:
        marker = json.loads(config.active_marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ServiceConfigurationError("local_model_not_ready") from exc
    if not isinstance(marker, dict) or {
        "marker_version": marker.get("marker_version"),
        "preset_id": marker.get("preset_id"),
        "revision": marker.get("revision"),
        "integrity": marker.get("integrity"),
    } != {
        "marker_version": 1,
        "preset_id": MODEL_ID,
        "revision": config.revision,
        "integrity": "verified",
    }:
        raise ServiceConfigurationError("local_model_not_ready")
    if not config.model_dir.is_dir() or config.model_dir.is_symlink():
        raise ServiceConfigurationError("local_model_not_ready")
    if any(not _regular_file(config.model_dir / filename) for filename in REQUIRED_MODEL_FILES):
        raise ServiceConfigurationError("local_model_not_ready")


def load_model(config: RuntimeConfig) -> Any:
    """Load strictly from the verified local directory, without hub update checks."""

    validate_model_installation(config)
    from funasr import AutoModel

    return AutoModel(
        model=str(config.model_dir),
        device=config.device,
        trust_remote_code=False,
        check_latest=False,
        disable_update=True,
    )


def _duration_seconds(source: Path) -> float:
    try:
        completed = subprocess.run(
            (
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ),
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("audio_probe_failed") from exc
    if completed.returncode != 0:
        raise ValueError("audio_probe_failed")
    try:
        duration = float((completed.stdout or "").strip())
    except ValueError as exc:
        raise ValueError("audio_probe_failed") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("audio_probe_failed")
    return duration


def normalize_audio(source: Path, destination: Path, *, max_seconds: int) -> float:
    duration = _duration_seconds(source)
    if duration > max_seconds:
        raise ValueError("audio_too_long")
    try:
        completed = subprocess.run(
            (
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-map",
                "a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                "-t",
                str(max_seconds),
                str(destination),
            ),
            check=False,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("audio_normalization_failed") from exc
    if completed.returncode != 0 or not _regular_file(destination):
        raise ValueError("audio_normalization_failed")
    maximum_output_bytes = max_seconds * NORMALIZED_BYTES_PER_SECOND + 65_536
    try:
        output_size = destination.stat().st_size
    except OSError as exc:
        raise ValueError("audio_normalization_failed") from exc
    if output_size > maximum_output_bytes:
        raise ValueError("audio_normalization_failed")
    normalized_duration = _duration_seconds(destination)
    if normalized_duration > max_seconds + 0.1:
        raise ValueError("audio_normalization_failed")
    return normalized_duration


async def save_upload(upload: UploadFile, destination: Path, *, max_bytes: int) -> int:
    size = 0
    with destination.open("xb") as handle:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("audio_too_large")
            handle.write(chunk)
    if size == 0:
        raise ValueError("audio_empty")
    return size


def _clean_text(value: object) -> str:
    return MODEL_TAG_PATTERN.sub("", str(value or "")).strip()


def transcribe_wav(model: Any, source: Path, *, language: str | None) -> str:
    arguments: dict[str, object] = {"input": str(source), "batch_size_s": 60}
    if language:
        arguments["language"] = language
    result = model.generate(**arguments)
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise ValueError("invalid_model_response")
    text = _clean_text(result[0].get("text"))
    if not text:
        raise ValueError("empty_model_response")
    return text


def process_audio(
    model: Any,
    source: Path,
    normalized: Path,
    *,
    language: str | None,
    max_seconds: int,
) -> tuple[str, float]:
    """Run all blocking audio work in one tracked worker-thread task."""

    duration = normalize_audio(source, normalized, max_seconds=max_seconds)
    return transcribe_wav(model, normalized, language=language), duration


class RuntimeState:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.model: Any | None = None
        self.ready = False
        self.semaphore = asyncio.Semaphore(config.max_concurrent_requests)


def _schedule_cleanup(directory: Path) -> None:
    asyncio.create_task(asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True))


def _complete_transcription_task(
    task: asyncio.Task[tuple[str, float]],
    *,
    state: RuntimeState,
    directory: Path,
) -> None:
    """Release capacity only after its worker thread has truly stopped.

    Cancelling an HTTP request must not permit a second request to enter
    ``model.generate`` while its original ``asyncio.to_thread`` worker still
    runs.  Reading the exception also prevents an abandoned client from
    generating an unhandled-task warning.
    """

    try:
        task.exception()
    except asyncio.CancelledError:
        pass
    finally:
        state.semaphore.release()
        _schedule_cleanup(directory)


def start_transcription_task(
    *,
    state: RuntimeState,
    directory: Path,
    source: Path,
    normalized: Path,
    language: str | None,
) -> asyncio.Task[tuple[str, float]]:
    if state.model is None:
        raise ServiceConfigurationError("local_model_not_ready")
    task = asyncio.create_task(
        asyncio.to_thread(
            process_audio,
            state.model,
            source,
            normalized,
            language=language,
            max_seconds=state.config.max_audio_seconds,
        ),
        name="local-asr-transcription",
    )
    task.add_done_callback(
        lambda completed: _complete_transcription_task(
            completed,
            state=state,
            directory=directory,
        )
    )
    return task


# Importing the ASGI application must remain side-effect free.  Docker supplies
# the environment just before Uvicorn starts it, but keeping configuration
# construction inside the lifespan hook also makes static inspection and unit
# tests possible without a local model configuration.
CONFIG: RuntimeConfig | None = None
STATE: RuntimeState | None = None


def runtime_state() -> RuntimeState:
    if STATE is None:
        raise HTTPException(status_code=503, detail="local_asr_not_ready")
    return STATE


@asynccontextmanager
async def lifespan(_: FastAPI):
    global CONFIG, STATE
    config = RuntimeConfig.from_environment()
    state = RuntimeState(config)
    state.model = await asyncio.to_thread(load_model, config)
    state.ready = True
    CONFIG = config
    STATE = state
    try:
        yield
    finally:
        state.ready = False
        state.model = None
        STATE = None
        CONFIG = None


app = FastAPI(title="Interview Helper Local ASR", version="1.0.0", lifespan=lifespan)


class AudioBodyLimitMiddleware:
    """Enforce a streaming request cap before multipart parsing reaches tmpfs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = STATE
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/v1/audio/transcriptions"
            or state is None
        ):
            await self.app(scope, receive, send)
            return

        maximum = state.config.max_audio_bytes + MAX_MULTIPART_OVERHEAD_BYTES
        declared_length = next(
            (value for key, value in scope["headers"] if key == b"content-length"),
            None,
        )
        if declared_length is not None:
            try:
                if int(declared_length) > maximum:
                    response = JSONResponse({"detail": "audio_too_large"}, status_code=413)
                    await response(scope, receive, send)
                    return
            except ValueError:
                # Malformed Content-Length is handled by the HTTP server; keep
                # the streaming guard for transfer-encoded requests.
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise HTTPException(status_code=413, detail="audio_too_large")
            return message

        await self.app(scope, limited_receive, send)


app.add_middleware(AudioBodyLimitMiddleware)


@app.get("/health")
async def health() -> dict[str, object]:
    state = runtime_state()
    if not state.ready:
        raise HTTPException(status_code=503, detail="local_asr_not_ready")
    return {"status": "ready", "model": MODEL_ID, "revision": state.config.revision}


@app.get("/v1/models")
async def list_models() -> dict[str, object]:
    state = STATE
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "owned_by": "interview-helper-local",
                "ready": state.ready if state is not None else False,
            }
        ],
    }


@app.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(default=MODEL_ID),
    language: str | None = Form(default=None, max_length=20),
    response_format: str = Form(default="json"),
) -> dict[str, object]:
    state = runtime_state()
    if not state.ready or state.model is None:
        raise HTTPException(status_code=503, detail="local_asr_not_ready")
    if model != MODEL_ID:
        raise HTTPException(status_code=400, detail="model_not_supported")
    if response_format not in ALLOWED_RESPONSE_FORMATS:
        raise HTTPException(status_code=400, detail="response_format_not_supported")
    try:
        await asyncio.wait_for(state.semaphore.acquire(), timeout=0.05)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="local_asr_busy",
            headers={"Retry-After": "2"},
        ) from exc
    temporary_dir: Path | None = None
    worker: asyncio.Task[tuple[str, float]] | None = None
    capacity_owned_by_request = True
    try:
        temporary_dir = Path(tempfile.mkdtemp(prefix="asr-", dir="/tmp"))
        source = temporary_dir / "upload.bin"
        normalized = temporary_dir / "normalized.wav"
        await save_upload(file, source, max_bytes=state.config.max_audio_bytes)
        worker = start_transcription_task(
            state=state,
            directory=temporary_dir,
            source=source,
            normalized=normalized,
            language=language,
        )
        capacity_owned_by_request = False
        text, duration = await asyncio.shield(worker)
        if response_format == "verbose_json":
            return {
                "text": text,
                "language": language or "auto",
                "duration": round(duration, 3),
                "model": MODEL_ID,
            }
        return {"text": text}
    except ValueError as exc:
        error_code = str(exc)
        status_code = 413 if error_code in {"audio_too_large", "audio_too_long"} else 422
        raise HTTPException(status_code=status_code, detail=error_code) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="local_transcription_failed") from exc
    finally:
        if capacity_owned_by_request:
            state.semaphore.release()
            if temporary_dir is not None:
                _schedule_cleanup(temporary_dir)
        await file.close()
