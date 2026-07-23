import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { diagnosticsApi, type DiagnosticBundle } from "../features/diagnostics/api";
import { DiagnosticsPage } from "./DiagnosticsPage";

vi.mock("../features/diagnostics/api", async () => {
  const actual = await vi.importActual("../features/diagnostics/api");
  return {
    ...actual,
    diagnosticsApi: {
      get: vi.fn(),
      bundle: vi.fn(),
    },
  };
});

const snapshot = {
  generated_at: "2026-07-23T10:00:00Z",
  application: { name: "Interview Helper", version: "0.1.0", environment: "test" },
  database: { status: "connected" },
  worker: {
    state: "healthy" as const,
    active_workers: 1,
    stale_workers: 0,
    recent_worker_errors: 0,
    last_heartbeat_at: "2026-07-23T09:59:58Z",
    last_job_type: "plan_generation",
    last_error_type: null,
    last_error_at: null,
    job_counts: { queued: 1 },
    stale_running_jobs: 0,
    recent_failed_jobs: 0,
    heartbeat_stale_after_seconds: 30,
  },
  models: {
    connection_count: 2,
    binding_count: 3,
    status_counts: { healthy: 2 },
    required_ready: true,
    missing_required_roles: [],
    degraded_required_roles: [],
    transcriber_configured: false,
  },
  files: { configured: true, exists: true, writable: true, file_count: 4, total_bytes: 4096 },
  privacy: {
    redaction_applied: true,
    contains_secrets: false,
    contains_answer_content: false,
    contains_local_paths: false,
  },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DiagnosticsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DiagnosticsPage", () => {
  const writeText = vi.fn();

  beforeEach(() => {
    vi.mocked(diagnosticsApi.get).mockResolvedValue(snapshot);
    vi.mocked(diagnosticsApi.bundle).mockResolvedValue({
      request_id: "request-1",
      snapshot,
    } satisfies DiagnosticBundle);
    writeText.mockReset();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  });

  it("shows operational counts and copies only the server-redacted bundle", async () => {
    renderPage();

    expect(await screen.findByText("PostgreSQL 已连接")).toBeInTheDocument();
    expect(screen.getByText("Worker 正在运行")).toBeInTheDocument();
    expect(screen.getByText(/1 个活跃 Worker/)).toBeInTheDocument();
    expect(screen.getByText("2 个连接 · 3 个绑定")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /复制脱敏诊断包/ }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = String(writeText.mock.calls[0][0]);
    expect(copied).toContain('"redaction_applied": true');
    expect(copied).not.toMatch(/api[_-]?key|storage_path|parsed_text|transcript/i);
    expect(await screen.findByRole("button", { name: /已复制/ })).toBeInTheDocument();
  });
});
