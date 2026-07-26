import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { modelConnectionApi } from "./api";
import { ModelSettingsPage } from "./ModelSettingsPage";
import type { EmbeddingIndexStatus, LocalCapability, RoleBinding } from "./types";

vi.mock("./api", () => ({
  modelConnectionApi: {
    list: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    redactCredentials: vi.fn(),
    test: vi.fn(),
    listBindings: vi.fn(),
    bindRole: vi.fn(),
    unbindRole: vi.fn(),
    readiness: vi.fn(),
    listLocalCapabilities: vi.fn(),
    testLocalCapability: vi.fn(),
    embeddingIndexStatus: vi.fn(),
    rebuildEmbeddingIndex: vi.fn(),
  },
}));

const emptyEmbeddingIndexStatus: EmbeddingIndexStatus = {
  active_profile: null,
  building_profile: null,
  latest_failed_profile: null,
  job: null,
  interview_active: false,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ModelSettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ModelSettingsPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(modelConnectionApi.list).mockResolvedValue([]);
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([]);
    vi.mocked(modelConnectionApi.readiness).mockResolvedValue({
      ready: false,
      missing_roles: ["interviewer", "evaluator"],
      degraded_roles: [],
    });
    vi.mocked(modelConnectionApi.listLocalCapabilities).mockResolvedValue([]);
    vi.mocked(modelConnectionApi.embeddingIndexStatus).mockResolvedValue(emptyEmbeddingIndexStatus);
    vi.mocked(modelConnectionApi.rebuildEmbeddingIndex).mockResolvedValue({
      embedding_profile: {
        id: "embedding-profile-1",
        created_at: "2026-07-25T00:00:00Z",
        updated_at: "2026-07-25T00:00:00Z",
        version: 1,
        target_kind: "local_capability",
        model_name: "interview-helper-local-embedding",
        model_revision: "revision-1",
        vector_dimensions: null,
        normalized: true,
        distance_metric: "cosine",
        status: "building",
        activated_at: null,
        failed_at: null,
        failure_code: null,
        failure_summary: null,
      },
      job: {
        id: "embedding-job-1",
        created_at: "2026-07-25T00:00:00Z",
        updated_at: "2026-07-25T00:00:00Z",
        version: 1,
        status: "queued",
        progress: 0,
        phase: "queued",
        memory_scanned: 0,
        memory_embeddings: 0,
        plan_question_scanned: 0,
        plan_question_embeddings: 0,
        vector_dimensions: null,
        error_code: null,
        attempts: 0,
        max_attempts: 3,
        available_at: "2026-07-25T00:00:00Z",
      },
      created: true,
    });
    vi.mocked(modelConnectionApi.testLocalCapability).mockResolvedValue({
      key: "sensevoice-small",
      role: "transcriber",
      title: "SenseVoice 本地语音转写",
      summary: "Docker 内离线 FunASR。",
      runtime: "funasr",
      compose_profile: "local-asr",
      model_name: "sensevoice-small",
      revision: "43d0ed61231c41f8393fa347b838a1f6e2d264f6",
      vector_dimensions: null,
      status: "unavailable",
      latency_ms: null,
      error_code: "provider_connection_failed",
    });
    vi.mocked(modelConnectionApi.create).mockResolvedValue({
      id: "connection-1",
      name: "主模型",
      provider_type: "openai_compatible",
      base_url: "https://models.test/v1",
      model_name: "model-1",
      context_window_tokens: 128000,
      max_output_tokens: 4096,
      tokenizer_type: "estimated",
      supports_prompt_caching: false,
      supports_token_count_endpoint: false,
      status: "untested",
      has_api_key: true,
    });
    vi.mocked(modelConnectionApi.redactCredentials).mockResolvedValue({
      id: "connection-1",
      name: "主模型",
      provider_type: "openai_compatible",
      base_url: "https://models.test/v1",
      model_name: "model-1",
      context_window_tokens: 128000,
      max_output_tokens: 4096,
      tokenizer_type: "estimated",
      supports_prompt_caching: false,
      supports_token_count_endpoint: false,
      status: "disabled",
      has_api_key: false,
    });
  });

  it("creates an encrypted provider connection from the settings console", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /新建连接/ }));
    fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "主模型" } });
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "model-1" } });
    fireEvent.change(screen.getByLabelText("API 密钥"), { target: { value: "local-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "保存加密连接" }));

    await waitFor(() => expect(modelConnectionApi.create).toHaveBeenCalledTimes(1));
    expect(modelConnectionApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "主模型",
        model_name: "model-1",
        api_key: "local-secret",
        provider_type: "openai_compatible",
      }),
      expect.anything(),
    );
  });

  it("shows an inactive embedding alternative instead of reporting a false model mismatch", async () => {
    const capabilities: LocalCapability[] = [
      {
        key: "multilingual-e5-small",
        role: "embedding",
        title: "E5 轻量本地检索",
        summary: "384 维多语言 dense embedding。",
        runtime: "tei",
        compose_profile: "local-embedding-e5",
        model_name: "interview-helper-local-embedding",
        revision: "bdd905ef05181adf3ebbfaac5cd5bd4ed9a58760",
        vector_dimensions: 384,
        status: "ready",
        latency_ms: 23,
        error_code: null,
      },
      {
        key: "bge-m3",
        role: "embedding",
        title: "BGE-M3 高质量本地检索",
        summary: "1024 维 dense embedding。",
        runtime: "tei",
        compose_profile: "local-embedding-bge",
        model_name: "interview-helper-local-embedding",
        revision: "e44369c5623cc146f016da906583db4ee0e3488d",
        vector_dimensions: 1024,
        status: "mismatch",
        latency_ms: 24,
        error_code: "provider_invalid_response",
      },
    ];
    vi.mocked(modelConnectionApi.listLocalCapabilities).mockResolvedValue(capabilities);

    renderPage();

    const bgeTitle = await screen.findByText("BGE-M3 高质量本地检索");
    const bgeCard = bgeTitle.closest("article");
    expect(bgeCard).not.toBeNull();
    expect(within(bgeCard as HTMLElement).getByRole("status")).toHaveTextContent(
      "当前未启用（E5 轻量本地检索 已就绪）",
    );
    expect(within(bgeCard as HTMLElement).getByRole("status")).toHaveTextContent(
      "一次只能运行一种嵌入模型",
    );
    expect(bgeCard?.querySelector(".local-card-mobile-status")).toHaveTextContent("当前未启用");
    expect(
      screen.getByRole("option", { name: /BGE-M3 高质量本地检索 · 当前未启用/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("模型校验未通过")).not.toBeInTheDocument();
  });

  it("shows live, per-card checking feedback without disabling other local services", async () => {
    const capabilities: LocalCapability[] = [
      {
        key: "sensevoice-small",
        role: "transcriber",
        title: "SenseVoice 本地语音转写",
        summary: "Docker 内离线 FunASR。",
        runtime: "funasr",
        compose_profile: "local-asr",
        model_name: "sensevoice-small",
        revision: "43d0ed61231c41f8393fa347b838a1f6e2d264f6",
        vector_dimensions: null,
        status: "unavailable",
        latency_ms: null,
        error_code: "provider_connection_failed",
      },
      {
        key: "multilingual-e5-small",
        role: "embedding",
        title: "E5 轻量本地检索",
        summary: "384 维多语言 dense embedding。",
        runtime: "tei",
        compose_profile: "local-embedding-e5",
        model_name: "interview-helper-local-embedding",
        revision: "bdd905ef05181adf3ebbfaac5cd5bd4ed9a58760",
        vector_dimensions: 384,
        status: "unavailable",
        latency_ms: null,
        error_code: "provider_connection_failed",
      },
    ];
    let resolveCheck!: (capability: LocalCapability) => void;
    const pendingCheck = new Promise<LocalCapability>((resolve) => {
      resolveCheck = resolve;
    });
    vi.mocked(modelConnectionApi.listLocalCapabilities).mockResolvedValue(capabilities);
    vi.mocked(modelConnectionApi.testLocalCapability).mockReturnValue(pendingCheck);

    renderPage();

    const senseVoiceButton = await screen.findByRole("button", {
      name: "检查 SenseVoice 本地语音转写",
    });
    const e5Button = screen.getByRole("button", { name: "检查 E5 轻量本地检索" });
    fireEvent.click(senseVoiceButton);

    const senseVoiceCard = senseVoiceButton.closest("article") as HTMLElement;
    await waitFor(() => expect(senseVoiceButton).toHaveAttribute("aria-busy", "true"));
    expect(senseVoiceButton).toBeDisabled();
    expect(e5Button).not.toBeDisabled();
    expect(within(senseVoiceCard).getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(within(senseVoiceCard).getByRole("status")).toHaveTextContent("正在检查…");

    resolveCheck(capabilities[0]);
    await waitFor(() => expect(senseVoiceButton).toHaveAttribute("aria-busy", "false"));
  });

  it("marks an unavailable local role binding as a preconfiguration and supports unbinding it", async () => {
    const capability: LocalCapability = {
      key: "sensevoice-small",
      role: "transcriber",
      title: "SenseVoice 本地语音转写",
      summary: "Docker 内离线 FunASR。",
      runtime: "funasr",
      compose_profile: "local-asr",
      model_name: "sensevoice-small",
      revision: "43d0ed61231c41f8393fa347b838a1f6e2d264f6",
      vector_dimensions: null,
      status: "unavailable",
      latency_ms: null,
      error_code: "provider_connection_failed",
    };
    const binding: RoleBinding = {
      id: "binding-1",
      role: "transcriber",
      target_kind: "local_capability",
      connection_id: null,
      connection_name: null,
      model_name: null,
      connection_status: null,
      local_capability_key: "sensevoice-small",
    };
    vi.mocked(modelConnectionApi.listLocalCapabilities).mockResolvedValue([capability]);
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([binding]);
    vi.mocked(modelConnectionApi.unbindRole).mockResolvedValue();

    renderPage();

    await screen.findByText("SenseVoice 本地语音转写");
    await waitFor(() =>
      expect(screen.getByText(/已保存为预配置：待配置/)).toHaveTextContent(
        "服务就绪前不会自动改用云端模型",
      ),
    );
    expect(
      screen.getByRole("option", { name: /SenseVoice 本地语音转写 · 待配置/ }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("语音转写模型"), { target: { value: "" } });
    await waitFor(() => expect(modelConnectionApi.unbindRole).toHaveBeenCalled());
    expect(vi.mocked(modelConnectionApi.unbindRole).mock.calls[0]?.[0]).toBe("transcriber");
  });

  it("queues semantic indexing explicitly and explains that a prior active index stays live", async () => {
    const embeddingBinding: RoleBinding = {
      id: "embedding-binding-1",
      role: "embedding",
      target_kind: "local_capability",
      connection_id: null,
      connection_name: null,
      model_name: "interview-helper-local-embedding",
      connection_status: null,
      local_capability_key: "multilingual-e5-small",
    };
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([embeddingBinding]);
    vi.mocked(modelConnectionApi.embeddingIndexStatus).mockResolvedValue({
      active_profile: {
        id: "embedding-profile-active",
        created_at: "2026-07-25T00:00:00Z",
        updated_at: "2026-07-25T00:00:00Z",
        version: 2,
        target_kind: "local_capability",
        model_name: "interview-helper-local-embedding",
        model_revision: "revision-e5",
        vector_dimensions: 384,
        normalized: true,
        distance_metric: "cosine",
        status: "active",
        activated_at: "2026-07-25T00:00:00Z",
        failed_at: null,
        failure_code: null,
        failure_summary: null,
      },
      building_profile: {
        id: "embedding-profile-building",
        created_at: "2026-07-25T00:00:01Z",
        updated_at: "2026-07-25T00:00:01Z",
        version: 1,
        target_kind: "local_capability",
        model_name: "interview-helper-local-embedding",
        model_revision: "revision-e5",
        vector_dimensions: 384,
        normalized: true,
        distance_metric: "cosine",
        status: "building",
        activated_at: null,
        failed_at: null,
        failure_code: null,
        failure_summary: null,
      },
      latest_failed_profile: null,
      job: {
        id: "embedding-job-building",
        created_at: "2026-07-25T00:00:01Z",
        updated_at: "2026-07-25T00:00:01Z",
        version: 3,
        status: "running",
        progress: 0.62,
        phase: "indexing_plan_question",
        memory_scanned: 3,
        memory_embeddings: 3,
        plan_question_scanned: 5,
        plan_question_embeddings: 5,
        vector_dimensions: 384,
        error_code: null,
        attempts: 1,
        max_attempts: 3,
        available_at: "2026-07-25T00:00:01Z",
      },
      interview_active: false,
    });

    renderPage();

    expect(await screen.findByText("正在后台构建语义索引")).toBeInTheDocument();
    expect(screen.getByText("旧索引仍在服务本次面试；新索引验证完成后才会原子切换。")).toBeInTheDocument();
    expect(screen.getByText(/62% · 已写入 8 条缓存 · 384 维/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "后台构建中" })).toBeDisabled();
  });

  it("surfaces a failed replacement while preserving the prior active semantic index", async () => {
    const embeddingBinding: RoleBinding = {
      id: "embedding-binding-1",
      role: "embedding",
      target_kind: "model_connection",
      connection_id: "connection-embedding",
      connection_name: "Embedding cloud",
      model_name: "text-embedding-test",
      connection_status: "healthy",
      local_capability_key: null,
    };
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([embeddingBinding]);
    vi.mocked(modelConnectionApi.embeddingIndexStatus).mockResolvedValue({
      active_profile: {
        id: "embedding-profile-active",
        created_at: "2026-07-25T00:00:00Z",
        updated_at: "2026-07-25T00:00:00Z",
        version: 2,
        target_kind: "model_connection",
        model_name: "text-embedding-test",
        model_revision: "provider-configured",
        vector_dimensions: 1024,
        normalized: true,
        distance_metric: "cosine",
        status: "active",
        activated_at: "2026-07-25T00:00:00Z",
        failed_at: null,
        failure_code: null,
        failure_summary: null,
      },
      building_profile: null,
      latest_failed_profile: {
        id: "embedding-profile-failed",
        created_at: "2026-07-25T00:02:00Z",
        updated_at: "2026-07-25T00:02:00Z",
        version: 1,
        target_kind: "model_connection",
        model_name: "text-embedding-test",
        model_revision: "provider-configured",
        vector_dimensions: 1024,
        normalized: true,
        distance_metric: "cosine",
        status: "failed",
        activated_at: null,
        failed_at: "2026-07-25T00:02:00Z",
        failure_code: "embedding_dimension_mismatch",
        failure_summary: "新索引校验未完成。",
      },
      job: {
        id: "embedding-job-failed",
        created_at: "2026-07-25T00:02:00Z",
        updated_at: "2026-07-25T00:02:00Z",
        version: 2,
        status: "failed",
        progress: 1,
        phase: "failed",
        memory_scanned: 2,
        memory_embeddings: 2,
        plan_question_scanned: 1,
        plan_question_embeddings: 1,
        vector_dimensions: 1024,
        error_code: "embedding_dimension_mismatch",
        attempts: 1,
        max_attempts: 3,
        available_at: "2026-07-25T00:02:00Z",
      },
      interview_active: false,
    });

    renderPage();

    expect(await screen.findByText("新索引未构建完成，仍在使用上一版")).toBeInTheDocument();
    expect(
      screen.getByText("新索引校验未完成。旧索引仍在服务，可修正配置后重新构建。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新构建" })).toBeEnabled();
  });

  it("blocks rebuilding a locally bound index until its Docker embedding service is ready", async () => {
    const capability: LocalCapability = {
      key: "multilingual-e5-small",
      role: "embedding",
      title: "E5 轻量本地检索",
      summary: "384 维多语言 dense embedding。",
      runtime: "tei",
      compose_profile: "local-embedding-e5",
      model_name: "interview-helper-local-embedding",
      revision: "revision-e5",
      vector_dimensions: 384,
      status: "unavailable",
      latency_ms: null,
      error_code: "provider_connection_failed",
    };
    const embeddingBinding: RoleBinding = {
      id: "embedding-binding-1",
      role: "embedding",
      target_kind: "local_capability",
      connection_id: null,
      connection_name: null,
      model_name: "interview-helper-local-embedding",
      connection_status: null,
      local_capability_key: "multilingual-e5-small",
    };
    vi.mocked(modelConnectionApi.listLocalCapabilities).mockResolvedValue([capability]);
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([embeddingBinding]);

    renderPage();

    const embeddingSelect = await screen.findByLabelText("向量检索模型");
    await waitFor(() => expect(embeddingSelect).toHaveValue("local:multilingual-e5-small"));
    const rebuild = screen.getByRole("button", { name: "建立索引" });
    expect(rebuild).toBeDisabled();
    expect(document.querySelector(".embedding-index-blocked")).toHaveTextContent(
      /已绑定 E5 轻量本地检索；请先启动 local-embedding-e5\s*并点击“检查”/,
    );
  });

  it("offers explicit credential redaction for an unused cloud connection", async () => {
    vi.mocked(modelConnectionApi.list).mockResolvedValue([
      {
        id: "connection-1",
        name: "待撤销连接",
        provider_type: "openai_compatible",
        base_url: "https://models.test/v1",
        model_name: "model-1",
        context_window_tokens: 128000,
        max_output_tokens: 4096,
        tokenizer_type: "estimated",
        supports_prompt_caching: false,
        supports_token_count_endpoint: false,
        status: "healthy",
        has_api_key: true,
      },
    ]);
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "清除 待撤销连接 的密钥并停用" }));
    await waitFor(() => expect(modelConnectionApi.redactCredentials).toHaveBeenCalled());
    expect(vi.mocked(modelConnectionApi.redactCredentials).mock.calls[0]?.[0]).toBe("connection-1");
    confirmation.mockRestore();
  });

  it("offers an explicit rebuild only after an embedding target is bound", async () => {
    const embeddingBinding: RoleBinding = {
      id: "embedding-binding-1",
      role: "embedding",
      target_kind: "model_connection",
      connection_id: "connection-embedding",
      connection_name: "Embedding cloud",
      model_name: "text-embedding-test",
      connection_status: "healthy",
      local_capability_key: null,
    };
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([embeddingBinding]);
    vi.mocked(modelConnectionApi.embeddingIndexStatus).mockResolvedValue({
      ...emptyEmbeddingIndexStatus,
      active_profile: {
        id: "embedding-profile-active",
        created_at: "2026-07-25T00:00:00Z",
        updated_at: "2026-07-25T00:00:00Z",
        version: 2,
        target_kind: "local_capability",
        model_name: "interview-helper-local-embedding",
        model_revision: "revision-e5",
        vector_dimensions: 384,
        normalized: true,
        distance_metric: "cosine",
        status: "active",
        activated_at: "2026-07-25T00:00:00Z",
        failed_at: null,
        failure_code: null,
        failure_summary: null,
      },
    });

    renderPage();

    const rebuild = await screen.findByRole("button", { name: "重新构建" });
    expect(rebuild).toBeEnabled();
    fireEvent.click(rebuild);
    await waitFor(() => expect(modelConnectionApi.rebuildEmbeddingIndex).toHaveBeenCalledTimes(1));
  });
});
