import { apiRequest } from "../../../lib/api/client";
import type {
  Company,
  CompanyDraft,
  CompanyUpdateDraft,
  RoundDraft,
  RoundProfile,
  RoundUpdateDraft,
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
};
