import CodeMirror from "@uiw/react-codemirror";
import type { Extension } from "@codemirror/state";
import { Check, Clipboard, Code2, Paperclip, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

export const MAX_CODE_ATTACHMENT_BYTES = 32 * 1024;

const languageOptions = [
  { value: "python", label: "Python", filename: "solution.py" },
  { value: "typescript", label: "TypeScript", filename: "solution.ts" },
  { value: "javascript", label: "JavaScript", filename: "solution.js" },
  { value: "sql", label: "SQL", filename: "query.sql" },
  { value: "json", label: "JSON", filename: "data.json" },
  { value: "text", label: "Plain text", filename: "notes.txt" },
] as const;

export type CodeLanguage = (typeof languageOptions)[number]["value"];

export interface CodeAttachmentDraft {
  attachment_type: "code";
  language: CodeLanguage;
  content: string;
  filename: string;
}

function byteLength(value: string) {
  return new TextEncoder().encode(value).byteLength;
}

export function CodeWhiteboard({
  disabled = false,
  onAttach,
}: {
  disabled?: boolean;
  onAttach: (attachment: CodeAttachmentDraft) => void;
}) {
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState<CodeLanguage>("python");
  const [code, setCode] = useState("");
  const [copied, setCopied] = useState(false);
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const bytes = byteLength(code);
  const tooLarge = bytes > MAX_CODE_ATTACHMENT_BYTES;

  useEffect(() => {
    let current = true;
    const loadLanguage = async () => {
      let extension: Extension | null = null;
      if (language === "python") {
        extension = (await import("@codemirror/lang-python")).python();
      } else if (language === "typescript" || language === "javascript") {
        extension = (await import("@codemirror/lang-javascript")).javascript({
          typescript: language === "typescript",
        });
      } else if (language === "sql") {
        extension = (await import("@codemirror/lang-sql")).sql();
      } else if (language === "json") {
        extension = (await import("@codemirror/lang-json")).json();
      }
      if (current) setExtensions(extension ? [extension] : []);
    };
    void loadLanguage();
    return () => {
      current = false;
    };
  }, [language]);

  const copy = async () => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  };

  const attach = () => {
    const content = code.trim();
    if (!content || tooLarge) return;
    const option = languageOptions.find((item) => item.value === language)!;
    onAttach({
      attachment_type: "code",
      language,
      content,
      filename: option.filename,
    });
    setOpen(false);
  };

  if (!open) {
    return (
      <button
        className="whiteboard-launch"
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <Code2 size={15} aria-hidden="true" /> 代码白板
      </button>
    );
  }

  return (
    <section className="code-whiteboard" aria-label="代码白板">
      <header>
        <div>
          <span>代码附件</span>
          <strong>代码白板</strong>
        </div>
        <button type="button" aria-label="关闭代码白板" onClick={() => setOpen(false)}>
          <X size={15} aria-hidden="true" />
        </button>
      </header>
      <div className="whiteboard-toolbar">
        <label>
          语言
          <select value={language} onChange={(event) => setLanguage(event.target.value as CodeLanguage)}>
            {languageOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <div>
          <button type="button" disabled={!code} onClick={() => void copy()}>
            {copied ? <Check size={14} /> : <Clipboard size={14} />}
            {copied ? "已复制" : "复制"}
          </button>
          <button type="button" disabled={!code} onClick={() => setCode("")}>
            <Trash2 size={14} /> 清空
          </button>
        </div>
      </div>
      <CodeMirror
        aria-label="代码编辑器"
        value={code}
        height="220px"
        extensions={extensions}
        basicSetup={{
          autocompletion: false,
          bracketMatching: true,
          closeBrackets: true,
          foldGutter: false,
          highlightActiveLine: true,
          lineNumbers: true,
        }}
        onChange={setCode}
      />
      <footer>
        <span className={tooLarge ? "too-large" : ""}>
          {bytes.toLocaleString("zh-CN")} / {MAX_CODE_ATTACHMENT_BYTES.toLocaleString("zh-CN")} bytes
        </span>
        <button type="button" disabled={!code.trim() || tooLarge} onClick={attach}>
          <Paperclip size={14} /> 附加到回答
        </button>
      </footer>
      <p>白板内容只会作为文本保存和提供给面试官，不会在服务端执行。</p>
    </section>
  );
}
