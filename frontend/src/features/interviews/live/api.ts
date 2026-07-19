import { apiRequest } from "../../../lib/api/client";
import type { InterviewPlan } from "../planning/types";
import type { RealtimeMessage } from "../../../lib/realtime/interviewSocket";

export interface InterviewSession {
  id: string;
  status: "ready" | "interviewing" | "paused" | "completed" | "failed";
  started_at: string | null;
  ended_at: string | null;
  current_question_sequence: number | null;
  last_event_sequence: number;
  plan: InterviewPlan;
  messages: RealtimeMessage[];
}

export const liveInterviewApi = {
  create: ({ planId, excludedMemoryIds = [] }: { planId: string; excludedMemoryIds?: string[] }) => apiRequest<InterviewSession>("/interview-sessions", {
    method: "POST",
    body: JSON.stringify({ plan_id: planId, excluded_memory_ids: excludedMemoryIds }),
  }),
  start: (sessionId: string) => apiRequest<InterviewSession>(`/interview-sessions/${sessionId}/start`, {
    method: "POST",
  }),
  get: (sessionId: string) => apiRequest<InterviewSession>(`/interview-sessions/${sessionId}`),
};
