export type EvaluationAnchor =
  | "evidence_insufficient"
  | "insufficient"
  | "partial"
  | "solid"
  | "strong";

export interface EvidenceReference {
  message_id: string;
  claim: string;
}
export interface PracticeAction {
  title: string;
  instruction: string;
  success_criteria: string;
  priority: number;
}

export interface EvaluationJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
}

export interface EvidenceMessage {
  id: string;
  plan_question_id: string | null;
  sequence: number;
  content: string;
}

export interface QuestionEvaluation {
  id: string;
  plan_question_id: string;
  question_sequence: number;
  question_prompt: string;
  anchor: EvaluationAnchor;
  summary: string | null;
  evidence: EvidenceReference[];
  gaps: string[];
  actions: string[];
  confidence: number;
}

export interface DimensionEvaluation {
  id: string;
  dimension: string;
  anchor: EvaluationAnchor;
  evidence: EvidenceReference[];
  gaps: string[];
  action: string | null;
  confidence: number;
}

export interface EvaluationReport {
  id: string;
  session_id: string;
  status: "pending" | "running" | "completed" | "failed";
  overall_anchor: EvaluationAnchor;
  overview: string | null;
  strengths: string[];
  gaps: string[];
  action_plan: PracticeAction[];
  trend_comparison: {
    comparable_session_count?: number;
    previous_overall_anchors?: EvaluationAnchor[];
    note?: string;
  };
  completed_at: string | null;
  questions: QuestionEvaluation[];
  dimensions: DimensionEvaluation[];
  evidence_messages: EvidenceMessage[];
  job: EvaluationJob | null;
}

export interface ReportListItem {
  report_id: string;
  session_id: string;
  status: EvaluationReport["status"];
  overall_anchor: EvaluationAnchor;
  overview: string | null;
  created_at: string;
  updated_at: string;
  company_name: string;
  round_name: string;
  role_name: string;
}

export type CoachMode = "explain" | "rewrite" | "practice";

export interface CoachResponse {
  mode: CoachMode;
  title: string;
  explanation: string;
  original_answer: string | null;
  suggested_answer: string | null;
  practice_prompts: string[];
  source_message_ids: string[];
}
