import { apiRequest } from "../../lib/api/client";
import type {
  Question,
  QuestionBank,
  QuestionDraft,
  QuestionPage,
  Resume,
  ResumeUploadResult,
} from "./types";

export const knowledgeApi = {
  listBanks: () => apiRequest<QuestionBank[]>("/question-banks"),
  createBank: (name: string) =>
    apiRequest<QuestionBank>("/question-banks", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  listQuestions: (bankId?: string, search?: string) => {
    const params = new URLSearchParams();
    if (bankId) params.set("bank_id", bankId);
    if (search) params.set("search", search);
    params.set("limit", "100");
    return apiRequest<QuestionPage>(`/questions?${params.toString()}`);
  },
  createQuestion: (draft: QuestionDraft) =>
    apiRequest<Question>("/questions", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  archiveQuestion: (questionId: string) =>
    apiRequest<{ updated: number }>(`/questions/${questionId}`, { method: "DELETE" }),
  listResumes: () => apiRequest<Resume[]>("/resumes"),
  uploadResume: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return apiRequest<ResumeUploadResult>("/resumes", { method: "POST", body });
  },
};
