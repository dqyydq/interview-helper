import { apiRequest } from "../../lib/api/client";
import type {
  BackgroundJob,
  Question,
  QuestionBank,
  QuestionDraft,
  QuestionListQuery,
  QuestionPage,
  QuestionUpdateDraft,
  QuestionVariant,
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
  archiveBank: (bankId: string) =>
    apiRequest<void>(`/question-banks/${bankId}`, { method: "DELETE" }),
  listQuestions: (query: QuestionListQuery = {}) => {
    const params = new URLSearchParams();
    if (query.bankId) params.set("bank_id", query.bankId);
    if (query.search) params.set("search", query.search);
    if (query.status) params.set("status", query.status);
    if (query.questionType) params.set("question_type", query.questionType);
    if (query.difficulty) params.set("difficulty", query.difficulty);
    if (query.tag) params.set("tag", query.tag);
    if (query.sortBy) params.set("sort_by", query.sortBy);
    if (query.sortOrder) params.set("sort_order", query.sortOrder);
    if (query.offset !== undefined) params.set("offset", String(query.offset));
    params.set("limit", String(query.limit ?? 20));
    return apiRequest<QuestionPage>(`/questions?${params.toString()}`);
  },
  createQuestion: (draft: QuestionDraft) =>
    apiRequest<Question>("/questions", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  archiveQuestion: (questionId: string) =>
    apiRequest<{ updated: number }>(`/questions/${questionId}`, { method: "DELETE" }),
  updateQuestion: (questionId: string, draft: QuestionUpdateDraft) =>
    apiRequest<Question>(`/questions/${questionId}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }),
  bulkArchiveQuestions: (questionIds: string[]) =>
    apiRequest<{ updated: number }>("/questions/bulk-archive", {
      method: "POST",
      body: JSON.stringify({ question_ids: questionIds }),
    }),
  createQuestionVariant: (questionId: string, prompt: string, variantType: string) =>
    apiRequest<QuestionVariant>(`/questions/${questionId}/variants`, {
      method: "POST",
      body: JSON.stringify({ prompt, variant_type: variantType }),
    }),
  listResumes: () => apiRequest<Resume[]>("/resumes"),
  uploadResume: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return apiRequest<ResumeUploadResult>("/resumes", { method: "POST", body });
  },
  retryResumeParse: (resumeId: string) =>
    apiRequest<BackgroundJob>(`/resumes/${resumeId}/parse`, { method: "POST" }),
  deleteResume: (resumeId: string) =>
    apiRequest<void>(`/resumes/${resumeId}`, { method: "DELETE" }),
};
