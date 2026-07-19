import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { liveInterviewApi } from "../interviews/live/api";
import { VoiceRecorder } from "./VoiceRecorder";

vi.mock("../interviews/live/api", () => ({
  liveInterviewApi: { transcribe: vi.fn() },
}));

class MediaRecorderStub {
  static latest: MediaRecorderStub;
  static isTypeSupported = vi.fn(() => true);
  state: RecordingState = "inactive";
  mimeType = "audio/webm;codecs=opus";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor() {
    MediaRecorderStub.latest = this;
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["audio"], { type: this.mimeType }) } as BlobEvent);
    this.onstop?.();
  }
}

describe("VoiceRecorder", () => {
  beforeEach(() => {
    vi.stubGlobal("MediaRecorder", MediaRecorderStub);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
    vi.mocked(liveInterviewApi.transcribe).mockResolvedValue({
      text: "我会先确认容量和一致性目标。",
      language: "zh",
      duration_seconds: 5,
      provider_request_id: "stt-1",
    });
  });

  it("requires review before copying a transcript into the answer draft", async () => {
    const onConfirm = vi.fn();
    render(<VoiceRecorder onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole("button", { name: "语音回答" }));
    await screen.findByRole("button", { name: "停止并转写" });
    fireEvent.click(screen.getByRole("button", { name: "停止并转写" }));

    const transcript = await screen.findByLabelText("确认或修改转写结果");
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.change(transcript, { target: { value: "修改后的回答" } });
    fireEvent.click(screen.getByRole("button", { name: "填入回答草稿" }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("修改后的回答"));
  });
});
