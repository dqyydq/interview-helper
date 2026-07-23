export interface RoundProfile {
  id: string;
  round_key: string;
  name: string;
  sequence: number;
  opening_style: string | null;
  topic_weights: Record<string, number>;
  follow_up_patterns: string[];
  pressure_level: number;
  answer_expectations: string[];
  evaluation_weights: Record<string, number>;
  duration_minutes: number;
}

export interface CompanyStylePack {
  id: string;
  name: string;
  pack_version: number;
  supported_roles: string[];
  default_interviewer_behavior: Record<string, unknown>;
  field_confidence: Record<string, number>;
  status: "draft" | "active" | "archived";
  visibility: "private" | "unlisted" | "public";
  evidence_count: number;
  evidence_label: string;
  rounds: RoundProfile[];
}

export interface Company {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  is_system: boolean;
  archived: boolean;
  latest_style_pack: CompanyStylePack | null;
}

export interface CompanyDraft {
  name: string;
  description?: string;
  style_pack: {
    name: string;
    supported_roles: string[];
  };
  rounds: Array<{
    round_key: string;
    name: string;
    sequence: number;
    duration_minutes: number;
  }>;
}

export interface CompanyUpdateDraft {
  name?: string;
  description?: string;
}

export interface RoundDraft {
  round_key: string;
  name: string;
  sequence: number;
  opening_style?: string | null;
  topic_weights?: Record<string, number>;
  follow_up_patterns?: string[];
  pressure_level?: number;
  answer_expectations?: string[];
  evaluation_weights?: Record<string, number>;
  duration_minutes?: number;
}

export type RoundUpdateDraft = Partial<Omit<RoundDraft, "sequence">> & {
  sequence?: number;
};
