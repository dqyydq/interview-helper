import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { discoveryApi } from "../../discovery/api";
import type { DiscoveryConnector } from "../../discovery/types";
import { DiscoverySettingsPage } from "./DiscoverySettingsPage";

vi.mock("../../discovery/api", () => ({
  discoveryApi: {
    listConnectors: vi.fn(),
    createConnector: vi.fn(),
    updateConnector: vi.fn(),
    removeConnector: vi.fn(),
    testConnector: vi.fn(),
  },
}));

const baseConnector: DiscoveryConnector = {
  id: "connector-1",
  created_at: "2026-07-24T00:00:00Z",
  updated_at: "2026-07-24T00:00:00Z",
  version: 1,
  name: "本地 Tavily",
  provider_type: "tavily",
  enabled: true,
  capabilities: { supports_domain_filters: true, supports_extract: true, safe_extract: true },
  configuration: { default_country: "cn" },
  configuration_version: 1,
  status: "healthy",
  last_tested_at: "2026-07-24T00:00:00Z",
  last_error_code: null,
  has_api_key: true,
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/settings/discovery"]}>
        <DiscoverySettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DiscoverySettingsPage", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(discoveryApi.listConnectors).mockResolvedValue([]);
    vi.mocked(discoveryApi.createConnector).mockResolvedValue(baseConnector);
  });

  it("groups connectors by provider and counts disabled connectors toward each provider limit", async () => {
    vi.mocked(discoveryApi.listConnectors).mockResolvedValue([
      baseConnector,
      { ...baseConnector, id: "connector-2", name: "备用 Tavily" },
      { ...baseConnector, id: "connector-3", name: "已停用 Tavily", enabled: false },
      { ...baseConnector, id: "connector-4", name: "本地 Firecrawl", provider_type: "firecrawl" },
    ]);

    renderPage();

    expect(await screen.findByText("TAVILY · 已保存 3/3")).toBeInTheDocument();
    expect(screen.getByText("FIRECRAWL · 已保存 1/3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建 Tavily 连接器" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建 Firecrawl 连接器" })).toBeEnabled();
  });

  it("creates a Firecrawl connector from the provider-specific create action", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建 Firecrawl 连接器" }));
    fireEvent.change(screen.getByLabelText("连接器名称"), { target: { value: "我的 Firecrawl" } });
    fireEvent.change(screen.getByLabelText("Firecrawl API Key"), { target: { value: "fc-local-key" } });
    fireEvent.click(screen.getByRole("button", { name: "保存连接器" }));

    await waitFor(() => expect(discoveryApi.createConnector).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "我的 Firecrawl",
        provider_type: "firecrawl",
        api_key: "fc-local-key",
      }),
      expect.anything(),
    ));
  });

  it("keeps a connector provider fixed while editing", async () => {
    const firecrawlConnector: DiscoveryConnector = {
      ...baseConnector,
      id: "connector-firecrawl",
      name: "本地 Firecrawl",
      provider_type: "firecrawl",
    };
    vi.mocked(discoveryApi.listConnectors).mockResolvedValue([firecrawlConnector]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "编辑 本地 Firecrawl" }));

    expect(screen.getByRole("button", { name: "Firecrawl · 连接类型固定" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Tavily ·/ })).not.toBeInTheDocument();
  });
});
