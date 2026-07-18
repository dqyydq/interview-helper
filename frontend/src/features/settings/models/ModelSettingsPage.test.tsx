import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { modelConnectionApi } from "./api";
import { ModelSettingsPage } from "./ModelSettingsPage";

vi.mock("./api", () => ({
  modelConnectionApi: {
    list: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
    listBindings: vi.fn(),
    bindRole: vi.fn(),
    readiness: vi.fn(),
  },
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ModelSettingsPage />
    </QueryClientProvider>,
  );
}

describe("ModelSettingsPage", () => {
  beforeEach(() => {
    vi.mocked(modelConnectionApi.list).mockResolvedValue([]);
    vi.mocked(modelConnectionApi.listBindings).mockResolvedValue([]);
    vi.mocked(modelConnectionApi.readiness).mockResolvedValue({
      ready: false,
      missing_roles: ["interviewer", "evaluator"],
      degraded_roles: [],
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
});
