import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { companyApi } from "./api";
import { CompanySelectionPage } from "./CompanySelectionPage";

vi.mock("./api", () => ({
  companyApi: {
    list: vi.fn(),
    create: vi.fn(),
  },
}));

const company = {
  id: "company-1",
  name: "字节跳动",
  slug: "bytedance",
  description: "系统预置公司骨架",
  is_system: true,
  archived: false,
  latest_style_pack: {
    id: "pack-1",
    name: "通用轮次骨架",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "active" as const,
    visibility: "private" as const,
    evidence_count: 0,
    evidence_label: "轮次骨架 · 非风格结论",
    rounds: [
      {
        id: "round-1",
        round_key: "round_1",
        name: "一面",
        sequence: 1,
        opening_style: null,
        topic_weights: {},
        follow_up_patterns: [],
        pressure_level: 1,
        answer_expectations: [],
        evaluation_weights: {},
        duration_minutes: 45,
      },
    ],
  },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CompanySelectionPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CompanySelectionPage", () => {
  beforeEach(() => {
    vi.mocked(companyApi.list).mockResolvedValue([company]);
    vi.mocked(companyApi.create).mockResolvedValue(company);
  });

  it("shows safe company round skeletons and creates a custom company", async () => {
    renderPage();

    expect(await screen.findByRole("button", { name: /字节跳动/ })).toBeInTheDocument();
    expect(screen.getAllByText("轮次骨架 · 非风格结论")).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "添加公司" })[0]);
    fireEvent.change(screen.getByLabelText("公司名称"), { target: { value: "某云计算公司" } });
    fireEvent.click(screen.getByRole("button", { name: "创建轮次骨架" }));

    await waitFor(() => expect(companyApi.create).toHaveBeenCalledTimes(1));
    expect(companyApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: "某云计算公司", rounds: expect.any(Array) }),
      expect.anything(),
    );
    const [payload] = vi.mocked(companyApi.create).mock.calls[0];
    expect(payload.rounds).toHaveLength(3);
  });
});
