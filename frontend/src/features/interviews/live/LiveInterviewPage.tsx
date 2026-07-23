import { useQuery } from "@tanstack/react-query";
import { Clock3, Code2, LoaderCircle, Pause, Play, RotateCcw, Send, ShieldCheck, Square } from "lucide-react";
import { lazy, Suspense, type FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { InterviewSocket, type RealtimeMessage, type ServerEvent } from "../../../lib/realtime/interviewSocket";
import { ContextUsage } from "../../diagnostics/ContextUsage";
import { VoiceRecorder } from "../../live-interview/VoiceRecorder";
import type { CodeAttachmentDraft } from "../../live-interview/CodeWhiteboard";
import { liveInterviewApi } from "./api";

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
  const [error, setError] = useState<string>();
  const [codeAttachments, setCodeAttachments] = useState<CodeAttachmentDraft[]>([]);
  const socketRef = useRef<InterviewSocket | null>(null);
  const session = useQuery({
    queryKey: ["interview-session", sessionId],
    queryFn: () => liveInterviewApi.start(sessionId),
    enabled: Boolean(sessionId),
    retry: false,
  });

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
      } else if (event.type === "assistant.delta") {
        setStreaming((current) => current + String(event.payload.text ?? ""));
      } else if (event.type === "timer.update") {
        setRemainingSeconds(Number(event.payload.remaining_seconds ?? 0));
      } else if (event.type === "input.ack" || event.type === "assistant.message") {
        const message = event.payload.message as RealtimeMessage | undefined;
        if (message) {
          setMessages((current) => current.some((item) => item.id === message.id) ? current : [...current, message]);
        }
        if (event.type === "assistant.message") {
          setStreaming("");
          setBusy(false);
        }
      } else if (event.type === "error") {
        setError(String(event.payload.message ?? "面试官暂时无法继续"));
        setStreaming("");
        setBusy(false);
      }
    };
    const socket = new InterviewSocket(sessionId, handleEvent, setConnection);
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
      setDraft("");
      setCodeAttachments([]);
      setBusy(true);
      setError(undefined);
    }
  };

  if (session.isLoading) return <div className="setup-loading"><LoaderCircle className="spin" /> 正在进入面试房间</div>;
  if (session.isError || !session.data) return <div className="setup-missing"><h1>无法进入面试房间</h1></div>;
  const total = session.data.plan.questions.length;
  const current = session.data.current_question_sequence ?? 1;
  const timerLabel = `${String(Math.floor(remainingSeconds / 60)).padStart(2, "0")}:${String(remainingSeconds % 60).padStart(2, "0")}`;

  return (
    <section className="live-room" aria-labelledby="live-room-title">
      <header className="live-header">
        <div><span>LIVE INTERVIEW / 03</span><h1 id="live-room-title">实时模拟面试</h1></div>
        <div className={`connection-indicator ${connection}`}><i />{connection === "connected" ? "连接正常" : "正在恢复连接"}</div>
        <div className="live-metrics"><span><Clock3 size={14} />{timerLabel}</span><span>{current} / {total} 题</span></div>
      </header>
      <main className="live-transcript">
        <div className="transcript-rule"><span>TRANSCRIPT</span><strong>面试中不显示评分</strong></div>
        {messages.map((message) => (
          <article key={message.id} className={`transcript-message ${message.role}`}>
            <span>{message.role === "assistant" ? "INTERVIEWER" : "YOU"}</span>
            <p>{message.content}</p>
          </article>
        ))}
        {streaming && <article className="transcript-message assistant streaming"><span>INTERVIEWER</span><p>{streaming}<i /></p></article>}
        {error && <div className="live-error">{error}。你的回答已保存，可配置模型后继续。</div>}
      </main>
      <aside className="live-side">
        <section><ShieldCheck size={18} /><h2>本场规则</h2><p>一次只处理一个问题；回答确认后写入记录；断线会自动补发已确认事件。</p></section>
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
          onConfirm={(text) => setDraft((current) => current.trim() ? `${current.trim()}\n${text}` : text)}
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
      <footer className="live-footer"><button className="text-button" type="button" onClick={() => navigate("/interviews")}>退出房间</button><span>SESSION {sessionId.slice(0, 8).toUpperCase()}</span></footer>
    </section>
  );
}
