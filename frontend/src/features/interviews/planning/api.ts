import { apiRequest, apiUrl } from "../../../lib/api/client";
import type { MemoryPreview } from "../../settings/memory/types";
import type {
  InterviewPlan,
  InterviewReadiness,
  PlanCreateResult,
  PlanDraft,
} from "./types";

export const planningApi = {
  create: (draft: PlanDraft) =>
    apiRequest<PlanCreateResult>("/interview-plans", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  get: (planId: string) => apiRequest<InterviewPlan>(`/interview-plans/${planId}`),
  memoryPreview: (planId: string) =>
    apiRequest<MemoryPreview>(`/interview-plans/${planId}/memory-preview`),
  readiness: ({ companyId, roundProfileId }: { companyId: string; roundProfileId: string }) =>
    apiRequest<InterviewReadiness>(
      `/interview-readiness?company_id=${encodeURIComponent(companyId)}&round_profile_id=${encodeURIComponent(roundProfileId)}`,
    ),
  jobEventsUrl: (jobId: string) => apiUrl(`/jobs/${jobId}/events`),
};
