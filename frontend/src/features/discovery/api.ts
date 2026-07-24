import { apiRequest } from "../../lib/api/client";

import type {
  DiscoveryConnector,
  DiscoveryConnectorCreate,
  DiscoveryConnectorTestResult,
  DiscoveryConnectorUpdate,
  DiscoveryImportDraft,
  DiscoveryImportResult,
  DiscoveryPage,
  QuestionDiscoveryCandidate,
  QuestionDiscoveryCandidateEvidence,
  QuestionDiscoveryDraft,
  QuestionDiscoveryRun,
  QuestionDiscoverySource,
} from "./types";

function pageQuery(offset = 0, limit = 50) {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return `?${params.toString()}`;
}

export const discoveryApi = {
  listConnectors: () => apiRequest<DiscoveryConnector[]>("/discovery-connectors"),
  createConnector: (draft: DiscoveryConnectorCreate) =>
    apiRequest<DiscoveryConnector>("/discovery-connectors", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  updateConnector: (connectorId: string, draft: DiscoveryConnectorUpdate) =>
    apiRequest<DiscoveryConnector>(`/discovery-connectors/${connectorId}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }),
  removeConnector: (connectorId: string) =>
    apiRequest<void>(`/discovery-connectors/${connectorId}`, { method: "DELETE" }),
  testConnector: (connectorId: string) =>
    apiRequest<DiscoveryConnectorTestResult>(`/discovery-connectors/${connectorId}/test`, {
      method: "POST",
    }),
  listRuns: (offset = 0, limit = 50) =>
    apiRequest<DiscoveryPage<QuestionDiscoveryRun>>(`/question-discoveries${pageQuery(offset, limit)}`),
  createRun: (draft: QuestionDiscoveryDraft) =>
    apiRequest<QuestionDiscoveryRun>("/question-discoveries", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  getRun: (runId: string) => apiRequest<QuestionDiscoveryRun>(`/question-discoveries/${runId}`),
  listSources: (runId: string, offset = 0, limit = 100) =>
    apiRequest<DiscoveryPage<QuestionDiscoverySource>>(
      `/question-discoveries/${runId}/sources${pageQuery(offset, limit)}`,
    ),
  listCandidates: (runId: string, offset = 0, limit = 100) =>
    apiRequest<DiscoveryPage<QuestionDiscoveryCandidate>>(
      `/question-discoveries/${runId}/candidates${pageQuery(offset, limit)}`,
    ),
  listCandidateEvidence: (runId: string, candidateId: string) =>
    apiRequest<QuestionDiscoveryCandidateEvidence[]>(
      `/question-discoveries/${runId}/candidates/${candidateId}/evidence`,
    ),
  importCandidates: (runId: string, draft: DiscoveryImportDraft, idempotencyKey: string) =>
    apiRequest<DiscoveryImportResult>(`/question-discoveries/${runId}/imports`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(draft),
    }),
  cancelRun: (runId: string) =>
    apiRequest<QuestionDiscoveryRun>(`/question-discoveries/${runId}/cancel`, { method: "POST" }),
  removeRun: (runId: string) =>
    apiRequest<void>(`/question-discoveries/${runId}`, { method: "DELETE" }),
};
