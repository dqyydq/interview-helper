import { Check, Mic, RotateCcw, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { liveInterviewApi } from "../interviews/live/api";

type RecorderState = "idle" | "requesting" | "recording" | "transcribing" | "review";

const preferredMimeTypes = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

function supportedMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  return preferredMimeTypes.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

function recordingFilename(mimeType: string) {
  if (mimeType.includes("ogg")) return "answer.ogg";
  if (mimeType.includes("mp4")) return "answer.m4a";
  return "answer.webm";
}

export function VoiceRecorder({
  disabled = false,
  onConfirm,
}: {
  disabled?: boolean;
  onConfirm: (text: string) => void;
}) {
  const [state, setState] = useState<RecorderState>("idle");
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string>();
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mountedRef = useRef(true);
  const supported =
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== "undefined";

  const releaseStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  useEffect(() => () => {
    mountedRef.current = false;
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") recorder.stop();
    releaseStream();
  }, []);

  const start = async () => {
    setError(undefined);
    setState("requesting");
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const mimeType = supportedMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        releaseStream();
        if (mountedRef.current) {
          setError("录音失败，请继续使用文字回答");
          setState("idle");
        }
      };
      recorder.onstop = async () => {
        releaseStream();
        if (!mountedRef.current) return;
        const type = recorder.mimeType || mimeType || "audio/webm";
        const audio = new Blob(chunksRef.current, { type });
        chunksRef.current = [];
        if (!audio.size) {
          setError("没有检测到录音内容，请重试或使用文字回答");
          setState("idle");
          return;
        }
        setState("transcribing");
        try {
          const result = await liveInterviewApi.transcribe(
            audio,
            recordingFilename(type),
          );
          if (!mountedRef.current) return;
          setTranscript(result.text);
          setState("review");
        } catch (reason) {
          if (!mountedRef.current) return;
          setError(reason instanceof Error ? reason.message : "语音转写暂时不可用");
          setState("idle");
        }
      };
      recorder.start(1_000);
      setState("recording");
    } catch {
      releaseStream();
      if (mountedRef.current) {
        setError("无法访问麦克风，请检查浏览器权限或继续使用文字回答");
        setState("idle");
      }
    }
  };

  const stop = () => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  };

  const reset = () => {
    setTranscript("");
    setError(undefined);
    setState("idle");
  };

  const confirm = () => {
    const text = transcript.trim();
    if (!text) return;
    onConfirm(text);
    reset();
  };

  if (!supported) {
    return <p className="voice-unavailable">当前浏览器不支持录音，文字回答仍可正常使用。</p>;
  }

  return (
    <section className="voice-recorder" aria-label="语音输入">
      <div className="voice-controls">
        {state === "idle" && (
          <button type="button" disabled={disabled} onClick={() => void start()}>
            <Mic size={15} aria-hidden="true" /> 语音回答
          </button>
        )}
        {state === "requesting" && <span>正在请求麦克风权限…</span>}
        {state === "recording" && (
          <button className="recording" type="button" onClick={stop}>
            <Square size={14} aria-hidden="true" /> 停止并转写
          </button>
        )}
        {state === "transcribing" && <span>正在转写录音片段…</span>}
        {error && (
          <button className="voice-retry" type="button" disabled={disabled} onClick={() => void start()}>
            <RotateCcw size={14} aria-hidden="true" /> 重试录音
          </button>
        )}
      </div>
      {error && <p className="voice-error">{error}</p>}
      {state === "review" && (
        <div className="voice-review">
          <label htmlFor="voice-transcript">确认或修改转写结果</label>
          <textarea
            id="voice-transcript"
            value={transcript}
            onChange={(event) => setTranscript(event.target.value)}
          />
          <div>
            <button type="button" onClick={reset}><X size={14} /> 放弃</button>
            <button type="button" disabled={!transcript.trim()} onClick={confirm}>
              <Check size={14} /> 填入回答草稿
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
