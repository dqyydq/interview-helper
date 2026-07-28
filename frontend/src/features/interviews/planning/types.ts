export interface PlanDraft {
  company_id: string;
  round_profile_id: string;
  role_name: string;
  duration_minutes: number;
  target_question_count: number;
  question_bank_ids: string[];
  resume_id: string | null;
  source_weights: Record<string, number>;
  preferences: Record<string, unknown>;
  /**
   * A short trial and a focused practice session deliberately stay out of the
   * long-term trend until the candidate explicitly promotes the result.
   */
  session_kind?: "standard" | "quick_trial" | "targeted_practice";
  practice_task_id?: string | null;
}

export interface ReadinessItem {
  key: string;
  status: "ready" | "blocked" | "available" | "unavailable" | "processing" | "not_configured";
  label: string;
  detail?: string | null;
  action?: string | null;
}

export interface InterviewReadiness {
  ready: boolean;
  blocking: ReadinessItem[];
  enhancements: ReadinessItem[];
  defaults: {
    quick_trial: {
      session_kind: "quick_trial";
      duration_minutes: number;
      target_question_count: number;
      include_in_trends: boolean;
      role_name: string;
    };
  };
  company_profile: {
    company_id?: string | null;
    round_profile_id?: string | null;
    style_pack_id?: string | null;
    pack_version?: number | null;
    trust_status?: "template" | "draft" | "source_backed" | null;
    trust_label?: string | null;
    evidence_count: number;
    latest_evidence_at?: string | null;
    source_summaries: Array<{
      title: string;
      url: string;
      excerpt?: string | null;
    }>;
  } | null;
}

export interface PlanQuestion {
  id: string;
  sequence: number;
  source_type: "manual" | "resume" | "generated";
  source_ref: Record<string, unknown>;
  capability_tags: string[];
  allocated_seconds: number;
  follow_up_budget: number;
  selection_reason: string;
}

export type StylePackTrustStatus = "template" | "draft" | "source_backed";

export interface StylePackTrustSnapshot {
  trust_status: StylePackTrustStatus;
  evidence_count: number;
  latest_evidence_at?: string | null;
  source_summaries?: Array<{
    title: string;
    url?: string | null;
    excerpt?: string | null;
  }>;
}

export interface InterviewPlan {
  id: string;
  version: number;
  config?: {
    company_id: string;
    round_profile_id: string;
    role_name: string;
    session_kind: "standard" | "quick_trial" | "targeted_practice";
    practice_task_id: string | null;
  };
  status: "draft" | "ready" | "frozen" | "cancelled";
  total_minutes: number;
  plan_snapshot: {
    phase?: string;
    planner?: string;
    session_kind?: "standard" | "quick_trial" | "targeted_practice";
    style_pack_version?: number;
    /** Current format: a self-contained, persisted style-pack evidence snapshot. */
    style_pack_trust?: StylePackTrustSnapshot;
    /** Legacy flat fields remain for plans created before the snapshot format. */
    style_pack_trust_status?: "template" | "draft" | "source_backed";
    style_pack_evidence_count?: number;
    source_distribution?: Record<string, number>;
    capability_coverage?: Record<string, number>;
    selected_count?: number;
  };
  rationale: string | null;
  questions: PlanQuestion[];
}

export interface PlanJob {
  id: string;
  version: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
}

export interface PlanCreateResult {
  plan: InterviewPlan;
  job: PlanJob;
}
