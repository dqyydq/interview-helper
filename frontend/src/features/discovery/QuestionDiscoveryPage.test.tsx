import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { knowledgeApi } from "../knowledge/api";
import { modelConnectionApi } from "../settings/models/api";
import { discoveryApi } from "./api";
import { QuestionDiscoveryPage } from "./QuestionDiscoveryPage";

vi.mock("./api", () => ({
  discoveryApi: {
    listConnectors: vi.fn(),
    createConnector: vi.fn(),
    updateConnector: vi.fn(),
    removeConnector: vi.fn(),
    testConnector: vi.fn(),
    listRuns: vi.fn(),
    createRun: vi.fn(),
    getRun: vi.fn(),
    listSources: vi.fn(),
    listCandidates: vi.fn(),
    listCandidateEvidence: vi.fn(),
    importCandidates: vi.fn(),
    cancelRun: vi.fn(),
    removeRun: vi.fn(),
  },
}));

vi.mock("../knowledge/api", () => ({
  knowledgeApi: {
    listBanks: vi.fn(),
  },
}));

vi.mock("../settings/models/api", () => ({
  modelConnectionApi: {
    listBindings: vi.fn(),
  },
}));

const connector = {
  id: "connector-1",
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:00:00Z",
  version: 1,
  name: "本地 Tavily",
  provider_type: "tavily" as const,
  enabled: true,
  capabilities: { supports_domain_filters: true, supports_extract: true, safe_extract: true },
  configuration: { default_country: "cn" },
  configuration_version: 1,
  status: "healthy" as const,
  last_tested_at: "2026-07-23T00:00:00Z",
  last_error_code: null,
  has_api_key: true,
};

const run = {
  id: "run-1",
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:01:00Z",
  version: 1,
  connector_id: connector.id,
  connector_configuration_version: 1,
  initiated_by: "local",
  source_mode: "search" as const,
  query_snapshot: { search_query: "字节跳动 LLM 应用开发 面试题" },
  status: "succeeded" as const,
  stage: "completed",
  progress: 1,
  source_count: 1,
  candidate_count: 1,
  failed_source_count: 0,
  error_code: null,
  error_summary: null,
  cancel_requested_at: null,
  completed_at: "2026-07-23T00:01:00Z",
  expires_at: "2026-07-30T00:00:00Z",
};

