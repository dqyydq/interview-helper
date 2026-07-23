import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock3,
  FileSearch,
  Pencil,
  Plus,
  ShieldCheck,
  Target,
  Trash2,
  X,
} from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { companyApi } from "./api";
import type { CompanyDraft, RoundDraft, RoundProfile } from "./types";

const defaultCompanyDraft = (): CompanyDraft => ({
  name: "",
  description: "",
  style_pack: {
    name: "自定义风格草案",
    supported_roles: ["llm_application_engineer"],
  },
  rounds: [
    { round_key: "round_1", name: "一面", sequence: 1, duration_minutes: 45 },
    { round_key: "round_2", name: "二面", sequence: 2, duration_minutes: 45 },
    { round_key: "round_3", name: "三面", sequence: 3, duration_minutes: 45 },
  ],
});

function companyMonogram(name: string) {
  return name.replace(/科技|集团|公司/g, "").slice(0, 2).toUpperCase();
}

function nextRoundDraft(rounds: RoundProfile[]): RoundDraft {
  const nextPosition = Math.max(0, ...rounds.map((item) => item.sequence)) + 1;
  const usedKeys = new Set(rounds.map((item) => item.round_key));
  let roundKey = `round_${nextPosition}`;
  let suffix = 2;
  while (usedKeys.has(roundKey)) {
    roundKey = `round_${nextPosition}_${suffix}`;
    suffix += 1;
  }

  return {
    round_key: roundKey,
    name: `第 ${nextPosition} 轮`,
    sequence: nextPosition,
    opening_style: "",
    follow_up_patterns: [],
    pressure_level: 1,
    duration_minutes: 45,
  };
}

function editableRound(round: RoundProfile): RoundDraft {
  return {
    round_key: round.round_key,
    name: round.name,
    sequence: round.sequence,
    opening_style: round.opening_style,
    topic_weights: round.topic_weights,
    follow_up_patterns: round.follow_up_patterns,
    pressure_level: round.pressure_level,
    answer_expectations: round.answer_expectations,
    evaluation_weights: round.evaluation_weights,
    duration_minutes: round.duration_minutes,
  };
}

function mutationMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

type CompanyFormMode = "create" | "edit" | null;
type RoundFormMode = "create" | "edit" | null;

