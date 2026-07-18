import { describe, expect, it } from "vitest";

import { decodeApiError } from "./errors";

describe("decodeApiError", () => {
  it("preserves the stable backend error contract", async () => {
    const response = new Response(
      JSON.stringify({
        code: "validation_error",
        message: "请求参数不符合要求",
        request_id: "request-1",
        field_errors: { "body.name": ["Field required"] },
        retryable: false,
      }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );

    const error = await decodeApiError(response);

    expect(error.code).toBe("validation_error");
    expect(error.requestId).toBe("request-1");
    expect(error.fieldErrors).toEqual({ "body.name": ["Field required"] });
    expect(error.retryable).toBe(false);
  });

  it("falls back without exposing malformed response bodies", async () => {
    const response = new Response("internal stack detail", {
      status: 500,
      headers: { "X-Request-ID": "request-2" },
    });

    const error = await decodeApiError(response);

    expect(error.message).toBe("请求失败");
    expect(error.code).toBe("http_error");
    expect(error.requestId).toBe("request-2");
    expect(error.message).not.toContain("stack detail");
  });
});
