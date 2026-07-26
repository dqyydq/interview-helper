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
    extractVisualEvidence: vi.fn(),
    addEvidence: vi.fn(),
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
    vi.mocked(companyApi.extractVisualEvidence).mockResolvedValue({
      source_url: "https://example.com/interview-notes",
      source_title: "匿名面试复盘页面",
      candidates: [
        {
          field_path: "rounds.round_1.follow_up_patterns",
          excerpt: "围绕项目取舍和验证方式继续追问。",
          confidence: 0.74,
        },
      ],
      allowed_field_paths: [
        "default_interviewer_behavior",
        "rounds.round_1.follow_up_patterns",
      ],
      warning_codes: ["image_not_retained"],
      image_retained: false,
    });
    vi.mocked(companyApi.addEvidence).mockResolvedValue({
      id: "evidence-1",
      source_url: "https://example.com/interview-notes",
      source_title: "匿名面试复盘页面",
      field_path: "rounds.round_1.follow_up_patterns",
      excerpt: "围绕项目取舍和验证方式继续追问。",
      published_at: null,
      fetched_at: "2026-07-26T00:00:00Z",
      confidence: 0.74,
    });
  });

  it("keeps system skeletons visibly evidence-limited and read-only", async () => {
    renderPage([systemCompany]);

    expect(await screen.findByRole("button", { name: /字节跳动/ })).toBeInTheDocument();
    expect(screen.getAllByText("证据不足")).toHaveLength(2);
    expect(screen.getByText(/系统公司只提供轮次骨架/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新增轮次" })).not.toBeInTheDocument();
    expect(screen.getByText(/创建我的版本后即可编辑公司与轮次/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "创建我的版本" }));
    const createDialog = screen.getByRole("dialog", { name: "添加公司骨架" });
    expect(within(createDialog).getByLabelText("公司名称")).toHaveValue("字节跳动（我的版本）");
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

  it("turns a private screenshot into review-only evidence before the user explicitly writes it", async () => {
    renderPage([customCompany]);

    await screen.findByRole("button", { name: "用截图整理" });
    fireEvent.click(screen.getByRole("button", { name: "用截图整理" }));
    const dialog = screen.getByRole("dialog", { name: /用截图整理.*面试信号/ });
    fireEvent.change(within(dialog).getByLabelText("原始页面链接"), {
      target: { value: "https://example.com/interview-notes" },
    });
    fireEvent.change(within(dialog).getByLabelText("来源标题"), {
      target: { value: "匿名面试复盘页面" },
    });
    const image = new File(["safe image"], "notes.png", { type: "image/png" });
    fireEvent.change(within(dialog).getByLabelText(/选择已脱敏截图/), {
      target: { files: [image] },
    });
    fireEvent.click(within(dialog).getByLabelText(/我确认资料已去除个人信息/));
    const extractButton = within(dialog).getByRole("button", { name: "解析为证据草案" });
    await waitFor(() => expect(extractButton).toBeEnabled());
    fireEvent.click(extractButton);

    await waitFor(() => expect(companyApi.extractVisualEvidence).toHaveBeenCalledWith(
      "pack-custom",
      expect.objectContaining({
        sourceUrl: "https://example.com/interview-notes",
        sourceTitle: "匿名面试复盘页面",
        sourceConfirmed: true,
        image,
      }),
    ));
    expect(await within(dialog).findByDisplayValue("围绕项目取舍和验证方式继续追问。")).toBeInTheDocument();
    expect(companyApi.addEvidence).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "确认写入草案" }));
    await waitFor(() => expect(companyApi.addEvidence).toHaveBeenCalledWith(
      "pack-custom",
      {
        source_url: "https://example.com/interview-notes",
        source_title: "匿名面试复盘页面",
        field_path: "rounds.round_1.follow_up_patterns",
        excerpt: "围绕项目取舍和验证方式继续追问。",
        confidence: 0.74,
      },
    ));
  });
});
