export type ProviderType = "openai_compatible" | "anthropic_compatible";
export type ConnectionStatus = "untested" | "healthy" | "degraded" | "disabled";

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
  connection_id: string;
  connection_name: string;
  model_name: string;
  connection_status: ConnectionStatus;
}

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
