import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InterviewSocket } from "./interviewSocket";

class WebSocketHarness {
  static OPEN = 1;
  static instances: WebSocketHarness[] = [];
  readyState = WebSocketHarness.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(public url: URL) {
    WebSocketHarness.instances.push(this);
  }
}

describe("InterviewSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    WebSocketHarness.instances = [];
    vi.stubGlobal("WebSocket", WebSocketHarness);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reconnects from the last server sequence and resends an unacknowledged answer once", () => {
    const events = vi.fn();
    const socket = new InterviewSocket("session-1", events, vi.fn());
    socket.lastSequence = 8;
    socket.connect();

    const first = WebSocketHarness.instances[0];
    expect(String(first.url)).toContain("last_sequence=8");
    first.onopen?.();
    expect(socket.send("user.text.submit", { text: "我的回答" })).toBe(true);
    expect(socket.send("user.answer.commit", { text: "重复回答" })).toBe(false);
    expect(first.send).toHaveBeenCalledTimes(1);
    const serialized = first.send.mock.calls[0][0] as string;
    const clientEvent = JSON.parse(serialized) as { event_id: string };

    first.onclose?.();
    vi.advanceTimersByTime(800);
    const reconnected = WebSocketHarness.instances[1];
    reconnected.onopen?.();
    expect(reconnected.send).toHaveBeenCalledWith(serialized);

    reconnected.onmessage?.({
      data: JSON.stringify({
        event_id: "server-ack",
        session_id: "session-1",
        type: "input.ack",
        sequence: 9,
        timestamp: new Date().toISOString(),
        payload: { client_event_id: clientEvent.event_id },
      }),
    } as MessageEvent);
    reconnected.onclose?.();
    vi.advanceTimersByTime(800);
    const afterAck = WebSocketHarness.instances[2];
    afterAck.onopen?.();
    expect(afterAck.send).not.toHaveBeenCalled();
    expect(String(afterAck.url)).toContain("last_sequence=9");
  });
});
