import { apiRequest, apiUrl } from "../../lib/api/client";
import type {
  CoachMode,
  CoachResponse,
  EvaluationJob,
  EvaluationReport,
  PracticeTask,
  PracticeTaskStatus,
  ReportListItem,
} from "./types";

export const reportApi = {
  list: () => apiRequest<ReportListItem[]>("/reports"),
  get: (reportId: string) => apiRequest<EvaluationReport>(`/reports/${reportId}`),
  forSession: (sessionId: string) =>
    apiRequest<EvaluationReport>(`/interview-sessions/${sessionId}/report`),
  retry: (reportId: string) =>
    apiRequest<EvaluationJob>(`/reports/${reportId}/retry`, { method: "POST" }),
  listPracticeTasks: (status?: PracticeTaskStatus) =>
    apiRequest<PracticeTask[]>(
      `/practice-tasks${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  getPracticeTask: (taskId: string) => apiRequest<PracticeTask>(`/practice-tasks/${taskId}`),
  createPracticeTasks: (reportId: string, actionIndices: number[]) =>
    apiRequest<PracticeTask[]>(`/reports/${reportId}/practice-tasks`, {
      method: "POST",
      body: JSON.stringify({ action_indices: actionIndices }),
    }),
  updatePracticeTask: (taskId: string, status: PracticeTaskStatus) =>
    apiRequest<PracticeTask>(`/practice-tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  updateTrendInclusion: (sessionId: string, includeInTrends: boolean) =>
    apiRequest<{ include_in_trends?: boolean }>(`/interview-sessions/${sessionId}/trend-inclusion`, {
      method: "PATCH",
      body: JSON.stringify({ include_in_trends: includeInTrends }),
    }),
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
