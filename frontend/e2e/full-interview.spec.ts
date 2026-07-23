import { expect, test } from "@playwright/test";

const company = {
  id: "company-1",
  name: "字节跳动",
  slug: "bytedance",
  description: "本地演示公司",
  is_system: true,
  archived: false,
  latest_style_pack: {
    id: "style-1",
    name: "工程深挖",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "active",
    visibility: "private",
    evidence_count: 2,
    evidence_label: "用户资料与公开经验整理",
    rounds: [
      {
        id: "round-1",
        round_key: "round_2",
        name: "二面",
        sequence: 2,
        opening_style: "从项目实现切入，连续追问技术取舍与失败处理。",
        topic_weights: { Agent工程: 0.6, 系统设计: 0.4 },
        follow_up_patterns: ["为什么采用这个方案？", "出现超时后如何降级？"],
        pressure_level: 4,
        answer_expectations: ["结论明确", "说明取舍"],
        evaluation_weights: { technical_depth: 0.6, communication: 0.4 },
        duration_minutes: 45,
      },
    ],
  },
};

const planReady = {
  id: "plan-1",
  version: 2,
  status: "ready",
  total_minutes: 45,
  plan_snapshot: {
    phase: "ready",
    planner: "deterministic-v1",
    source_distribution: { generated: 1 },
    capability_coverage: { agent_engineering: 1, system_design: 1 },
  },
  rationale: "覆盖项目深挖与系统设计。",
  questions: [
    {
      id: "question-1",
      sequence: 1,
      source_type: "generated",
      source_ref: {},
      capability_tags: ["agent_engineering", "system_design"],
      allocated_seconds: 2_700,
      follow_up_budget: 2,
      selection_reason: "验证端到端模拟流程",
    },
  ],
};

const session = {
  id: "session-1",
  status: "interviewing",
  started_at: new Date().toISOString(),
  ended_at: null,
  current_question_sequence: 1,
  last_event_sequence: 1,
  plan: { ...planReady, status: "frozen" },
  messages: [
    {
      id: "message-opening",
      role: "assistant",
      content: "请介绍你设计的长会话上下文管理方案。",
      sequence: 1,
    },
  ],
};

const reportListItem = {
  report_id: "report-1",
  session_id: "session-1",
  status: "completed",
  overall_anchor: "partial",
  overview: "方案完整，下一步需要补充量化指标与异常降级边界。",
  created_at: "2026-07-23T10:00:00Z",
  updated_at: "2026-07-23T10:01:00Z",
  company_name: "字节跳动",
  round_name: "二面",
  role_name: "llm_application_engineer",
};

const report = {
  id: "report-1",
  session_id: "session-1",
  status: "completed",
  overall_anchor: "partial",
  overview: reportListItem.overview,
  strengths: ["能够分层管理上下文并保留原始证据。"],
  gaps: ["压缩触发阈值和失败降级仍需量化。"],
  action_plan: [
    {
      title: "量化上下文预算",
      instruction: "补充 token 阈值、压缩比和恢复策略。",
      success_criteria: "能够给出至少两个指标与一个失败降级路径。",
      priority: 1,
    },
  ],
  trend_comparison: {},
  completed_at: "2026-07-23T10:01:00Z",
  questions: [
    {
      id: "question-evaluation-1",
      plan_question_id: "question-1",
      question_sequence: 1,
      question_prompt: "请介绍你设计的长会话上下文管理方案。",
      anchor: "partial",
      summary: "说明了分层上下文、摘要和原始证据之间的关系。",
      evidence: [
        {
          message_id: "message-answer",
          claim: "回答明确保留原始消息作为最终事实源。",
        },
      ],
      gaps: ["补充量化指标。"],
      actions: ["说明压缩失败时如何回退。"],
      confidence: 0.76,
    },
  ],
  dimensions: [
    {
      id: "dimension-1",
      dimension: "technical_depth",
      anchor: "partial",
      evidence: [
        {
          message_id: "message-answer",
          claim: "能够解释分层上下文。",
        },
      ],
      gaps: ["需要给出压力测试数据。"],
      action: "补充 token 与延迟指标。",
      confidence: 0.72,
    },
  ],
  evidence_messages: [
    {
      id: "message-answer",
      plan_question_id: "question-1",
      sequence: 2,
      content: "我会保留完整原始消息，以分层预算和可追溯摘要控制 token。",
      attachments: [],
    },
  ],
  job: null,
};

