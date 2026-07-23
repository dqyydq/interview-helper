import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { knowledgeApi } from "./api";
import { KnowledgeBasePage } from "./KnowledgeBasePage";

vi.mock("./api", () => ({
  knowledgeApi: {
    listBanks: vi.fn(),
    createBank: vi.fn(),
    listQuestions: vi.fn(),
    createQuestion: vi.fn(),
    archiveQuestion: vi.fn(),
    updateQuestion: vi.fn(),
    bulkArchiveQuestions: vi.fn(),
    createQuestionVariant: vi.fn(),
    listResumes: vi.fn(),
    uploadResume: vi.fn(),
    retryResumeParse: vi.fn(),
    deleteResume: vi.fn(),
  },
}));

const bank = {
  id: "bank-1",
  name: "LLM 应用开发",
  description: null,
  visibility: "private",
  question_count: 0,
  archived: false,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <KnowledgeBasePage />
    </QueryClientProvider>,
  );
}

describe("KnowledgeBasePage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.mocked(knowledgeApi.listBanks).mockResolvedValue([bank]);
    vi.mocked(knowledgeApi.listQuestions).mockResolvedValue({
      data: [],
      count: 0,
      offset: 0,
      limit: 20,
    });
    vi.mocked(knowledgeApi.listResumes).mockResolvedValue([]);
    vi.mocked(knowledgeApi.retryResumeParse).mockResolvedValue({
      id: "resume-retry-job",
      version: 1,
      status: "queued",
      progress: 0,
      error_code: null,
      error_message: null,
    });
    vi.mocked(knowledgeApi.deleteResume).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(knowledgeApi.createQuestion).mockResolvedValue({
      id: "question-1",
      bank_id: bank.id,
      prompt: "如何压缩长对话上下文？",
      question_type: "project_deep_dive",
      difficulty: "intermediate",
      status: "active",
      reference_points: [],
      follow_up_suggestions: [],
      applicable_companies: [],
      applicable_rounds: [],
      source_type: "manual",
      source_note: null,
      user_note: null,
      times_used: 0,
      tags: [],
      variants: [],
    });
    vi.mocked(knowledgeApi.updateQuestion).mockResolvedValue({
      id: "question-1",
      bank_id: bank.id,
      prompt: "如何压缩长对话上下文？",
      question_type: "project_deep_dive",
      difficulty: "intermediate",
      status: "active",
      reference_points: [],
      follow_up_suggestions: [],
      applicable_companies: [],
      applicable_rounds: [],
      source_type: "manual",
      source_note: null,
      user_note: null,
      times_used: 0,
      tags: [],
      variants: [],
    });
    vi.mocked(knowledgeApi.bulkArchiveQuestions).mockResolvedValue({ updated: 0 });
    vi.mocked(knowledgeApi.createQuestionVariant).mockResolvedValue({
      id: "variant-1",
      prompt: "上下文变长时，你会怎样控制 token 预算？",
      variant_type: "paraphrase",
    });
  });

  it("adds a manually managed question to the selected bank", async () => {
    renderPage();

    expect(await screen.findByText("LLM 应用开发")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "添加题目" }));
    fireEvent.change(screen.getByLabelText("题目内容"), {
      target: { value: "如何压缩长对话上下文？" },
    });
    fireEvent.change(screen.getByLabelText("标签"), { target: { value: "Agent, 上下文工程" } });
    fireEvent.click(screen.getByRole("button", { name: "保存题目" }));

    await waitFor(() => expect(knowledgeApi.createQuestion).toHaveBeenCalledTimes(1));
    expect(knowledgeApi.createQuestion).toHaveBeenCalledWith(
      expect.objectContaining({
        bank_id: bank.id,
        prompt: "如何压缩长对话上下文？",
        tag_names: ["Agent", "上下文工程"],
      }),
      expect.anything(),
    );
  });

  it("shows pending, failed, and ready resumes with the appropriate retry and delete actions", async () => {
    vi.mocked(knowledgeApi.listResumes).mockResolvedValue([
      {
        id: "resume-pending",
        filename: "待解析简历.pdf",
        mime_type: "application/pdf",
        content_hash: "pending-hash",
        parse_status: "pending",
        parse_error_code: null,
        sections: [],
        claims: [],
      },
      {
        id: "resume-failed",
        filename: "解析失败简历.pdf",
        mime_type: "application/pdf",
        content_hash: "failed-hash",
        parse_status: "failed",
        parse_error_code: "resume_parse_timeout",
        sections: [],
        claims: [],
      },
      {
        id: "resume-ready",
        filename: "已解析简历.pdf",
        mime_type: "application/pdf",
        content_hash: "ready-hash",
        parse_status: "ready",
        parse_error_code: null,
        sections: [{ id: "section-1", section_type: "project", heading: "项目", content: "项目经历" }],
        claims: [{ id: "claim-1", claim_type: "skill", content: "FastAPI", confidence: 0.9 }],
      },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "简历" }));

    expect(await screen.findByText("等待解析")).toBeInTheDocument();
    expect(screen.getByText("解析失败")).toBeInTheDocument();
    expect(screen.getByText("解析完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新解析" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /删除简历/ })).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "重新解析" }));
    await waitFor(() => expect(knowledgeApi.retryResumeParse).toHaveBeenCalledWith("resume-failed", expect.anything()));

    fireEvent.click(screen.getByRole("button", { name: "删除简历 已解析简历.pdf" }));
    await waitFor(() => expect(knowledgeApi.deleteResume).toHaveBeenCalledWith("resume-ready", expect.anything()));
  });

  it("edits a question with source and reference points, then adds a manual variant", async () => {
    vi.mocked(knowledgeApi.listQuestions).mockResolvedValue({
      data: [{
        id: "question-1",
        bank_id: bank.id,
        prompt: "如何压缩长对话上下文？",
        question_type: "project_deep_dive",
        difficulty: "intermediate",
        status: "active",
        reference_points: ["保留当前问题"],
        follow_up_suggestions: [],
        applicable_companies: ["字节跳动"],
        applicable_rounds: ["二面"],
        source_type: "manual",
        source_note: "自己的面经整理",
        user_note: null,
        times_used: 2,
        tags: [{ id: "tag-1", name: "Agent", slug: "agent", category: "general" }],
        variants: [],
      }],
      count: 1,
      offset: 0,
      limit: 20,
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "编辑题目：如何压缩长对话上下文？" }));

    fireEvent.change(screen.getByLabelText("来源说明"), { target: { value: "复盘笔记" } });
    fireEvent.change(screen.getByLabelText("参考要点"), { target: { value: "保留当前问题\n压缩历史摘要" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(knowledgeApi.updateQuestion).toHaveBeenCalledWith(
      "question-1",
      expect.objectContaining({
        source_note: "复盘笔记",
        reference_points: ["保留当前问题", "压缩历史摘要"],
        tag_names: ["Agent"],
      }),
    ));

    fireEvent.click(screen.getByRole("button", { name: "编辑题目：如何压缩长对话上下文？" }));
    fireEvent.change(screen.getByLabelText("变体内容"), { target: { value: "上下文变长时，你会怎样控制 token 预算？" } });
    fireEvent.click(screen.getByRole("button", { name: "添加变体" }));

    await waitFor(() => expect(knowledgeApi.createQuestionVariant).toHaveBeenCalledWith(
      "question-1",
      "上下文变长时，你会怎样控制 token 预算？",
      "paraphrase",
    ));
  });

  it("sends filter, sorting, and pagination state to the question API", async () => {
    vi.mocked(knowledgeApi.listQuestions).mockResolvedValue({
      data: [
        {
          id: "question-1",
          bank_id: bank.id,
          prompt: "第一个问题",
          question_type: "project_deep_dive",
          difficulty: "intermediate",
          status: "draft",
          reference_points: [],
          follow_up_suggestions: [],
          applicable_companies: [],
          applicable_rounds: [],
          source_type: "manual",
          source_note: null,
          user_note: null,
          times_used: 1,
          tags: [],
          variants: [],
        },
        {
          id: "question-2",
          bank_id: bank.id,
          prompt: "第二个问题",
          question_type: "system_design",
          difficulty: "advanced",
          status: "draft",
          reference_points: [],
          follow_up_suggestions: [],
          applicable_companies: [],
          applicable_rounds: [],
          source_type: "manual",
          source_note: null,
          user_note: null,
          times_used: 5,
          tags: [],
          variants: [],
        },
      ],
      count: 40,
      offset: 0,
      limit: 20,
    });

    renderPage();
    await screen.findByText("第一个问题");
    fireEvent.change(screen.getByLabelText("状态"), { target: { value: "draft" } });
    fireEvent.change(screen.getByLabelText("排序"), { target: { value: "times_used" } });
    fireEvent.change(screen.getByLabelText("顺序"), { target: { value: "asc" } });

    await waitFor(() => expect(knowledgeApi.listQuestions).toHaveBeenLastCalledWith(expect.objectContaining({
      bankId: bank.id,
      status: "draft",
      sortBy: "times_used",
      sortOrder: "asc",
      offset: 0,
      limit: 20,
    })));

    fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
    await waitFor(() => expect(knowledgeApi.listQuestions).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 })));
  });

  it("archives all selected questions through the bulk API", async () => {
    vi.mocked(knowledgeApi.listQuestions).mockResolvedValue({
      data: [
        {
          id: "question-1",
          bank_id: bank.id,
          prompt: "第一个问题",
          question_type: "project_deep_dive",
          difficulty: "intermediate",
          status: "active",
          reference_points: [],
          follow_up_suggestions: [],
          applicable_companies: [],
          applicable_rounds: [],
          source_type: "manual",
          source_note: null,
          user_note: null,
          times_used: 0,
          tags: [],
          variants: [],
        },
        {
          id: "question-2",
          bank_id: bank.id,
          prompt: "第二个问题",
          question_type: "system_design",
          difficulty: "advanced",
          status: "active",
          reference_points: [],
          follow_up_suggestions: [],
          applicable_companies: [],
          applicable_rounds: [],
          source_type: "manual",
          source_note: null,
          user_note: null,
          times_used: 0,
          tags: [],
          variants: [],
        },
      ],
      count: 2,
      offset: 0,
      limit: 20,
    });

    renderPage();
    await screen.findByText("第一个问题");
    fireEvent.click(screen.getByRole("checkbox", { name: "选择当前页全部题目" }));

    fireEvent.click(await screen.findByRole("button", { name: "归档已选 2 道" }));
    await waitFor(() => expect(knowledgeApi.bulkArchiveQuestions).toHaveBeenCalledWith(
      ["question-1", "question-2"],
      expect.anything(),
    ));
  });
});
