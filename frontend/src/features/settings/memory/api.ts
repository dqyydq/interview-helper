import { apiRequest } from "../../../lib/api/client";
import type { MemoryItem, MemorySettings, MemoryStatus } from "./types";

export const memoryApi = {
  list: (status?: MemoryStatus) =>
    apiRequest<MemoryItem[]>(`/memories${status ? `?status=${status}` : ""}`),
  settings: () => apiRequest<MemorySettings>("/memory-settings"),
  updateSettings: (memoryEnabled: boolean) =>
    apiRequest<MemorySettings>("/memory-settings", {
      method: "PATCH",
      body: JSON.stringify({ memory_enabled: memoryEnabled }),
    }),
  update: (memoryId: string, content: string) =>
    apiRequest<MemoryItem>(`/memories/${memoryId}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    }),
  confirm: (memoryId: string) =>
    apiRequest<MemoryItem>(`/memories/${memoryId}/confirm`, { method: "POST" }),
  pin: (memoryId: string, pinned: boolean) =>
    apiRequest<MemoryItem>(`/memories/${memoryId}/pin`, {
      method: "PATCH",
      body: JSON.stringify({ pinned }),
    }),
  reject: (memoryId: string) =>
    apiRequest<MemoryItem>(`/memories/${memoryId}/reject`, { method: "POST" }),
  remove: (memoryId: string) =>
    apiRequest<void>(`/memories/${memoryId}`, { method: "DELETE" }),
  resolveConflict: (conflictId: string, winningMemoryId: string) =>
    apiRequest<MemoryItem>(`/memory-conflicts/${conflictId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ winning_memory_id: winningMemoryId }),
    }),
};
