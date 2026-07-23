import { apiBaseUrl } from "../api/client";

export interface RealtimeMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sequence: number;
}

export interface ServerEvent {
  event_id: string;
  session_id: string;
  type: string;
  sequence: number;
  timestamp: string;
  transient?: boolean;
  payload: Record<string, unknown>;
}

type ClientEventType =
  | "session.resume"
  | "session.restate"
  | "user.transcript.partial"
  | "user.answer.commit"
  | "user.text.submit"
  | "session.pause"
  | "session.finish";

interface ClientEvent {
  event_id: string;
  type: ClientEventType;
  sequence: number;
  payload: Record<string, unknown>;
}

export class InterviewSocket {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private closed = false;
  private clientSequence = 0;
  private pendingAnswer: ClientEvent | null = null;
  lastSequence = 0;

  constructor(
    private readonly sessionId: string,
    private readonly onEvent: (event: ServerEvent) => void,
    private readonly onState: (state: "connecting" | "connected" | "reconnecting") => void,
  ) {}

  connect() {
    this.closed = false;
    this.open("connecting");
  }

  private open(state: "connecting" | "reconnecting") {
    this.onState(state);
    const url = new URL(apiBaseUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = `${url.pathname}/interviews/${this.sessionId}/live`;
    url.search = `last_sequence=${this.lastSequence}`;
    this.socket = new WebSocket(url);
    this.socket.onopen = () => {
      this.onState("connected");
      if (this.pendingAnswer) this.socket?.send(JSON.stringify(this.pendingAnswer));
    };
    this.socket.onmessage = (message) => {
      const event = JSON.parse(message.data as string) as ServerEvent;
      if (event.sequence > 0) this.lastSequence = Math.max(this.lastSequence, event.sequence);
      if (
        event.type === "input.ack"
        && event.payload.client_event_id === this.pendingAnswer?.event_id
      ) {
        this.pendingAnswer = null;
      }
      this.onEvent(event);
    };
    this.socket.onclose = () => {
      if (this.closed) return;
      this.reconnectTimer = window.setTimeout(() => this.open("reconnecting"), 800);
    };
  }

  send(type: ClientEventType, payload: Record<string, unknown> = {}) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    if (
      (type === "user.text.submit" || type === "user.answer.commit")
      && this.pendingAnswer
    ) {
      return false;
    }
    this.clientSequence += 1;
    const event: ClientEvent = {
      event_id: crypto.randomUUID(),
      type,
      sequence: this.clientSequence,
      payload,
    };
    if (type === "user.text.submit" || type === "user.answer.commit") {
      this.pendingAnswer = event;
    }
    this.socket.send(JSON.stringify(event));
    return true;
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.socket?.close();
  }
}
