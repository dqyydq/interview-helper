import { apiRequest, apiUrl } from "../../../lib/api/client";
import type { MemoryPreview } from "../../settings/memory/types";
import type { InterviewPlan, PlanCreateResult, PlanDraft } from "./types";

export const planningApi = {
  create: (draft: PlanDraft) =>
    apiRequest<PlanCreateResult>("/interview-plans", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  get: (planId: string) => apiRequest<InterviewPlan>(`/interview-plans/${planId}`),
  memoryPreview: (planId: string) =>
    apiRequest<MemoryPreview>(`/interview-plans/${planId}/memory-preview`),
  jobEventsUrl: (jobId: string) => apiUrl(`/jobs/${jobId}/events`),
};
