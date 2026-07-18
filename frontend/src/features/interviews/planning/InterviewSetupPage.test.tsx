import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { knowledgeApi } from "../../knowledge/api";
import { companyApi } from "../companies/api";
import { planningApi } from "./api";
import { InterviewSetupPage } from "./InterviewSetupPage";

vi.mock("../../knowledge/api", () => ({
  knowledgeApi: {
    listBanks: vi.fn(),
    listResumes: vi.fn(),
  },
}));
vi.mock("../companies/api", () => ({ companyApi: { list: vi.fn() } }));
vi.mock("./api", () => ({
  planningApi: {
    create: vi.fn(),
    get: vi.fn(),
    jobEventsUrl: vi.fn(() => "http://test/jobs/job-1/events"),
  },
}));

class EventSourceStub {
  addEventListener = vi.fn();
  close = vi.fn();
}

const company = {
  id: "company-1",
  name: "字节跳动",
  slug: "bytedance",
  description: null,
  is_system: true,
  archived: false,
  latest_style_pack: {
    id: "pack-1",
    name: "通用轮次骨架",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "active" as const,
    visibility: "private" as const,
    evidence_count: 0,
    evidence_label: "轮次骨架 · 非风格结论",
    rounds: [{
      id: "round-1",
      round_key: "round_1",
      name: "一面",
      sequence: 1,
      opening_style: null,
      topic_weights: {},
      follow_up_patterns: [],
      pressure_level: 1,
      answer_expectations: [],
      evaluation_weights: {},
      duration_minutes: 45,
    }],
  },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/interviews/setup?company=company-1&round=round-1"]}>
        <InterviewSetupPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InterviewSetupPage", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", EventSourceStub);
    vi.mocked(companyApi.list).mockResolvedValue([company]);
    vi.mocked(knowledgeApi.listBanks).mockResolvedValue([{
      id: "bank-1",
      name: "LLM 基础",
      description: null,
      visibility: "private",
      question_count: 8,
      archived: false,
    }]);
    vi.mocked(knowledgeApi.listResumes).mockResolvedValue([]);
    vi.mocked(planningApi.create).mockResolvedValue({
      plan: {
        id: "plan-1",
        version: 1,
        status: "draft",
        total_minutes: 45,
        plan_snapshot: { phase: "queued" },
        rationale: null,
        questions: [],
      },
      job: {
        id: "job-1",
        version: 1,
        status: "queued",
        progress: 0,
        result: {},
        error_code: null,
        error_message: null,
      },
    });
  });

  it("submits only the selected interview sources", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "配置本场模拟" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /LLM 基础/ }));
    fireEvent.click(screen.getByRole("button", { name: "生成面试计划" }));

    await waitFor(() => expect(planningApi.create).toHaveBeenCalledTimes(1));
    expect(planningApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        company_id: "company-1",
        round_profile_id: "round-1",
        question_bank_ids: ["bank-1"],
        role_name: "llm_application_engineer",
      }),
      expect.anything(),
    );
  });
});
