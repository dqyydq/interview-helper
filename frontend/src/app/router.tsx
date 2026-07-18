import {
  BookOpen,
  FileBarChart,
  MessagesSquare,
} from "lucide-react";
import {
  Navigate,
  Outlet,
  createBrowserRouter,
  createMemoryRouter,
  type RouteObject,
} from "react-router-dom";

import { ModelSettingsPage } from "../features/settings/models/ModelSettingsPage";
import { EmptyStatePage } from "../pages/EmptyStatePage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { AppShell } from "./shell/AppShell";

export const appRoutes: RouteObject[] = [
  {
    path: "/",
    element: (
      <AppShell>
        <Outlet />
      </AppShell>
    ),
    children: [
      { index: true, element: <Navigate replace to="/interviews" /> },
      {
        path: "interviews",
        element: (
          <EmptyStatePage
            eyebrow="SIMULATION DESK"
            title="模拟面试"
            description="公司、轮次与岗位配置将在下一里程碑接入。"
            icon={MessagesSquare}
          />
        ),
      },
      {
        path: "questions",
        element: (
          <EmptyStatePage
            eyebrow="KNOWLEDGE BASE"
            title="面试知识库"
            description="题库管理与手动录入将在领域模型完成后开放。"
            icon={BookOpen}
          />
        ),
      },
      {
        path: "reports",
        element: (
          <EmptyStatePage
            eyebrow="EVIDENCE REPORTS"
            title="评估报告"
            description="完成首场模拟后，这里会展示逐题证据与能力趋势。"
            icon={FileBarChart}
          />
        ),
      },
      {
        path: "settings",
        element: <ModelSettingsPage />,
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export const appRouter = createBrowserRouter(appRoutes);

export function createTestRouter(initialEntries: string[]) {
  return createMemoryRouter(appRoutes, { initialEntries });
}
