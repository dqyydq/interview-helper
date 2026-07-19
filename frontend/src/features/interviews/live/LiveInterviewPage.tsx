import { useQuery } from "@tanstack/react-query";
import { Clock3, LoaderCircle, Pause, Send, ShieldCheck, Square } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { InterviewSocket, type RealtimeMessage, type ServerEvent } from "../../../lib/realtime/interviewSocket";
import { ContextUsage } from "../../diagnostics/ContextUsage";
import { liveInterviewApi } from "./api";

export function LiveInterviewPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<RealtimeMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("ready");
  const [connection, setConnection] = useState<"connecting" | "connected" | "reconnecting">("connecting");
  const [error, setError] = useState<string>();
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
    const handleEvent = (event: ServerEvent) => {
      if (event.type === "session.state") {
        setStatus(String(event.payload.status));
      } else if (event.type === "assistant.delta") {
        setStreaming((current) => current + String(event.payload.text ?? ""));
      } else if (event.type === "input.ack" || event.type === "assistant.message") {
        const message = event.payload.message as RealtimeMessage;
        setMessages((current) => current.some((item) => item.id === message.id) ? current : [...current, message]);
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

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    if (socketRef.current?.send("user.text.submit", { text })) {
      setDraft("");
      setBusy(true);
      setError(undefined);
    }
  };

  if (session.isLoading) return <div className="setup-loading"><LoaderCircle className="spin" /> 正在进入面试房间</div>;
  if (session.isError || !session.data) return <div className="setup-missing"><h1>无法进入面试房间</h1></div>;
  const total = session.data.plan.questions.length;
  const current = session.data.current_question_sequence ?? 1;

  return (
    <section className="live-room" aria-labelledby="live-room-title">
      <header className="live-header">
        <div><span>LIVE INTERVIEW / 03</span><h1 id="live-room-title">实时模拟面试</h1></div>
        <div className={`connection-indicator ${connection}`}><i />{connection === "connected" ? "连接正常" : "正在恢复连接"}</div>
        <div className="live-metrics"><span><Clock3 size={14} />{session.data.plan.total_minutes} 分钟</span><span>{current} / {total} 题</span></div>
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
        <button className="secondary-button" type="button" onClick={() => socketRef.current?.send("session.pause")}><Pause size={15} /> 暂停</button>
        <button className="text-button danger" type="button" onClick={() => socketRef.current?.send("session.finish")}><Square size={14} /> 提前结束</button>
      </aside>
      <form className="answer-composer" onSubmit={submit}>
        <label htmlFor="answer-text">你的回答</label>
        <textarea id="answer-text" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={busy || status !== "interviewing"} placeholder="先给结论，再说明依据、取舍与边界。" />
        <div><small>{draft.length} / 50000</small><button className="primary-button" type="submit" disabled={!draft.trim() || busy || connection !== "connected"}><Send size={15} />{busy ? "等待面试官" : "确认回答"}</button></div>
      </form>
      <footer className="live-footer"><button className="text-button" type="button" onClick={() => navigate("/interviews")}>退出房间</button><span>SESSION {sessionId.slice(0, 8).toUpperCase()}</span></footer>
    </section>
  );
}
