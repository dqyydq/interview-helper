import { apiRequest } from "../../../lib/api/client";
import type {
  ConnectionDraft,
  ModelConnection,
  ModelReadiness,
  ModelRole,
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
  test: (connectionId: string) =>
    apiRequest<{ status: string; latency_ms: number; error_code?: string }>(
      `/model-connections/${connectionId}/test`,
      { method: "POST" },
    ),
  listBindings: () => apiRequest<RoleBinding[]>("/model-connections/roles"),
  bindRole: (role: ModelRole, connectionId: string) =>
    apiRequest<RoleBinding>(`/model-connections/roles/${role}`, {
      method: "PUT",
      body: JSON.stringify({ connection_id: connectionId }),
    }),
  readiness: () => apiRequest<ModelReadiness>("/model-connections/readiness"),
};
