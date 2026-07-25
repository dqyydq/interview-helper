import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { modelConnectionApi } from "./api";
import { ModelSettingsPage } from "./ModelSettingsPage";
import type { LocalCapability, RoleBinding } from "./types";

vi.mock("./api", () => ({
  modelConnectionApi: {
    list: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
    listBindings: vi.fn(),
    bindRole: vi.fn(),
    unbindRole: vi.fn(),
    readiness: vi.fn(),
    listLocalCapabilities: vi.fn(),
    testLocalCapability: vi.fn(),
  },
}));

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
  });

  it("creates an encrypted provider connection from the settings console", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /新建连接/ }));
    fireEvent.change(screen.getByLabelText("连接名称"), { target: { value: "主模型" } });
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "model-1" } });
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "local-secret" } });
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
});
