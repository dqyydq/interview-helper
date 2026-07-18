import {
  FileBarChart,
} from "lucide-react";
import {
  Navigate,
  Outlet,
  createBrowserRouter,
  createMemoryRouter,
  type RouteObject,
} from "react-router-dom";

import { ModelSettingsPage } from "../features/settings/models/ModelSettingsPage";
import { CompanySelectionPage } from "../features/interviews/companies/CompanySelectionPage";
import { InterviewSetupPage } from "../features/interviews/planning/InterviewSetupPage";
import { LiveInterviewPage } from "../features/interviews/live/LiveInterviewPage";
import { KnowledgeBasePage } from "../features/knowledge/KnowledgeBasePage";
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
        element: <CompanySelectionPage />,
      },
      {
        path: "interviews/setup",
        element: <InterviewSetupPage />,
      },
      {
        path: "interviews/:sessionId/live",
        element: <LiveInterviewPage />,
      },
      {
        path: "questions",
        element: <KnowledgeBasePage />,
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
