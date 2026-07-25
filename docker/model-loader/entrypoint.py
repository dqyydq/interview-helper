"""Verified, Docker-only model artifact loader.

The commands in this module deliberately separate the only networked action
(``install --source modelscope``) from ``status`` and ``verify``.  Runtime
containers only consume an already verified active revision; they must never
invoke this program during an interview.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from uuid import uuid4


PRESET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
INSTALL_LOCK_POLL_SECONDS = 0.1
# A model install can legitimately take much longer than a fixed TTL on a
# student connection. Never steal a live-looking lock: callers fail safely and
# can retry after the owning command exits.
INSTALL_LOCK_TIMEOUT_SECONDS = 0.0


class LoaderError(RuntimeError):
    """A safe, stable failure code that can be shown by a settings UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArtifactFile:
    path: PurePosixPath
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Preset:
    preset_id: str
    capability: str
    repository: str
    revision: str
    revision_policy: str
    files: tuple[ArtifactFile, ...]
    download_bytes: int
    recommended_free_bytes: int


def fail_if_not(condition: bool, code: str) -> None:
    if not condition:
        raise LoaderError(code)


def safe_relative_path(value: object) -> PurePosixPath:
    """Validate a manifest path before it is ever joined to a filesystem root."""

    fail_if_not(isinstance(value, str) and value != "", "invalid_artifact_path")
    fail_if_not("\\" not in value and ":" not in value, "invalid_artifact_path")
    candidate = PurePosixPath(value)
    fail_if_not(not candidate.is_absolute(), "invalid_artifact_path")
    fail_if_not(all(part not in {"", ".", ".."} for part in candidate.parts), "invalid_artifact_path")
    return candidate


