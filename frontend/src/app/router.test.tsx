import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { createTestRouter } from "./router";

vi.mock("../features/settings/models/api", () => ({
  modelConnectionApi: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
    listBindings: vi.fn().mockResolvedValue([]),
    bindRole: vi.fn(),
    readiness: vi.fn().mockResolvedValue({
      ready: false,
      missing_roles: ["interviewer", "evaluator"],
      degraded_roles: [],
    }),
    listLocalCapabilities: vi.fn().mockResolvedValue([]),
    testLocalCapability: vi.fn(),
    embeddingIndexStatus: vi.fn().mockResolvedValue({
      active_profile: null,
      building_profile: null,
      latest_failed_profile: null,
      job: null,
      interview_active: false,
    }),
    rebuildEmbeddingIndex: vi.fn(),
  },
}));

vi.mock("../features/settings/memory/api", () => ({
  memoryApi: {
    list: vi.fn().mockResolvedValue([]),
    settings: vi.fn().mockResolvedValue({ memory_enabled: true }),
    updateSettings: vi.fn(),
  },
}));

vi.mock("../features/interviews/companies/api", () => ({
  companyApi: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn(),
  },
}));

vi.mock("../features/knowledge/api", () => ({
  knowledgeApi: {
    listBanks: vi.fn().mockResolvedValue([]),
    createBank: vi.fn(),
    listQuestions: vi.fn().mockResolvedValue({ data: [], count: 0, offset: 0, limit: 100 }),
    createQuestion: vi.fn(),
    archiveQuestion: vi.fn(),
    listResumes: vi.fn().mockResolvedValue([]),
    uploadResume: vi.fn(),
  },
}));

vi.mock("../features/reports/api", () => ({
  reportApi: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
    retry: vi.fn(),
    coach: vi.fn(),
    jobEventsUrl: vi.fn(),
  },
}));

vi.mock("../features/diagnostics/api", () => ({
  diagnosticsApi: {
    get: vi.fn().mockResolvedValue({
      generated_at: "2026-07-19T10:00:00Z",
      application: { name: "Interview Helper", version: "0.1.0", environment: "test" },
      database: { status: "connected" },
      worker: {
        state: "not_running",
        active_workers: 0,
        stale_workers: 0,
        recent_worker_errors: 0,
        last_heartbeat_at: null,
        last_job_type: null,
        last_error_type: null,
        last_error_at: null,
        job_counts: {},
        stale_running_jobs: 0,
        recent_failed_jobs: 0,
        heartbeat_stale_after_seconds: 30,
      },
      models: {
        connection_count: 0,
        binding_count: 0,
        status_counts: {},
        required_ready: false,
        missing_required_roles: ["interviewer", "evaluator"],
        degraded_required_roles: [],
        transcriber_configured: false,
      },
      files: { configured: true, exists: false, writable: false, file_count: 0, total_bytes: 0 },
      privacy: {
        redaction_applied: true,
        contains_secrets: false,
        contains_answer_content: false,
        contains_local_paths: false,
      },
    }),
    bundle: vi.fn(),
  },
}));

const routes = [
  ["/interviews", "选择公司"],
  ["/questions", "面试知识库"],
  ["/reports", "面试评估报告"],
  ["/settings", "系统设置"],
  ["/settings/memory", "长期记忆"],
  ["/settings/diagnostics", "系统诊断"],
] as const;

describe("application routes", () => {
  const renderRoute = (path: string) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createTestRouter([path]);
    const view = render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider
          router={router}
          future={{ v7_startTransition: true }}
        />
      </QueryClientProvider>,
    );
    return { ...view, queryClient, router };
  };

  it.each(routes)("renders %s", (path, heading) => {
    renderRoute(path);
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("renders a safe not-found page", () => {
    renderRoute("/not-a-real-route");
    expect(screen.getByRole("heading", { name: "页面不存在" })).toBeInTheDocument();
    expect(screen.queryByText(/stack|exception|traceback/i)).not.toBeInTheDocument();
  });

  it("keeps one primary workspace frame mounted across primary navigation", async () => {
    const { container, queryClient, router, unmount } = renderRoute("/interviews");

    try {
      const workspace = container.querySelector(".workspace");
      const frame = container.querySelector("[data-page-frame='primary']");

      expect(workspace).not.toBeNull();
      expect(frame).not.toBeNull();

      await router.navigate("/questions");
      await waitFor(() =>
        expect(screen.getByRole("heading", { name: "面试知识库" })).toBeInTheDocument(),
      );

      expect(container.querySelector(".workspace")).toBe(workspace);
      expect(container.querySelector("[data-page-frame='primary']")).toBe(frame);
    } finally {
      unmount();
      router.dispose();
      await queryClient.cancelQueries();
      queryClient.clear();
    }
  });
});
