export type ProviderType = "openai_compatible" | "anthropic_compatible";
export type ConnectionStatus = "untested" | "healthy" | "degraded" | "disabled";
export type LocalCapabilityStatus = "ready" | "unavailable" | "mismatch";
export type EmbeddingProfileStatus = "building" | "active" | "failed" | "retired";
export type BackgroundJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export const modelRoles = [
  "interviewer",
  "evaluator",
  "planner",
  "context_summarizer",
  "researcher",
  "coach",
  "embedding",
  "transcriber",
] as const;

export type ModelRole = (typeof modelRoles)[number];

export interface ModelConnection {
  id: string;
  name: string;
  provider_type: ProviderType;
  base_url: string;
  model_name: string;
  context_window_tokens: number;
  max_output_tokens: number;
  tokenizer_type: string;
  supports_prompt_caching: boolean;
  supports_token_count_endpoint: boolean;
  status: ConnectionStatus;
  has_api_key: boolean;
}

export interface RoleBinding {
  id: string;
  role: ModelRole;
  target_kind: "model_connection" | "local_capability";
  connection_id: string | null;
  connection_name: string | null;
  model_name: string | null;
  connection_status: ConnectionStatus | null;
  local_capability_key: string | null;
}

export interface LocalCapability {
  key: string;
  role: ModelRole;
  title: string;
  summary: string;
  runtime: string;
  compose_profile: string;
  model_name: string;
  revision: string;
  vector_dimensions: number | null;
  status: LocalCapabilityStatus;
  latency_ms: number | null;
  error_code: string | null;
}

export type RoleTarget =
  | { connection_id: string; local_capability_key?: never }
  | { connection_id?: never; local_capability_key: string };

export interface ModelReadiness {
  ready: boolean;
  missing_roles: ModelRole[];
  degraded_roles: ModelRole[];
}

export interface EmbeddingIndexProfile {
  id: string;
  created_at: string;
  updated_at: string;
  version: number;
  target_kind: "model_connection" | "local_capability";
  model_name: string;
  model_revision: string;
  vector_dimensions: number | null;
  normalized: boolean;
  distance_metric: string;
  status: EmbeddingProfileStatus;
  activated_at: string | null;
  failed_at: string | null;
  failure_code: string | null;
  failure_summary: string | null;
}

export interface EmbeddingIndexJob {
  id: string;
  created_at: string;
  updated_at: string;
  version: number;
  status: BackgroundJobStatus;
  progress: number;
  phase: string;
  memory_scanned: number;
  memory_embeddings: number;
  plan_question_scanned: number;
  plan_question_embeddings: number;
  vector_dimensions: number | null;
  error_code: string | null;
  attempts: number;
  max_attempts: number;
  available_at: string;
}

export interface EmbeddingIndexStatus {
  active_profile: EmbeddingIndexProfile | null;
  building_profile: EmbeddingIndexProfile | null;
  latest_failed_profile: EmbeddingIndexProfile | null;
  job: EmbeddingIndexJob | null;
  interview_active: boolean;
}

export interface EmbeddingIndexRebuildResult {
  embedding_profile: EmbeddingIndexProfile;
  job: EmbeddingIndexJob;
  created: boolean;
}

export interface ConnectionDraft {
  name: string;
  provider_type: ProviderType;
  base_url: string;
  api_key: string;
  model_name: string;
  extra_headers: Record<string, string>;
  context_window_tokens: number;
  max_output_tokens: number;
  tokenizer_type: string;
  supports_prompt_caching: boolean;
  supports_token_count_endpoint: boolean;
}