const contextDiagnostics = {
  session_id: "session-1",
  current_state: {},
  summary: {
    snapshot_count: 1,
    max_compaction_level: 0,
    total_input_tokens: 320,
    average_compression_ratio: 1,
    retrieval_candidate_count: 0,
    retrieval_included_count: 0,
  },
  snapshots: [],
  segments: [],
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    class BrowserTestEventSource {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;
      readyState = BrowserTestEventSource.OPEN;
      onerror: ((event: Event) => void) | null = null;
      readonly url: string;

      constructor(url: string | URL) {
        this.url = String(url);
      }

      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        if (type !== "job") return;
        window.setTimeout(() => {
          const event = new MessageEvent("job", {
            data: JSON.stringify({
              id: "job-plan-1",
              version: 2,
              status: "completed",
              progress: 1,
              result: { plan_id: "plan-1" },
              error_code: null,
              error_message: null,
            }),
          });
          if (typeof listener === "function") listener(event);
          else listener.handleEvent(event);
        }, 10);
      }

      removeEventListener() {}
      dispatchEvent() { return true; }
      close() { this.readyState = BrowserTestEventSource.CLOSED; }
    }

    class BrowserTestSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = BrowserTestSocket.OPEN;
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: (() => void) | null = null;

      constructor() {
        window.setTimeout(() => this.onopen?.(), 0);
      }

      private emit(type: string, sequence: number, payload: Record<string, unknown>) {
        this.onmessage?.(
          new MessageEvent("message", {
            data: JSON.stringify({
              event_id: crypto.randomUUID(),
              session_id: "session-1",
              type,
              sequence,
              timestamp: new Date().toISOString(),
              payload,
            }),
          }),
        );
      }

      send(raw: string) {
        const event = JSON.parse(raw) as {
          event_id: string;
          type: string;
          payload: Record<string, unknown>;
        };
        if (event.type === "user.text.submit") {
          window.setTimeout(() => {
            this.emit("input.ack", 2, {
              client_event_id: event.event_id,
              message: {
                id: "message-answer",
                role: "user",
                content: String(event.payload.text),
                sequence: 2,
              },
            });
            this.emit("assistant.message", 3, {
              message: {
                id: "message-follow-up",
                role: "assistant",
                content: "如果摘要任务失败，你如何保证面试继续并避免证据失真？",
                sequence: 3,
              },
            });
          }, 10);
        }
        if (event.type === "session.finish") {
          window.setTimeout(() => {
            this.emit("session.state", 4, { status: "evaluating" });
          }, 0);
        }
      }

      close() {
        this.readyState = BrowserTestSocket.CLOSED;
        this.onclose?.();
      }
    }

    Object.defineProperty(window, "EventSource", { configurable: true, value: BrowserTestEventSource });
    Object.defineProperty(window, "WebSocket", { configurable: true, value: BrowserTestSocket });
  });

  await page.route("**/api/companies", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([company]) }),
  );
  await page.route("**/api/question-banks", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/resumes", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/interview-plans", (route) =>
    route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        plan: { ...planReady, version: 1, status: "draft", questions: [] },
        job: {
          id: "job-plan-1",
          version: 1,
          status: "queued",
          progress: 0,
          result: {},
          error_code: null,
          error_message: null,
        },
      }),
    }),
  );
  await page.route("**/api/interview-plans/plan-1", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(planReady) }),
  );
  await page.route("**/api/interview-plans/plan-1/memory-preview", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: true, items: [] }),
    }),
  );
  await page.route("**/api/interview-sessions", (route) =>
    route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(session) }),
  );
  await page.route("**/api/interview-sessions/session-1/start", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(session) }),
  );
  await page.route("**/api/interview-sessions/session-1/context/diagnostics", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(contextDiagnostics),
    }),
  );
  await page.route("**/api/reports", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([reportListItem]),
    }),
  );
  await page.route("**/api/reports/report-1", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(report) }),
  );
});

test("completes the core journey from company selection to evidence report", async ({ page }) => {
  await page.goto("/interviews");
  await expect(page.getByRole("heading", { name: "选择公司" })).toBeVisible();
  await expect(page.getByText("字节跳动", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "配置本场模拟" }).click();

  await expect(page.getByRole("heading", { name: "配置本场模拟" })).toBeVisible();
  await page.getByRole("button", { name: "生成面试计划" }).click();
  await expect(page.getByText("计划校验通过")).toBeVisible();
  await page.getByRole("button", { name: "开始模拟面试" }).click();

  await expect(page.getByRole("heading", { name: "实时模拟面试" })).toBeVisible();
  await expect(page.getByText("请介绍你设计的长会话上下文管理方案。")).toBeVisible();
  await page.getByLabel("你的回答").fill(
    "我会保留完整原始消息，以分层预算和可追溯摘要控制 token。",
  );
  await page.getByRole("button", { name: "确认回答" }).click();
  await expect(page.getByText("如果摘要任务失败，你如何保证面试继续并避免证据失真？")).toBeVisible();
  await page.getByRole("button", { name: "提前结束" }).click();

  await page.getByRole("link", { name: "评估报告" }).click();
  await expect(page.getByRole("heading", { name: "面试评估报告" })).toBeVisible();
  await page.getByText(reportListItem.overview).click();
  await expect(page.getByRole("heading", { name: "本场能力结论" })).toBeVisible();
  await expect(page.getByText("能够分层管理上下文并保留原始证据。")).toBeVisible();
  await expect(page.getByRole("button", { name: /定位原回答/ })).toBeVisible();
});
