import { decodeApiError } from "./errors";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api").replace(
  /\/$/,
  "",
);

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const requestId = crypto.randomUUID();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
      ...init.headers,
    },
  });

  if (!response.ok) {
    throw await decodeApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
