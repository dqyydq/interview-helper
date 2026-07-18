import { apiRequest } from "../../../lib/api/client";
import type { Company, CompanyDraft } from "./types";

export const companyApi = {
  list: () => apiRequest<Company[]>("/companies"),
  create: (draft: CompanyDraft) =>
    apiRequest<Company>("/companies", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
};
