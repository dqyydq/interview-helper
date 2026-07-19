import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

const routes = [
  ["/interviews", "选择公司"],
  ["/questions", "面试知识库"],
  ["/reports", "面试评估报告"],
  ["/settings", "系统设置"],
  ["/settings/memory", "长期记忆"],
] as const;

describe("application routes", () => {
  const renderRoute = (path: string) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider
          router={createTestRouter([path])}
          future={{ v7_startTransition: true }}
        />
      </QueryClientProvider>,
    );
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
});
