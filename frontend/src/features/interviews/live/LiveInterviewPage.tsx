import { useQuery } from "@tanstack/react-query";
import { Clock3, Code2, LoaderCircle, Pause, Play, RotateCcw, Send, ShieldCheck, Square } from "lucide-react";
import { lazy, Suspense, type FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { InterviewSocket, type RealtimeMessage, type ServerEvent } from "../../../lib/realtime/interviewSocket";
import { ContextUsage } from "../../diagnostics/ContextUsage";
import { VoiceRecorder } from "../../live-interview/VoiceRecorder";
import type { CodeAttachmentDraft } from "../../live-interview/CodeWhiteboard";
import { liveInterviewApi } from "./api";
import "./LiveInterviewPage.p0.css";

type LiveError = { message: string; retryable: boolean };
type TrustStatus = "template" | "draft" | "source_backed";

const draftStorageKey = (sessionId: string) => `interview-helper:answer-draft:${sessionId}`;

const trustStatusLabel: Record<TrustStatus, string> = {
  template: "轮次骨架",
  draft: "自定义草案",
  source_backed: "有来源支持",
};

const stylePackBoundaryCopy = (status: TrustStatus) => (
  status === "source_backed"
    ? "用于本场的提问节奏与侧重点；来源仅在本地使用。"
    : "该画像仅用于模拟提问节奏，不代表官方面试事实。"
);

const turnStatusLabel = (stage?: string) => {
  const labels: Record<string, string> = {
    answer_saved: "回答已保存，正在准备下一步",
    choosing_follow_up: "正在判断追问方向",
    advancing: "正在切换问题节奏",
    generating_question: "正在生成下一步问题",
    retrying: "回答已保存，正在恢复本轮",
  };
  return stage ? labels[stage] ?? "正在处理本轮回答" : undefined;
};

const CodeWhiteboard = lazy(() =>
  import("../../live-interview/CodeWhiteboard").then((module) => ({
    default: module.CodeWhiteboard,
  })),
);

export function LiveInterviewPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<RealtimeMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("ready");
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const [connection, setConnection] = useState<"connecting" | "connected" | "reconnecting">("connecting");
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [error, setRawError] = useState<string>();
  const [retryableError, setRetryableError] = useState(false);
  const [turnStatus, setTurnStatus] = useState<string>();
  const [codeAttachments, setCodeAttachments] = useState<CodeAttachmentDraft[]>([]);
  const socketRef = useRef<InterviewSocket | null>(null);
  const setError = (value: LiveError | string | undefined) => {
    if (typeof value === "string") {
      setRawError(value);
      setRetryableError(false);
      return;
    }
    setRawError(value?.message);
    setRetryableError(Boolean(value?.retryable));
  };
  const session = useQuery({
    queryKey: ["interview-session", sessionId],
    queryFn: () => liveInterviewApi.start(sessionId),
    enabled: Boolean(sessionId),
    retry: false,
  });

  useEffect(() => {
    if (!sessionId) return;
    setDraft(window.sessionStorage.getItem(draftStorageKey(sessionId)) ?? "");
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    if (draft) window.sessionStorage.setItem(draftStorageKey(sessionId), draft);
    else window.sessionStorage.removeItem(draftStorageKey(sessionId));
  }, [draft, sessionId]);

  useEffect(() => {
    if (!session.data) return;
    setMessages(session.data.messages);
    setStatus(session.data.status);
    const totalSeconds = session.data.plan.total_minutes * 60;
    const elapsedSeconds = session.data.started_at
      ? Math.max(0, Math.floor((Date.now() - new Date(session.data.started_at).getTime()) / 1_000))
      : 0;
    setRemainingSeconds(Math.max(0, totalSeconds - elapsedSeconds));
    const handleEvent = (event: ServerEvent) => {
      if (event.type === "session.state") {
        setStatus(String(event.payload.status));
        if (event.payload.pending_turn === true) {
          setBusy(false);
          setError({
            message: "回答已保存，等待恢复下一步",
            retryable: true,
          });
          setTurnStatus("回答已保存，可重试本轮生成");
        }
      } else if (event.type === "assistant.delta") {
        setStreaming((current) => current + String(event.payload.text ?? ""));
      } else if (event.type === "turn.status") {
        setTurnStatus(turnStatusLabel(String(event.payload.stage ?? "")));
      } else if (event.type === "timer.update") {
        setRemainingSeconds(Number(event.payload.remaining_seconds ?? 0));
      } else if (event.type === "input.ack" || event.type === "assistant.message") {
        const message = event.payload.message as RealtimeMessage | undefined;
        if (message) {
          setMessages((current) => current.some((item) => item.id === message.id) ? current : [...current, message]);
        }
        if (event.type === "input.ack" && event.payload.committed !== false) {
          window.sessionStorage.removeItem(draftStorageKey(sessionId));
          setDraft("");
          setCodeAttachments([]);
        }
        if (event.type === "assistant.message") {
          setStreaming("");
          setBusy(false);
          setTurnStatus(undefined);
          setError(undefined);
        }
      } else if (event.type === "error") {
        setError({
          message: String(event.payload.message ?? "面试官暂时无法继续"),
          retryable: Boolean(event.payload.retryable),
        });
        setStreaming("");
        setBusy(false);
        setTurnStatus("回答已保存，可在准备好后重试本轮");
      }
    };
    const socket = new InterviewSocket(sessionId, handleEvent, (nextConnection, attempt = 0) => {
      setConnection(nextConnection);
      setReconnectAttempt(attempt);
    });
    socket.lastSequence = session.data.last_event_sequence;
    socket.connect();
    socketRef.current = socket;
    return () => socket.close();
  }, [session.data, sessionId]);

  const timerIsActive = status === "interviewing" && remainingSeconds > 0;

  useEffect(() => {
    if (!timerIsActive) return;
    const timer = window.setInterval(
      () => setRemainingSeconds((current) => Math.max(0, current - 1)),
      1_000,
    );
    return () => window.clearInterval(timer);
  }, [timerIsActive]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    if (socketRef.current?.send("user.text.submit", { text, attachments: codeAttachments })) {
      setBusy(true);
      setError(undefined);
      setTurnStatus("正在保存你的回答");
    }
  };

  const updateDraft = (value: string) => {
    setDraft(value);
    if (sessionId) window.sessionStorage.setItem(draftStorageKey(sessionId), value);
  };

  const retryTurn = () => {
    if (socketRef.current?.send("turn.retry")) {
      setBusy(true);
      setError(undefined);
      setTurnStatus("回答已保存，正在重新生成下一步");
    }
  };

  if (session.isLoading) return <div className="setup-loading"><LoaderCircle className="spin" /> 正在进入面试房间</div>;
  if (session.isError || !session.data) return <div className="setup-missing"><h1>无法进入面试房间</h1></div>;
  const total = session.data.plan.questions.length;
  const current = session.data.current_question_sequence ?? 1;
  const timerLabel = `${String(Math.floor(remainingSeconds / 60)).padStart(2, "0")}:${String(remainingSeconds % 60).padStart(2, "0")}`;
  const planSnapshot = session.data.plan.plan_snapshot;
  const stylePackTrust = planSnapshot.style_pack_trust;
  const trustStatus = stylePackTrust?.trust_status ?? planSnapshot.style_pack_trust_status ?? "template";
  const evidenceCount = stylePackTrust?.evidence_count ?? planSnapshot.style_pack_evidence_count ?? 0;
  const stylePackVersion = planSnapshot.style_pack_version;

  return (
    <section className="live-room" aria-labelledby="live-room-title">
      <header className="live-header">
        <div className="live-recovery-note" aria-live="polite">
          {connection === "connected"
            ? "连接正常"
            : `正在恢复连接${reconnectAttempt ? `（第 ${reconnectAttempt} 次）` : ""}`}
        </div>
        <div><span>实时面试 · 03</span><h1 id="live-room-title">实时模拟面试</h1></div>
        <div className={`connection-indicator ${connection}`}><i />{connection === "connected" ? "连接正常" : "正在恢复连接"}</div>
        <div className="live-metrics"><span><Clock3 size={14} />{timerLabel}</span><span>{current} / {total} 题</span></div>
      </header>
      <main className="live-transcript">
        <div className="transcript-rule"><span>对话记录</span><strong>面试中不显示评分</strong></div>
        {messages.map((message) => (
          <article key={message.id} className={`transcript-message ${message.role}`}>
            <span>{message.role === "assistant" ? "面试官" : "你"}</span>
            <p>{message.content}</p>
          </article>
        ))}
        {streaming && <article className="transcript-message assistant streaming"><span>面试官</span><p>{streaming}<i /></p></article>}
        {error && <div className="live-error">{error}。你的回答已保存，可配置模型后继续。</div>}
        {turnStatus && <div className="live-turn-status" role="status"><LoaderCircle size={15} className="spin" />{turnStatus}</div>}
        {error && retryableError && (
          <button className="live-retry-button secondary-button" type="button" onClick={retryTurn} disabled={connection !== "connected" || busy}>
            <RotateCcw size={14} /> 重试本轮
          </button>
        )}
      </main>
      <aside className="live-side">
        <section><ShieldCheck size={18} /><h2>本场规则</h2><p>一次只处理一个问题；回答确认后写入记录；断线会自动补发已确认事件。</p></section>
        <section className="live-profile-boundary" aria-label="公司画像适用边界">
          <h2>画像边界</h2>
          <span className={`live-trust-badge ${trustStatus}`}>{trustStatusLabel[trustStatus]}</span>
          <div className="live-profile-meta">
            <span>版本 {stylePackVersion ? `v${stylePackVersion}` : "未记录"}</span>
            <span>{evidenceCount} 条证据</span>
          </div>
          <p>{stylePackBoundaryCopy(trustStatus)}</p>
        </section>
        <section><h2>会话状态</h2><strong>{status}</strong></section>
        <ContextUsage sessionId={sessionId} />
        <button
          className="secondary-button"
          type="button"
          disabled={connection !== "connected" || !["interviewing", "paused"].includes(status)}
          onClick={() => socketRef.current?.send(
            status === "paused" ? "session.resume" : "session.pause",
            { last_sequence: socketRef.current?.lastSequence ?? 0 },
          )}
        >
          {status === "paused" ? <Play size={15} /> : <Pause size={15} />}
          {status === "paused" ? "继续" : "暂停"}
        </button>
        <button
          className="secondary-button"
          type="button"
          disabled={connection !== "connected" || !["interviewing", "paused"].includes(status)}
          onClick={() => socketRef.current?.send("session.restate")}
        ><RotateCcw size={15} /> 重述问题</button>
        <button className="text-button danger" type="button" onClick={() => socketRef.current?.send("session.finish")}><Square size={14} /> 提前结束</button>
      </aside>
      <form className="answer-composer" onSubmit={submit}>
        <label htmlFor="answer-text">你的回答</label>
        <VoiceRecorder
          disabled={busy || status !== "interviewing" || connection !== "connected"}
          onConfirm={(text) => updateDraft(draft.trim() ? `${draft.trim()}\n${text}` : text)}
        />
        <div className="answer-tools">
          <Suspense fallback={<span className="whiteboard-loading">正在加载代码白板…</span>}>
            <CodeWhiteboard
              disabled={busy || status !== "interviewing" || connection !== "connected"}
              onAttach={(attachment) => setCodeAttachments([attachment])}
            />
          </Suspense>
          {codeAttachments.map((attachment) => (
            <span className="answer-attachment" key={`${attachment.language}-${attachment.filename}`}>
              <Code2 size={13} aria-hidden="true" /> {attachment.filename}
              <button
                type="button"
                aria-label={`移除 ${attachment.filename}`}
                onClick={() => setCodeAttachments([])}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <textarea id="answer-text" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={busy || status !== "interviewing" || connection !== "connected"} placeholder="先给结论，再说明依据、取舍与边界。" />
        <div><small>{draft.length} / 50000</small><button className="primary-button" type="submit" disabled={!draft.trim() || busy || connection !== "connected"}><Send size={15} />{busy ? "等待面试官" : "确认回答"}</button></div>
      </form>
      <footer className="live-footer"><button className="text-button" type="button" onClick={() => navigate("/interviews")}>退出房间</button><span>会话 {sessionId.slice(0, 8).toUpperCase()}</span></footer>
    </section>
  );
}
