import subprocess

import pytest

from app.local_ai import docker


class FakeRunner:
    def __init__(
        self,
        *,
        daemon: docker.ProbeResult,
        compose: docker.ProbeResult,
        engine: docker.EngineProbeResult | None = None,
        compose_config: docker.ProbeResult | None = None,
    ) -> None:
        self.daemon = daemon
        self.compose = compose
        self.engine = engine or docker.EngineProbeResult(
            docker.ProbeState.AVAILABLE,
            container_os="linux",
            locality="local",
        )
        self.compose_config = compose_config or docker.ProbeResult(docker.ProbeState.AVAILABLE)
        self.calls: list[str] = []

    def probe_daemon(self, *, timeout_seconds: float) -> docker.ProbeResult:
        assert 0 < timeout_seconds <= 1.0
        self.calls.append("daemon")
        return self.daemon

    def probe_compose(self, *, timeout_seconds: float) -> docker.ProbeResult:
        assert 0 < timeout_seconds <= 1.0
        self.calls.append("compose")
        return self.compose

    def probe_engine(self, *, timeout_seconds: float) -> docker.EngineProbeResult:
        assert 0 < timeout_seconds <= 1.0
        self.calls.append("engine")
        return self.engine

    def probe_compose_config(self, *, timeout_seconds: float) -> docker.ProbeResult:
        assert 0 < timeout_seconds <= 1.0
        self.calls.append("compose_config")
        return self.compose_config


def test_docker_diagnostics_reports_ready_without_exposing_probe_output() -> None:
    runner = FakeRunner(
        daemon=docker.ProbeResult(docker.ProbeState.AVAILABLE),
        compose=docker.ProbeResult(docker.ProbeState.AVAILABLE),
    )

    diagnostics = docker.collect_docker_diagnostics(runner=runner, timeout_seconds=1.0)

    assert diagnostics.status == "ready"
    assert diagnostics.docker.state == "available"
    assert diagnostics.compose.state == "available"
    assert diagnostics.engine.container_os == "linux"
    assert diagnostics.engine.locality == "local"
    assert diagnostics.compose_project.state == "available"
    assert diagnostics.gpu_check == "not_checked"
    assert diagnostics.next_step == "configure_local_ai"
    assert runner.calls == ["daemon", "compose", "engine", "compose_config"]
    assert "stdout" not in diagnostics.model_dump()
    assert "stderr" not in diagnostics.model_dump()


def test_docker_diagnostics_skips_compose_when_daemon_is_unavailable() -> None:
    runner = FakeRunner(
        daemon=docker.ProbeResult(docker.ProbeState.EXECUTABLE_MISSING),
        compose=docker.ProbeResult(docker.ProbeState.AVAILABLE),
    )

    diagnostics = docker.collect_docker_diagnostics(runner=runner, timeout_seconds=1.0)

    assert diagnostics.status == "unavailable"
    assert diagnostics.docker.state == "executable_missing"
    assert diagnostics.compose.state == "not_checked"
    assert diagnostics.next_step == "install_or_start_docker"
    assert diagnostics.engine.state == "not_checked"
    assert diagnostics.compose_project.state == "not_checked"
    assert runner.calls == ["daemon"]


@pytest.mark.parametrize(
    "engine",
    [
        docker.EngineProbeResult(
            docker.ProbeState.AVAILABLE,
            container_os="windows",
            locality="local",
        ),
        docker.EngineProbeResult(
            docker.ProbeState.AVAILABLE,
            container_os="linux",
            locality="remote",
        ),
        docker.EngineProbeResult(docker.ProbeState.TIMED_OUT),
    ],
)
def test_docker_diagnostics_requires_a_local_linux_engine(
    engine: docker.EngineProbeResult,
) -> None:
    runner = FakeRunner(
        daemon=docker.ProbeResult(docker.ProbeState.AVAILABLE),
        compose=docker.ProbeResult(docker.ProbeState.AVAILABLE),
        engine=engine,
    )

    diagnostics = docker.collect_docker_diagnostics(runner=runner, timeout_seconds=1.0)

    assert diagnostics.status == "degraded"
    assert diagnostics.next_step == "switch_to_local_linux_docker"
    assert diagnostics.compose_project.state == "not_checked"
    assert runner.calls == ["daemon", "compose", "engine"]


