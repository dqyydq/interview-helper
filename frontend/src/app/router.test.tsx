import { render, screen } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { createTestRouter } from "./router";

const routes = [
  ["/interviews", "模拟面试"],
  ["/questions", "面试知识库"],
  ["/reports", "评估报告"],
  ["/settings", "系统设置"],
] as const;

describe("application routes", () => {
  it.each(routes)("renders %s", (path, heading) => {
    render(
      <RouterProvider
        router={createTestRouter([path])}
        future={{ v7_startTransition: true }}
      />,
    );
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("renders a safe not-found page", () => {
    render(
      <RouterProvider
        router={createTestRouter(["/not-a-real-route"])}
        future={{ v7_startTransition: true }}
      />,
    );
    expect(screen.getByRole("heading", { name: "页面不存在" })).toBeInTheDocument();
    expect(screen.queryByText(/stack|exception|traceback/i)).not.toBeInTheDocument();
  });
});
