import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  ExternalLink,
  FileSearch,
  Globe,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import {
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";

import { knowledgeApi } from "../knowledge/api";
import { modelConnectionApi } from "../settings/models/api";
import { discoveryApi } from "./api";
import type {
  DiscoveryCandidateStatus,
  DiscoveryDifficulty,
  DiscoveryProviderType,
  DiscoveryQuestionType,
  DiscoveryRunStatus,
  DiscoverySourceMode,
  QuestionDiscoveryCandidate,
  QuestionDiscoveryDraft,
  QuestionDiscoveryRun,
} from "./types";

const activeRunStatuses = new Set<DiscoveryRunStatus>([
  "queued",
  "running",
  "cancel_requested",
]);

const terminalRunStatuses = new Set<DiscoveryRunStatus>([
  "succeeded",
  "partial",
  "no_results",
  "failed",
  "cancelled",
]);

const runStatusLabels: Record<DiscoveryRunStatus, string> = {
  queued: "等待执行",
  running: "正在发现",
  cancel_requested: "正在取消",
  succeeded: "已完成",
  partial: "部分完成",
  no_results: "没有结果",
  failed: "执行失败",
  cancelled: "已取消",
};

const candidateStatusLabels: Record<DiscoveryCandidateStatus, string> = {
  proposed: "待审核",
  selected: "已选择",
  rejected: "已排除",
  duplicate: "疑似重复",
  imported: "已导入",
  failed: "生成失败",
};

const questionTypeLabels: Record<DiscoveryQuestionType, string> = {
  open_ended: "开放问答",
  project_deep_dive: "项目深挖",
  system_design: "系统设计",
  code_discussion: "代码讨论",
  scenario: "场景问题",
};

const difficultyLabels: Record<DiscoveryDifficulty, string> = {
  foundational: "基础",
  intermediate: "进阶",
  advanced: "高级",
  expert: "专家",
};

const discoveryProviderLabels: Record<DiscoveryProviderType, string> = {
  tavily: "Tavily",
  firecrawl: "Firecrawl",
};

interface DiscoveryFormState {
  connectorId: string;
  sourceMode: DiscoverySourceMode;
  company: string;
  round: string;
  role: string;
  skills: string;
  keywords: string;
  query: string;
  questionType: DiscoveryQuestionType | "";
  difficulty: DiscoveryDifficulty | "";
  country: string;
  urls: string;
  fullWeb: boolean;
  allowDomains: string;
  denyDomains: string;
}

const initialForm: DiscoveryFormState = {
  connectorId: "",
  sourceMode: "search",
  company: "",
  round: "",
  role: "",
  skills: "",
  keywords: "",
  query: "",
  questionType: "",
  difficulty: "",
  country: "",
  urls: "",
  fullWeb: false,
  allowDomains: "",
  denyDomains: "",
};

function toOptional(value: string) {
  const cleaned = value.trim();
  return cleaned || undefined;
}

function toCommaList(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function toLineList(value: string) {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function runIsActive(status?: DiscoveryRunStatus) {
  return Boolean(status && activeRunStatuses.has(status));
}

function describeRun(run: QuestionDiscoveryRun) {
  const snapshot = run.query_snapshot;
  const directQuery = typeof snapshot.search_query === "string" ? snapshot.search_query : undefined;
  const role = typeof snapshot.role === "string" ? snapshot.role : undefined;
  const company = typeof snapshot.company === "string" ? snapshot.company : undefined;
  const urlCount = typeof snapshot.url_count === "number" ? snapshot.url_count : undefined;

  if (directQuery) return directQuery;
  if (run.source_mode === "urls") return `${urlCount ?? 0} 条链接`;
  return [company, role].filter(Boolean).join(" / ") || "自定义检索";
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function isCandidateImportable(candidate: QuestionDiscoveryCandidate) {
  return candidate.status === "proposed" || candidate.status === "selected";
}

function formatTimestamp(value: string | null) {
  if (!value) return "未完成";
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formToDraft(form: DiscoveryFormState): QuestionDiscoveryDraft {
  const draft: QuestionDiscoveryDraft = {
    connector_id: form.connectorId,
    source_mode: form.sourceMode,
    company: toOptional(form.company),
    round: toOptional(form.round),
    role: toOptional(form.role),
    skills: toCommaList(form.skills),
    keywords: toCommaList(form.keywords),
    query: toOptional(form.query),
    question_type: form.questionType || undefined,
    difficulty: form.difficulty || undefined,
    country: toOptional(form.country),
    full_web: form.fullWeb,
    allow_domains: toCommaList(form.allowDomains),
    deny_domains: toCommaList(form.denyDomains),
  };

  if (form.sourceMode === "urls") {
    draft.urls = toLineList(form.urls);
  }
  return draft;
}

export function QuestionDiscoveryPage() {
  const queryClient = useQueryClient();
  const importKeys = useRef(new Map<string, string>());
  const [form, setForm] = useState<DiscoveryFormState>(initialForm);
  const [selectedRunId, setSelectedRunId] = useState<string>();
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [selectedEvidenceCandidate, setSelectedEvidenceCandidate] = useState<QuestionDiscoveryCandidate>();
  const [selectedBankId, setSelectedBankId] = useState("");
  const [actionError, setActionError] = useState<string>();
  const [actionNotice, setActionNotice] = useState<string>();

  const connectors = useQuery({ queryKey: ["discovery-connectors"], queryFn: discoveryApi.listConnectors });
  const bindings = useQuery({ queryKey: ["model-bindings"], queryFn: modelConnectionApi.listBindings });
  const banks = useQuery({ queryKey: ["question-banks"], queryFn: knowledgeApi.listBanks });
  const runs = useQuery({
    queryKey: ["question-discoveries"],
    queryFn: () => discoveryApi.listRuns(),
    refetchInterval: (query) => query.state.data?.data.some((run) => runIsActive(run.status)) ? 1_800 : false,
  });

  useEffect(() => {
    if (!selectedBankId && banks.data?.[0]) setSelectedBankId(banks.data[0].id);
  }, [banks.data, selectedBankId]);

  useEffect(() => {
    if (!selectedRunId && runs.data?.data[0]) setSelectedRunId(runs.data.data[0].id);
  }, [runs.data, selectedRunId]);

  const run = useQuery({
    queryKey: ["question-discovery-run", selectedRunId],
    queryFn: () => discoveryApi.getRun(selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) => runIsActive(query.state.data?.status) ? 1_800 : false,
  });
  const selectedRun = run.data ?? runs.data?.data.find((item) => item.id === selectedRunId);
  const selectedRunActive = runIsActive(selectedRun?.status);
  const sources = useQuery({
    queryKey: ["question-discovery-sources", selectedRunId],
    queryFn: () => discoveryApi.listSources(selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: selectedRunActive ? 1_800 : false,
  });
  const candidates = useQuery({
    queryKey: ["question-discovery-candidates", selectedRunId],
    queryFn: () => discoveryApi.listCandidates(selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: selectedRunActive ? 1_800 : false,
  });
  const evidence = useQuery({
    queryKey: ["question-discovery-evidence", selectedRunId, selectedEvidenceCandidate?.id],
    queryFn: () => discoveryApi.listCandidateEvidence(selectedRunId!, selectedEvidenceCandidate!.id),
    enabled: Boolean(selectedRunId && selectedEvidenceCandidate),
  });

  const researcherReady = bindings.data?.some((binding) => binding.role === "researcher") ?? false;
  const availableConnectors = connectors.data?.filter((connector) => connector.enabled && connector.has_api_key) ?? [];
  const hasSelectedAvailableConnector = availableConnectors.some(
    (connector) => connector.id === form.connectorId,
  );
  const selectedCandidates = useMemo(
    () => candidates.data?.data.filter((candidate) => selectedCandidateIds.includes(candidate.id)) ?? [],
    [candidates.data, selectedCandidateIds],
  );

  const refreshRun = async (runId: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["question-discoveries"] }),
      queryClient.invalidateQueries({ queryKey: ["question-discovery-run", runId] }),
      queryClient.invalidateQueries({ queryKey: ["question-discovery-sources", runId] }),
      queryClient.invalidateQueries({ queryKey: ["question-discovery-candidates", runId] }),
    ]);
  };

  const createRun = useMutation({
    mutationFn: discoveryApi.createRun,
    onSuccess: async (created) => {
      setSelectedRunId(created.id);
      setSelectedCandidateIds([]);
      setActionError(undefined);
      setActionNotice("发现任务已加入本地队列。");
      await refreshRun(created.id);
    },
    onError: (error) => setActionError(errorMessage(error, "创建发现任务失败，请稍后重试。")),
  });
  const cancelRun = useMutation({
    mutationFn: discoveryApi.cancelRun,
    onSuccess: async (updated) => {
      setActionError(undefined);
      setActionNotice("已请求取消发现任务。");
      await refreshRun(updated.id);
    },
    onError: (error) => setActionError(errorMessage(error, "无法取消该发现任务。")),
  });
  const removeRun = useMutation({
    mutationFn: discoveryApi.removeRun,
    onSuccess: async () => {
      const remaining = runs.data?.data.filter((item) => item.id !== selectedRunId) ?? [];
      setSelectedRunId(remaining[0]?.id);
      setSelectedCandidateIds([]);
      setActionError(undefined);
      await queryClient.invalidateQueries({ queryKey: ["question-discoveries"] });
    },
    onError: (error) => setActionError(errorMessage(error, "无法删除该发现任务。")),
  });
  const importCandidates = useMutation({
    mutationFn: ({
      runId,
      bankId,
      idempotencyKey,
      items,
    }: {
      runId: string;
      bankId: string;
      idempotencyKey: string;
      items: QuestionDiscoveryCandidate[];
    }) => discoveryApi.importCandidates(
      runId,
      {
        bank_id: bankId,
        items: items.map((candidate) => ({
          candidate_id: candidate.id,
          candidate_revision: candidate.candidate_revision,
        })),
      },
      idempotencyKey,
    ),
    onSuccess: async (result) => {
      setSelectedCandidateIds([]);
      setActionError(undefined);
      setActionNotice(result.replayed ? "已恢复上一次导入结果。" : `已导入 ${result.items.length} 道草稿题。`);
      await Promise.all([
        refreshRun(result.run_id),
        queryClient.invalidateQueries({ queryKey: ["questions"] }),
        queryClient.invalidateQueries({ queryKey: ["question-banks"] }),
      ]);
    },
    onError: (error) => setActionError(errorMessage(error, "候选题导入失败，请刷新后重试。")),
  });

  const submitDiscovery = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActionError(undefined);
    setActionNotice(undefined);
    const draft = formToDraft(form);
    const hasSearchInput = Boolean(
      draft.company || draft.round || draft.role || draft.query || draft.skills?.length || draft.keywords?.length,
    );
    if (draft.source_mode === "search" && !hasSearchInput) {
      setActionError("请至少填写一个检索条件。" );
      return;
    }
    if (draft.source_mode === "urls" && !draft.urls?.length) {
      setActionError("请添加至少一条公开链接。" );
      return;
    }
    if (!hasSelectedAvailableConnector) {
      setActionError("请选择一个可用的发现连接器。");
      return;
    }
    if (!researcherReady) {
      setActionError("请先在模型设置中绑定 Researcher。" );
      return;
    }
    createRun.mutate(draft);
  };

  const toggleCandidate = (candidate: QuestionDiscoveryCandidate) => {
    if (!isCandidateImportable(candidate)) return;
    setSelectedCandidateIds((current) => current.includes(candidate.id)
      ? current.filter((id) => id !== candidate.id)
      : [...current, candidate.id]);
  };

  const submitImport = () => {
    if (!selectedRun || !selectedBankId || selectedCandidates.length === 0) return;
    const keySeed = [
      selectedRun.id,
      selectedBankId,
      ...selectedCandidates
        .map((candidate) => `${candidate.id}:${candidate.candidate_revision}`)
        .sort(),
    ].join("|");
    const idempotencyKey = importKeys.current.get(keySeed) ?? crypto.randomUUID();
    importKeys.current.set(keySeed, idempotencyKey);
    importCandidates.mutate({
      runId: selectedRun.id,
      bankId: selectedBankId,
      idempotencyKey,
      items: selectedCandidates,
    });
  };

  const combinedError = actionError
    ?? ([connectors.error, runs.error, run.error, sources.error, candidates.error]
      .find((error) => error instanceof Error) as Error | undefined)?.message;

  return (
    <section className="discovery-console" aria-labelledby="discovery-title">
      <header className="discovery-heading">
        <div>
          <span>题目发现</span>
          <h1 id="discovery-title">发现题目</h1>
          <p>从可追溯的公开资料整理候选题，审核后再进入你的个人题库。</p>
        </div>
        <Link className="secondary-button discovery-settings-link" to="/settings/discovery">
          <SlidersHorizontal size={15} aria-hidden="true" /> 管理连接器
        </Link>
      </header>

      {combinedError && <p className="console-error" role="alert">{combinedError}</p>}
      {actionNotice && <p className="discovery-notice" role="status"><Check size={15} aria-hidden="true" />{actionNotice}</p>}

      <div className="discovery-workspace">
        <aside className="discovery-run-rail" aria-label="发现任务历史">
          <div className="discovery-rail-heading">
            <div>
              <span>发现记录</span>
              <h2>发现记录</h2>
            </div>
            <button
              className="row-icon-button"
              type="button"
              aria-label="刷新发现记录"
              title="刷新发现记录"
              disabled={runs.isFetching}
              onClick={() => void runs.refetch()}
            >
              <RefreshCw size={15} aria-hidden="true" />
            </button>
          </div>
          <div className="discovery-run-list">
            {runs.isLoading && <p className="discovery-list-empty">正在读取发现记录。</p>}
            {!runs.isLoading && runs.data?.data.length === 0 && (
              <p className="discovery-list-empty">创建第一个发现任务后，记录会保留在这里。</p>
            )}
            {runs.data?.data.map((item) => (
              <div className={item.id === selectedRunId ? "discovery-run-row active" : "discovery-run-row"} key={item.id}>
                <button
                  type="button"
                  aria-label={`查看发现任务：${describeRun(item)}`}
                  onClick={() => {
                    setSelectedRunId(item.id);
                    setSelectedCandidateIds([]);
                    setActionError(undefined);
                    setActionNotice(undefined);
                  }}
                >
                  <span className={`discovery-run-state ${item.status}`} aria-hidden="true" />
                  <span>
                    <strong>{describeRun(item)}</strong>
                    <small>{runStatusLabels[item.status]} · {formatTimestamp(item.created_at)}</small>
                  </span>
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
                {terminalRunStatuses.has(item.status) && (
                  <button
                    className="discovery-delete-run"
                    type="button"
                    aria-label={`删除发现任务：${describeRun(item)}`}
                    title="删除已结束的发现任务"
                    disabled={removeRun.isPending}
                    onClick={() => {
                      if (window.confirm("删除这条发现记录及其临时来源和候选题？")) removeRun.mutate(item.id);
                    }}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </aside>

        <main className="discovery-main">
          <form className="discovery-form" onSubmit={submitDiscovery}>
            <div className="discovery-form-heading">
              <div>
                <span>新建任务</span>
                <h2>收集候选题</h2>
              </div>
              <div className="discovery-mode-switch" role="tablist" aria-label="发现来源模式">
                <button
                  className={form.sourceMode === "search" ? "selected" : ""}
                  type="button"
                  role="tab"
                  aria-selected={form.sourceMode === "search"}
                  onClick={() => setForm((current) => ({ ...current, sourceMode: "search" }))}
                >
                  <Search size={14} aria-hidden="true" /> 搜索资料
                </button>
                <button
                  className={form.sourceMode === "urls" ? "selected" : ""}
                  type="button"
                  role="tab"
                  aria-selected={form.sourceMode === "urls"}
                  onClick={() => setForm((current) => ({ ...current, sourceMode: "urls" }))}
                >
                  <Globe size={14} aria-hidden="true" /> 公开链接
                </button>
              </div>
            </div>

            {availableConnectors.length === 0 ? (
              <div className="discovery-prerequisite">
                <AlertTriangle size={18} aria-hidden="true" />
                <span><strong>还没有可用的发现连接器</strong><small>先在设置中添加并启用一个连接器，才能发起公开资料发现。</small></span>
                <Link to="/settings/discovery">去配置 <ArrowRight size={14} /></Link>
              </div>
            ) : (
              <div className="discovery-form-grid">
                <label>
                  发现连接器
                  <select
                    required
                    value={form.connectorId}
                    onChange={(event) => setForm({ ...form, connectorId: event.target.value })}
                  >
                    <option value="" disabled>请选择连接器</option>
                    {availableConnectors.map((connector) => (
                      <option key={connector.id} value={connector.id}>
                        {connector.name} · {discoveryProviderLabels[connector.provider_type]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  国家或地区
                  <input
                    value={form.country}
                    onChange={(event) => setForm({ ...form, country: event.target.value })}
                    placeholder="例如：China"
                  />
                </label>

                {form.sourceMode === "search" ? (
                  <>
                    <label>
                      目标公司
                      <input value={form.company} onChange={(event) => setForm({ ...form, company: event.target.value })} placeholder="例如：字节跳动" />
                    </label>
                    <label>
                      面试轮次
                      <input value={form.round} onChange={(event) => setForm({ ...form, round: event.target.value })} placeholder="例如：二面" />
                    </label>
                    <label>
                      岗位方向
                      <input value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })} placeholder="例如：LLM 应用开发" />
                    </label>
                    <label>
                      技能关键词
                      <input value={form.skills} onChange={(event) => setForm({ ...form, skills: event.target.value })} placeholder="RAG, Agent, FastAPI" />
                    </label>
                    <label className="discovery-form-wide">
                      自定义检索词
                      <input value={form.query} onChange={(event) => setForm({ ...form, query: event.target.value })} placeholder="可覆盖上方字段，用于更精确的检索语句" />
                    </label>
                    <label>
                      补充关键词
                      <input value={form.keywords} onChange={(event) => setForm({ ...form, keywords: event.target.value })} placeholder="上下文压缩, 评估" />
                    </label>
                  </>
                ) : (
                  <label className="discovery-form-wide">
                    公开资料链接
                    <textarea
                      required
                      value={form.urls}
                      onChange={(event) => setForm({ ...form, urls: event.target.value })}
                      placeholder={"每行一条，最多 5 条\nhttps://example.com/public-interview-notes"}
                    />
                  </label>
                )}

                <label>
                  题型倾向
                  <select value={form.questionType} onChange={(event) => setForm({ ...form, questionType: event.target.value as DiscoveryQuestionType | "" })}>
                    <option value="">不限题型</option>
                    {Object.entries(questionTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label>
                  难度倾向
                  <select value={form.difficulty} onChange={(event) => setForm({ ...form, difficulty: event.target.value as DiscoveryDifficulty | "" })}>
                    <option value="">不限难度</option>
                    {Object.entries(difficultyLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label>
                  允许域名
                  <input value={form.allowDomains} onChange={(event) => setForm({ ...form, allowDomains: event.target.value })} placeholder="可选，逗号分隔" />
                </label>
                <label>
                  排除域名
                  <input value={form.denyDomains} onChange={(event) => setForm({ ...form, denyDomains: event.target.value })} placeholder="可选，逗号分隔" />
                </label>
              </div>
            )}

            <div className="discovery-form-footer">
              <label className="discovery-full-web">
                <input type="checkbox" checked={form.fullWeb} onChange={(event) => setForm({ ...form, fullWeb: event.target.checked })} />
                <span><strong>扩展到全网</strong><small>关闭时使用默认社区资料范围。</small></span>
              </label>
              <button
                className="primary-button"
                type="submit"
                disabled={
                  availableConnectors.length === 0
                  || !hasSelectedAvailableConnector
                  || !researcherReady
                  || createRun.isPending
                }
              >
                {createRun.isPending ? <Loader2 className="spinning" size={16} aria-hidden="true" /> : <FileSearch size={16} aria-hidden="true" />}
                开始发现
              </button>
            </div>
            {!researcherReady && !bindings.isLoading && (
              <p className="discovery-model-note"><ShieldCheck size={14} aria-hidden="true" />需要在 <Link to="/settings">模型设置</Link> 中显式绑定 Researcher。</p>
            )}
          </form>

          {selectedRun ? (
            <section className="discovery-review" aria-labelledby="discovery-review-title">
              <header className="discovery-review-heading">
                <div>
                  <span>任务审核</span>
                  <h2 id="discovery-review-title">{describeRun(selectedRun)}</h2>
                </div>
                <div className="discovery-review-actions">
                  <span className={`discovery-status ${selectedRun.status}`}>{runStatusLabels[selectedRun.status]}</span>
                  {selectedRunActive && (
                    <button className="secondary-button" type="button" disabled={cancelRun.isPending} onClick={() => cancelRun.mutate(selectedRun.id)}>
                      {cancelRun.isPending ? <Loader2 className="spinning" size={15} /> : <X size={15} />} 取消
                    </button>
                  )}
                </div>
              </header>

              <div className="discovery-progress" aria-label="发现进度">
                <div><span>{selectedRun.stage || "等待开始"}</span><strong>{Math.round(selectedRun.progress * 100)}%</strong></div>
                <i aria-hidden="true"><b style={{ width: `${Math.round(selectedRun.progress * 100)}%` }} /></i>
              </div>

              <div className="discovery-stats">
                <span><strong>{selectedRun.source_count}</strong> 个来源</span>
                <span><strong>{selectedRun.candidate_count}</strong> 道候选题</span>
                <span><strong>{selectedRun.failed_source_count}</strong> 个来源未读</span>
              </div>
              {selectedRun.error_summary && <p className="discovery-run-error"><AlertTriangle size={15} />{selectedRun.error_summary}</p>}

              <div className="discovery-review-grid">
                <section className="discovery-candidates" aria-labelledby="candidate-list-title">
                  <div className="discovery-section-heading">
                    <div><span>候选审核</span><h3 id="candidate-list-title">候选题</h3></div>
                    <small>{candidates.data?.count ?? 0} 条</small>
                  </div>
                  {candidates.isLoading && <p className="discovery-list-empty">正在读取候选题。</p>}
                  {!candidates.isLoading && candidates.data?.data.length === 0 && (
                    <p className="discovery-list-empty">{selectedRunActive ? "正在从来源中整理候选题。" : "该任务没有产生可审核的候选题。"}</p>
                  )}
                  <div className="discovery-candidate-list" aria-live="polite">
                    {candidates.data?.data.map((candidate) => {
                      const importable = isCandidateImportable(candidate);
                      const selected = selectedCandidateIds.includes(candidate.id);
                      return (
                        <article className={`discovery-candidate ${candidate.status}`} key={candidate.id}>
                          <label className="discovery-candidate-select">
                            <input
                              type="checkbox"
                              aria-label={`选择候选题：${candidate.prompt}`}
                              checked={selected}
                              disabled={!importable}
                              onChange={() => toggleCandidate(candidate)}
                            />
                          </label>
                          <div className="discovery-candidate-content">
                            <div className="discovery-candidate-meta">
                              <span>{questionTypeLabels[candidate.question_type]}</span>
                              <span>{difficultyLabels[candidate.difficulty]}</span>
                              <span className={`candidate-status ${candidate.status}`}>{candidateStatusLabels[candidate.status]}</span>
                              <span>{Math.round(candidate.confidence * 100)}% 置信</span>
                            </div>
                            <h4>{candidate.prompt}</h4>
                            {candidate.matching_reason && <p>{candidate.matching_reason}</p>}
                            {candidate.suggested_tags.length > 0 && (
                              <div className="discovery-tag-line">{candidate.suggested_tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
                            )}
                            {candidate.reference_points.length > 0 && (
                              <details className="discovery-reference-points">
                                <summary>参考要点 · {candidate.reference_points.length}</summary>
                                <ul>{candidate.reference_points.map((point) => <li key={point}>{point}</li>)}</ul>
                              </details>
                            )}
                          </div>
                          <button
                            className="row-icon-button"
                            type="button"
                            aria-label={`查看候选题证据：${candidate.prompt}`}
                            title="查看来源证据"
                            onClick={() => setSelectedEvidenceCandidate(candidate)}
                          >
                            <BookOpen size={15} aria-hidden="true" />
                          </button>
                        </article>
                      );
                    })}
                  </div>
                </section>

                <aside className="discovery-sources" aria-labelledby="source-list-title">
                  <div className="discovery-section-heading">
                    <div><span>来源记录</span><h3 id="source-list-title">来源</h3></div>
                    <small>{sources.data?.count ?? 0} 个来源</small>
                  </div>
                  {sources.isLoading && <p className="discovery-list-empty">正在读取来源。</p>}
                  <div className="discovery-source-list">
                    {sources.data?.data.map((source) => {
                      const href = source.final_url ?? source.normalized_url;
                      const openable = href.startsWith("http");
                      return (
                        <article className={`discovery-source ${source.status}`} key={source.id}>
                          <div>
                            <span className={`source-status ${source.status}`}>{source.status}</span>
                            <strong>{source.title || source.domain}</strong>
                            <small>{source.domain} · {source.source_category}</small>
                          </div>
                          {openable && (
                            <a href={href} target="_blank" rel="noreferrer" aria-label={`打开来源：${source.title || source.domain}`} title="在新标签页打开来源">
                              <ExternalLink size={14} aria-hidden="true" />
                            </a>
                          )}
                          {source.excerpt && <p>{source.excerpt}</p>}
                          {source.failure_summary && <p className="source-failure">{source.failure_summary}</p>}
                        </article>
                      );
                    })}
                    {!sources.isLoading && sources.data?.data.length === 0 && (
                      <p className="discovery-list-empty">来源会在任务运行时陆续出现。</p>
                    )}
                  </div>
                </aside>
              </div>

              <footer className="discovery-import-bar">
                <div>
                  <span>导入题库</span>
                  <strong>{selectedCandidates.length === 0 ? "选择候选题后导入" : `已选择 ${selectedCandidates.length} 道候选题`}</strong>
                </div>
                <label>
                  <span className="sr-only">导入目标题库</span>
                  <select value={selectedBankId} onChange={(event) => setSelectedBankId(event.target.value)} disabled={banks.isLoading || !banks.data?.length}>
                    {banks.data?.map((bank) => <option key={bank.id} value={bank.id}>{bank.name}</option>)}
                  </select>
                </label>
                <button className="primary-button" type="button" disabled={!selectedBankId || selectedCandidates.length === 0 || importCandidates.isPending} onClick={submitImport}>
                  {importCandidates.isPending ? <Loader2 className="spinning" size={16} /> : <ArrowRight size={16} />} 导入草稿
                </button>
              </footer>
            </section>
          ) : (
            <div className="discovery-empty-state"><FileSearch size={25} /><strong>开始一次题目发现</strong><p>通过检索条件或公开链接建立来源，再审核候选题。</p></div>
          )}
        </main>
      </div>

      {selectedEvidenceCandidate && selectedRun && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setSelectedEvidenceCandidate(undefined);
        }}>
          <section className="console-dialog discovery-evidence-dialog" role="dialog" aria-modal="true" aria-labelledby="discovery-evidence-title">
            <div className="dialog-heading">
              <div>
                <span>证据溯源</span>
                <h2 id="discovery-evidence-title">候选题来源证据</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭候选题证据" onClick={() => setSelectedEvidenceCandidate(undefined)}><X size={18} /></button>
            </div>
            <p className="discovery-evidence-prompt">{selectedEvidenceCandidate.prompt}</p>
            {evidence.isLoading && <p className="discovery-list-empty">正在读取来源证据。</p>}
            {evidence.error instanceof Error && <p className="inline-error" role="alert">{evidence.error.message}</p>}
            {!evidence.isLoading && evidence.data?.length === 0 && <p className="discovery-list-empty">没有可展示的来源证据。</p>}
            <div className="discovery-evidence-list">
              {evidence.data?.map((item) => (
                <article key={item.id}>
                  <header><span>{item.source_category}</span><strong>{item.source_title}</strong><small>{Math.round(item.confidence * 100)}% 关联度</small></header>
                  <blockquote>{item.excerpt}</blockquote>
                  <a href={item.normalized_url} target="_blank" rel="noreferrer"><ExternalLink size={14} />打开原始来源</a>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
