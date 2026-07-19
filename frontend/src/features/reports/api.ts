import { apiRequest, apiUrl } from "../../lib/api/client";
import type {
  CoachMode,
  CoachResponse,
  EvaluationJob,
  EvaluationReport,
  ReportListItem,
} from "./types";

export const reportApi = {
  list: () => apiRequest<ReportListItem[]>("/reports"),
  get: (reportId: string) => apiRequest<EvaluationReport>(`/reports/${reportId}`),
  forSession: (sessionId: string) =>
    apiRequest<EvaluationReport>(`/interview-sessions/${sessionId}/report`),
  retry: (reportId: string) =>
    apiRequest<EvaluationJob>(`/reports/${reportId}/retry`, { method: "POST" }),
  coach: ({
    reportId,
    mode,
    questionEvaluationId,
    focus,
  }: {
    reportId: string;
    mode: CoachMode;
    questionEvaluationId?: string;
    focus?: string;
  }) =>
    apiRequest<CoachResponse>(`/reports/${reportId}/coach`, {
      method: "POST",
      body: JSON.stringify({
        mode,
        question_evaluation_id: questionEvaluationId ?? null,
        focus: focus || null,
      }),
    }),
  jobEventsUrl: (jobId: string) => apiUrl(`/jobs/${jobId}/events`),
};
