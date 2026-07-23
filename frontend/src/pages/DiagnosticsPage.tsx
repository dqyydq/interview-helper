import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Check,
  Clipboard,
  Database,
  FolderLock,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";

import { diagnosticsApi } from "../features/diagnostics/api";
import { SettingsTabs } from "../features/settings/SettingsTabs";

function bytes(value: number) {
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KB`;
  return `${(value / 1_048_576).toFixed(1)} MB`;
}

function workerStatusLabel(state: string) {
  const labels: Record<string, string> = {
    healthy: "Worker 正在运行",
    degraded: "Worker 运行但需要检查",
    stale: "Worker 心跳已过期",
    not_running: "尚未检测到运行中的 Worker",
    unavailable: "Worker 状态暂不可用",
  };
  return labels[state] ?? "Worker 状态未知";
}

function StatusMark({ ok }: { ok: boolean }) {
  return ok ? <Check size={15} aria-label="正常" /> : <TriangleAlert size={15} aria-label="需检查" />;
}

export function DiagnosticsPage() {
  const diagnostics = useQuery({ queryKey: ["diagnostics"], queryFn: diagnosticsApi.get });
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const snapshot = diagnostics.data;

  const copyBundle = async () => {
    try {
      const bundle = await diagnosticsApi.bundle();
      await navigator.clipboard.writeText(JSON.stringify(bundle, null, 2));
      setCopyState("copied");
      window.setTimeout(() => setCopyState("idle"), 2_000);
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <section className="diagnostics-page" aria-labelledby="diagnostics-title">
      <header className="diagnostics-intro">
        <div>
          <p className="eyebrow">LOCAL OPERATIONS</p>
          <h1 id="diagnostics-title">系统诊断</h1>
          <p>仅展示运行状态和计数；诊断包不包含密钥、回答正文、本地路径或简历内容。</p>
        </div>
        <ShieldCheck size={28} aria-hidden="true" />
      </header>

      <SettingsTabs />

      <div className="diagnostics-actions">
        <span>{snapshot ? `更新于 ${new Date(snapshot.generated_at).toLocaleString("zh-CN")}` : "正在读取本地状态"}</span>
        <div>
          <button type="button" disabled={diagnostics.isFetching} onClick={() => void diagnostics.refetch()}>
            <RefreshCw size={14} /> 刷新
          </button>
          <button type="button" onClick={() => void copyBundle()}>
            <Clipboard size={14} /> {copyState === "copied" ? "已复制" : "复制脱敏诊断包"}
          </button>
        </div>
      </div>

      {diagnostics.isError && <p className="diagnostics-error">诊断接口暂时不可用，请确认后端与数据库已启动。</p>}
      {copyState === "failed" && <p className="diagnostics-error">无法写入剪贴板，请检查浏览器权限。</p>}

      {snapshot && (
        <div className="diagnostics-grid">
          <article>
            <header><Database size={18} /><span>DATABASE</span><StatusMark ok={snapshot.database.status === "connected"} /></header>
            <strong>{snapshot.database.status === "connected" ? "PostgreSQL 已连接" : "PostgreSQL 不可用"}</strong>
            <p>诊断不会输出数据库地址或凭据。</p>
          </article>
          <article>
            <header><ServerCog size={18} /><span>WORKER</span><StatusMark ok={snapshot.worker.state === "healthy"} /></header>
            <strong>{workerStatusLabel(snapshot.worker.state)}</strong>
            <p>{snapshot.worker.active_workers} 个活跃 Worker · {snapshot.worker.stale_workers} 个过期心跳 · {snapshot.worker.recent_worker_errors} 条近期异常</p>
            {snapshot.worker.last_heartbeat_at && (
              <small>最后心跳：{new Date(snapshot.worker.last_heartbeat_at).toLocaleString("zh-CN")}{snapshot.worker.last_job_type ? ` · 最近任务：${snapshot.worker.last_job_type}` : ""}</small>
            )}
            <dl>
              {Object.entries(snapshot.worker.job_counts).map(([status, count]) => (
                <div key={status}><dt>{status}</dt><dd>{count}</dd></div>
              ))}
            </dl>
          </article>
          <article>
            <header><Activity size={18} /><span>MODEL ROUTING</span><StatusMark ok={snapshot.models.required_ready} /></header>
            <strong>{snapshot.models.connection_count} 个连接 · {snapshot.models.binding_count} 个绑定</strong>
            <p>{snapshot.models.required_ready ? "核心面试链路已就绪" : `缺少：${snapshot.models.missing_required_roles.join("、") || "健康连接"}`}</p>
            <small>STT {snapshot.models.transcriber_configured ? "已配置" : "未配置（文字输入可用）"}</small>
          </article>
          <article>
            <header><FolderLock size={18} /><span>LOCAL FILES</span><StatusMark ok={snapshot.files.exists && snapshot.files.writable} /></header>
            <strong>{snapshot.files.file_count} 个隔离文件 · {bytes(snapshot.files.total_bytes)}</strong>
            <p>{snapshot.files.exists ? (snapshot.files.writable ? "上传目录可写" : "上传目录只读") : "上传目录尚未创建"}</p>
          </article>
          <article className="diagnostics-privacy">
            <header><ShieldCheck size={18} /><span>PRIVACY CHECK</span><StatusMark ok={snapshot.privacy.redaction_applied} /></header>
            <strong>诊断脱敏已启用</strong>
            <ul>
              <li>不包含模型密钥</li>
              <li>不包含回答与转写正文</li>
              <li>不包含本地绝对路径</li>
            </ul>
          </article>
        </div>
      )}
    </section>
  );
}
