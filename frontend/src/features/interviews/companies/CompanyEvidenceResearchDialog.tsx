import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Check, FileSearch, ImagePlus, LoaderCircle, ShieldCheck, X } from "lucide-react";
import { type ChangeEvent, type FormEvent, useState } from "react";

import { companyApi } from "./api";
import type {
  CompanyStylePack,
  VisualEvidenceCandidate,
  VisualEvidenceExtraction,
} from "./types";
import "./CompanyEvidenceResearchDialog.css";

interface EditableCandidate extends VisualEvidenceCandidate {
  selected: boolean;
}

interface CompanyEvidenceResearchDialogProps {
  companyName: string;
  stylePack: CompanyStylePack;
  onClose: () => void;
  onCompleted: () => Promise<void>;
}

const warningMessages: Record<string, string> = {
  image_not_retained: "截图只用于这一次解析，完成后不会保存到题库、向量库或公司画像。",
  manual_review_recommended: "这份资料存在歧义或敏感边界，请回到原始页面逐条核验后再写入。",
  sensitive_contact_omitted: "系统已略过包含联系方式的候选内容。",
  no_safe_claims: "没有提取到适合沉淀为公司画像的安全结论；可以换一张更清晰、已脱敏的资料。",
};

function mutationMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function fieldPathLabel(fieldPath: string) {
  const labels: Record<string, string> = {
    default_interviewer_behavior: "整体面试官行为",
    opening_style: "开场与沟通方式",
    follow_up_patterns: "追问模式",
    answer_expectations: "回答期待",
    topic_weights: "关注维度",
    evaluation_weights: "评估维度",
  };
  const parts = fieldPath.split(".");
  if (parts[0] !== "rounds") return labels[fieldPath] ?? fieldPath;
  return `${parts[1] ?? "轮次"} · ${labels[parts[2] ?? ""] ?? parts[2] ?? fieldPath}`;
}

