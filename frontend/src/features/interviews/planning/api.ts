import { apiRequest, apiUrl } from "../../../lib/api/client";
import type { InterviewPlan, PlanCreateResult, PlanDraft } from "./types";

export const planningApi = {
  create: (draft: PlanDraft) =>
    apiRequest<PlanCreateResult>("/interview-plans", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  get: (planId: string) => apiRequest<InterviewPlan>(`/interview-plans/${planId}`),
  jobEventsUrl: (jobId: string) => apiUrl(`/jobs/${jobId}/events`),
};