def inside(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and reject a traversal or symlink escape."""

    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve(strict=False)
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise LoaderError("artifact_path_outside_root") from exc
    return candidate_resolved


def artifact_path(root: Path, relative: PurePosixPath) -> Path:
    return inside(root, root.joinpath(*relative.parts))


def read_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoaderError(code) from exc


def parse_preset(payload: object) -> Preset:
    fail_if_not(isinstance(payload, Mapping), "invalid_preset")
    allowed = {
        "schema_version",
        "id",
        "display_name",
        "capability",
        "source",
        "files",
        "storage",
        "license",
        "runtime",
        "embedding",
        "asr",
    }
    fail_if_not(set(payload).issubset(allowed), "invalid_preset")
    fail_if_not(payload.get("schema_version") == 1, "invalid_preset")

    preset_id = payload.get("id")
    display_name = payload.get("display_name")
    capability = payload.get("capability")
    source = payload.get("source")
    files_payload = payload.get("files")
    fail_if_not(
        isinstance(preset_id, str) and PRESET_ID_PATTERN.fullmatch(preset_id) is not None,
        "invalid_preset",
    )
    fail_if_not(isinstance(display_name, str) and display_name != "", "invalid_preset")
    fail_if_not(capability in {"embedding", "transcription"}, "invalid_preset")
    fail_if_not(isinstance(source, Mapping), "invalid_preset")
    fail_if_not(set(source) == {"provider", "repository", "revision", "revision_policy"}, "invalid_preset")

    repository = source.get("repository")
    revision = source.get("revision")
    revision_policy = source.get("revision_policy")
    fail_if_not(source.get("provider") == "modelscope", "invalid_preset")
    fail_if_not(
        isinstance(repository, str) and REPOSITORY_PATTERN.fullmatch(repository) is not None,
        "invalid_preset",
    )
    fail_if_not(
        isinstance(revision, str) and COMMIT_SHA_PATTERN.fullmatch(revision) is not None,
        "invalid_preset",
    )
    fail_if_not(revision_policy == "pinned", "invalid_preset")

    fail_if_not(isinstance(files_payload, list) and len(files_payload) > 0, "invalid_preset")
    artifacts: list[ArtifactFile] = []
    seen_paths: set[PurePosixPath] = set()
    for item in files_payload:
        fail_if_not(isinstance(item, Mapping), "invalid_preset")
        fail_if_not(set(item) == {"path", "sha256", "size_bytes"}, "invalid_preset")
        relative = safe_relative_path(item.get("path"))
        checksum = item.get("sha256")
        size_bytes = item.get("size_bytes")
        fail_if_not(isinstance(checksum, str) and SHA256_PATTERN.fullmatch(checksum) is not None, "invalid_preset")
        fail_if_not(isinstance(size_bytes, int) and size_bytes > 0, "invalid_preset")
        fail_if_not(relative not in seen_paths, "invalid_preset")
        seen_paths.add(relative)
        artifacts.append(ArtifactFile(relative, checksum, size_bytes))

    storage = payload.get("storage")
    runtime = payload.get("runtime")
    fail_if_not(
        isinstance(storage, Mapping) and set(storage) == {"download_bytes", "recommended_free_bytes"},
        "invalid_preset",
    )
    fail_if_not(all(isinstance(storage.get(key), int) and storage[key] > 0 for key in storage), "invalid_preset")
    fail_if_not(storage["download_bytes"] == sum(artifact.size_bytes for artifact in artifacts), "invalid_preset")
    fail_if_not(storage["recommended_free_bytes"] >= storage["download_bytes"], "invalid_preset")
    fail_if_not(isinstance(runtime, Mapping) and set(runtime) == {"consumer", "network_at_runtime"}, "invalid_preset")
    fail_if_not(runtime.get("consumer") in {"tei", "funasr"} and runtime.get("network_at_runtime") is False, "invalid_preset")
    fail_if_not(isinstance(payload.get("license"), str) and payload["license"] != "", "invalid_preset")
    fail_if_not(
        isinstance(payload.get("display_name"), str) and 0 < len(payload["display_name"]) <= 120,
        "invalid_preset",
    )
    if capability == "embedding":
        fail_if_not("asr" not in payload, "invalid_preset")
        embedding = payload.get("embedding")
        fail_if_not(isinstance(embedding, Mapping), "invalid_preset")
        fail_if_not(set(embedding) == {"dimensions", "normalize", "query_prefix", "passage_prefix"}, "invalid_preset")
        fail_if_not(isinstance(embedding.get("dimensions"), int) and embedding["dimensions"] > 0, "invalid_preset")
        fail_if_not(isinstance(embedding.get("normalize"), bool), "invalid_preset")
        fail_if_not(all(isinstance(embedding.get(key), str) for key in ("query_prefix", "passage_prefix")), "invalid_preset")
        fail_if_not(runtime.get("consumer") == "tei", "invalid_preset")
    else:
        fail_if_not("embedding" not in payload, "invalid_preset")
        asr = payload.get("asr")
        fail_if_not(isinstance(asr, Mapping) and set(asr) == {"languages", "supports_itn"}, "invalid_preset")
        fail_if_not(isinstance(asr.get("languages"), list) and all(isinstance(language, str) for language in asr["languages"]), "invalid_preset")
        fail_if_not(isinstance(asr.get("supports_itn"), bool), "invalid_preset")
        fail_if_not(runtime.get("consumer") == "funasr", "invalid_preset")

    return Preset(
        preset_id=preset_id,
        capability=capability,
        repository=repository,
        revision=revision,
        revision_policy=revision_policy,
        files=tuple(artifacts),
        download_bytes=storage["download_bytes"],
        recommended_free_bytes=storage["recommended_free_bytes"],
    )


def load_preset(presets_dir: Path, preset_id: str) -> Preset:
    fail_if_not(PRESET_ID_PATTERN.fullmatch(preset_id) is not None, "unknown_preset")
    root = presets_dir.resolve()
    preset_file = inside(root, root / f"{preset_id}.json")
    return parse_preset(read_json(preset_file, "invalid_preset"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_tree(preset: Preset, root: Path) -> None:
    """Perform an entirely offline structural and checksum verification."""

    fail_if_not(root.is_dir() and not root.is_symlink(), "artifact_missing")
    expected_paths = {artifact.path for artifact in preset.files}
    discovered_paths: set[PurePosixPath] = set()
    for item in root.rglob("*"):
        relative = PurePosixPath(item.relative_to(root).as_posix())
        fail_if_not(not item.is_symlink(), "artifact_unexpected")
        if item.is_dir():
            continue
        fail_if_not(item.is_file() and relative in expected_paths, "artifact_unexpected")
        discovered_paths.add(relative)
    fail_if_not(discovered_paths == expected_paths, "artifact_missing")
    for artifact in preset.files:
        target = artifact_path(root, artifact.path)
        fail_if_not(target.exists() and target.is_file() and not target.is_symlink(), "artifact_missing")
        fail_if_not(target.stat().st_size == artifact.size_bytes, "artifact_size_mismatch")
        fail_if_not(sha256_file(target) == artifact.sha256, "artifact_checksum_mismatch")


def expected_offline_manifest(preset: Preset) -> dict[str, object]:
    return {
        "manifest_version": 1,
        "preset_id": preset.preset_id,
        "revision": preset.revision,
        "files": [
            {"path": artifact.path.as_posix(), "sha256": artifact.sha256, "size_bytes": artifact.size_bytes}
            for artifact in preset.files
        ],
    }


def verify_offline_manifest(preset: Preset, offline_dir: Path) -> None:
    root = offline_dir.resolve()
    manifest_path = inside(root, root / "offline-manifest.json")
    fail_if_not(manifest_path.is_file() and not manifest_path.is_symlink(), "offline_manifest_missing")
    supplied = read_json(manifest_path, "invalid_offline_manifest")
    fail_if_not(supplied == expected_offline_manifest(preset), "offline_manifest_mismatch")


def copy_offline_artifacts(preset: Preset, offline_dir: Path, destination: Path) -> None:
    verify_offline_manifest(preset, offline_dir)
    root = offline_dir.resolve()
    for artifact in preset.files:
        source = artifact_path(root, artifact.path)
        fail_if_not(source.is_file() and not source.is_symlink(), "artifact_missing")
        target = artifact_path(destination, artifact.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def download_modelscope_artifacts(preset: Preset, destination: Path) -> None:
    """The sole networked code path; it is reachable only from install."""

    try:
        from modelscope_hub import HubApi

        client = HubApi()
        for artifact in preset.files:
            downloaded = client.download_file(
                preset.repository,
                "model",
                artifact.path.as_posix(),
                revision=preset.revision,
                local_dir=destination,
                expected_sha256=artifact.sha256,
            )
            inside(destination, Path(downloaded))
    except LoaderError:
        raise
    except Exception as exc:
        # Do not retain SDK exception text: it may include a token or local path.
        raise LoaderError("modelscope_download_failed") from exc


def active_marker_path(state_root: Path, preset: Preset) -> Path:
    return artifact_path(state_root, PurePosixPath("active") / f"{preset.preset_id}.json")


def install_directory(models_root: Path, preset: Preset) -> Path:
    return artifact_path(models_root, PurePosixPath("installs") / preset.preset_id / preset.revision)


def staging_directory(models_root: Path, preset: Preset) -> Path:
    return artifact_path(models_root, PurePosixPath("staging") / preset.preset_id / preset.revision)


def cleanup_staging_payload(staging: Path, payload: Path) -> None:
    """Best-effort cleanup of only this invocation's generated staging tree."""

    try:
        staging_root = staging.resolve()
        fail_if_not(payload.parent == staging_root, "invalid_staging_payload")
        fail_if_not(payload.name.startswith("payload-"), "invalid_staging_payload")
        metadata = payload.lstat()
        fail_if_not(stat.S_ISDIR(metadata.st_mode), "invalid_staging_payload")
        shutil.rmtree(payload)
    except FileNotFoundError:
        return
    except (LoaderError, OSError):
        # An original download/checksum error is more useful to the caller;
        # never follow an unexpected path merely to clean it up.
        return


def ensure_storage_available(models_root: Path, preset: Preset) -> None:
    """Fail before any network access when the Docker model volume is too full."""

    try:
        free_bytes = shutil.disk_usage(models_root).free
    except OSError as exc:
        raise LoaderError("model_storage_probe_failed") from exc
    fail_if_not(free_bytes >= preset.recommended_free_bytes, "insufficient_model_storage")


def install_lock_path(state_root: Path, preset: Preset) -> Path:
    """Return the one shared lock for all revisions of a logical preset."""

    return artifact_path(state_root, PurePosixPath("locks") / f"{preset.preset_id}.lock")


def _release_install_lock(lock_path: Path, owner_token: str) -> None:
    """Release only a regular lock we still own; recovery is explicit otherwise."""

    try:
        metadata = lock_path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return
        with lock_path.open("r", encoding="utf-8") as handle:
            if handle.read(128) != owner_token:
                return
        lock_path.unlink()
    except OSError:
        return


@contextmanager
def held_install_lock(
    state_root: Path,
    preset: Preset,
    *,
    timeout_seconds: float = INSTALL_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Acquire a portable, shared-volume lock before touching a preset."""

    fail_if_not(timeout_seconds >= 0, "invalid_install_lock_timeout")
    lock_path = install_lock_path(state_root, preset)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fail_if_not(lock_path.parent.is_dir() and not lock_path.parent.is_symlink(), "invalid_state_root")
    owner_token = uuid4().hex
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LoaderError("install_in_progress")
            time.sleep(min(INSTALL_LOCK_POLL_SECONDS, remaining))
            continue
        except OSError as exc:
            raise LoaderError("install_lock_unavailable") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(owner_token)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            _release_install_lock(lock_path, owner_token)
            raise LoaderError("install_lock_unavailable") from exc
        break
    try:
        yield
    finally:
        _release_install_lock(lock_path, owner_token)


def recover_install_lock(state_root: Path, preset: Preset) -> dict[str, object]:
    """Explicitly clear a lock after the operator confirms no install is active.

    This deliberately never runs as part of ``install``: a slow 2 GB download
    is not evidence that its owner died. The command only removes the exact,
    regular lock file for an allowlisted preset and does not touch artifacts or
    active markers.
    """

    state_root.mkdir(parents=True, exist_ok=True)
    fail_if_not(state_root.is_dir() and not state_root.is_symlink(), "invalid_state_root")
    lock_path = install_lock_path(state_root, preset)
    try:
        metadata = lock_path.lstat()
    except FileNotFoundError:
        return {"preset_id": preset.preset_id, "state": "not_locked"}
    except OSError as exc:
        raise LoaderError("install_lock_unavailable") from exc
    fail_if_not(stat.S_ISREG(metadata.st_mode), "invalid_install_lock")
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return {"preset_id": preset.preset_id, "state": "not_locked"}
    except OSError as exc:
        raise LoaderError("install_lock_unavailable") from exc
    return {"preset_id": preset.preset_id, "state": "lock_recovered"}


def write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def activate(state_root: Path, preset: Preset) -> None:
    marker = active_marker_path(state_root, preset)
    # The marker contains no host path, source URL, or credential. os.replace is
    # atomic within the named volume, preserving the previous active revision on
    # a failed staging/verification run.
    write_atomic_json(
        marker,
        {
            "marker_version": 1,
            "preset_id": preset.preset_id,
            "revision": preset.revision,
            "integrity": "verified",
            "activated_at_epoch": int(time.time()),
        },
    )


def active_directory(models_root: Path, state_root: Path, preset: Preset) -> Path | None:
    marker = active_marker_path(state_root, preset)
    if not marker.is_file() or marker.is_symlink():
        return None
    payload = read_json(marker, "invalid_active_marker")
    if not isinstance(payload, Mapping):
        raise LoaderError("invalid_active_marker")
    if payload.get("marker_version") != 1 or payload.get("preset_id") != preset.preset_id:
        raise LoaderError("invalid_active_marker")
    if payload.get("revision") != preset.revision or payload.get("integrity") != "verified":
        raise LoaderError("invalid_active_marker")
    return install_directory(models_root, preset)


def install_preset(
    preset: Preset,
    models_root: Path,
    *,
    state_root: Path,
    source: str,
    offline_dir: Path | None = None,
    lock_timeout_seconds: float = INSTALL_LOCK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Stage, verify, promote, then atomically mark one revision active."""

    fail_if_not(source in {"modelscope", "offline"}, "invalid_source")
    models_root.mkdir(parents=True, exist_ok=True)
    fail_if_not(models_root.is_dir() and not models_root.is_symlink(), "invalid_models_root")
    state_root.mkdir(parents=True, exist_ok=True)
    fail_if_not(state_root.is_dir() and not state_root.is_symlink(), "invalid_state_root")
    with held_install_lock(state_root, preset, timeout_seconds=lock_timeout_seconds):
        target = install_directory(models_root, preset)
        if target.exists():
            verify_tree(preset, target)
            activate(state_root, preset)
            return safe_status(preset, models_root, state_root)

        ensure_storage_available(models_root, preset)
        staging = staging_directory(models_root, preset)
        staging.mkdir(parents=True, exist_ok=True)
        payload = artifact_path(staging, PurePosixPath(f"payload-{uuid4().hex}"))
        payload.mkdir()
        try:
            if source == "offline":
                fail_if_not(offline_dir is not None, "offline_source_required")
                copy_offline_artifacts(preset, offline_dir, payload)
            else:
                download_modelscope_artifacts(preset, payload)

            verify_tree(preset, payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            # The shared preset lock excludes concurrent promotion and activation.
            os.replace(payload, target)
            verify_tree(preset, target)
            activate(state_root, preset)
            return safe_status(preset, models_root, state_root)
        finally:
            cleanup_staging_payload(staging, payload)


def safe_status(preset: Preset, models_root: Path, state_root: Path) -> dict[str, object]:
    """Return only UI-safe metadata. This performs no network access."""

    try:
        directory = active_directory(models_root, state_root, preset)
        if directory is None:
            return {"preset_id": preset.preset_id, "revision": preset.revision, "state": "not_installed"}
        verify_tree(preset, directory)
        return {
            "preset_id": preset.preset_id,
            "revision": preset.revision,
            "capability": preset.capability,
            "state": "ready",
            "integrity": "verified",
        }
    except LoaderError as exc:
        return {
            "preset_id": preset.preset_id,
            "revision": preset.revision,
            "state": "integrity_failed",
            "error_code": exc.code,
        }


def command_install(arguments: argparse.Namespace) -> dict[str, object]:
    preset = load_preset(arguments.presets_dir, arguments.preset)
    return install_preset(
        preset,
        arguments.models_root,
        state_root=arguments.state_root,
        source=arguments.source,
        offline_dir=arguments.offline_dir,
    )


def command_status(arguments: argparse.Namespace) -> dict[str, object]:
    preset = load_preset(arguments.presets_dir, arguments.preset)
    return safe_status(preset, arguments.models_root, arguments.state_root)


def command_verify(arguments: argparse.Namespace) -> dict[str, object]:
    preset = load_preset(arguments.presets_dir, arguments.preset)
    status = safe_status(preset, arguments.models_root, arguments.state_root)
    if status["state"] != "ready":
        raise LoaderError(str(status.get("error_code", "artifact_not_ready")))
    return status


def command_recover_lock(arguments: argparse.Namespace) -> dict[str, object]:
    preset = load_preset(arguments.presets_dir, arguments.preset)
    fail_if_not(arguments.confirm_no_active_install is True, "lock_recovery_confirmation_required")
    return recover_install_lock(arguments.state_root, preset)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verified local-model artifact loader")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "status", "verify", "recover-lock"):
        command = subcommands.add_parser(name)
        command.add_argument("--preset", required=True)
        command.add_argument("--presets-dir", type=Path, default=Path("/presets"))
        command.add_argument("--models-root", type=Path, default=Path("/models"))
        command.add_argument("--state-root", type=Path, default=Path("/state"))
    install = subcommands.choices["install"]
    install.add_argument("--source", choices=("modelscope", "offline"), default="modelscope")
    install.add_argument("--offline-dir", type=Path, default=Path("/offline"))
    recover_lock = subcommands.choices["recover-lock"]
    recover_lock.add_argument("--confirm-no-active-install", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "install":
            result = command_install(arguments)
        elif arguments.command == "status":
            result = command_status(arguments)
        elif arguments.command == "verify":
            result = command_verify(arguments)
        else:
            result = command_recover_lock(arguments)
        print(json.dumps(result, sort_keys=True))
        return 0
    except LoaderError as exc:
        print(json.dumps({"state": "error", "error_code": exc.code}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        # A production container must not leak raw paths or environment values.
        print(json.dumps({"state": "error", "error_code": "internal_error"}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
