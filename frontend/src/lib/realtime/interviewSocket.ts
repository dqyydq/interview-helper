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

export class InterviewSocket {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private closed = false;
  private clientSequence = 0;
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
    this.socket.onopen = () => this.onState("connected");
    this.socket.onmessage = (message) => {
      const event = JSON.parse(message.data as string) as ServerEvent;
      if (event.sequence > 0) this.lastSequence = Math.max(this.lastSequence, event.sequence);
      this.onEvent(event);
    };
    this.socket.onclose = () => {
      if (this.closed) return;
      this.reconnectTimer = window.setTimeout(() => this.open("reconnecting"), 800);
    };
  }

  send(type: "user.text.submit" | "session.pause" | "session.finish", payload = {}) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.clientSequence += 1;
    this.socket.send(JSON.stringify({
      event_id: crypto.randomUUID(),
      type,
      sequence: this.clientSequence,
      payload,
    }));
    return true;
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.socket?.close();
  }
}
