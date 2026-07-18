import { z } from "zod";

export const errorPayloadSchema = z.object({
  code: z.string(),
  message: z.string(),
  request_id: z.string(),
  field_errors: z.record(z.string(), z.array(z.string())).default({}),
  retryable: z.boolean().default(false),
});

export type ErrorPayload = z.infer<typeof errorPayloadSchema>;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
    readonly retryable = false,
    readonly fieldErrors: Record<string, string[]> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function decodeApiError(response: Response): Promise<ApiError> {
  const rawPayload: unknown = await response.json().catch(() => undefined);
  const payload = errorPayloadSchema.safeParse(rawPayload);
  if (!payload.success) {
    return new ApiError(
      "请求失败",
      response.status,
      "http_error",
      response.headers.get("X-Request-ID") ?? undefined,
    );
  }
  return new ApiError(
    payload.data.message,
    response.status,
    payload.data.code,
    payload.data.request_id,
    payload.data.retryable,
    payload.data.field_errors,
  );
}
