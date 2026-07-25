export type ProviderType = "openai_compatible" | "anthropic_compatible";
export type ConnectionStatus = "untested" | "healthy" | "degraded" | "disabled";
export type LocalCapabilityStatus = "ready" | "unavailable" | "mismatch";

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
