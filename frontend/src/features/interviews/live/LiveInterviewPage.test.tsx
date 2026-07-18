import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { liveInterviewApi } from "./api";
import { LiveInterviewPage } from "./LiveInterviewPage";

vi.mock("./api", () => ({ liveInterviewApi: { start: vi.fn() } }));

class WebSocketStub {
  static OPEN = 1;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
}

describe("LiveInterviewPage", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", WebSocketStub);
    vi.mocked(liveInterviewApi.start).mockResolvedValue({
      id: "session-1",
      status: "interviewing",
      started_at: new Date().toISOString(),
      ended_at: null,
      current_question_sequence: 1,
      last_event_sequence: 0,
      plan: {
        id: "plan-1",
        version: 2,
        status: "frozen",
        total_minutes: 45,
        plan_snapshot: {},
        rationale: null,
        questions: [{
          id: "question-1",
          sequence: 1,
          source_type: "generated",
          source_ref: {},
          capability_tags: ["system_design"],
          allocated_seconds: 450,
          follow_up_budget: 2,
          selection_reason: "岗位矩阵",
        }],
      },
      messages: [{
        id: "message-1",
        role: "assistant",
        content: "请设计一个多模型网关。",
        sequence: 1,
      }],
    });
  });

  it("shows the confirmed transcript without scores", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/interviews/session-1/live"]}>
          <Routes><Route path="/interviews/:sessionId/live" element={<LiveInterviewPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("请设计一个多模型网关。")).toBeInTheDocument();
    expect(screen.getByText("面试中不显示评分")).toBeInTheDocument();
    expect(screen.getByLabelText("你的回答")).toBeEnabled();
  });
});
