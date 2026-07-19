import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { reportApi } from "../features/reports/api";
import type { EvaluationReport } from "../features/reports/types";
import { InterviewReportPage } from "./InterviewReportPage";

vi.mock("../features/reports/api", () => ({
  reportApi: {
    list: vi.fn(),
    get: vi.fn(),
    retry: vi.fn(),
    coach: vi.fn(),
    jobEventsUrl: vi.fn(),
  },
}));

const report: EvaluationReport = {
  id: "report-1",
  session_id: "session-1",
  status: "completed",
  overall_anchor: "solid",
  overview: "能够给出完整技术主线，但容量估算仍需补强。",
  strengths: ["先澄清目标，再给出架构取舍"],
  gaps: ["缺少具体容量数字"],
  action_plan: [
    {
      title: "容量估算训练",
      instruction: "每天完成一道量级估算。",
      success_criteria: "五分钟内得到合理数量级。",
      priority: 1,
    },
  ],
  trend_comparison: {},
  completed_at: "2026-07-19T10:00:00Z",
  questions: [
    {
      id: "question-evaluation-1",
      plan_question_id: "plan-question-1",
      question_sequence: 1,
      question_prompt: "如何设计长会话上下文压缩？",
      anchor: "solid",
      summary: "回答覆盖分层、预算和失败降级。",
      evidence: [{ message_id: "message-2", claim: "解释了分层预算" }],
      gaps: ["缺少压测数字"],
      actions: ["补充 token 水位和触发阈值"],
      confidence: 0.82,
    },
  ],
  dimensions: [
    {
      id: "dimension-1",
      dimension: "technical_depth",
      anchor: "solid",
      evidence: [{ message_id: "message-2", claim: "解释了分层预算" }],
      gaps: ["量化不足"],
      action: "补充容量估算。",
      confidence: 0.76,
    },
  ],
  evidence_messages: [
    {
      id: "message-2",
      plan_question_id: "plan-question-1",
      sequence: 2,
      content: "我会先划分固定层、短期层和检索层，再分别设置 token 预算。",
    },
  ],
  job: null,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/reports/report-1"]}>
        <Routes>
          <Route path="/reports/:reportId" element={<InterviewReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InterviewReportPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = vi.fn(function showModal(this: HTMLDialogElement) {
      this.setAttribute("open", "");
    });
    HTMLDialogElement.prototype.close = vi.fn(function close(this: HTMLDialogElement) {
      this.removeAttribute("open");
    });
    vi.mocked(reportApi.get).mockResolvedValue(report);
    vi.mocked(reportApi.coach).mockResolvedValue({
      mode: "rewrite",
      title: "更完整的重答",
      explanation: "保留原主线并增加量化。",
      original_answer: report.evidence_messages[0].content,
      suggested_answer: "先说明预算，再给出压缩阈值、回退链路和观测指标。",
      practice_prompts: [],
      source_message_ids: ["message-2"],
    });
  });

  it("opens the exact raw answer from an evidence citation", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "本场能力结论" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /定位原回答 #2/ }));

    expect(screen.getByRole("heading", { name: "原回答证据" })).toBeInTheDocument();
    expect(screen.getByText(report.evidence_messages[0].content)).toBeInTheDocument();
    expect(screen.getByText("解释了分层预算")).toBeInTheDocument();
  });

  it("hides trends until two comparable sessions and never shows an offer probability", async () => {
    renderPage();

    await screen.findByRole("heading", { name: "能力维度" });
    expect(screen.queryByRole("heading", { name: "跨场趋势" })).not.toBeInTheDocument();
    expect(screen.queryByText(/offer|录用概率/i)).not.toBeInTheDocument();
  });

  it("labels the original answer separately from the coach suggestion", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "复盘教练" });

    fireEvent.click(screen.getAllByRole("button", { name: "示范重答" })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: "示范重答" }).at(-1)!);

    await waitFor(() => expect(reportApi.coach).toHaveBeenCalled());
    expect(await screen.findByText("用户原回答")).toBeInTheDocument();
    expect(screen.getByText("建议答案")).toBeInTheDocument();
    expect(screen.getByText(/先说明预算/)).toBeInTheDocument();
  });
});
