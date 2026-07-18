import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { knowledgeApi } from "./api";
import { KnowledgeBasePage } from "./KnowledgeBasePage";

vi.mock("./api", () => ({
  knowledgeApi: {
    listBanks: vi.fn(),
    createBank: vi.fn(),
    listQuestions: vi.fn(),
    createQuestion: vi.fn(),
    archiveQuestion: vi.fn(),
    listResumes: vi.fn(),
    uploadResume: vi.fn(),
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
  beforeEach(() => {
    vi.mocked(knowledgeApi.listBanks).mockResolvedValue([bank]);
    vi.mocked(knowledgeApi.listQuestions).mockResolvedValue({
      data: [],
      count: 0,
      offset: 0,
      limit: 100,
    });
    vi.mocked(knowledgeApi.listResumes).mockResolvedValue([]);
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
});
