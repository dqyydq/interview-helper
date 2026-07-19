import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { memoryApi } from "./api";
import { MemorySettingsPage } from "./MemorySettingsPage";
import type { MemoryItem } from "./types";

vi.mock("./api", () => ({
  memoryApi: {
    list: vi.fn(), settings: vi.fn(), updateSettings: vi.fn(), update: vi.fn(),
    confirm: vi.fn(), pin: vi.fn(), reject: vi.fn(), remove: vi.fn(), resolveConflict: vi.fn(),
  },
}));

const proposedMemory: MemoryItem = {
  id: "memory-1", memory_type: "recurring_gap", canonical_key: "gap:system-design",
  memory_version: 1, content: "系统设计回答中经常遗漏容量估算", structured_value: {},
  status: "proposed", confidence: 0.82, first_observed_at: "2026-07-18T10:00:00Z",
  last_verified_at: null, last_used_at: null, expires_at: null, pinned: false,
  sources: [{ id: "source-1", session_id: "session-1", message_id: null,
    source_type: "interview_evaluation", evidence_excerpt: "未说明容量规划",
    observed_at: "2026-07-18T10:00:00Z" }],
  open_conflicts: [],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/settings/memory"]}><MemorySettingsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MemorySettingsPage", () => {
  beforeEach(() => {
    vi.mocked(memoryApi.list).mockResolvedValue([proposedMemory]);
    vi.mocked(memoryApi.settings).mockResolvedValue({ memory_enabled: true });
    vi.mocked(memoryApi.confirm).mockResolvedValue({ ...proposedMemory, status: "active" });
    vi.mocked(memoryApi.updateSettings).mockResolvedValue({ memory_enabled: false });
  });

  it("lets the user confirm proposed memory and disable cross-session use", async () => {
    renderPage();
    expect(await screen.findByText("系统设计回答中经常遗漏容量估算")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认记忆" }));
    await waitFor(() => expect(memoryApi.confirm).toHaveBeenCalledWith("memory-1"));
    fireEvent.click(screen.getByRole("checkbox", { name: /跨场记忆/ }));
    await waitFor(() => expect(memoryApi.updateSettings).toHaveBeenCalledWith(false, expect.anything()));
  });
});
