export type QuestionType =
  | "open_ended"
  | "project_deep_dive"
  | "system_design"
  | "code_discussion"
  | "scenario";
export type Difficulty = "foundational" | "intermediate" | "advanced" | "expert";

export interface QuestionBank {
  id: string;
  name: string;
  description: string | null;
  visibility: string;
  question_count: number;
  archived: boolean;
}

export interface QuestionTag {
  id: string;
  name: string;
  slug: string;
  category: string;
}

export interface Question {
  id: string;
  bank_id: string;
  prompt: string;
  question_type: QuestionType;
  difficulty: Difficulty;
  status: "draft" | "active" | "archived";
  reference_points: string[];
  follow_up_suggestions: string[];
  applicable_companies: string[];
  applicable_rounds: string[];
  source_type: string;
  source_note: string | null;
  user_note: string | null;
  times_used: number;
  tags: QuestionTag[];
  variants: Array<{ id: string; prompt: string; variant_type: string }>;
}

export interface QuestionPage {
  data: Question[];
  count: number;
  offset: number;
  limit: number;
}

export interface QuestionDraft {
  bank_id: string;
  prompt: string;
  question_type: QuestionType;
  difficulty: Difficulty;
  status: "draft" | "active";
  tag_names: string[];
}

export interface ResumeSection {
  id: string;
  section_type: string;
  heading: string | null;
  content: string;
}

export interface Resume {
  id: string;
  filename: string;
  mime_type: string;
  content_hash: string;
  parse_status: "pending" | "parsing" | "ready" | "failed";
  parse_error_code: string | null;
  sections: ResumeSection[];
  claims: Array<{ id: string; claim_type: string; content: string; confidence: number }>;
}

export interface BackgroundJob {
  id: string;
  version: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  error_code: string | null;
  error_message: string | null;
}

export interface ResumeUploadResult {
  resume: Resume;
  job: BackgroundJob | null;
  reused: boolean;
}
