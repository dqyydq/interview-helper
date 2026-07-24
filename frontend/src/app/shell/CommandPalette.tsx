import {
  ArrowRight,
  BookOpen,
  FileBarChart,
  MessagesSquare,
  Search,
  Settings,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

const commands = [
  {
    to: "/interviews",
    label: "模拟面试",
    description: "选择公司与轮次，开始一场专项面试",
    keywords: "公司 轮次 interview",
    icon: MessagesSquare,
  },
  {
    to: "/questions",
    label: "面试知识库",
    description: "管理题库、题目与专项简历",
    keywords: "题库 简历 question resume",
    icon: BookOpen,
  },
  {
    to: "/questions/discover",
    label: "发现题目",
    description: "从公开资料中收集带来源证据的候选面试题",
    keywords: "题目发现 搜索 来源 Tavily discovery research",
    icon: Search,
  },
  {
    to: "/reports",
    label: "评估报告",
    description: "查看逐题证据、能力结论与训练计划",
    keywords: "评估 复盘 coach report",
    icon: FileBarChart,
  },
  {
    to: "/settings",
    label: "系统设置",
    description: "配置模型连接、记忆和本地诊断",
    keywords: "模型 记忆 诊断 model settings",
    icon: Settings,
  },
];

export function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const filteredCommands = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return commands;
    return commands.filter((command) =>
      `${command.label} ${command.description} ${command.keywords}`
        .toLocaleLowerCase()
        .includes(normalized),
    );
  }, [query]);

  const select = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  return (
    <>
      <button
        className="command-trigger"
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Search size={15} aria-hidden="true" />
        <span>快速搜索…</span>
        <kbd>⌘ K</kbd>
      </button>

      {open && (
        <div
          className="command-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="command-palette"
            role="dialog"
            aria-modal="true"
            aria-labelledby="command-title"
          >
            <header>
              <span id="command-title">COMMAND INDEX</span>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭快速搜索">
                ESC
              </button>
            </header>
            <label>
              <Search size={18} aria-hidden="true" />
              <span className="sr-only">搜索页面</span>
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && filteredCommands.length === 1) {
                    select(filteredCommands[0].to);
                  }
                }}
                placeholder="输入页面或功能名称"
              />
            </label>
            <div className="command-results">
              {filteredCommands.map(({ to, label, description, icon: Icon }, index) => (
                <button key={to} type="button" onClick={() => select(to)}>
                  <span className="command-index">{String(index + 1).padStart(2, "0")}</span>
                  <Icon size={18} aria-hidden="true" />
                  <span>
                    <strong>{label}</strong>
                    <small>{description}</small>
                  </span>
                  <ArrowRight size={16} aria-hidden="true" />
                </button>
              ))}
              {filteredCommands.length === 0 && (
                <p>没有匹配页面。可以尝试“题库”“报告”或“模型”。</p>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
