from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("local_asr_app", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
asr = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = asr
SPEC.loader.exec_module(asr)


class LocalAsrAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.models = self.root / "models"
        self.state = self.root / "state"
        self.model_directory = self.models / "sensevoice"
        self.model_directory.mkdir(parents=True)
        for filename in asr.REQUIRED_MODEL_FILES:
            (self.model_directory / filename).write_text("fixture", encoding="utf-8")
        self.marker = self.state / "active" / "sensevoice-small.json"
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text(
            json.dumps(
                {
                    "marker_version": 1,
                    "preset_id": "sensevoice-small",
                    "revision": "a" * 40,
                    "integrity": "verified",
                }
            ),
            encoding="utf-8",
        )
        self.config = asr.RuntimeConfig(
            model_dir=self.model_directory,
            active_marker=self.marker,
            revision="a" * 40,
            device="cpu",
            max_audio_bytes=1024,
            max_audio_seconds=30,
            max_concurrent_requests=1,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_load_model_requires_verified_active_marker_and_disables_hub_checks(self) -> None:
        captured: dict[str, object] = {}

        class FakeAutoModel:
            def __new__(cls, **kwargs: object) -> object:
                captured.update(kwargs)
                return object()

        module = ModuleType("funasr")
        module.AutoModel = FakeAutoModel  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"funasr": module}):
            asr.load_model(self.config)

        self.assertEqual(captured["model"], str(self.model_directory))
        self.assertEqual(captured["device"], "cpu")
        self.assertIs(captured["trust_remote_code"], False)
        self.assertIs(captured["check_latest"], False)
        self.assertIs(captured["disable_update"], True)

    def test_invalid_marker_prevents_any_funasr_import(self) -> None:
        self.marker.write_text("{}", encoding="utf-8")
        with self.assertRaises(asr.ServiceConfigurationError) as raised:
            asr.load_model(self.config)

        self.assertEqual(str(raised.exception), "local_model_not_ready")

    def test_audio_normalization_uses_fixed_non_shell_ffmpeg_commands(self) -> None:
        source = self.root / "upload.bin"
        destination = self.root / "normalized.wav"
        source.write_bytes(b"audio")
        calls: list[tuple[str, ...]] = []

        def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[0] == "ffprobe":
                return subprocess.CompletedProcess(command, 0, stdout="12.5\n")
            destination.write_bytes(b"wav")
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(asr.subprocess, "run", side_effect=fake_run):
            duration = asr.normalize_audio(source, destination, max_seconds=30)

        self.assertEqual(duration, 12.5)
        self.assertEqual(calls[0][0], "ffprobe")
        self.assertEqual(calls[1][0:6], ("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i"))
        limit_index = calls[1].index("-t")
        self.assertEqual(calls[1][limit_index : limit_index + 2], ("-t", "30"))


class LocalAsrConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_client_does_not_release_model_capacity_early(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary_directory = Path(temporary_name)
            config = asr.RuntimeConfig(
                model_dir=temporary_directory / "model",
                active_marker=temporary_directory / "marker.json",
                revision="a" * 40,
                device="cpu",
                max_audio_bytes=1024,
                max_audio_seconds=30,
                max_concurrent_requests=1,
            )
            state = asr.RuntimeState(config)
            state.model = object()
            await state.semaphore.acquire()
            started = threading.Event()
            allow_completion = threading.Event()

            def fake_process(*_: object, **__: object) -> tuple[str, float]:
                started.set()
                if not allow_completion.wait(timeout=2):
                    raise RuntimeError("test worker did not complete")
                return "完成", 1.0

            with mock.patch.object(asr, "process_audio", side_effect=fake_process):
                worker = asr.start_transcription_task(
                    state=state,
                    directory=temporary_directory,
                    source=temporary_directory / "upload.bin",
                    normalized=temporary_directory / "normalized.wav",
                    language=None,
                )

                async def await_worker() -> tuple[str, float]:
                    return await asyncio.shield(worker)

                client_waiter = asyncio.create_task(await_worker())
                await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1.2)
                client_waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await client_waiter

                self.assertTrue(state.semaphore.locked())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(state.semaphore.acquire(), timeout=0.02)

                allow_completion.set()
                self.assertEqual(await worker, ("完成", 1.0))
                await asyncio.sleep(0.05)
                await asyncio.wait_for(state.semaphore.acquire(), timeout=0.2)
                state.semaphore.release()


class AudioBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_declared_oversized_body_is_rejected_before_downstream(self) -> None:
        state = asr.RuntimeState(
            asr.RuntimeConfig(
                model_dir=Path("/tmp/model"),
                active_marker=Path("/tmp/marker"),
                revision="a" * 40,
                device="cpu",
                max_audio_bytes=4,
                max_audio_seconds=30,
                max_concurrent_requests=1,
            )
        )
        downstream_called = False
        sent: list[dict[str, object]] = []

        async def downstream(*_: object) -> None:
            nonlocal downstream_called
            downstream_called = True

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope: dict[str, object] = {
            "type": "http",
            "method": "POST",
            "path": "/v1/audio/transcriptions",
            "headers": [(b"content-length", b"999999")],
        }
        middleware = asr.AudioBodyLimitMiddleware(downstream)
        with mock.patch.object(asr, "STATE", state):
            await middleware(scope, receive, send)

        self.assertFalse(downstream_called)
        self.assertEqual(sent[0]["status"], 413)

    async def test_chunked_body_is_limited_before_multipart_can_read_it_all(self) -> None:
        state = asr.RuntimeState(
            asr.RuntimeConfig(
                model_dir=Path("/tmp/model"),
                active_marker=Path("/tmp/marker"),
                revision="a" * 40,
                device="cpu",
                max_audio_bytes=4,
                max_audio_seconds=30,
                max_concurrent_requests=1,
            )
        )
        received_messages = [
            {"type": "http.request", "body": b"a" * (asr.MAX_MULTIPART_OVERHEAD_BYTES + 3), "more_body": True},
            {"type": "http.request", "body": b"bb", "more_body": False},
        ]

        async def downstream(_: object, receive: object, __: object) -> None:
            first = await receive()  # type: ignore[operator]
            self.assertEqual(first["type"], "http.request")
            await receive()  # type: ignore[operator]

        async def receive() -> dict[str, object]:
            return received_messages.pop(0)

        async def send(_: dict[str, object]) -> None:
            self.fail("chunked limit should raise before a response is sent downstream")

        scope: dict[str, object] = {
            "type": "http",
            "method": "POST",
            "path": "/v1/audio/transcriptions",
            "headers": [],
        }
        middleware = asr.AudioBodyLimitMiddleware(downstream)
        with mock.patch.object(asr, "STATE", state):
            with self.assertRaises(asr.HTTPException) as raised:
                await middleware(scope, receive, send)

        self.assertEqual(raised.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
