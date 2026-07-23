import { BookOpen, FileBarChart, Github, MessagesSquare, Settings } from "lucide-react";
import type { PropsWithChildren } from "react";
import { NavLink } from "react-router-dom";

import { CommandPalette } from "./CommandPalette";

const navigation = [
  { to: "/interviews", label: "模拟面试", icon: MessagesSquare },
  { to: "/questions", label: "面试知识库", icon: BookOpen },
  { to: "/reports", label: "评估报告", icon: FileBarChart },
  { to: "/settings", label: "设置", icon: Settings },
];

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink className="brand" to="/interviews" aria-label="Interview Helper 首页">
          <span className="brand-mark" aria-hidden="true">
            IH
          </span>
          <span>
            <strong>INTERVIEW HELPER</strong>
            <small>开源 · AI 模拟面试</small>
          </span>
        </NavLink>

        <CommandPalette />

        <nav className="primary-nav" aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
              to={to}
            >
              <Icon size={17} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <span
          className="github-link"
          aria-label="GitHub 仓库尚未配置"
          title="GitHub 仓库尚未配置"
        >
          <Github size={19} aria-hidden="true" />
          <span>GitHub · 待配置</span>
        </span>
      </header>

      <main className="workspace">{children}</main>
      <footer className="statusbar">
        <span>Interview Helper v0.1.0</span>
        <span>LOCAL-FIRST / MIT</span>
      </footer>
    </div>
  );
}
