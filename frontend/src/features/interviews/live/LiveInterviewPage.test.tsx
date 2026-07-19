import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { liveInterviewApi } from "./api";
import { LiveInterviewPage } from "./LiveInterviewPage";

vi.mock("./api", () => ({ liveInterviewApi: { start: vi.fn(), diagnostics: vi.fn() } }));
vi.mock("../../live-interview/CodeWhiteboard", () => ({
  CodeWhiteboard: ({ onAttach }: { onAttach: (attachment: Record<string, string>) => void }) => (
    <button
      type="button"
      onClick={() => onAttach({
        attachment_type: "code",
        language: "python",
        filename: "solution.py",
        content: "print('answer')",
      })}
    >
      附加测试代码
    </button>
  ),
}));

class WebSocketStub {
  static OPEN = 1;
  static latest: WebSocketStub;
  readyState = 1;
  private openHandler: (() => void) | null = null;
  set onopen(handler: (() => void) | null) {
    this.openHandler = handler;
    if (handler) queueMicrotask(handler);
  }
  get onopen() { return this.openHandler; }
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor() {
    WebSocketStub.latest = this;
  }
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
    vi.mocked(liveInterviewApi.diagnostics).mockResolvedValue({
      session_id: "session-1",
      current_state: {},
      summary: {
        snapshot_count: 1,
        max_compaction_level: 2,
        total_input_tokens: 2_100,
        average_compression_ratio: 0.7,
        retrieval_candidate_count: 3,
        retrieval_included_count: 2,
      },
      snapshots: [{
        id: "snapshot-1",
        created_at: new Date().toISOString(),
        agent_role: "interviewer",
        prompt_schema_version: "interviewer.v2",
        included_refs: {},
        excluded_refs: [],
        token_by_layer: {
          effective_input_budget: 3_000,
          selected_input_tokens: 2_100,
          compression_ratio: 0.7,
          retrieval_candidate_count: 3,
          retrieval_included_count: 2,
        },
        count_method: "conservative_estimate:estimated",
        compaction_level: 2,
        input_tokens: 2_100,
        output_tokens: 120,
      }],
      segments: [],
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
    expect(screen.getByText(/^\d{2}:\d{2}$/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "附加测试代码" }));
    fireEvent.change(screen.getByLabelText("你的回答"), { target: { value: "这是我的思路" } });
    fireEvent.click(screen.getByRole("button", { name: "确认回答" }));
    await waitFor(() => expect(WebSocketStub.latest.send).toHaveBeenCalled());
    const submitted = JSON.parse(String(WebSocketStub.latest.send.mock.calls.at(-1)?.[0]));
    expect(submitted.payload).toEqual({
      text: "这是我的思路",
      attachments: [{
        attachment_type: "code",
        language: "python",
        filename: "solution.py",
        content: "print('answer')",
      }],
    });
    fireEvent.click(screen.getByRole("button", { name: "重述问题" }));
    await waitFor(() => expect(WebSocketStub.latest.send).toHaveBeenCalled());
    expect(WebSocketStub.latest.send.mock.calls.at(-1)?.[0]).toContain("session.restate");
    act(() => WebSocketStub.latest.onmessage?.({
      data: JSON.stringify({
        event_id: "state-paused",
        session_id: "session-1",
        type: "session.state",
        sequence: 2,
        timestamp: new Date().toISOString(),
        payload: { status: "paused" },
      }),
    } as MessageEvent));
    expect(await screen.findByRole("button", { name: "继续" })).toBeEnabled();
    expect(screen.getByLabelText("你的回答")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(WebSocketStub.latest.send.mock.calls.at(-1)?.[0]).toContain("session.resume");
    expect(await screen.findByText("2,100 / 3,000")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
  });
});
