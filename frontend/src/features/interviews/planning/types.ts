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

export interface InterviewPlan {
  id: string;
  version: number;
  status: "draft" | "ready" | "frozen" | "cancelled";
  total_minutes: number;
  plan_snapshot: {
    phase?: string;
    planner?: string;
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
