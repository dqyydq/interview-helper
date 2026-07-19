import { apiRequest } from "../../../lib/api/client";
import type { InterviewPlan } from "../planning/types";
import type { RealtimeMessage } from "../../../lib/realtime/interviewSocket";

export interface InterviewSession {
  id: string;
  status: "ready" | "interviewing" | "paused" | "completing" | "completed" | "evaluating" | "failed";
  started_at: string | null;
  ended_at: string | null;
  current_question_sequence: number | null;
  last_event_sequence: number;
  plan: InterviewPlan;
  messages: RealtimeMessage[];
}

export interface ContextSnapshotDiagnostic {
  id: string;
  created_at: string;
  agent_role: string;
  prompt_schema_version: string;
  included_refs: Record<string, string[]>;
  excluded_refs: Array<{ type: string; id: string; reason: string }>;
  token_by_layer: Record<string, number>;
  count_method: string;
  compaction_level: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ContextDiagnostics {
  session_id: string;
  current_state: Record<string, unknown>;
  summary: {
    snapshot_count: number;
    max_compaction_level: number;
    total_input_tokens: number;
    average_compression_ratio: number;
    retrieval_candidate_count: number;
    retrieval_included_count: number;
  };
  snapshots: ContextSnapshotDiagnostic[];
  segments: Array<{
    id: string;
    sequence: number;
    status: string;
    token_count: number;
    valid_summary_ids: string[];
  }>;
}

export interface TranscriptionResult {
  text: string;
  language: string | null;
  duration_seconds: number | null;
  provider_request_id: string | null;
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
  diagnostics: (sessionId: string) =>
    apiRequest<ContextDiagnostics>(`/interview-sessions/${sessionId}/context/diagnostics`),
  transcribe: (audio: Blob, filename: string) => {
    const body = new FormData();
    body.append("file", audio, filename);
    body.append("language", "zh");
    return apiRequest<TranscriptionResult>("/transcriptions", { method: "POST", body });
  },
};
