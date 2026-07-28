import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { knowledgeApi } from "../../knowledge/api";
import { reportApi } from "../../reports/api";
import { companyApi } from "../companies/api";
import { liveInterviewApi } from "../live/api";
import { planningApi } from "./api";
import { InterviewSetupPage } from "./InterviewSetupPage";

vi.mock("../../knowledge/api", () => ({
  knowledgeApi: {
    listBanks: vi.fn(),
    listResumes: vi.fn(),
  },
}));
vi.mock("../companies/api", () => ({ companyApi: { list: vi.fn() } }));
vi.mock("../live/api", () => ({ liveInterviewApi: { create: vi.fn(), get: vi.fn() } }));
vi.mock("../../reports/api", () => ({ reportApi: { getPracticeTask: vi.fn(), get: vi.fn() } }));
vi.mock("./api", () => ({
  planningApi: {
    create: vi.fn(),
    get: vi.fn(),
    readiness: vi.fn(),
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

function renderPage(initialEntry = "/interviews/setup?company=company-1&round=round-1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <InterviewSetupPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InterviewSetupPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
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
    vi.mocked(planningApi.readiness).mockResolvedValue({
      ready: true,
      blocking: [
        { key: "database", status: "ready", label: "本地数据库" },
        { key: "worker", status: "ready", label: "后台 Worker" },
      ],
      enhancements: [
        { key: "resume", status: "unavailable", label: "简历", detail: "可以先试跑" },
        { key: "question_bank", status: "unavailable", label: "题库", detail: "可以先试跑" },
      ],
      defaults: {
        quick_trial: {
          session_kind: "quick_trial",
          duration_minutes: 10,
          target_question_count: 2,
          include_in_trends: false,
          role_name: "llm_application_engineer",
        },
      },
      company_profile: {
        pack_version: 1,
        trust_status: "template",
        trust_label: "轮次骨架 · 非风格结论",
        evidence_count: 0,
        latest_evidence_at: null,
        source_summaries: [],
      },
    });
    vi.mocked(reportApi.getPracticeTask).mockResolvedValue({
      id: "task-1",
      created_at: "2026-07-28T00:00:00Z",
      updated_at: "2026-07-28T00:00:00Z",
      version: 1,
      report_id: "report-1",
      action_index: 0,
      title: "容量估算训练",
      instruction: "给出一条完整的容量估算链路。",
      success_criteria: "五分钟内给出合理数量级。",
      priority: 1,
      status: "pending",
      last_session_id: null,
      completed_at: null,
    });
    vi.mocked(reportApi.get).mockResolvedValue({ session_id: "session-1" } as never);
    vi.mocked(liveInterviewApi.get).mockResolvedValue({
      id: "session-1",
      status: "completed",
      started_at: null,
      ended_at: null,
      current_question_sequence: null,
      last_event_sequence: 0,
      plan: {
        id: "source-plan-1",
        version: 1,
        status: "frozen",
        total_minutes: 45,
        config: {
          company_id: "company-1",
          round_profile_id: "round-1",
          role_name: "llm_application_engineer",
          session_kind: "standard",
          practice_task_id: null,
        },
        plan_snapshot: {},
        rationale: null,
        questions: [],
      },
      messages: [],
    });
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

  it("creates a zero-material ten-minute quick trial without entering trends by default", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "用 10 分钟试跑" }));
    fireEvent.click(screen.getByRole("button", { name: "生成面试计划" }));

    await waitFor(() => expect(planningApi.create).toHaveBeenCalled());
    expect(planningApi.create).toHaveBeenLastCalledWith(
      expect.objectContaining({
        duration_minutes: 10,
        target_question_count: 2,
        question_bank_ids: [],
        resume_id: null,
        session_kind: "quick_trial",
      }),
      expect.anything(),
    );
  });

  it("waits for readiness before allowing plan generation", async () => {
    vi.mocked(planningApi.readiness).mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    renderPage();

    const button = await screen.findByRole("button", { name: /准备条件/ });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(planningApi.create).not.toHaveBeenCalled();
  });

  it("resolves the source company and round when a training task opens the preparation desk", async () => {
    renderPage("/interviews/setup?task=task-1");

    expect(await screen.findByText("专项短模拟")).toBeInTheDocument();
    expect(screen.getByText(/容量估算训练/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成面试计划" }));

    await waitFor(() => expect(planningApi.create).toHaveBeenCalled());
    expect(planningApi.create).toHaveBeenLastCalledWith(
      expect.objectContaining({
        company_id: "company-1",
        round_profile_id: "round-1",
        duration_minutes: 10,
        target_question_count: 2,
        session_kind: "targeted_practice",
        practice_task_id: "task-1",
      }),
      expect.anything(),
    );
  });
});
