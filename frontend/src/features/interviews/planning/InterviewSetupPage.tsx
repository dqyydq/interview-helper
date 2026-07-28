import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  BadgeCheck,
  Check,
  CircleAlert,
  Database,
  FileText,
  Gauge,
  ListChecks,
  LoaderCircle,
  MemoryStick,
  ShieldCheck,
  Sparkles,
  Target,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { knowledgeApi } from "../../knowledge/api";
import { companyApi } from "../companies/api";
import type { CompanyStylePack } from "../companies/types";
import { planningApi } from "./api";
import { liveInterviewApi } from "../live/api";
import { reportApi } from "../../reports/api";
import type {
  InterviewPlan,
  InterviewReadiness,
  PlanDraft,
  PlanJob,
  ReadinessItem,
} from "./types";
import "./interview-preparation.css";

const sourceLabels: Record<string, string> = {
  manual: "用户题库",
  resume: "简历专项",
  generated: "岗位场景",
};

const capabilityLabels: Record<string, string> = {
  llm_fundamentals: "大模型基础",
  rag_and_retrieval: "RAG 与检索",
  agent_engineering: "Agent 工程",
  system_design: "系统设计",
  evaluation: "评估与可观测性",
  delivery: "工程交付",
};

const quickTrialFallback = {
  session_kind: "quick_trial" as const,
  duration_minutes: 10,
  target_question_count: 2,
  include_in_trends: false,
  role_name: "llm_application_engineer",
};

const readinessActionRoutes: Record<string, string> = {
  diagnostics: "/settings/diagnostics",
  worker: "/settings/diagnostics",
  models: "/settings",
  companies: "/interviews",
  resume: "/questions",
  question_bank: "/questions",
  transcription: "/settings",
};

function profileBoundary(
  readiness: InterviewReadiness | undefined,
  stylePack: CompanyStylePack | null | undefined,
) {
  const profile = readiness?.company_profile;
  return {
    version: profile?.pack_version ?? stylePack?.pack_version ?? null,
    trustStatus: profile?.trust_status ?? stylePack?.trust_status ?? "template",
    trustLabel: profile?.trust_label ?? stylePack?.evidence_label ?? "轮次骨架 · 非风格结论",
    evidenceCount: profile?.evidence_count ?? stylePack?.evidence_count ?? 0,
    latestEvidenceAt: profile?.latest_evidence_at ?? stylePack?.latest_evidence_at ?? null,
    sources: profile?.source_summaries ?? stylePack?.evidence?.slice(0, 3).map((item) => ({
      title: item.source_title,
      url: item.source_url,
      excerpt: item.excerpt,
    })) ?? [],
  };
}