const candidate = {
  id: "candidate-1",
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:01:00Z",
  version: 1,
  run_id: run.id,
  prompt: "如何为 RAG 系统建立离线评估集？",
  question_type: "system_design" as const,
  difficulty: "advanced" as const,
  suggested_tags: ["RAG", "评估"],
  suggested_roles: ["LLM 应用开发"],
  suggested_skills: ["检索评估"],
  applicable_companies: ["字节跳动"],
  applicable_rounds: ["二面"],
  reference_points: ["说明样本构成与指标"],
  follow_up_suggestions: ["发生回归时如何定位？"],
  matching_reason: "验证候选人的检索评估能力。",
  confidence: 0.92,
  researcher_model_name: "researcher-model",
  schema_version: "1",
  candidate_revision: 1,
  similar_question_ids: [],
  status: "proposed" as const,
  import_count: 0,
  failure_code: null,
  failure_summary: null,
  expires_at: "2026-07-30T00:00:00Z",
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <QuestionDiscoveryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("QuestionDiscoveryPage", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(discoveryApi.listConnectors).mockResolvedValue([connector]);
    vi.mocked(discoveryApi.listRuns).mockResolvedValue({ data: [], count: 0, offset: 0, limit: 50 });
    vi.mocked(discoveryApi.getRun).mockResolvedValue(run);
    vi.mocked(discoveryApi.listSources).mockResolvedValue({ data: [], count: 0, offset: 0, limit: 100 });
    vi.mocked(discoveryApi.listCandidates).mockResolvedValue({ data: [], count: 0, offset: 0, limit: 100 });
    vi.mocked(discoveryApi.listCandidateEvidence).mockResolvedValue([]);
    vi.mocked(discoveryApi.createRun).mockResolvedValue({ ...run, status: "queued", stage: "queued", progress: 0 });
    vi.mocked(discoveryApi.importCandidates).mockResolvedValue({
      run_id: run.id,
      bank_id: "bank-1",
      batch_id: "batch-1",
      request_hash: "request-hash",
      items: [{ candidate_id: candidate.id, candidate_revision: 1, question_id: "question-1", import_id: "import-1" }],
      replayed: false,
    });
    vi.mocked(knowledgeApi.listBanks).mockResolvedValue([
      { id: "bank-1", name: "LLM 应用", description: null, visibility: "private", question_count: 0, archived: false },
    ]);
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([
      {
        id: "binding-researcher",
        role: "researcher",
        target_kind: "model_connection",
        connection_id: "model-1",
        connection_name: "Researcher",
        model_name: "researcher-model",
        connection_status: "healthy",
        local_capability_key: null,
      },
    ]);
  });

  it("creates a search discovery run from explicit interview conditions", async () => {
    renderPage();

    fireEvent.change(await screen.findByLabelText("发现连接器"), { target: { value: connector.id } });
    fireEvent.change(await screen.findByLabelText("目标公司"), { target: { value: "字节跳动" } });
    fireEvent.change(screen.getByLabelText("岗位方向"), { target: { value: "LLM 应用开发" } });
    fireEvent.click(screen.getByRole("button", { name: "开始发现" }));

    await waitFor(() => expect(discoveryApi.createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(discoveryApi.createRun).mock.calls[0]?.[0]).toEqual(expect.objectContaining({
      connector_id: connector.id,
      source_mode: "search",
      company: "字节跳动",
      role: "LLM 应用开发",
    }));
  });

  it("requires an explicit connector selection before a discovery run can start", async () => {
    renderPage();

    expect(await screen.findByLabelText("发现连接器")).toHaveValue("");
    expect(await screen.findByRole("button", { name: "开始发现" })).toBeDisabled();
    expect(discoveryApi.createRun).not.toHaveBeenCalled();
  });

  it("uses the explicitly selected Firecrawl connector and omits unavailable connectors", async () => {
    const firecrawlConnector = {
      ...connector,
      id: "connector-firecrawl",
      name: "本地 Firecrawl",
      provider_type: "firecrawl" as const,
    };
    vi.mocked(discoveryApi.listConnectors).mockResolvedValue([
      connector,
      firecrawlConnector,
      { ...connector, id: "connector-disabled", name: "停用连接器", enabled: false },
      { ...connector, id: "connector-empty-key", name: "无密钥连接器", has_api_key: false },
    ]);
    renderPage();

    const connectorSelect = await screen.findByLabelText("发现连接器");
    expect(screen.getByRole("option", { name: /本地 Firecrawl · Firecrawl/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /停用连接器/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /无密钥连接器/ })).not.toBeInTheDocument();
    fireEvent.change(connectorSelect, { target: { value: firecrawlConnector.id } });
    fireEvent.change(screen.getByLabelText("目标公司"), { target: { value: "字节跳动" } });
    fireEvent.click(screen.getByRole("button", { name: "开始发现" }));

    await waitFor(() => expect(discoveryApi.createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(discoveryApi.createRun).mock.calls[0]?.[0]).toEqual(expect.objectContaining({
      connector_id: firecrawlConnector.id,
      source_mode: "search",
    }));
  });

  it("shows source evidence and imports selected candidates as drafts", async () => {
    vi.mocked(discoveryApi.listRuns).mockResolvedValue({ data: [run], count: 1, offset: 0, limit: 50 });
    vi.mocked(discoveryApi.listSources).mockResolvedValue({
      data: [{
        id: "source-1",
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
        version: 1,
        run_id: run.id,
        normalized_url: "https://example.com/interview-notes",
        final_url: "https://example.com/interview-notes",
        title: "公开面经",
        domain: "example.com",
        source_category: "community_notes",
        status: "fetched",
        fetched_at: "2026-07-23T00:00:00Z",
        excerpt: "候选人需要解释检索评估和失败分析。",
        attribution: {},
        policy_metadata: {},
        failure_code: null,
        failure_summary: null,
        expires_at: "2026-07-30T00:00:00Z",
      }],
      count: 1,
      offset: 0,
      limit: 100,
    });
    vi.mocked(discoveryApi.listCandidates).mockResolvedValue({ data: [candidate], count: 1, offset: 0, limit: 100 });
    vi.mocked(discoveryApi.listCandidateEvidence).mockResolvedValue([{
      id: "evidence-1",
      created_at: "2026-07-23T00:00:00Z",
      updated_at: "2026-07-23T00:00:00Z",
      version: 1,
      run_id: run.id,
      candidate_id: candidate.id,
      source_id: "source-1",
      source_title: "公开面经",
      normalized_url: "https://example.com/interview-notes",
      source_domain: "example.com",
      source_category: "community_notes",
      excerpt: "解释检索评估和失败分析。",
      source_locator: null,
      confidence: 0.93,
    }]);

    renderPage();

    expect(await screen.findByText(candidate.prompt)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `查看候选题证据：${candidate.prompt}` }));
    expect(await screen.findByRole("dialog", { name: "候选题来源证据" })).toBeInTheDocument();
    expect(await screen.findByText("解释检索评估和失败分析。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭候选题证据" }));

    fireEvent.click(screen.getByRole("checkbox", { name: `选择候选题：${candidate.prompt}` }));
    fireEvent.click(screen.getByRole("button", { name: "导入草稿" }));

    await waitFor(() => expect(discoveryApi.importCandidates).toHaveBeenCalledWith(
      run.id,
      {
        bank_id: "bank-1",
        items: [{ candidate_id: candidate.id, candidate_revision: 1 }],
      },
      expect.any(String),
    ));
  });
});