export function CompanyEvidenceResearchDialog({
  companyName,
  stylePack,
  onClose,
  onCompleted,
}: CompanyEvidenceResearchDialogProps) {
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceTitle, setSourceTitle] = useState("");
  const [image, setImage] = useState<File>();
  const [sourceConfirmed, setSourceConfirmed] = useState(false);
  const [result, setResult] = useState<VisualEvidenceExtraction>();
  const [candidates, setCandidates] = useState<EditableCandidate[]>([]);
  const [error, setError] = useState<string>();

  const extract = useMutation({
    mutationFn: () => {
      if (!image) throw new Error("请先选择一张 PNG、JPEG 或 WebP 截图");
      return companyApi.extractVisualEvidence(stylePack.id, {
        sourceUrl: sourceUrl.trim(),
        sourceTitle: sourceTitle.trim(),
        sourceConfirmed,
        image,
      });
    },
    onSuccess: (nextResult) => {
      setResult(nextResult);
      setCandidates(nextResult.candidates.map((item) => ({ ...item, selected: true })));
      setError(undefined);
    },
    onError: (nextError) => setError(mutationMessage(nextError, "资料解析失败，请稍后重试。")),
  });

  const saveEvidence = useMutation({
    mutationFn: async () => {
      if (!result) throw new Error("请先生成证据草案");
      const selected = candidates.filter((item) => item.selected && item.excerpt.trim());
      if (!selected.length) throw new Error("请至少选择一条要写入草案的证据");
      for (const item of selected) {
        await companyApi.addEvidence(stylePack.id, {
          source_url: result.source_url,
          source_title: result.source_title,
          field_path: item.field_path,
          excerpt: item.excerpt.trim(),
          confidence: item.confidence,
        });
      }
    },
    onSuccess: async () => {
      await onCompleted();
      onClose();
    },
    onError: (nextError) => setError(mutationMessage(nextError, "写入证据草案失败，请检查后重试。")),
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(undefined);
    extract.mutate();
  };

  const selectImage = (event: ChangeEvent<HTMLInputElement>) => {
    const nextImage = event.target.files?.[0];
    if (!nextImage) return;
    setImage(nextImage);
    setResult(undefined);
    setCandidates([]);
    setError(undefined);
  };

  const updateCandidate = (index: number, patch: Partial<EditableCandidate>) => {
    setCandidates((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )));
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="console-dialog visual-evidence-dialog"
        aria-modal="true"
        aria-labelledby="visual-evidence-dialog-title"
        role="dialog"
      >
        <div className="dialog-heading">
          <div>
            <span>资料证据</span>
            <h2 id="visual-evidence-dialog-title">用截图整理 {companyName} 面试信号</h2>
          </div>
          <button className="icon-button" type="button" aria-label="关闭" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <div className="visual-evidence-privacy-note">
          <ShieldCheck size={18} aria-hidden="true" />
          <p>图片会发送给当前绑定的“视觉资料解析”模型；它仅用于本次提取，不会被保存或自动写入公司画像。</p>
        </div>

        <form className="visual-evidence-form" onSubmit={submit}>
          <label>
            原始页面链接
            <input
              required
              inputMode="url"
              type="url"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="https://…"
            />
          </label>
          <label>
            来源标题
            <input
              required
              value={sourceTitle}
              onChange={(event) => setSourceTitle(event.target.value)}
              placeholder="例如：2026 春招大模型应用岗位复盘"
            />
          </label>
          <label className="visual-evidence-file-picker">
            <ImagePlus size={18} aria-hidden="true" />
            <span>{image ? image.name : "选择已脱敏截图（PNG / JPEG / WebP，≤ 3 MB）"}</span>
            <input
              accept="image/png,image/jpeg,image/webp"
              type="file"
              onChange={selectImage}
            />
          </label>
          <label className="visual-evidence-confirmation">
            <input
              checked={sourceConfirmed}
              type="checkbox"
              onChange={(event) => setSourceConfirmed(event.target.checked)}
            />
            <span>我确认资料已去除个人信息、保密内容和完整题目原文，并有权将其用于本次模型解析。</span>
          </label>
          <div className="visual-evidence-form-actions">
            <button className="secondary-button" type="button" onClick={onClose}>取消</button>
            <button
              className="primary-button"
              disabled={!sourceConfirmed || extract.isPending || saveEvidence.isPending}
              type="submit"
            >
              {extract.isPending ? <LoaderCircle className="visual-evidence-spinner" size={15} /> : <FileSearch size={15} />}
              {extract.isPending ? "正在提炼可审核信号…" : "解析为证据草案"}
            </button>
          </div>
        </form>

        {error && <p className="inline-error visual-evidence-error" role="alert">{error}</p>}

        {result && (
          <section className="visual-evidence-result" aria-live="polite">
            <header>
              <div>
                <span>人工确认</span>
                <h3>选择要写入当前草案的结论</h3>
              </div>
              <span className="visual-evidence-result-count">{candidates.length} 条候选</span>
            </header>
            {result.warning_codes.map((code) => (
              <p className="visual-evidence-warning" key={code}>
                <AlertTriangle size={15} aria-hidden="true" />
                {warningMessages[code] ?? "请在写入前核验这条候选证据。"}
              </p>
            ))}
            {candidates.length ? (
              <div className="visual-evidence-candidates">
                {candidates.map((candidate, index) => (
                  <article className="visual-evidence-candidate" key={`${candidate.field_path}-${index}`}>
                    <label className="visual-evidence-candidate-toggle">
                      <input
                        checked={candidate.selected}
                        type="checkbox"
                        onChange={(event) => updateCandidate(index, { selected: event.target.checked })}
                      />
                      <span>写入草案</span>
                    </label>
                    <label>
                      对应画像字段
                      <select
                        aria-label={`第 ${index + 1} 条证据对应字段`}
                        value={candidate.field_path}
                        onChange={(event) => updateCandidate(index, { field_path: event.target.value })}
                      >
                        {result.allowed_field_paths.map((fieldPath) => (
                          <option key={fieldPath} value={fieldPath}>{fieldPathLabel(fieldPath)}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      匿名化结论
                      <textarea
                        aria-label={`第 ${index + 1} 条匿名化结论`}
                        maxLength={600}
                        value={candidate.excerpt}
                        onChange={(event) => updateCandidate(index, { excerpt: event.target.value })}
                      />
                    </label>
                    <label className="visual-evidence-confidence">
                      置信度 <strong>{Math.round(candidate.confidence * 100)}%</strong>
                      <input
                        aria-label={`第 ${index + 1} 条置信度`}
                        max="1"
                        min="0"
                        step="0.05"
                        type="range"
                        value={candidate.confidence}
                        onChange={(event) => updateCandidate(index, { confidence: Number(event.target.value) })}
                      />
                    </label>
                  </article>
                ))}
              </div>
            ) : (
              <div className="console-empty compact visual-evidence-empty">
                <FileSearch size={26} aria-hidden="true" />
                <h3>没有可安全沉淀的结论</h3>
                <p>可以更换一张已脱敏、文字清晰的资料，或直接手动补充来源证据。</p>
              </div>
            )}
            {candidates.length > 0 && (
              <div className="visual-evidence-save-actions">
                <span><Check size={15} aria-hidden="true" /> 写入后仍是私有草案，不会自动启用或公开。</span>
                <button className="primary-button" disabled={saveEvidence.isPending || extract.isPending} type="button" onClick={() => saveEvidence.mutate()}>
                  {saveEvidence.isPending ? <LoaderCircle className="visual-evidence-spinner" size={15} /> : <Check size={15} />}
                  {saveEvidence.isPending ? "正在写入…" : "确认写入草案"}
                </button>
              </div>
            )}
          </section>
        )}
      </section>
    </div>
  );
}
