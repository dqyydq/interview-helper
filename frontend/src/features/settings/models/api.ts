import { apiRequest } from "../../../lib/api/client";
import type {
  ConnectionDraft,
  EmbeddingIndexRebuildResult,
  EmbeddingIndexStatus,
  LocalCapability,
  ModelConnection,
  ModelReadiness,
  ModelRole,
  RoleTarget,
  RoleBinding,
} from "./types";

export const modelConnectionApi = {
  list: () => apiRequest<ModelConnection[]>("/model-connections"),
  create: (draft: ConnectionDraft) =>
    apiRequest<ModelConnection>("/model-connections", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  remove: (connectionId: string) =>
    apiRequest<void>(`/model-connections/${connectionId}`, { method: "DELETE" }),
  redactCredentials: (connectionId: string) =>
    apiRequest<ModelConnection>(`/model-connections/${connectionId}/redact-credentials`, {
      method: "POST",
    }),
  test: (connectionId: string) =>
    apiRequest<{ status: string; latency_ms: number; error_code?: string }>(
      `/model-connections/${connectionId}/test`,
      { method: "POST" },
    ),
  listBindings: () => apiRequest<RoleBinding[]>("/model-connections/roles"),
  bindRole: (role: ModelRole, target: RoleTarget) =>
    apiRequest<RoleBinding>(`/model-connections/roles/${role}`, {
      method: "PUT",
      body: JSON.stringify(target),
    }),
  unbindRole: (role: ModelRole) =>
    apiRequest<void>(`/model-connections/roles/${role}`, { method: "DELETE" }),
  readiness: () => apiRequest<ModelReadiness>("/model-connections/readiness"),
  listLocalCapabilities: () => apiRequest<LocalCapability[]>("/local-ai/capabilities"),
  testLocalCapability: (key: string) =>
    apiRequest<LocalCapability>(`/local-ai/capabilities/${key}/test`, { method: "POST" }),
  embeddingIndexStatus: () => apiRequest<EmbeddingIndexStatus>("/embedding-index"),
  rebuildEmbeddingIndex: () =>
    apiRequest<EmbeddingIndexRebuildResult>("/embedding-index/rebuild", { method: "POST" }),
};
