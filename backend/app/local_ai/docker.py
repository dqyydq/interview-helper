"""Safe, bounded Docker Desktop readiness checks for local AI.

Only fixed command tuples live in this module. Request data never reaches
``subprocess.run`` and raw command output is discarded after the tiny,
allowlisted facts needed for the public diagnostics response are derived.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol

from app.core.config import REPOSITORY_ROOT
from app.schemas.local_ai import (
    DockerComponentDiagnostics,
    DockerComponentState,
    DockerEngineDiagnostics,
    LocalAiDockerDiagnostics,
    LocalAiDockerDiagnosticsStatus,
)

_DOCKER_DAEMON_PROBE: tuple[str, ...] = (
    "docker",
    "version",
    "--format",
    "{{.Server.Version}}",
)
_DOCKER_COMPOSE_PROBE: tuple[str, ...] = ("docker", "compose", "version", "--short")
_DOCKER_ENGINE_PROBE: tuple[str, ...] = ("docker", "info", "--format", "{{.OSType}}")
_DOCKER_CONTEXT_PROBE: tuple[str, ...] = ("docker", "context", "inspect", "--format", "{{json .}}")
_DOCKER_COMPOSE_CONFIG_PROBE: tuple[str, ...] = (
    "docker",
    "compose",
    "--profile",
    "model-loader",
    "config",
    "--quiet",
)
_MAX_PROBE_OUTPUT_CHARS = 1_024


class ProbeState(StrEnum):
    AVAILABLE = "available"
    EXECUTABLE_MISSING = "executable_missing"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    state: ProbeState


@dataclass(frozen=True, slots=True)
class EngineProbeResult:
    state: ProbeState
    container_os: Literal["linux", "windows", "unknown"] = "unknown"
    locality: Literal["local", "remote", "unknown"] = "unknown"


@dataclass(frozen=True, slots=True)
class _TextProbeResult:
    state: ProbeState
    output: str = ""


class DockerProbeRunner(Protocol):
    """Injectable runner with no public API for caller-controlled commands."""

    def probe_daemon(self, *, timeout_seconds: float) -> ProbeResult: ...

    def probe_compose(self, *, timeout_seconds: float) -> ProbeResult: ...

    def probe_engine(self, *, timeout_seconds: float) -> EngineProbeResult: ...

    def probe_compose_config(self, *, timeout_seconds: float) -> ProbeResult: ...


class SubprocessDockerProbeRunner:
    """Execute fixed Docker CLI probes without a shell or external input."""

    def probe_daemon(self, *, timeout_seconds: float) -> ProbeResult:
        return self._run_silent(_DOCKER_DAEMON_PROBE, timeout_seconds=timeout_seconds)

    def probe_compose(self, *, timeout_seconds: float) -> ProbeResult:
        return self._run_silent(_DOCKER_COMPOSE_PROBE, timeout_seconds=timeout_seconds)

    def probe_compose_config(self, *, timeout_seconds: float) -> ProbeResult:
        return self._run_silent(_DOCKER_COMPOSE_CONFIG_PROBE, timeout_seconds=timeout_seconds)

    def probe_engine(self, *, timeout_seconds: float) -> EngineProbeResult:
        deadline = time.monotonic() + timeout_seconds
        os_result = self._run_text(_DOCKER_ENGINE_PROBE, timeout_seconds=timeout_seconds)
        if os_result.state is not ProbeState.AVAILABLE:
            return EngineProbeResult(state=os_result.state)
        container_os = os_result.output.strip().casefold()
        if container_os not in {"linux", "windows"}:
            return EngineProbeResult(state=ProbeState.UNKNOWN)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return EngineProbeResult(state=ProbeState.TIMED_OUT, container_os=container_os)
        context_result = self._run_text(_DOCKER_CONTEXT_PROBE, timeout_seconds=remaining)
        if context_result.state is not ProbeState.AVAILABLE:
            return EngineProbeResult(state=context_result.state, container_os=container_os)
        try:
            contexts = json.loads(context_result.output)
            context = contexts[0] if isinstance(contexts, list) else contexts
            host = context["Endpoints"]["docker"]["Host"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            return EngineProbeResult(state=ProbeState.UNKNOWN, container_os=container_os)
        if not isinstance(host, str):
            return EngineProbeResult(state=ProbeState.UNKNOWN, container_os=container_os)
        configured_host = os.environ.get("DOCKER_HOST")
        effective_host = configured_host if configured_host else host
        locality = "local" if effective_host.startswith(("npipe://", "unix://")) else "remote"
        return EngineProbeResult(
            state=ProbeState.AVAILABLE,
            container_os=container_os,
            locality=locality,
        )

    @staticmethod
    def _run_silent(command: tuple[str, ...], *, timeout_seconds: float) -> ProbeResult:
        result = SubprocessDockerProbeRunner._run(command, timeout_seconds=timeout_seconds)
        return ProbeResult(result.state)

    @staticmethod
    def _run_text(command: tuple[str, ...], *, timeout_seconds: float) -> _TextProbeResult:
        return SubprocessDockerProbeRunner._run(command, timeout_seconds=timeout_seconds, text=True)

    @staticmethod
    def _run(
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        text: bool = False,
    ) -> _TextProbeResult:
        try:
            completed = subprocess.run(
                command,
                check=False,
                shell=False,
                stdout=subprocess.PIPE if text else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                timeout=timeout_seconds,
                cwd=REPOSITORY_ROOT,
            )
        except FileNotFoundError:
            return _TextProbeResult(ProbeState.EXECUTABLE_MISSING)
        except subprocess.TimeoutExpired:
            return _TextProbeResult(ProbeState.TIMED_OUT)
        except OSError:
            return _TextProbeResult(ProbeState.UNKNOWN)
        state = ProbeState.AVAILABLE if completed.returncode == 0 else ProbeState.UNAVAILABLE
        output = str(completed.stdout or "")[:_MAX_PROBE_OUTPUT_CHARS] if text else ""
        return _TextProbeResult(state, output)


def _safe_probe(
    probe: Callable[..., ProbeResult],
    *,
    timeout_seconds: float,
) -> ProbeResult:
    """Normalize unexpected runner failures without retaining exception text."""

    try:
        result = probe(timeout_seconds=timeout_seconds)
    except Exception:
        return ProbeResult(ProbeState.UNKNOWN)
    return result if isinstance(result, ProbeResult) else ProbeResult(ProbeState.UNKNOWN)


def _safe_engine_probe(
    probe: Callable[..., EngineProbeResult],
    *,
    timeout_seconds: float,
) -> EngineProbeResult:
    try:
        result = probe(timeout_seconds=timeout_seconds)
    except Exception:
        return EngineProbeResult(ProbeState.UNKNOWN)
    if isinstance(result, EngineProbeResult):
        return result
    return EngineProbeResult(ProbeState.UNKNOWN)


def _component(state: ProbeState) -> DockerComponentDiagnostics:
    state_map = {
        ProbeState.AVAILABLE: DockerComponentState.AVAILABLE,
        ProbeState.EXECUTABLE_MISSING: DockerComponentState.EXECUTABLE_MISSING,
        ProbeState.UNAVAILABLE: DockerComponentState.UNAVAILABLE,
        ProbeState.TIMED_OUT: DockerComponentState.TIMED_OUT,
        ProbeState.UNKNOWN: DockerComponentState.UNKNOWN,
        ProbeState.NOT_CHECKED: DockerComponentState.NOT_CHECKED,
    }
    return DockerComponentDiagnostics(state=state_map[state])


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _probe_with_budget(
    probe: Callable[..., ProbeResult],
    *,
    deadline: float,
) -> ProbeResult:
    remaining = _remaining(deadline)
    if remaining <= 0:
        return ProbeResult(ProbeState.TIMED_OUT)
    return _safe_probe(probe, timeout_seconds=remaining)


def _engine_probe_with_budget(
    probe: Callable[..., EngineProbeResult],
    *,
    deadline: float,
) -> EngineProbeResult:
    remaining = _remaining(deadline)
    if remaining <= 0:
        return EngineProbeResult(ProbeState.TIMED_OUT)
    return _safe_engine_probe(probe, timeout_seconds=remaining)


def _collect_docker_diagnostics(
    *,
    runner: DockerProbeRunner,
    timeout_seconds: float,
) -> LocalAiDockerDiagnostics:
    deadline = time.monotonic() + timeout_seconds
    docker_result = _probe_with_budget(runner.probe_daemon, deadline=deadline)
    compose_result = ProbeResult(ProbeState.NOT_CHECKED)
    engine_result = EngineProbeResult(ProbeState.NOT_CHECKED)
    compose_config_result = ProbeResult(ProbeState.NOT_CHECKED)

    if docker_result.state is not ProbeState.AVAILABLE:
        status = LocalAiDockerDiagnosticsStatus.UNAVAILABLE
        next_step = "install_or_start_docker"
    else:
        compose_result = _probe_with_budget(runner.probe_compose, deadline=deadline)
        if compose_result.state is not ProbeState.AVAILABLE:
            status = LocalAiDockerDiagnosticsStatus.DEGRADED
            next_step = "enable_docker_compose"
        else:
            engine_result = _engine_probe_with_budget(runner.probe_engine, deadline=deadline)
            if (
                engine_result.state is not ProbeState.AVAILABLE
                or engine_result.container_os != "linux"
                or engine_result.locality != "local"
            ):
                status = LocalAiDockerDiagnosticsStatus.DEGRADED
                next_step = "switch_to_local_linux_docker"
            else:
                compose_config_result = _probe_with_budget(
                    runner.probe_compose_config,
                    deadline=deadline,
                )
                if compose_config_result.state is ProbeState.AVAILABLE:
                    status = LocalAiDockerDiagnosticsStatus.READY
                    next_step = "configure_local_ai"
                else:
                    status = LocalAiDockerDiagnosticsStatus.DEGRADED
                    next_step = "repair_compose_project"

    return LocalAiDockerDiagnostics(
        checked_at=datetime.now(UTC),
        status=status,
        docker=_component(docker_result.state),
        compose=_component(compose_result.state),
        engine=DockerEngineDiagnostics(
            state=_component(engine_result.state).state,
            container_os=engine_result.container_os,
            locality=engine_result.locality,
        ),
        compose_project=_component(compose_config_result.state),
        gpu_check="not_checked",
        next_step=next_step,
    )


_cache_lock = threading.Lock()
_cache_expires_at = 0.0
_cached_diagnostics: LocalAiDockerDiagnostics | None = None


def clear_docker_diagnostics_cache() -> None:
    """Test helper; production callers use the short single-flight cache."""

    global _cache_expires_at, _cached_diagnostics
    with _cache_lock:
        _cache_expires_at = 0.0
        _cached_diagnostics = None


def collect_docker_diagnostics(
    *,
    runner: DockerProbeRunner | None = None,
    timeout_seconds: float,
    cache_seconds: float = 5.0,
) -> LocalAiDockerDiagnostics:
    """Collect local Linux Docker readiness without starting containers.

    Real endpoint calls share a short cache under a single lock, so dashboard
    refreshes cannot create an unbounded number of Docker CLI subprocesses.
    Supplying a runner intentionally bypasses that cache for deterministic tests.
    """

    global _cache_expires_at, _cached_diagnostics

    if runner is not None:
        return _collect_docker_diagnostics(runner=runner, timeout_seconds=timeout_seconds)

    now = time.monotonic()
    with _cache_lock:
        if _cached_diagnostics is not None and now < _cache_expires_at:
            return _cached_diagnostics
        result = _collect_docker_diagnostics(
            runner=SubprocessDockerProbeRunner(),
            timeout_seconds=timeout_seconds,
        )
        _cached_diagnostics = result
        _cache_expires_at = time.monotonic() + max(0.0, cache_seconds)
        return result
