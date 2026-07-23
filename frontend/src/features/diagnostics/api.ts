import { apiRequest } from "../../lib/api/client";

export interface DiagnosticSnapshot {
  generated_at: string;
  application: { name: string; version: string; environment: string };
  database: { status: string };
  worker: {
    state: "healthy" | "degraded" | "stale" | "not_running" | "unavailable";
    active_workers: number;
    stale_workers: number;
    recent_worker_errors: number;
    last_heartbeat_at: string | null;
    last_job_type: string | null;
    last_error_type: string | null;
    last_error_at: string | null;
    job_counts: Record<string, number>;
    stale_running_jobs: number;
    recent_failed_jobs: number;
    heartbeat_stale_after_seconds: number;
  };
  models: {
    connection_count: number;
    binding_count: number;
    status_counts: Record<string, number>;
    required_ready: boolean;
    missing_required_roles: string[];
    degraded_required_roles: string[];
    transcriber_configured: boolean;
  };
  files: {
    configured: boolean;
    exists: boolean;
    writable: boolean;
    file_count: number;
    total_bytes: number;
  };
  privacy: {
    redaction_applied: boolean;
    contains_secrets: boolean;
    contains_answer_content: boolean;
    contains_local_paths: boolean;
  };
}

export interface DiagnosticBundle {
  request_id: string;
  snapshot: DiagnosticSnapshot;
}

export const diagnosticsApi = {
  get: () => apiRequest<DiagnosticSnapshot>("/diagnostics"),
  bundle: () => apiRequest<DiagnosticBundle>("/diagnostics/bundle"),
};
