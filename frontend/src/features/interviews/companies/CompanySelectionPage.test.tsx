import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { companyApi } from "./api";
import { CompanySelectionPage } from "./CompanySelectionPage";
import type { Company } from "./types";

vi.mock("./api", () => ({
  companyApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    archive: vi.fn(),
    createRound: vi.fn(),
    updateRound: vi.fn(),
    deleteRound: vi.fn(),
  },
}));

const systemCompany: Company = {
  id: "company-system",
  name: "字节跳动",
  slug: "bytedance",
  description: "系统预置公司骨架",
  is_system: true,
  archived: false,
  latest_style_pack: {
    id: "pack-system",
    name: "通用轮次骨架",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "active",
    visibility: "private",
    evidence_count: 0,
    evidence_label: "轮次骨架 · 非风格结论",
    rounds: [
      {
        id: "round-system-1",
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

const customCompany: Company = {
  id: "company-custom",
  name: "我的 AI 公司",
  slug: "my-ai-company",
  description: "我维护的公司草案",
  is_system: false,
  archived: false,
  latest_style_pack: {
    id: "pack-custom",
    name: "自定义风格草案",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "draft",
    visibility: "private",
    evidence_count: 0,
    evidence_label: "自定义草案 · 未提供来源",
    rounds: [
      {
        id: "round-1",
        round_key: "round_1",
        name: "初面",
        sequence: 1,
        opening_style: "从项目边界开始追问",
        topic_weights: {},
        follow_up_patterns: ["如何验证这个方案？"],
        pressure_level: 2,
        answer_expectations: [],
        evaluation_weights: {},
        duration_minutes: 45,
      },
      {
        id: "round-2",
        round_key: "round_2",
        name: "技术面",
        sequence: 2,
        opening_style: null,
        topic_weights: {},
        follow_up_patterns: [],
        pressure_level: 3,
        answer_expectations: [],
        evaluation_weights: {},
        duration_minutes: 60,
      },
    ],
  },
};

function renderPage(companies: Company[]) {
  vi.mocked(companyApi.list).mockResolvedValue(companies);
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
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(companyApi.create).mockResolvedValue(customCompany);
    vi.mocked(companyApi.update).mockResolvedValue(customCompany);
    vi.mocked(companyApi.archive).mockResolvedValue(undefined);
    vi.mocked(companyApi.createRound).mockResolvedValue({
      ...customCompany.latest_style_pack!.rounds[1],
      id: "round-3",
      round_key: "round_3",
      name: "第 3 轮",
      sequence: 3,
    });
    vi.mocked(companyApi.updateRound).mockResolvedValue(customCompany.latest_style_pack!.rounds[0]);
    vi.mocked(companyApi.deleteRound).mockResolvedValue(undefined);
  });

  it("keeps system skeletons visibly evidence-limited and read-only", async () => {
    renderPage([systemCompany]);

    expect(await screen.findByRole("button", { name: /字节跳动/ })).toBeInTheDocument();
    expect(screen.getAllByText("证据不足")).toHaveLength(2);
    expect(screen.getByText(/系统公司只提供轮次骨架/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新增轮次" })).not.toBeInTheDocument();
  });

  it("creates a custom company with an editable three-round draft", async () => {
    renderPage([systemCompany]);

    await screen.findByRole("button", { name: /字节跳动/ });
    fireEvent.click(screen.getAllByRole("button", { name: "添加公司" })[0]);
    const createDialog = screen.getByRole("dialog", { name: "添加公司骨架" });
    fireEvent.change(within(createDialog).getByLabelText("公司名称"), { target: { value: "某云计算公司" } });
    fireEvent.click(within(createDialog).getByRole("button", { name: "创建轮次骨架" }));

    await waitFor(() => expect(companyApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: "某云计算公司", rounds: expect.any(Array) }),
    ));
    expect(vi.mocked(companyApi.create).mock.calls[0][0].rounds).toHaveLength(3);
  });

  it("makes draft provenance explicit and supports company editing plus round creation", async () => {
    renderPage([customCompany]);

    expect(await screen.findAllByText("自定义草案")).toHaveLength(2);
    expect(screen.getAllByText("证据不足")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "编辑公司" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "编辑公司" }));
    const companyDialog = screen.getByRole("dialog", { name: "编辑公司" });
    fireEvent.change(within(companyDialog).getByLabelText("公司名称"), { target: { value: "我的 AI 实验室" } });
    fireEvent.click(within(companyDialog).getByRole("button", { name: "保存公司" }));

    await waitFor(() => expect(companyApi.update).toHaveBeenCalledWith(
      "company-custom",
      { name: "我的 AI 实验室", description: "我维护的公司草案" },
    ));

    fireEvent.click(screen.getByRole("button", { name: "新增轮次" }));
    const roundDialog = screen.getByRole("dialog", { name: "新增轮次" });
    fireEvent.change(within(roundDialog).getByLabelText("轮次名称"), { target: { value: "交叉面" } });
    fireEvent.change(within(roundDialog).getByLabelText("轮次标识"), { target: { value: "cross_round" } });
    fireEvent.click(within(roundDialog).getByRole("button", { name: "新增轮次" }));

    await waitFor(() => expect(companyApi.createRound).toHaveBeenCalledWith(
      "pack-custom",
      expect.objectContaining({
        round_key: "cross_round",
        name: "交叉面",
        sequence: 3,
      }),
    ));
  });

  it("edits, reorders, deletes rounds and archives a custom company through the matching APIs", async () => {
    renderPage([customCompany]);

    await screen.findByRole("button", { name: /我的 AI 公司/ });

    fireEvent.click(screen.getByRole("button", { name: "编辑轮次" }));
    const editDialog = screen.getByRole("dialog", { name: "编辑轮次" });
    fireEvent.change(within(editDialog).getByLabelText("面试时长（分钟）"), { target: { value: "50" } });
    fireEvent.click(within(editDialog).getByRole("button", { name: "保存轮次" }));

    await waitFor(() => expect(companyApi.updateRound).toHaveBeenCalledWith(
      "round-1",
      expect.objectContaining({ duration_minutes: 50, name: "初面" }),
    ));

    vi.mocked(companyApi.updateRound).mockClear();
    fireEvent.click(screen.getByRole("button", { name: "下移初面" }));
    await waitFor(() => expect(companyApi.updateRound).toHaveBeenNthCalledWith(1, "round-1", { sequence: 3 }));
    expect(companyApi.updateRound).toHaveBeenNthCalledWith(2, "round-2", { sequence: 1 });
    expect(companyApi.updateRound).toHaveBeenNthCalledWith(3, "round-1", { sequence: 2 });

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const deleteDialog = screen.getByRole("dialog", { name: "删除 初面" });
    fireEvent.click(within(deleteDialog).getByRole("button", { name: "删除轮次" }));
    await waitFor(() => expect(companyApi.deleteRound).toHaveBeenCalledWith("round-1"));

    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    const archiveDialog = screen.getByRole("dialog", { name: "归档 我的 AI 公司" });
    fireEvent.click(within(archiveDialog).getByRole("button", { name: "确认归档" }));
    await waitFor(() => expect(companyApi.archive).toHaveBeenCalledWith("company-custom"));
  });
});
