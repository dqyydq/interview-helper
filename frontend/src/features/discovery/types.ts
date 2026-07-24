export type DiscoveryProviderType = "tavily" | "firecrawl";
export type DiscoveryConnectionStatus = "untested" | "healthy" | "degraded" | "disabled";
export type DiscoverySourceMode = "search" | "urls";
export type DiscoveryRunStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "succeeded"
  | "partial"
  | "no_results"
  | "failed"
  | "cancelled";
export type DiscoverySourceStatus = "pending" | "fetched" | "failed" | "blocked";
export type DiscoveryCandidateStatus =
  | "proposed"
  | "selected"
  | "rejected"
  | "duplicate"
  | "imported"
  | "failed";
export type DiscoveryQuestionType =
  | "open_ended"
  | "project_deep_dive"
  | "system_design"
  | "code_discussion"
  | "scenario";
export type DiscoveryDifficulty = "foundational" | "intermediate" | "advanced" | "expert";

export interface DiscoveryEntity {
  id: string;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface DiscoveryConnectorCapabilities {
  supports_domain_filters: boolean;
  supports_extract: boolean;
  safe_extract: boolean;
}

export interface DiscoveryConnectorConfiguration {
  default_country?: string | null;
}

export interface DiscoveryConnector extends DiscoveryEntity {
  name: string;
  provider_type: DiscoveryProviderType;
  enabled: boolean;
  capabilities: DiscoveryConnectorCapabilities;
  configuration: DiscoveryConnectorConfiguration;
  configuration_version: number;
  status: DiscoveryConnectionStatus;
  last_tested_at: string | null;
  last_error_code: string | null;
  has_api_key: boolean;
}

export interface DiscoveryConnectorCreate {
  name: string;
  provider_type: DiscoveryProviderType;
  api_key: string;
  enabled: boolean;
  configuration: DiscoveryConnectorConfiguration;
}

export interface DiscoveryConnectorUpdate {
  name?: string;
  api_key?: string;
  enabled?: boolean;
  configuration?: DiscoveryConnectorConfiguration;
}

export interface DiscoveryConnectorTestResult {
  status: DiscoveryConnectionStatus;
  latency_ms: number;
  error_code: string | null;
}

export interface QuestionDiscoveryDraft {
  connector_id: string;
  source_mode: DiscoverySourceMode;
  company?: string;
  round?: string;
  role?: string;
  skills?: string[];
  keywords?: string[];
  query?: string;
  question_type?: DiscoveryQuestionType;
  difficulty?: DiscoveryDifficulty;
  country?: string;
  urls?: string[];
  full_web?: boolean;
  allow_domains?: string[];
  deny_domains?: string[];
}

export interface QuestionDiscoveryRun extends DiscoveryEntity {
  connector_id: string;
  connector_configuration_version: number;
  initiated_by: string;
  source_mode: DiscoverySourceMode;
  query_snapshot: Record<string, unknown>;
  status: DiscoveryRunStatus;
  stage: string | null;
  progress: number;
  source_count: number;
  candidate_count: number;
  failed_source_count: number;
  error_code: string | null;
  error_summary: string | null;
  cancel_requested_at: string | null;
  completed_at: string | null;
  expires_at: string;
}

export interface QuestionDiscoverySource extends DiscoveryEntity {
  run_id: string;
  normalized_url: string;
  final_url: string | null;
  title: string | null;
  domain: string;
  source_category: string;
  status: DiscoverySourceStatus;
  fetched_at: string | null;
  excerpt: string | null;
  attribution: Record<string, unknown>;
  policy_metadata: Record<string, unknown>;
  failure_code: string | null;
  failure_summary: string | null;
  expires_at: string;
}

export interface QuestionDiscoveryCandidate extends DiscoveryEntity {
  run_id: string;
  prompt: string;
  question_type: DiscoveryQuestionType;
  difficulty: DiscoveryDifficulty;
  suggested_tags: string[];
  suggested_roles: string[];
  suggested_skills: string[];
  applicable_companies: string[];
  applicable_rounds: string[];
  reference_points: string[];
  follow_up_suggestions: string[];
  matching_reason: string | null;
  confidence: number;
  researcher_model_name: string | null;
  schema_version: string;
  candidate_revision: number;
  similar_question_ids: string[];
  status: DiscoveryCandidateStatus;
  import_count: number;
  failure_code: string | null;
  failure_summary: string | null;
  expires_at: string;
}

export interface QuestionDiscoveryCandidateEvidence extends DiscoveryEntity {
  run_id: string;
  candidate_id: string;
  source_id: string;
  source_title: string;
  normalized_url: string;
  source_domain: string;
  source_category: string;
  excerpt: string;
  source_locator: string | null;
  confidence: number;
}

export interface DiscoveryPage<T> {
  data: T[];
  count: number;
  offset: number;
  limit: number;
}

export interface DiscoveryImportItem {
  candidate_id: string;
  candidate_revision: number;
  prompt?: string;
  question_type?: DiscoveryQuestionType;
  difficulty?: DiscoveryDifficulty;
  tag_names?: string[];
  reference_points?: string[];
  follow_up_suggestions?: string[];
  applicable_companies?: string[];
  applicable_rounds?: string[];
  source_note?: string;
  user_note?: string;
}

export interface DiscoveryImportDraft {
  bank_id: string;
  items: DiscoveryImportItem[];
}

export interface DiscoveryImportResult {
  run_id: string;
  bank_id: string;
  batch_id: string;
  request_hash: string;
  items: Array<{
    candidate_id: string;
    candidate_revision: number;
    question_id: string;
    import_id: string;
  }>;
  replayed: boolean;
}
