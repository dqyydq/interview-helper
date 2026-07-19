export type MemoryType =
  | "project_fact"
  | "stable_skill"
  | "recurring_gap"
  | "communication_preference"
  | "interview_preference"
  | "practice_goal";

export type MemoryStatus = "proposed" | "active" | "conflicted" | "rejected" | "expired";

export interface MemorySource {
  id: string;
  session_id: string | null;
  message_id: string | null;
  source_type: string;
  evidence_excerpt: string | null;
  observed_at: string;
}

export interface MemoryConflict {
  id: string;
  memory_id: string;
  conflicting_memory_id: string;
  status: "open" | "resolved" | "dismissed";
  resolution: string | null;
  resolved_at: string | null;
}

export interface MemoryItem {
  id: string;
  memory_type: MemoryType;
  canonical_key: string;
  memory_version: number;
  content: string;
  structured_value: Record<string, unknown>;
  status: MemoryStatus;
  confidence: number;
  first_observed_at: string;
  last_verified_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  pinned: boolean;
  sources: MemorySource[];
  open_conflicts: MemoryConflict[];
}

export interface MemorySettings {
  memory_enabled: boolean;
}

export interface MemoryPreviewItem {
  id: string;
  memory_type: MemoryType;
  content: string;
  pinned: boolean;
  reason: string;
}

export interface MemoryPreview {
  enabled: boolean;
  items: MemoryPreviewItem[];
}
