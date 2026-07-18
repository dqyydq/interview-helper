import { z } from "zod";

const errorPayloadSchema = z.object({
  code: z.string().optional(),
  message: z.string().optional(),
  request_id: z.string().optional(),
});

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

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
    const rawPayload: unknown = await response.json().catch(() => ({}));
    const payload = errorPayloadSchema.safeParse(rawPayload);
    throw new ApiError(
      payload.success ? (payload.data.message ?? "请求失败") : "请求失败",
      response.status,
      payload.success ? (payload.data.code ?? "http_error") : "http_error",
      payload.success ? payload.data.request_id : response.headers.get("X-Request-ID") ?? undefined,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