function ReadinessList({
  items,
  tone,
  onAction,
}: {
  items: ReadinessItem[];
  tone: "blocking" | "enhancement";
  onAction?: (action: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul className={`p0-readiness-list p0-readiness-list--${tone}`}>
      {items.map((item) => {
        const ready = item.status === "ready" || item.status === "available";
        return (
          <li key={item.key}>
            {ready ? <BadgeCheck size={15} aria-hidden="true" /> : <CircleAlert size={15} aria-hidden="true" />}
            <span>
              <strong>{item.label}</strong>
              {item.detail && <small>{item.detail}</small>}
            </span>
            {item.action && readinessActionRoutes[item.action] && onAction && (
              <button type="button" onClick={() => onAction(item.action!)}>去处理</button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function InterviewSetupPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const companyId = searchParams.get("company") ?? "";
  const roundId = searchParams.get("round") ?? "";
  const practiceTaskId = searchParams.get("task");
  const companies = useQuery({ queryKey: ["companies"], queryFn: companyApi.list });
  const banks = useQuery({ queryKey: ["question-banks"], queryFn: knowledgeApi.listBanks });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: knowledgeApi.listResumes });
  const eventSourceRef = useRef<EventSource | null>(null);
  const [plan, setPlan] = useState<InterviewPlan>();
  const [job, setJob] = useState<PlanJob>();
  const [excludedMemoryIds, setExcludedMemoryIds] = useState<string[]>([]);
  const [form, setForm] = useState<Omit<PlanDraft, "company_id" | "round_profile_id">>({
    role_name: "llm_application_engineer",
    duration_minutes: 45,
    target_question_count: 6,
    question_bank_ids: [],
    resume_id: null,
    source_weights: { manual: 0.4, resume: 0.3, generated: 0.3 },
    preferences: { input_mode: "text" },
    session_kind: "standard",
    practice_task_id: null,
  });

  const practiceTask = useQuery({
    queryKey: ["practice-task", practiceTaskId],
    queryFn: () => reportApi.getPracticeTask(practiceTaskId!),
    enabled: Boolean(practiceTaskId),
    retry: false,
  });
  const practiceReport = useQuery({
    queryKey: ["practice-task-report", practiceTask.data?.report_id],
    queryFn: () => reportApi.get(practiceTask.data!.report_id),
    enabled: Boolean(practiceTask.data?.report_id),
    retry: false,
  });
  const practiceSession = useQuery({
    queryKey: ["practice-task-session", practiceReport.data?.session_id],
    queryFn: () => liveInterviewApi.get(practiceReport.data!.session_id),
    enabled: Boolean(practiceReport.data?.session_id),
    retry: false,
  });
  const selectedCompanyId = companyId || practiceSession.data?.plan.config?.company_id || "";
  const selectedRoundId = roundId || practiceSession.data?.plan.config?.round_profile_id || "";
  const company = companies.data?.find((item) => item.id === selectedCompanyId);
  const round = company?.latest_style_pack?.rounds.find((item) => item.id === selectedRoundId);
  const readiness = useQuery({
    queryKey: ["interview-readiness", selectedCompanyId, selectedRoundId],
    queryFn: () => planningApi.readiness({ companyId: selectedCompanyId, roundProfileId: selectedRoundId }),
    enabled: Boolean(selectedCompanyId && selectedRoundId),
    retry: false,
  });

  useEffect(() => {
    if (round) {
      setForm((current) => (
        current.session_kind === "standard"
          ? { ...current, duration_minutes: round.duration_minutes }
          : current
      ));
    }
  }, [round]);

  useEffect(() => {
    if (!practiceTaskId) return;
    setPlan(undefined);
    setJob(undefined);
    setForm((current) => ({
      ...current,
      duration_minutes: quickTrialFallback.duration_minutes,
      target_question_count: quickTrialFallback.target_question_count,
      session_kind: "targeted_practice",
      practice_task_id: practiceTaskId,
    }));
  }, [practiceTaskId]);

  useEffect(() => () => eventSourceRef.current?.close(), []);

  const generate = useMutation({
    mutationFn: planningApi.create,
    onSuccess: (result) => {
      setPlan(result.plan);
      setJob(result.job);
      eventSourceRef.current?.close();
      const events = new EventSource(planningApi.jobEventsUrl(result.job.id));
      eventSourceRef.current = events;
      events.addEventListener("job", async (event) => {
        const nextJob = JSON.parse((event as MessageEvent<string>).data) as PlanJob;
        setJob(nextJob);
        if (nextJob.status === "completed") {
          events.close();
          setPlan(await planningApi.get(result.plan.id));
        } else if (nextJob.status === "failed" || nextJob.status === "cancelled") {
          events.close();
        }
      });
    },
  });
  const createSession = useMutation({
    mutationFn: liveInterviewApi.create,
    onSuccess: (created) => navigate(`/interviews/${created.id}/live`),
  });

  const resetPlan = () => {
    setPlan(undefined);
    setJob(undefined);
  };

  const applyQuickTrial = () => {
    const quickTrial = readiness.data?.defaults.quick_trial ?? quickTrialFallback;
    resetPlan();
    setForm((current) => ({
      ...current,
      role_name: quickTrial.role_name || current.role_name,
      duration_minutes: quickTrial.duration_minutes,
      target_question_count: quickTrial.target_question_count,
      session_kind: "quick_trial",
      practice_task_id: null,
    }));
  };

  const restoreStandardSession = () => {
    resetPlan();
    setForm((current) => ({
      ...current,
      duration_minutes: round?.duration_minutes ?? 45,
      target_question_count: 6,
      session_kind: "standard",
      practice_task_id: null,
    }));
  };

  const toggleBank = (bankId: string) => {
    setForm((current) => ({
      ...current,
      question_bank_ids: current.question_bank_ids.includes(bankId)
        ? current.question_bank_ids.filter((item) => item !== bankId)
        : [...current.question_bank_ids, bankId],
    }));
  };

  const readyResumes = useMemo(
    () => resumes.data?.filter((resume) => resume.parse_status === "ready") ?? [],
    [resumes.data],
  );
  const isPlanning = job?.status === "queued" || job?.status === "running";
  const isReady = plan?.status === "ready";
  const isQuickTrial = form.session_kind === "quick_trial";
  const isTargetedPractice = form.session_kind === "targeted_practice";
  const noMaterialsSelected = form.question_bank_ids.length === 0 && !form.resume_id;
  const profile = profileBoundary(readiness.data, company?.latest_style_pack);
  // Do not race the preparation check: a user could otherwise submit while
  // the selected company/round is still being verified and bypass a visible
  // blocking prerequisite for a moment.
  const readinessBlocksPlanning = readiness.isLoading || readiness.isError || readiness.data?.ready !== true;
  const memoryPreview = useQuery({
    queryKey: ["interview-memory-preview", plan?.id],
    queryFn: () => planningApi.memoryPreview(plan!.id),
    enabled: isReady,
  });

  useEffect(() => {
    setExcludedMemoryIds([]);
  }, [plan?.id]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!company || !round || readiness.isLoading || readiness.isError || readiness.data?.ready !== true) return;
    generate.mutate({ ...form, company_id: company.id, round_profile_id: round.id });
  };

  const resolvingPracticeTarget = Boolean(
    practiceTaskId && (practiceTask.isLoading || practiceReport.isLoading || practiceSession.isLoading),
  );

  if (companies.isLoading || resolvingPracticeTarget) {
    return <div className="setup-loading"><LoaderCircle className="spin" /> 正在读取面试配置</div>;
  }
  if (practiceTaskId && (practiceTask.isError || practiceReport.isError || practiceSession.isError)) {
    return (
      <section className="setup-missing">
        <AlertCircle size={28} />
        <h1>无法读取这条训练任务</h1>
        <p>任务可能已被删除，或它关联的原始面试记录无法再使用。</p>
        <button className="primary-button" type="button" onClick={() => navigate("/reports")}>返回报告</button>
      </section>
    );
  }
  if (!company || !round) {
    return (
      <section className="setup-missing">
        <Target size={28} />
        <h1>需要先选择公司与轮次</h1>
        <button className="primary-button" type="button" onClick={() => navigate("/interviews")}>
          返回选择
        </button>
      </section>
    );
  }

  return (
    <section className="interview-setup p0-preparation" aria-labelledby="setup-title">
      <header className="setup-header">
        <button className="text-button" type="button" onClick={() => navigate("/interviews")}>
          <ArrowLeft size={15} /> 返回公司选择
        </button>
        <div>
          <span>面试准备台 · 02</span>
          <h1 id="setup-title">配置本场模拟</h1>
          <p>{company.name} · {round.name} · {profile.trustLabel}</p>
        </div>
        <div className={`setup-trust p0-profile-trust ${profile.trustStatus}`}>
          <ShieldCheck size={18} />
          <span>
            <strong>{profile.trustLabel}</strong>
            <small>本场会保留画像版本与证据边界，不把它当作事实保证。</small>
          </span>
        </div>
      </header>

      <form className="setup-grid" onSubmit={submit}>
        <main className="setup-form">
          <section className="p0-readiness-panel" aria-labelledby="readiness-title">
            <header>
              <div>
                <span>开场前检查</span>
                <h2 id="readiness-title">{readiness.data?.ready === false ? "还不能开始面试" : "可以开始一次试跑"}</h2>
              </div>
              {readiness.isLoading && <LoaderCircle className="spin" size={18} aria-label="正在检查" />}
              {!readiness.isLoading && !readiness.isError && readiness.data?.ready !== false && <BadgeCheck size={20} aria-hidden="true" />}
              {!readiness.isLoading && (readiness.isError || readiness.data?.ready === false) && <AlertCircle size={20} aria-hidden="true" />}
            </header>
            {readiness.isError && (
              <p className="p0-readiness-fallback">
                暂时无法读取就绪检查。为避免生成无法继续的计划，请确认本地服务后重新进入此页面。
              </p>
            )}
            {readiness.data && (
              <div className="p0-readiness-grid">
                <div>
                  <h3>必要条件</h3>
                  <ReadinessList
                    items={readiness.data.blocking}
                    tone="blocking"
                    onAction={(action) => navigate(readinessActionRoutes[action])}
                  />
                </div>
                <div>
                  <h3>可选增强</h3>
                  <ReadinessList
                    items={readiness.data.enhancements}
                    tone="enhancement"
                    onAction={(action) => navigate(readinessActionRoutes[action])}
                  />
                </div>
              </div>
            )}
            <div className="p0-readiness-actions">
              {!isQuickTrial && !isTargetedPractice && (
                <button className="p0-quick-trial-button" type="button" onClick={applyQuickTrial}>
                  <Sparkles size={15} /> 用 10 分钟试跑
                </button>
              )}
              {(isQuickTrial || isTargetedPractice) && (
                <button className="p0-link-button" type="button" onClick={restoreStandardSession}>
                  调整为完整模拟
                </button>
              )}
              <p>
                <strong>10 分钟试跑</strong>固定 2 个主问题；没有简历和题库也能开始，默认不计入长期趋势。
              </p>
            </div>
          </section>

          <section className="p0-profile-boundary" aria-labelledby="profile-boundary-title">
            <header>
              <ShieldCheck size={17} aria-hidden="true" />
              <div>
                <h2 id="profile-boundary-title">本场使用的公司画像</h2>
                <p>画像只约束提问节奏和轮次骨架；它不会替代真实面试经验，也不会自动公开来源。</p>
              </div>
            </header>
            <dl>
              <div><dt>版本</dt><dd>{profile.version ? `v${profile.version}` : "未提供"}</dd></div>
              <div><dt>证据</dt><dd>{profile.evidenceCount} 条</dd></div>
              <div><dt>最近证据</dt><dd>{profile.latestEvidenceAt ? new Date(profile.latestEvidenceAt).toLocaleDateString("zh-CN") : "未提供"}</dd></div>
            </dl>
            {profile.sources.length > 0 ? (
              <details>
                <summary>查看已纳入的来源摘要（{profile.sources.length}）</summary>
                <ul>
                  {profile.sources.map((source) => (
                    <li key={`${source.url}-${source.title}`}>
                      <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
                      {source.excerpt && <small>{source.excerpt}</small>}
                    </li>
                  ))}
                </ul>
              </details>
            ) : (
              <p className="p0-profile-empty">当前没有公开来源摘要；可用作轮次组织参考，不应解读为该公司的确定面试风格。</p>
            )}
          </section>

          {(isQuickTrial || isTargetedPractice) && (
            <section className="p0-session-intent" aria-live="polite">
              <Target size={18} aria-hidden="true" />
              <div>
                <strong>{isTargetedPractice ? "专项短模拟" : "10 分钟试跑"}</strong>
                <p>
                  {isTargetedPractice
                    ? practiceTask.data ? `${practiceTask.data.title}：${practiceTask.data.success_criteria}` : "正在读取训练目标；将以 2 个主问题聚焦本次练习。"
                    : "2 个主问题，允许零资料开始；本次默认不参与长期趋势。"}
                </p>
              </div>
            </section>
          )}

          <section className="setup-section">
            <div className="setup-section-index">01</div>
            <div>
              <h2>岗位与节奏</h2>
              <p>当前阶段提供 LLM 应用开发岗位矩阵，后续可安装其他专业矩阵。</p>
              <div className="setup-fields two-column">
                <label>
                  岗位方向
                  <select
                    value={form.role_name}
                    onChange={(event) => setForm({ ...form, role_name: event.target.value })}
                  >
                    <option value="llm_application_engineer">LLM 应用开发</option>
                  </select>
                </label>
                <label>
                  面试时长
                  <select
                    value={form.duration_minutes}
                    disabled={isQuickTrial || isTargetedPractice}
                    onChange={(event) => setForm({
                      ...form,
                      duration_minutes: Number(event.target.value),
                      session_kind: "standard",
                      practice_task_id: null,
                    })}
                  >
                    {[10, 30, 45, 60, 90].map((minutes) => <option key={minutes} value={minutes}>{minutes} 分钟</option>)}
                  </select>
                </label>
                <label>
                  目标题量
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={form.target_question_count}
                    disabled={isQuickTrial || isTargetedPractice}
                    onChange={(event) => setForm({
                      ...form,
                      target_question_count: Number(event.target.value),
                      session_kind: "standard",
                      practice_task_id: null,
                    })}
                  />
                </label>
                <label>
                  回答方式
                  <select disabled value="text">
                    <option value="text">文字输入（语音稍后接入）</option>
                  </select>
                </label>
              </div>
            </div>
          </section>

          <section className="setup-section">
            <div className="setup-section-index">02</div>
            <div>
              <h2>资料范围</h2>
              <p>没有选择题库或简历时，仍会使用岗位场景模板生成最小可用计划。</p>
              {noMaterialsSelected && (
                <div className="p0-zero-materials-note">
                  <ListChecks size={16} aria-hidden="true" />
                  <span><strong>零资料也可开始</strong>试跑会使用岗位场景与已选轮次的结构，不会虚构你的履历或题库内容。</span>
                </div>
              )}
              <div className="source-selector">
                <div>
                  <h3><Database size={15} /> 用户题库</h3>
                  {banks.data?.length ? banks.data.map((bank) => (
                    <label className="source-check" key={bank.id}>
                      <input
                        type="checkbox"
                        checked={form.question_bank_ids.includes(bank.id)}
                        onChange={() => toggleBank(bank.id)}
                      />
                      <span><strong>{bank.name}</strong><small>{bank.question_count} 道可管理题目</small></span>
                    </label>
                  )) : <p className="muted-copy">当前没有题库，可直接继续。</p>}
                </div>
                <div>
                  <h3><FileText size={15} /> 专项简历</h3>
                  <label>
                    本场使用
                    <select
                      value={form.resume_id ?? ""}
                      onChange={(event) => setForm({ ...form, resume_id: event.target.value || null })}
                    >
                      <option value="">不使用简历</option>
                      {readyResumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename}</option>)}
                    </select>
                  </label>
                  {readyResumes.length === 0 && <p className="muted-copy">没有已解析完成的简历。</p>}
                </div>
              </div>
            </div>
          </section>
        </main>

        <aside className="plan-preview" aria-live="polite">
          <div className="plan-preview-heading">
            <span>计划说明</span>
            <h2>计划预览</h2>
          </div>
          {!plan && (
            <div className="plan-awaiting">
              <Sparkles size={23} />
              <h3>等待生成</h3>
              <p>计划会展示来源比例与能力覆盖，但不会提前泄露题目正文。</p>
            </div>
          )}
          {plan && !isReady && (
            <div className="plan-progress">
              <LoaderCircle className={isPlanning ? "spin" : ""} size={23} />
              <h3>{job?.status === "failed" ? "生成失败" : "正在编排候选题"}</h3>
              <div className="progress-track"><span style={{ width: `${(job?.progress ?? 0) * 100}%` }} /></div>
              <p>{job?.error_message || "正在校验来源、题量和时间预算。"}</p>
            </div>
          )}
          {isReady && (
            <div className="plan-ready">
              <div className="ready-mark"><Check size={18} /> 计划校验通过</div>
              <dl>
                <div><dt>题目数量</dt><dd>{plan.questions.length}</dd></div>
                <div><dt>总时长</dt><dd>{plan.total_minutes} 分钟</dd></div>
                <div><dt>规划器</dt><dd>{plan.plan_snapshot.planner}</dd></div>
              </dl>
              <section className="p0-plan-profile-snapshot">
                <h3><ShieldCheck size={13} /> 画像快照</h3>
                <p>
                  <span>{profile.trustLabel}</span>
                  <strong>v{plan.plan_snapshot.style_pack_version ?? profile.version ?? "—"} · {plan.plan_snapshot.style_pack_trust?.evidence_count ?? plan.plan_snapshot.style_pack_evidence_count ?? profile.evidenceCount} 条证据</strong>
                </p>
                <small>本场已冻结此版本；后续编辑公司画像不会改写本次提问或报告。</small>
              </section>
              <section>
                <h3>来源分布</h3>
                {Object.entries(plan.plan_snapshot.source_distribution ?? {}).map(([key, value]) => (
                  <p key={key}><span>{sourceLabels[key] ?? key}</span><strong>{value} 题</strong></p>
                ))}
              </section>
              <section>
                <h3>能力覆盖</h3>
                <div className="coverage-tags">
                  {Object.keys(plan.plan_snapshot.capability_coverage ?? {}).map((key) => (
                    <span key={key}>{capabilityLabels[key] ?? key}</span>
                  ))}
                </div>
              </section>
              <section className="plan-memory-preview">
                <h3><MemoryStick size={13} /> 本场长期记忆</h3>
                {!memoryPreview.data?.enabled && <p className="memory-preview-muted">跨场记忆当前已关闭</p>}
                {memoryPreview.data?.enabled && memoryPreview.data.items.length === 0 && (
                  <p className="memory-preview-muted">本场没有匹配到长期记忆</p>
                )}
                {memoryPreview.data?.items.map((memory) => {
                  const included = !excludedMemoryIds.includes(memory.id);
                  return (
                    <label className="plan-memory-item" key={memory.id}>
                      <input
                        type="checkbox"
                        checked={included}
                        onChange={() => setExcludedMemoryIds((current) =>
                          included ? [...current, memory.id] : current.filter((id) => id !== memory.id)
                        )}
                      />
                      <span>{memory.content}</span>
                    </label>
                  );
                })}
                {memoryPreview.data?.enabled && memoryPreview.data.items.length > 0 && (
                  <small>取消勾选只对本场生效，不会修改长期记忆。</small>
                )}
              </section>
            </div>
          )}
          <div className="plan-policy">
            <Gauge size={17} />
            <p>本阶段使用确定性规划器。模型重排接入后也只能选择候选池内的题目。</p>
          </div>
        </aside>

        <footer className="setup-command">
          <div>
            <Target size={20} />
            <span>
              <strong>{company.name} · {round.name}</strong>
              <small>
                {isTargetedPractice ? "专项训练 · " : isQuickTrial ? "试跑 · " : ""}
                {form.duration_minutes} 分钟 / {form.target_question_count} 题
                {(isQuickTrial || isTargetedPractice) && " · 默认不纳入趋势"}
              </small>
            </span>
          </div>
          <button className="secondary-button" type="button" onClick={() => navigate("/questions")}>管理资料</button>
          {!isReady ? (
            <button className="primary-button" type="submit" disabled={isPlanning || generate.isPending || readinessBlocksPlanning}>
              {readiness.isLoading ? "正在检查准备条件" : readinessBlocksPlanning ? "完成必要配置后生成" : isPlanning ? "正在生成计划" : "生成面试计划"}
            </button>
          ) : (
            <button
              className="primary-button"
              type="button"
              disabled={createSession.isPending}
              onClick={() => plan && createSession.mutate({ planId: plan.id, excludedMemoryIds })}
            >
              {createSession.isPending ? "正在创建房间" : "开始模拟面试"}
            </button>
          )}
        </footer>
      </form>
    </section>
  );
}
