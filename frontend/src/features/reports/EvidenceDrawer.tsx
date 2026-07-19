import { Quote, X } from "lucide-react";
import { useEffect, useRef } from "react";

import type { EvidenceMessage, EvidenceReference } from "./types";

interface EvidenceDrawerProps {
  evidence: EvidenceReference | null;
  message: EvidenceMessage | null;
  onClose: () => void;
}
export function EvidenceDrawer({ evidence, message, onClose }: EvidenceDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (evidence && dialog && !dialog.open) dialog.showModal();
    if (!evidence && dialog?.open) dialog.close();
  }, [evidence]);

  return (
    <dialog
      className="evidence-drawer"
      ref={dialogRef}
      aria-labelledby="evidence-drawer-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialogRef.current) onClose();
      }}
      onClose={onClose}
    >
      <div className="evidence-drawer-sheet">
        <header>
          <div>
            <span>TRACEABLE EVIDENCE</span>
            <h2 id="evidence-drawer-title">原回答证据</h2>
          </div>
          <button type="button" aria-label="关闭证据" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        {evidence && message ? (
          <article id={`evidence-message-${message.id}`} tabIndex={-1}>
            <div className="evidence-sequence">
              <Quote size={15} aria-hidden="true" />
              MESSAGE {String(message.sequence).padStart(2, "0")}
            </div>
            <blockquote>{message.content}</blockquote>
            <dl>
              <div>
                <dt>评估引用</dt>
                <dd>{evidence.claim}</dd>
              </div>
              <div>
                <dt>消息 ID</dt>
                <dd>{message.id}</dd>
              </div>
            </dl>
          </article>
        ) : (
          <p className="evidence-missing">对应的原始消息不可用，本条证据不会用于展示强结论。</p>
        )}
        <footer>只显示本场面试中已确认的用户原回答；摘要与长期记忆不作为评分证据。</footer>
      </div>
    </dialog>
  );
}
