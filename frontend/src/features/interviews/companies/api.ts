import { apiRequest } from "../../../lib/api/client";
import type {
  Company,
  CompanyDraft,
  CompanyUpdateDraft,
  EvidenceDraft,
  EvidenceItem,
  RoundDraft,
  RoundProfile,
  RoundUpdateDraft,
  VisualEvidenceExtractDraft,
  VisualEvidenceExtraction,
} from "./types";

export const companyApi = {
  list: () => apiRequest<Company[]>("/companies"),
  create: (draft: CompanyDraft) =>
    apiRequest<Company>("/companies", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  update: (companyId: string, draft: CompanyUpdateDraft) =>
    apiRequest<Company>(`/companies/${companyId}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }),
  archive: (companyId: string) =>
    apiRequest<void>(`/companies/${companyId}`, { method: "DELETE" }),
  createRound: (stylePackId: string, draft: RoundDraft) =>
    apiRequest<RoundProfile>(`/style-packs/${stylePackId}/rounds`, {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  updateRound: (roundId: string, draft: RoundUpdateDraft) =>
    apiRequest<RoundProfile>(`/rounds/${roundId}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }),
  deleteRound: (roundId: string) =>
    apiRequest<void>(`/rounds/${roundId}`, { method: "DELETE" }),
  extractVisualEvidence: (stylePackId: string, draft: VisualEvidenceExtractDraft) => {
    const body = new FormData();
    body.set("image", draft.image);
    body.set("source_url", draft.sourceUrl);
    body.set("source_title", draft.sourceTitle);
    body.set("source_confirmed", String(draft.sourceConfirmed));
    return apiRequest<VisualEvidenceExtraction>(
      `/style-packs/${stylePackId}/evidence/visual-extract`,
      { method: "POST", body },
    );
  },
  addEvidence: (stylePackId: string, draft: EvidenceDraft) =>
    apiRequest<EvidenceItem>(`/style-packs/${stylePackId}/evidence`, {
      method: "POST",
      body: JSON.stringify(draft),
    }),
};