def test_docker_diagnostics_requires_a_valid_compose_project() -> None:
    runner = FakeRunner(
        daemon=docker.ProbeResult(docker.ProbeState.AVAILABLE),
        compose=docker.ProbeResult(docker.ProbeState.AVAILABLE),
        compose_config=docker.ProbeResult(docker.ProbeState.UNAVAILABLE),
    )

    diagnostics = docker.collect_docker_diagnostics(runner=runner, timeout_seconds=1.0)

    assert diagnostics.status == "degraded"
    assert diagnostics.compose_project.state == "unavailable"
    assert diagnostics.next_step == "repair_compose_project"


def test_docker_diagnostics_discards_unexpected_runner_exception_text() -> None:
    class BrokenRunner:
        def probe_daemon(self, *, timeout_seconds: float) -> docker.ProbeResult:
            raise RuntimeError(r"C:\private\model token=super-secret")

        def probe_compose(self, *, timeout_seconds: float) -> docker.ProbeResult:
            raise AssertionError("compose must not run")

        def probe_engine(self, *, timeout_seconds: float) -> docker.EngineProbeResult:
            raise AssertionError("engine must not run")

        def probe_compose_config(self, *, timeout_seconds: float) -> docker.ProbeResult:
            raise AssertionError("compose config must not run")

    diagnostics = docker.collect_docker_diagnostics(runner=BrokenRunner(), timeout_seconds=1.0)

    rendered = diagnostics.model_dump_json()
    assert diagnostics.status == "unavailable"
    assert diagnostics.docker.state == "unknown"
    assert diagnostics.compose.state == "not_checked"
    assert "super-secret" not in rendered
    assert "private" not in rendered


def test_subprocess_probe_uses_fixed_args_without_shell_or_output_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict]] = []

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(docker.subprocess, "run", fake_run)

    runner = docker.SubprocessDockerProbeRunner()
    assert runner.probe_daemon(timeout_seconds=1.0).state == docker.ProbeState.AVAILABLE
    assert runner.probe_compose(timeout_seconds=1.0).state == docker.ProbeState.AVAILABLE

    assert [call[0] for call in calls] == [
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
    ]
    for _, kwargs in calls:
        assert kwargs["shell"] is False
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert "input" not in kwargs
        assert kwargs["cwd"] == docker.REPOSITORY_ROOT


def test_engine_probe_treats_environment_remote_docker_host_as_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        [
            docker._TextProbeResult(docker.ProbeState.AVAILABLE, "linux"),
            docker._TextProbeResult(
                docker.ProbeState.AVAILABLE,
                '[{"Endpoints":{"docker":{"Host":"npipe:////./pipe/docker_engine"}}}]',
            ),
        ]
    )

    monkeypatch.setenv("DOCKER_HOST", "tcp://example.invalid:2376")
    monkeypatch.setattr(
        docker.SubprocessDockerProbeRunner,
        "_run_text",
        staticmethod(lambda _command, *, timeout_seconds: next(outcomes)),
    )

    result = docker.SubprocessDockerProbeRunner().probe_engine(timeout_seconds=1.0)

    assert result.state == docker.ProbeState.AVAILABLE
    assert result.container_os == "linux"
    assert result.locality == "remote"


def test_real_runner_diagnostics_are_short_term_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker.clear_docker_diagnostics_cache()
    calls: list[float] = []
    expected = docker._collect_docker_diagnostics(
        runner=FakeRunner(
            daemon=docker.ProbeResult(docker.ProbeState.AVAILABLE),
            compose=docker.ProbeResult(docker.ProbeState.AVAILABLE),
        ),
        timeout_seconds=1.0,
    )

    def fake_collect(*, runner: docker.DockerProbeRunner, timeout_seconds: float) -> object:
        calls.append(timeout_seconds)
        return expected

    monkeypatch.setattr(docker, "_collect_docker_diagnostics", fake_collect)
    try:
        first = docker.collect_docker_diagnostics(timeout_seconds=1.0, cache_seconds=30.0)
        second = docker.collect_docker_diagnostics(timeout_seconds=1.0, cache_seconds=30.0)
    finally:
        docker.clear_docker_diagnostics_cache()

    assert first is expected
    assert second is expected
    assert calls == [1.0]
