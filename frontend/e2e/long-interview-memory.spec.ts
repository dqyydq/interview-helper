import { expect, test } from "@playwright/test";

const session = {
  id: "session-long",
  status: "interviewing",
  started_at: "2026-07-19T08:00:00Z",
  ended_at: null,
  current_question_sequence: 8,
  last_event_sequence: 24,
  plan: {
    id: "plan-long",
    version: 2,
    status: "frozen",
    total_minutes: 60,
    plan_snapshot: {},
    rationale: null,
    questions: Array.from({ length: 8 }, (_, index) => ({
      id: `question-${index + 1}`,
      sequence: index + 1,
      source_type: "generated",
      source_ref: {},
      capability_tags: ["context_engineering"],
      allocated_seconds: 450,
      follow_up_budget: 2,
      selection_reason: "长会话验收",
    })),
  },
  messages: [{
    id: "message-current",
    role: "assistant",
    content: "请说明长会话压缩时如何保证证据不丢失。",
    sequence: 24,
  }],
};

const diagnostics = {
  session_id: "session-long",
  current_state: {
    current_plan_question_id: "question-8",
    completed_question_count: 7,
    unresolved_point_count: 1,
    token_count: 2_700,
  },
  summary: {
    snapshot_count: 18,
    max_compaction_level: 4,
    total_input_tokens: 31_400,
    average_compression_ratio: 0.62,
    retrieval_candidate_count: 6,
    retrieval_included_count: 2,
  },
  snapshots: [{
    id: "snapshot-latest",
    created_at: "2026-07-19T08:52:00Z",
    agent_role: "interviewer",
    prompt_schema_version: "interviewer.v2",
    included_refs: { memories: ["memory-1", "memory-2"] },
    excluded_refs: [],
    token_by_layer: {
      system: 310,
      state: 260,
      recent: 1_800,
      summaries: 330,
      retrieval: 0,
      effective_input_budget: 3_000,
      selected_input_tokens: 2_700,
      candidate_input_tokens: 8_000,
      compression_ratio: 0.3375,
      tokens_removed: 5_300,
      retrieval_candidate_count: 6,
      retrieval_included_count: 2,
    },
    count_method: "conservative_estimate:estimated",
    compaction_level: 4,
    input_tokens: 2_700,
    output_tokens: 120,
  }],
  segments: [],
};

const memory = {
  id: "memory-1",
  memory_type: "project_fact",
  canonical_key: "project.context",
  memory_version: 1,
  content: "我负责过长对话上下文压缩与证据追踪。",
  structured_value: {},
  status: "active",
  confidence: 0.94,
  first_observed_at: "2026-07-18T08:00:00Z",
  last_verified_at: "2026-07-19T08:00:00Z",
  last_used_at: null,
  expires_at: null,
  pinned: true,
  sources: [{
    id: "source-1",
    session_id: "session-origin",
    message_id: "message-origin",
    source_type: "interview_evaluation",
    evidence_excerpt: null,
    observed_at: "2026-07-18T08:00:00Z",
  }],
  open_conflicts: [],
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    class BrowserTestSocket {
      static OPEN = 1;
      readyState = BrowserTestSocket.OPEN;
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: (() => void) | null = null;

      constructor() {
        window.setTimeout(() => this.onopen?.(), 0);
      }

      send() {}
      close() { this.onclose?.(); }
    }
    Object.defineProperty(window, "WebSocket", { value: BrowserTestSocket });
  });
});

test("shows high-pressure context diagnostics without exposing transcript content", async ({ page }) => {
  await page.route("**/api/interview-sessions/session-long/start", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(session) }),
  );
  await page.route("**/api/interview-sessions/session-long/context/diagnostics", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(diagnostics) }),
  );

  await page.goto("/interviews/session-long/live");

  await expect(page.getByText("请说明长会话压缩时如何保证证据不丢失。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "上下文诊断" })).toBeVisible();
  await expect(page.getByText("L4")).toBeVisible();
  await expect(page.getByText("2,700 / 3,000")).toBeVisible();
  await expect(page.getByText("34%")).toBeVisible();
  await expect(page.getByText("2 / 6")).toBeVisible();
  await expect(page.getByText("仅展示计数、层级和引用数量，不记录回答正文。")).toBeVisible();
});

test("removes a deleted cross-session memory from the user workspace", async ({ page }) => {
  let deleted = false;
  await page.route("**/api/memory-settings", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ memory_enabled: true }) }),
  );
  await page.route("**/api/memories**", (route) => {
    if (route.request().method() === "DELETE") {
      deleted = true;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(deleted ? [] : [memory]),
    });
  });
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/settings/memory");
  await expect(page.getByText(memory.content)).toBeVisible();
  await page.getByRole("button", { name: "删除记忆" }).click();
  await expect(page.getByText("这里还没有记忆")).toBeVisible();
  await expect(page.getByText(memory.content)).not.toBeVisible();
});