export function CompanySelectionPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedCompanyId, setSelectedCompanyId] = useState<string>();
  const [selectedRoundId, setSelectedRoundId] = useState<string>();
  const [companyFormMode, setCompanyFormMode] = useState<CompanyFormMode>(null);
  const [companyForm, setCompanyForm] = useState({ name: "", description: "" });
  const [roundFormMode, setRoundFormMode] = useState<RoundFormMode>(null);
  const [roundDraft, setRoundDraft] = useState<RoundDraft>(() => nextRoundDraft([]));
  const [roundPendingDeletion, setRoundPendingDeletion] = useState<RoundProfile>();
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<string>();

  const companies = useQuery({ queryKey: ["companies"], queryFn: companyApi.list });
  const company =
    companies.data?.find((item) => item.id === selectedCompanyId) ?? companies.data?.[0];
  const stylePack = company?.latest_style_pack;
  const rounds = useMemo(
    () => [...(stylePack?.rounds ?? [])].sort((left, right) => left.sequence - right.sequence),
    [stylePack?.rounds],
  );
  const round = rounds.find((item) => item.id === selectedRoundId) ?? rounds[0];
  const topicNames = round ? Object.keys(round.topic_weights) : [];
  const isCustomDraft = Boolean(company && !company.is_system && stylePack?.status === "draft");
  const evidenceInsufficient = Boolean(stylePack && stylePack.evidence_count === 0);
  const canManageRounds = Boolean(stylePack && isCustomDraft);
  const selectedRoundIndex = round ? rounds.findIndex((item) => item.id === round.id) : -1;

  const refreshCompanies = async () => {
    await queryClient.invalidateQueries({ queryKey: ["companies"] });
    await queryClient.refetchQueries({ queryKey: ["companies"] });
  };

  const createCompany = useMutation({
    mutationFn: (draft: CompanyDraft) => companyApi.create(draft),
    onSuccess: async (created) => {
      setSelectedCompanyId(created.id);
      setSelectedRoundId(created.latest_style_pack?.rounds[0]?.id);
      setCompanyFormMode(null);
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: (error) => setActionError(mutationMessage(error, "创建公司失败，请稍后重试。")),
  });
  const updateCompany = useMutation({
    mutationFn: ({ companyId, name, description }: { companyId: string; name: string; description: string }) =>
      companyApi.update(companyId, { name, description }),
    onSuccess: async (updated) => {
      setSelectedCompanyId(updated.id);
      setCompanyFormMode(null);
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: (error) => setActionError(mutationMessage(error, "保存公司信息失败，请稍后重试。")),
  });
  const archiveCompany = useMutation({
    mutationFn: (companyId: string) => companyApi.archive(companyId),
    onSuccess: async () => {
      setSelectedCompanyId(undefined);
      setSelectedRoundId(undefined);
      setArchiveConfirmOpen(false);
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: (error) => setActionError(mutationMessage(error, "归档公司失败，请稍后重试。")),
  });
  const createRound = useMutation({
    mutationFn: ({ stylePackId, draft }: { stylePackId: string; draft: RoundDraft }) =>
      companyApi.createRound(stylePackId, draft),
    onSuccess: async (created) => {
      setSelectedRoundId(created.id);
      setRoundFormMode(null);
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: (error) => setActionError(mutationMessage(error, "新增轮次失败，请检查标识或位置。")),
  });
  const updateRound = useMutation({
    mutationFn: ({ roundId, draft }: { roundId: string; draft: RoundDraft }) =>
      companyApi.updateRound(roundId, {
        round_key: draft.round_key,
        name: draft.name,
        opening_style: draft.opening_style?.trim() || null,
        follow_up_patterns: draft.follow_up_patterns ?? [],
        pressure_level: draft.pressure_level,
        duration_minutes: draft.duration_minutes,
      }),
    onSuccess: async () => {
      setRoundFormMode(null);
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: (error) => setActionError(mutationMessage(error, "保存轮次失败，请稍后重试。")),
  });
  const deleteRound = useMutation({
    mutationFn: (roundId: string) => companyApi.deleteRound(roundId),
    onSuccess: async () => {
      setSelectedRoundId(undefined);
      setRoundPendingDeletion(undefined);
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: (error) => setActionError(mutationMessage(error, "删除轮次失败，请稍后重试。")),
  });
  const reorderRound = useMutation({
    mutationFn: async ({ current, neighbour }: { current: RoundProfile; neighbour: RoundProfile }) => {
      const temporaryPosition = Math.max(...rounds.map((item) => item.sequence)) + 1;
      let movedToTemporaryPosition = false;
      let neighbourMoved = false;
      try {
        await companyApi.updateRound(current.id, { sequence: temporaryPosition });
        movedToTemporaryPosition = true;
        await companyApi.updateRound(neighbour.id, { sequence: current.sequence });
        neighbourMoved = true;
        await companyApi.updateRound(current.id, { sequence: neighbour.sequence });
      } catch (error) {
        // The API protects sequence uniqueness. Restore a valid pre-swap state if one request fails midway.
        try {
          if (neighbourMoved) {
            await companyApi.updateRound(neighbour.id, { sequence: neighbour.sequence });
          }
          if (movedToTemporaryPosition) {
            await companyApi.updateRound(current.id, { sequence: current.sequence });
          }
        } catch {
          // A refresh below surfaces the server's final state even if this best-effort rollback also fails.
        }
        throw error;
      }
    },
    onSuccess: async () => {
      setActionError(undefined);
      await refreshCompanies();
    },
    onError: async (error) => {
      setActionError(mutationMessage(error, "调整轮次位置失败，已重新读取当前顺序。"));
      await refreshCompanies();
    },
  });

  const openCreateCompany = () => {
    setCompanyForm({ name: "", description: "" });
    setCompanyFormMode("create");
    setActionError(undefined);
  };
  const openEditCompany = () => {
    if (!company) return;
    setCompanyForm({ name: company.name, description: company.description ?? "" });
    setCompanyFormMode("edit");
    setActionError(undefined);
  };
  const openCreateRound = () => {
    setRoundDraft(nextRoundDraft(rounds));
    setRoundFormMode("create");
    setActionError(undefined);
  };
  const openEditRound = () => {
    if (!round) return;
    setRoundDraft(editableRound(round));
    setRoundFormMode("edit");
    setActionError(undefined);
  };

  const submitCompany = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (companyFormMode === "create") {
      const draft = defaultCompanyDraft();
      createCompany.mutate({ ...draft, name: companyForm.name.trim(), description: companyForm.description.trim() });
      return;
    }
    if (companyFormMode === "edit" && company) {
      updateCompany.mutate({
        companyId: company.id,
        name: companyForm.name.trim(),
        description: companyForm.description.trim(),
      });
    }
  };
  const submitRound = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const draft = {
      ...roundDraft,
      round_key: roundDraft.round_key.trim(),
      name: roundDraft.name.trim(),
      opening_style: roundDraft.opening_style?.trim() ?? "",
      follow_up_patterns: (roundDraft.follow_up_patterns ?? []).filter(Boolean),
    };
    if (roundFormMode === "create" && stylePack) {
      createRound.mutate({ stylePackId: stylePack.id, draft });
    }
    if (roundFormMode === "edit" && round) {
      updateRound.mutate({ roundId: round.id, draft });
    }
  };
  const moveRound = (direction: -1 | 1) => {
    if (!round || !canManageRounds) return;
    const neighbour = rounds[selectedRoundIndex + direction];
    if (!neighbour) return;
    reorderRound.mutate({ current: round, neighbour });
  };

  return (
    <section className="company-console" aria-labelledby="company-console-title">
      <aside className="company-rail" aria-label="公司列表">
        <div className="company-rail-heading">
          <div>
            <span>INTERVIEW TARGET</span>
            <h1 id="company-console-title">选择公司</h1>
          </div>
          <button className="icon-button" type="button" aria-label="添加公司" onClick={openCreateCompany}>
            <Plus size={18} aria-hidden="true" />
          </button>
        </div>

        {companies.isLoading && <div className="rail-skeleton" aria-label="正在加载公司" />}
        {companies.isError && <p className="inline-error">公司列表加载失败，请检查本地 API。</p>}
        <div className="company-list">
          {companies.data?.map((item) => (
            <button
              key={item.id}
              className={item.id === company?.id ? "company-option active" : "company-option"}
              type="button"
              onClick={() => {
                setSelectedCompanyId(item.id);
                setSelectedRoundId(item.latest_style_pack?.rounds[0]?.id);
                setActionError(undefined);
              }}
            >
              <span className="company-monogram" aria-hidden="true">{companyMonogram(item.name)}</span>
              <span>
                <strong>{item.name}</strong>
                <small>{item.is_system ? "系统骨架" : "我的公司"}</small>
              </span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
          ))}
        </div>
        <button className="add-company-row" type="button" onClick={openCreateCompany}>
          <Plus size={18} aria-hidden="true" />
          添加公司
        </button>

        {company && !company.is_system && (
          <div className="company-management" aria-label="公司管理">
            <span>MY COMPANY</span>
            <div>
              <button className="text-button" type="button" onClick={openEditCompany}>
                <Pencil size={14} aria-hidden="true" /> 编辑公司
              </button>
              <button className="text-button danger-text" type="button" onClick={() => setArchiveConfirmOpen(true)}>
                <Archive size={14} aria-hidden="true" /> 归档
              </button>
            </div>
          </div>
        )}
      </aside>

      <main className="round-workspace">
        <header className="round-workspace-heading">
          <div>
            <span>ROUND PROFILE</span>
            <h2>选择面试轮次</h2>
          </div>
          <div className="round-heading-actions">
            {stylePack && (
              <div className="pack-status-list" aria-label="风格包状态">
                {stylePack.status === "active" && (
                  <span className="pack-status active"><Check size={13} />已启用</span>
                )}
                {isCustomDraft && (
                  <span className="pack-status draft"><FileSearch size={13} />自定义草案</span>
                )}
                {evidenceInsufficient && (
                  <span className="pack-status caution"><AlertTriangle size={13} />证据不足</span>
                )}
              </div>
            )}
            {canManageRounds && (
              <button className="compact-action" type="button" onClick={openCreateRound}>
                <Plus size={15} aria-hidden="true" /> 新增轮次
              </button>
            )}
          </div>
        </header>

        {actionError && <p className="console-error" role="alert">{actionError}</p>}

        {rounds.length > 0 ? (
          <>
            <div className="round-switcher" role="tablist" aria-label="面试轮次">
              {rounds.map((item) => (
                <button
                  key={item.id}
                  className={item.id === round?.id ? "round-tab active" : "round-tab"}
                  type="button"
                  role="tab"
                  aria-selected={item.id === round?.id}
                  onClick={() => setSelectedRoundId(item.id)}
                >
                  <span>{String(item.sequence).padStart(2, "0")}</span>
                  <strong>{item.name}</strong>
                  <small>{item.duration_minutes} 分钟</small>
                </button>
              ))}
            </div>

            <article className="round-detail">
              <div className="round-title-block">
                <span className="round-accent" aria-hidden="true" />
                <div>
                  <h3>{round?.name} · {stylePack?.name}</h3>
                  <p>{round?.opening_style || "这是可编辑的轮次骨架，尚未添加具体开场与公司风格结论。"}</p>
                </div>
              </div>

              {canManageRounds && round ? (
                <section className="round-management-panel" aria-label="轮次管理">
                  <div>
                    <span>ROUND CONTROL</span>
                    <p>Position {round.sequence}。用上下移动调整轮次顺序，避免把面试阶段写死为一二三面。</p>
                  </div>
                  <div className="round-management-actions">
                    <button
                      className="row-icon-button"
                      type="button"
                      aria-label={`上移${round.name}`}
                      disabled={selectedRoundIndex <= 0 || reorderRound.isPending}
                      onClick={() => moveRound(-1)}
                    >
                      <ChevronUp size={16} aria-hidden="true" />
                    </button>
                    <button
                      className="row-icon-button"
                      type="button"
                      aria-label={`下移${round.name}`}
                      disabled={selectedRoundIndex === rounds.length - 1 || reorderRound.isPending}
                      onClick={() => moveRound(1)}
                    >
                      <ChevronDown size={16} aria-hidden="true" />
                    </button>
                    <button className="text-button" type="button" onClick={openEditRound}>
                      <Pencil size={14} aria-hidden="true" /> 编辑轮次
                    </button>
                    <button className="text-button danger-text" type="button" onClick={() => setRoundPendingDeletion(round)}>
                      <Trash2 size={14} aria-hidden="true" /> 删除
                    </button>
                  </div>
                </section>
              ) : (
                <div className="round-readonly-note">
                  <ShieldCheck size={16} aria-hidden="true" />
                  <p>{company?.is_system ? "系统公司只提供轮次骨架；添加自定义公司即可管理自己的轮次。" : "已启用的风格包会冻结轮次，避免历史模拟引用被改写。"}</p>
                </div>
              )}

              <section className="round-facts" aria-label="轮次信息">
                <div>
                  <Clock3 size={19} aria-hidden="true" />
                  <span><small>预计时长</small><strong>{round?.duration_minutes} 分钟</strong></span>
                </div>
                <div>
                  <Target size={19} aria-hidden="true" />
                  <span><small>压力等级</small><strong>{round?.pressure_level} / 5</strong></span>
                </div>
                <div>
                  <ShieldCheck size={19} aria-hidden="true" />
                  <span><small>资料状态</small><strong>{stylePack?.evidence_label}</strong></span>
                </div>
              </section>

              <section className="focus-section">
                <h4>关注维度</h4>
                {topicNames.length > 0 ? (
                  <div className="focus-tags">
                    {topicNames.map((topic) => <span key={topic}>{topic}</span>)}
                  </div>
                ) : (
                  <p>尚未添加有来源的关注维度。后续可在风格包中维护研究证据。</p>
                )}
              </section>

              <section className="follow-up-section">
                <h4>追问模式</h4>
                {round?.follow_up_patterns.length ? (
                  <ol>{round.follow_up_patterns.map((item) => <li key={item}>{item}</li>)}</ol>
                ) : (
                  <p>当前仅提供轮次结构，不推断该公司的追问习惯。</p>
                )}
              </section>
            </article>
          </>
        ) : (
          <div className="console-empty">
            <Building2 size={28} aria-hidden="true" />
            <h3>还没有可用轮次</h3>
            <p>添加一家公司后，系统会先建立一面、二面和三面骨架。</p>
            {canManageRounds && <button className="primary-button" type="button" onClick={openCreateRound}>新增第一轮</button>}
          </div>
        )}
      </main>

      <aside className="style-preview" aria-labelledby="style-preview-title">
        <div className="preview-heading">
          <span>STYLE PREVIEW</span>
          <h2 id="style-preview-title">面试官预览</h2>
        </div>
        <section className="style-integrity">
          <h3>风格结论状态</h3>
          <div className="style-integrity-badges">
            {isCustomDraft && <span className="integrity-badge draft">自定义草案</span>}
            {evidenceInsufficient && <span className="integrity-badge caution">证据不足</span>}
            {!isCustomDraft && !evidenceInsufficient && <span className="integrity-badge verified">有来源支持</span>}
          </div>
          <p>
            {isCustomDraft && evidenceInsufficient
              ? "这是你的可编辑轮次草案；尚未添加公开来源，系统不会把它呈现为公司事实。"
              : stylePack?.evidence_label || "尚未选择风格包"}
          </p>
        </section>
        <section>
          <h3>沟通方式</h3>
          <p>{round?.opening_style || "待用户补充或后续研究 Agent 生成有来源的草案。"}</p>
        </section>
        <section>
          <h3>可能追问</h3>
          <p>{round?.follow_up_patterns[0] || "当前没有足够证据，不展示推测性结论。"}</p>
        </section>
        <section>
          <h3>信息来源</h3>
          <p>{stylePack?.evidence_label || "尚未选择风格包"}</p>
        </section>
        <div className="preview-caution">
          <AlertTriangle size={17} aria-hidden="true" />
          <p>公司风格仅作为模拟参数。无来源内容会明确标为草案，不代表官方标准。</p>
        </div>
      </aside>

      <footer className="selection-command">
        <div>
          <Building2 size={20} aria-hidden="true" />
          <span>
            <strong>{company?.name || "未选择公司"} · {round?.name || "未选择轮次"}</strong>
            <small>岗位方向：LLM 应用开发</small>
          </span>
        </div>
        <button className="secondary-button" type="button" disabled={!company || company.is_system} onClick={openEditCompany}>
          调整公司
        </button>
        <button
          className="primary-button"
          type="button"
          disabled={!company || !round}
          onClick={() => navigate(`/interviews/setup?company=${company?.id}&round=${round?.id}`)}
        >
          配置本场模拟
        </button>
      </footer>

      {companyFormMode && (
        <div className="dialog-backdrop" role="presentation">
          <form className="console-dialog" aria-modal="true" aria-labelledby="company-dialog-title" role="dialog" onSubmit={submitCompany}>
            <div className="dialog-heading">
              <div>
                <span>{companyFormMode === "create" ? "CUSTOM COMPANY" : "COMPANY DETAILS"}</span>
                <h2 id="company-dialog-title">{companyFormMode === "create" ? "添加公司骨架" : "编辑公司"}</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setCompanyFormMode(null)}>
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <label>
              公司名称
              <input
                required
                autoFocus
                value={companyForm.name}
                onChange={(event) => setCompanyForm({ ...companyForm, name: event.target.value })}
                placeholder="例如：某云计算公司"
              />
            </label>
            <label>
              说明
              <textarea
                value={companyForm.description}
                onChange={(event) => setCompanyForm({ ...companyForm, description: event.target.value })}
                placeholder="说明资料范围或目标岗位，不要把传闻写成事实。"
              />
            </label>
            {actionError && <p className="inline-error" role="alert">{actionError}</p>}
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setCompanyFormMode(null)}>取消</button>
              <button className="primary-button" type="submit" disabled={createCompany.isPending || updateCompany.isPending}>
                {companyFormMode === "create" ? "创建轮次骨架" : "保存公司"}
              </button>
            </div>
          </form>
        </div>
      )}

      {roundFormMode && (
        <div className="dialog-backdrop" role="presentation">
          <form className="console-dialog round-editor-dialog" aria-modal="true" aria-labelledby="round-dialog-title" role="dialog" onSubmit={submitRound}>
            <div className="dialog-heading">
              <div>
                <span>ROUND PROFILE</span>
                <h2 id="round-dialog-title">{roundFormMode === "create" ? "新增轮次" : "编辑轮次"}</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setRoundFormMode(null)}>
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="round-editor-grid">
              <label>
                轮次名称
                <input required autoFocus value={roundDraft.name} onChange={(event) => setRoundDraft({ ...roundDraft, name: event.target.value })} />
              </label>
              <label>
                轮次标识
                <input
                  required
                  pattern="[A-Za-z0-9_-]+"
                  value={roundDraft.round_key}
                  onChange={(event) => setRoundDraft({ ...roundDraft, round_key: event.target.value })}
                />
              </label>
              <label>
                面试时长（分钟）
                <input
                  required
                  min={10}
                  max={240}
                  type="number"
                  value={roundDraft.duration_minutes ?? 45}
                  onChange={(event) => setRoundDraft({ ...roundDraft, duration_minutes: Number(event.target.value) })}
                />
              </label>
              <label>
                压力等级（0–5）
                <select
                  value={roundDraft.pressure_level ?? 1}
                  onChange={(event) => setRoundDraft({ ...roundDraft, pressure_level: Number(event.target.value) })}
                >
                  {[0, 1, 2, 3, 4, 5].map((level) => <option key={level} value={level}>{level}</option>)}
                </select>
              </label>
            </div>
            <label>
              开场与沟通方式
              <textarea value={roundDraft.opening_style ?? ""} onChange={(event) => setRoundDraft({ ...roundDraft, opening_style: event.target.value })} placeholder="例如：先确认项目边界，再连续追问工程取舍。" />
            </label>
            <label>
              追问方向（每行一条）
              <textarea
                value={(roundDraft.follow_up_patterns ?? []).join("\n")}
                onChange={(event) => setRoundDraft({
                  ...roundDraft,
                  follow_up_patterns: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean),
                })}
                placeholder="例如：\n如何验证这个方案？\n规模扩大后会怎样？"
              />
            </label>
            {roundFormMode === "create" && <p className="form-hint">新轮次初始 position 为 {roundDraft.sequence}；创建后可用上下箭头调整顺序。</p>}
            {actionError && <p className="inline-error" role="alert">{actionError}</p>}
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setRoundFormMode(null)}>取消</button>
              <button className="primary-button" type="submit" disabled={createRound.isPending || updateRound.isPending}>
                {roundFormMode === "create" ? "新增轮次" : "保存轮次"}
              </button>
            </div>
          </form>
        </div>
      )}

      {archiveConfirmOpen && company && (
        <div className="dialog-backdrop" role="presentation">
          <section className="console-dialog confirm-dialog" aria-modal="true" aria-labelledby="archive-dialog-title" role="dialog">
            <div className="dialog-heading">
              <div>
                <span>ARCHIVE COMPANY</span>
                <h2 id="archive-dialog-title">归档 {company.name}</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setArchiveConfirmOpen(false)}><X size={18} /></button>
            </div>
            <p>归档后，该公司会从模拟选择中隐藏；已生成的面试记录不会被删除。</p>
            {actionError && <p className="inline-error" role="alert">{actionError}</p>}
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setArchiveConfirmOpen(false)}>取消</button>
              <button className="danger-button" type="button" disabled={archiveCompany.isPending} onClick={() => archiveCompany.mutate(company.id)}>确认归档</button>
            </div>
          </section>
        </div>
      )}

      {roundPendingDeletion && (
        <div className="dialog-backdrop" role="presentation">
          <section className="console-dialog confirm-dialog" aria-modal="true" aria-labelledby="delete-round-dialog-title" role="dialog">
            <div className="dialog-heading">
              <div>
                <span>DELETE ROUND</span>
                <h2 id="delete-round-dialog-title">删除 {roundPendingDeletion.name}</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setRoundPendingDeletion(undefined)}><X size={18} /></button>
            </div>
            <p>这会从当前自定义草案中移除该轮次；已经生成的面试计划不会被改写。</p>
            {actionError && <p className="inline-error" role="alert">{actionError}</p>}
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setRoundPendingDeletion(undefined)}>取消</button>
              <button className="danger-button" type="button" disabled={deleteRound.isPending} onClick={() => deleteRound.mutate(roundPendingDeletion.id)}>删除轮次</button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
