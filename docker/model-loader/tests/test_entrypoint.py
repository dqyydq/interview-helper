from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "entrypoint.py"
PRESETS_DIR = Path(__file__).parents[2] / "model-presets"
SPEC = importlib.util.spec_from_file_location("model_loader_entrypoint", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
loader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = loader
SPEC.loader.exec_module(loader)


def fixture_preset(content: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "fixture-model",
        "display_name": "Fixture model",
        "capability": "embedding",
        "source": {
            "provider": "modelscope",
            "repository": "fixture/model",
            "revision": "a" * 40,
            "revision_policy": "pinned",
        },
        "files": [
            {
                "path": "payload.bin",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ],
        "storage": {"download_bytes": len(content), "recommended_free_bytes": 1024},
        "license": "mit",
        "runtime": {"consumer": "tei", "network_at_runtime": False},
        "embedding": {"dimensions": 4, "normalize": True, "query_prefix": "", "passage_prefix": ""},
    }


class ModelLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.models = self.root / "models"
        self.state = self.root / "state"
        self.offline = self.root / "offline"
        self.content = b"offline-model-fixture"
        self.preset = loader.parse_preset(fixture_preset(self.content))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_offline_bundle(self, content: bytes | None = None) -> None:
        self.offline.mkdir(parents=True, exist_ok=True)
        (self.offline / "payload.bin").write_bytes(self.content if content is None else content)
        (self.offline / "offline-manifest.json").write_text(
            json.dumps(loader.expected_offline_manifest(self.preset)), encoding="utf-8"
        )

    def test_offline_install_verifies_then_atomically_marks_active(self) -> None:
        self.write_offline_bundle()

        status = loader.install_preset(
            self.preset, self.models, state_root=self.state, source="offline", offline_dir=self.offline
        )

        self.assertEqual(status["state"], "ready")
        active = loader.active_directory(self.models, self.state, self.preset)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual((active / "payload.bin").read_bytes(), self.content)

    def test_bad_checksum_preserves_previous_active_revision(self) -> None:
        self.write_offline_bundle()
        loader.install_preset(self.preset, self.models, state_root=self.state, source="offline", offline_dir=self.offline)
        old_active = loader.active_directory(self.models, self.state, self.preset)
        assert old_active is not None

        next_payload = b"next-revision"
        next_definition = fixture_preset(next_payload)
        next_definition["source"]["revision"] = "b" * 40
        next_preset = loader.parse_preset(next_definition)
        next_offline = self.root / "next-offline"
        next_offline.mkdir()
        (next_offline / "payload.bin").write_bytes(b"x" * len(next_payload))
        (next_offline / "offline-manifest.json").write_text(
            json.dumps(loader.expected_offline_manifest(next_preset)), encoding="utf-8"
        )

        with self.assertRaises(loader.LoaderError) as raised:
            loader.install_preset(next_preset, self.models, state_root=self.state, source="offline", offline_dir=next_offline)

        self.assertEqual(raised.exception.code, "artifact_checksum_mismatch")
        self.assertEqual((old_active / "payload.bin").read_bytes(), self.content)
        self.assertEqual(loader.safe_status(self.preset, self.models, self.state)["state"], "ready")
        self.assertFalse(loader.install_lock_path(self.state, next_preset).exists())
        next_staging = loader.staging_directory(self.models, next_preset)
        self.assertEqual(list(next_staging.glob("payload-*")), [])

    def test_new_immutable_revision_keeps_old_artifact_and_becomes_active(self) -> None:
        self.write_offline_bundle()
        loader.install_preset(self.preset, self.models, state_root=self.state, source="offline", offline_dir=self.offline)
        first_install = loader.install_directory(self.models, self.preset)

        next_payload = b"next-revision"
        next_definition = fixture_preset(next_payload)
        next_definition["source"]["revision"] = "b" * 40
        next_preset = loader.parse_preset(next_definition)
        next_offline = self.root / "next-offline"
        next_offline.mkdir()
        (next_offline / "payload.bin").write_bytes(next_payload)
        (next_offline / "offline-manifest.json").write_text(
            json.dumps(loader.expected_offline_manifest(next_preset)), encoding="utf-8"
        )

        loader.install_preset(
            next_preset,
            self.models,
            state_root=self.state,
            source="offline",
            offline_dir=next_offline,
        )

        self.assertEqual((first_install / "payload.bin").read_bytes(), self.content)
        self.assertEqual(loader.active_directory(self.models, self.state, next_preset), loader.install_directory(self.models, next_preset))
        self.assertEqual(loader.safe_status(next_preset, self.models, self.state)["state"], "ready")

    def test_rejects_path_traversal_before_file_access(self) -> None:
        unsafe = fixture_preset(self.content)
        unsafe["files"] = [{"path": "../escape.bin", "sha256": "a" * 64, "size_bytes": 1}]

        with self.assertRaises(loader.LoaderError) as raised:
            loader.parse_preset(unsafe)

        self.assertEqual(raised.exception.code, "invalid_artifact_path")

    def test_rejects_a_mutable_branch_as_a_pinned_revision(self) -> None:
        mutable = fixture_preset(self.content)
        mutable["source"]["revision"] = "main"

        with self.assertRaises(loader.LoaderError) as raised:
            loader.parse_preset(mutable)

        self.assertEqual(raised.exception.code, "invalid_preset")

    def test_insufficient_model_storage_fails_before_staging_or_network(self) -> None:
        self.write_offline_bundle()

        with mock.patch.object(loader.shutil, "disk_usage", return_value=mock.Mock(free=1)):
            with self.assertRaises(loader.LoaderError) as raised:
                loader.install_preset(
                    self.preset,
                    self.models,
                    state_root=self.state,
                    source="offline",
                    offline_dir=self.offline,
                )

        self.assertEqual(raised.exception.code, "insufficient_model_storage")
        self.assertFalse(loader.staging_directory(self.models, self.preset).exists())

    def test_status_is_offline_and_does_not_leak_storage_paths(self) -> None:
        self.write_offline_bundle()
        loader.install_preset(self.preset, self.models, state_root=self.state, source="offline", offline_dir=self.offline)

        with mock.patch.object(loader, "download_modelscope_artifacts", side_effect=AssertionError("network")):
            status = loader.safe_status(self.preset, self.models, self.state)

        rendered = json.dumps(status)
        self.assertEqual(status["state"], "ready")
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("offline", rendered)

    def test_status_rejects_extra_artifacts_after_an_install(self) -> None:
        self.write_offline_bundle()
        loader.install_preset(
            self.preset,
            self.models,
            state_root=self.state,
            source="offline",
            offline_dir=self.offline,
        )
        active = loader.active_directory(self.models, self.state, self.preset)
        assert active is not None
        (active / "unverified.bin").write_bytes(b"not part of the manifest")

        status = loader.safe_status(self.preset, self.models, self.state)

        self.assertEqual(status["state"], "integrity_failed")
        self.assertEqual(status["error_code"], "artifact_unexpected")

    def test_modelscope_download_uses_the_pinned_sdk_contract(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeHubApi:
            def download_file(self, *args: object, **kwargs: object) -> Path:
                calls.append({"args": args, "kwargs": kwargs})
                return self_root / "payload.bin"

        module = ModuleType("modelscope_hub")
        module.HubApi = FakeHubApi  # type: ignore[attr-defined]
        self_root = self.root / "download-target"
        self_root.mkdir()

        with mock.patch.dict(sys.modules, {"modelscope_hub": module}):
            loader.download_modelscope_artifacts(self.preset, self_root)

        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["args"], ("fixture/model", "model", "payload.bin"))
        self.assertEqual(
            call["kwargs"],
            {
                "revision": "a" * 40,
                "local_dir": self_root,
                "expected_sha256": hashlib.sha256(self.content).hexdigest(),
            },
        )

    def test_same_preset_install_is_exclusive_across_callers(self) -> None:
        self.write_offline_bundle()
        started = threading.Event()
        continue_install = threading.Event()
        finished: list[object] = []
        original_copy = loader.copy_offline_artifacts

        def blocking_copy(*args: object, **kwargs: object) -> None:
            started.set()
            self.assertTrue(continue_install.wait(timeout=2.0))
            original_copy(*args, **kwargs)

        def first_install() -> None:
            try:
                finished.append(
                    loader.install_preset(
                        self.preset,
                        self.models,
                        state_root=self.state,
                        source="offline",
                        offline_dir=self.offline,
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                finished.append(exc)

        with mock.patch.object(loader, "copy_offline_artifacts", side_effect=blocking_copy):
            worker = threading.Thread(target=first_install)
            worker.start()
            self.assertTrue(started.wait(timeout=2.0))
            try:
                with self.assertRaises(loader.LoaderError) as raised:
                    loader.install_preset(
                        self.preset,
                        self.models,
                        state_root=self.state,
                        source="offline",
                        offline_dir=self.offline,
                        lock_timeout_seconds=0.0,
                    )
                self.assertEqual(raised.exception.code, "install_in_progress")
            finally:
                continue_install.set()
                worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(finished), 1)
        self.assertIsInstance(finished[0], dict)
        self.assertFalse(loader.install_lock_path(self.state, self.preset).exists())

    def test_lock_serializes_different_revisions_of_the_same_preset(self) -> None:
        next_definition = fixture_preset(b"next-revision")
        next_definition["source"]["revision"] = "b" * 40
        next_preset = loader.parse_preset(next_definition)

        with loader.held_install_lock(self.state, self.preset):
            with self.assertRaises(loader.LoaderError) as raised:
                loader.install_preset(
                    next_preset,
                    self.models,
                    state_root=self.state,
                    source="offline",
                    offline_dir=self.offline,
                    lock_timeout_seconds=0.0,
                )

        self.assertEqual(raised.exception.code, "install_in_progress")

    def test_explicit_recovery_removes_only_the_preset_lock(self) -> None:
        lock_path = loader.install_lock_path(self.state, self.preset)
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text("owner-token", encoding="utf-8")

        recovered = loader.recover_install_lock(self.state, self.preset)

        self.assertEqual(recovered, {"preset_id": "fixture-model", "state": "lock_recovered"})
        self.assertFalse(lock_path.exists())
        self.assertEqual(
            loader.recover_install_lock(self.state, self.preset),
            {"preset_id": "fixture-model", "state": "not_locked"},
        )

    def test_recover_lock_requires_an_explicit_confirmation_flag(self) -> None:
        arguments = loader.build_parser().parse_args(["recover-lock", "--preset", "fixture-model"])

        with mock.patch.object(loader, "load_preset", return_value=self.preset):
            with self.assertRaises(loader.LoaderError) as raised:
                loader.command_recover_lock(arguments)

        self.assertEqual(raised.exception.code, "lock_recovery_confirmation_required")

    def test_checked_release_presets_parse_with_the_loader_contract(self) -> None:
        for preset_path in sorted(PRESETS_DIR.glob("*.json")):
            if preset_path.name == "schema.json":
                continue
            parsed = loader.parse_preset(json.loads(preset_path.read_text(encoding="utf-8")))
            self.assertEqual(parsed.preset_id, preset_path.stem)


if __name__ == "__main__":
    unittest.main()
