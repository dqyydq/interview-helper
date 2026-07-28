import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  FileBarChart,
  ListChecks,
  Quote,
  RefreshCw,
  ShieldCheck,
  Target,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { EmptyState } from "../components/EmptyState";
import { reportApi } from "../features/reports/api";
import { CoachPanel } from "../features/reports/CoachPanel";
import { EvidenceDrawer } from "../features/reports/EvidenceDrawer";
import type {
  EvaluationAnchor,
  EvaluationReport,
  EvaluationJob,
  EvidenceReference,
  PracticeTask,
} from "../features/reports/types";
import "./interview-report-p0.css";

const anchorLabels: Record<EvaluationAnchor, string> = {
  evidence_insufficient: "证据不足",
  insufficient: "尚未达到",
  partial: "部分达到",
  solid: "稳定达到",
  strong: "表现突出",
};

const dimensionLabels: Record<string, string> = {
  technical_depth: "技术深度",
  problem_solving: "问题分析",
  communication: "结构表达",
  system_design: "系统设计",
};

const reportStatusLabels: Record<EvaluationReport["status"], string> = {
  pending: "等待评估",
  running: "生成中",
  failed: "需要重试",
  completed: "已完成",
};

const practiceStatusLabels: Record<PracticeTask["status"], string> = {
  pending: "待练",
  in_progress: "练习中",
  completed: "已完成",
  dismissed: "已忽略",
};

const styleProfileTrustLabels = {
  template: "轮次骨架",
  draft: "自定义草案",
  source_backed: "有来源支持",
} as const;

function phaseLabel(job: EvaluationJob | null) {
  const phase = String(job?.result.phase ?? "queued");
  return {
    queued: "等待评估资源",
    loading_sources: "冻结证据与计划",
    grouping_answers: "按题目整理原回答",
    persisting_report: "校验引用并生成报告",
    retry_wait: "等待自动重试",
  }[phase] ?? "正在生成评估报告";
}

export function InterviewReportPage() {
  const { reportId } = useParams();
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["reports"],
    queryFn: reportApi.list,
    enabled: !reportId,
  });
  const report = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => reportApi.get(reportId!),
    enabled: Boolean(reportId),
  });
  const practiceTasks = useQuery({
    queryKey: ["practice-tasks"],
    queryFn: () => reportApi.listPracticeTasks(),
    enabled: !reportId && typeof reportApi.listPracticeTasks === "function",
    retry: false,
  });
  const retry = useMutation({
    mutationFn: () => reportApi.retry(reportId!),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
    },
  });
  const evaluationJobId = report.data?.job?.id;
  const evaluationJobStatus = report.data?.job?.status;

  useEffect(() => {
    if (!reportId || !evaluationJobId || !evaluationJobStatus) return;
    if (!["queued", "running"].includes(evaluationJobStatus)) return;
    if (typeof EventSource === "undefined") return;
    const source = new EventSource(reportApi.jobEventsUrl(evaluationJobId));
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ["report", reportId] });
    };
    source.addEventListener("job", refresh);
    source.onerror = refresh;
    return () => source.close();
  }, [evaluationJobId, evaluationJobStatus, queryClient, reportId]);

  if (!reportId) {
    return (
      <ReportIndex
        isLoading={list.isLoading}
        isError={list.isError}
        reports={list.data ?? []}
        practiceTasks={practiceTasks.data ?? []}
        practiceTasksLoading={practiceTasks.isLoading}
      />
    );
  }
  if (report.isLoading) return <ReportSkeleton />;
  if (report.isError || !report.data) {
    return (
      <section className="report-load-error" aria-labelledby="report-error-title">
        <FileBarChart size={28} />
        <h1 id="report-error-title">报告暂时不可用</h1>
        <p>评估可能尚未开始，或本地服务暂时无法读取这份报告。</p>
        <Link to="/reports">返回报告列表</Link>
      </section>
    );
  }
  if (report.data.status !== "completed") {
    return (
      <ReportProgress
        status={report.data.status}
        job={report.data.job}
        retrying={retry.isPending}
        onRetry={() => retry.mutate()}
      />
    );
  }
  return <CompletedReport report={report.data} />;
}

function ReportIndex({
  isLoading,
  isError,
  reports,
  practiceTasks,
  practiceTasksLoading,
}: {
  isLoading: boolean;
  isError: boolean;
  reports: Awaited<ReturnType<typeof reportApi.list>>;
  practiceTasks: PracticeTask[];
  practiceTasksLoading: boolean;
}) {
  const activeTasks = practiceTasks.filter((task) => task.status === "pending" || task.status === "in_progress");
  return (
    <section className="report-index" aria-labelledby="report-index-title">
      <header>
        <div>
          <h1 id="report-index-title">面试评估报告</h1>
          <p>每条结论都能回到本场原回答。摘要用于恢复对话，不参与评分。</p>
        </div>
        <ShieldCheck size={30} aria-hidden="true" />
      </header>
      {isLoading && (
        <div className="report-index-loading" role="status">
          <RefreshCw className="spin" size={24} aria-hidden="true" />
          <p>正在读取评估报告…</p>
          <div className="report-index-loading-lines" aria-hidden="true">
            <span /><span /><span />
          </div>
        </div>
      )}
      {isError && <p className="report-index-error" role="alert">报告列表暂时无法读取，请确认本地服务已启动后重试。</p>}
      {!practiceTasksLoading && activeTasks.length > 0 && (
        <section className="p0-practice-queue" aria-labelledby="practice-queue-title">
          <header>
            <div>
              <span>个人训练队列</span>
              <h2 id="practice-queue-title">待练任务</h2>
            </div>
            <span className="p0-queue-count">{activeTasks.length}</span>
          </header>
          <div className="p0-practice-queue-list">
            {activeTasks.slice(0, 4).map((task) => (
              <article key={task.id}>
                <div>
                  <span className={`p0-task-status ${task.status}`}>{practiceStatusLabels[task.status]}</span>
                  <h3>{task.title}</h3>
                  <p>{task.success_criteria}</p>
                </div>
                <Link to={`/interviews/setup?task=${encodeURIComponent(task.id)}`}>开始 10 分钟专项 <ArrowRight size={15} /></Link>
              </article>
            ))}
          </div>
        </section>
      )}
      {!isLoading && !isError && reports.length === 0 && (
        <EmptyState
          className="report-index-empty"
          icon={FileBarChart}
          title="还没有评估报告"
          description="完成一次模拟面试后，系统会根据你的回答生成可回溯的评估结果，并在这里展示。"
          action={<Link to="/interviews">开始模拟面试 <ArrowRight size={15} /></Link>}
        />
      )}
      {!isLoading && !isError && reports.length > 0 && (
        <div className="report-register" aria-label="评估记录">
          <div className="report-register-header" aria-hidden="true">
            <span>公司与岗位</span>
            <span>面试轮次</span>
            <span>评估摘要</span>
            <span>状态</span>
            <span>更新时间</span>
            <span />
          </div>
          {reports.map((item) => (
            <Link to={`/reports/${item.report_id}`} className="report-register-row" key={item.report_id}>
              <div className="report-register-identity">
                <strong>{item.company_name}</strong>
                <span>{item.role_name}</span>
              </div>
              <span>{item.round_name}</span>
              <div className="report-register-summary">
                <strong>{anchorLabels[item.overall_anchor]}</strong>
                <span>{item.overview || "报告正在生成，结论稍后可见"}</span>
              </div>
              <span className={`report-status ${item.status}`}>{reportStatusLabels[item.status]}</span>
              <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString("zh-CN")}</time>
              <ChevronRight size={17} aria-hidden="true" />
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function ReportProgress({
  status,
  job,
  retrying,
  onRetry,
}: {
  status: "pending" | "running" | "failed";
  job: EvaluationJob | null;
  retrying: boolean;
  onRetry: () => void;
}) {
  const failed = status === "failed";
  return (
    <section className={`report-progress ${failed ? "failed" : ""}`} aria-labelledby="progress-title">
      {failed ? <AlertTriangle size={28} /> : <RefreshCw className="spin" size={28} />}
      <span>{failed ? "评估已暂停" : "正在生成评估报告"}</span>
      <h1 id="progress-title">{failed ? "评估未能完成" : phaseLabel(job)}</h1>
      <p>
        {failed
          ? job?.error_message || "会话和原始回答都已保留，可以安全重新评估。"
          : "后台正在进行结构化评估。页面会自动更新，不会显示模型的内部推理文本。"}
      </p>
      {!failed && (
        <div className="report-progress-track" aria-label={`评估进度 ${Math.round((job?.progress ?? 0) * 100)}%`}>
          <span style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} />
        </div>
      )}
      {failed && (
        <button type="button" disabled={retrying} onClick={onRetry}>
          <RefreshCw size={15} /> {retrying ? "正在重启" : "重新评估"}
        </button>
      )}
      <Link to="/reports"><ArrowLeft size={15} /> 返回报告列表</Link>
    </section>
  );
}

function CompletedReport({ report }: { report: EvaluationReport }) {
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceReference | null>(null);
  const [selectedActionIndices, setSelectedActionIndices] = useState<number[]>([]);
  const queryClient = useQueryClient();
  const practiceTasks = useQuery({
    queryKey: ["practice-tasks"],
    queryFn: () => reportApi.listPracticeTasks(),
    enabled: typeof reportApi.listPracticeTasks === "function",
    retry: false,
  });
  const reportTasks = (practiceTasks.data ?? []).filter((task) => task.report_id === report.id);
  const createPracticeTasks = useMutation({
    mutationFn: () => reportApi.createPracticeTasks(report.id, selectedActionIndices),
    onSuccess: async () => {
      setSelectedActionIndices([]);
      await queryClient.invalidateQueries({ queryKey: ["practice-tasks"] });
    },
  });
  const includeInTrends = useMutation({
    mutationFn: () => reportApi.updateTrendInclusion(report.session_id, true),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["report", report.id] });
      await queryClient.invalidateQueries({ queryKey: ["reports"] });
    },
  });
  const messagesById = useMemo(
    () => new Map(report.evidence_messages.map((message) => [message.id, message])),
    [report.evidence_messages],
  );
  const trendVisible = (report.trend_comparison.comparable_session_count ?? 0) >= 2;
  const isShortSession = report.session_kind === "quick_trial" || report.session_kind === "targeted_practice";
  const sessionTask = reportTasks.find((task) => task.last_session_id === report.session_id);

  const toggleAction = (actionIndex: number) => {
    setSelectedActionIndices((current) => (
      current.includes(actionIndex)
        ? current.filter((item) => item !== actionIndex)
        : [...current, actionIndex]
    ));
  };

  return (
    <article className="interview-report" aria-labelledby="report-title">
      <header className="report-hero">
        <div>
          <Link to="/reports"><ArrowLeft size={14} /> 全部报告</Link>
          <span>可回溯的面试复盘</span>
          <h1 id="report-title">本场能力结论</h1>
          <p>{report.overview}</p>
        </div>
        <div className="report-verdict">
          <small>整体结论</small>
          <strong>{anchorLabels[report.overall_anchor]}</strong>
          <span>{report.questions.length} 道题 · {report.evidence_messages.length} 条原始证据</span>
        </div>
      </header>

      {isShortSession && report.include_in_trends === false && (
        <section className="p0-trend-inclusion" aria-labelledby="trend-inclusion-title">
          <Target size={18} aria-hidden="true" />
          <div>
            <h2 id="trend-inclusion-title">这是一场{report.session_kind === "quick_trial" ? "试跑" : "专项训练"}</h2>
            <p>默认不计入长期趋势，避免短场练习放大或扭曲你的正式成长记录。</p>
          </div>
          <button
            type="button"
            disabled={includeInTrends.isPending}
            onClick={() => includeInTrends.mutate()}
          >
            {includeInTrends.isPending ? "正在纳入" : "纳入正式趋势"}
          </button>
        </section>
      )}

      {isShortSession && report.include_in_trends === true && (
        <section className="p0-trend-inclusion included" aria-label="趋势记录状态">
          <CheckCircle2 size={18} aria-hidden="true" />
          <p>已由你手动纳入正式趋势。</p>
        </section>
      )}

      {sessionTask && <TrainingTaskOutcome task={sessionTask} />}

      <StyleProfileBoundary styleProfile={report.style_profile} />

      <div className="report-layout">
        <aside className="report-outline" aria-label="报告目录">
          <span>报告目录</span>
          <a href="#report-overview">结论概览</a>
          {report.questions.map((question) => (
            <a href={`#question-${question.id}`} key={question.id}>
              <small>{String(question.question_sequence).padStart(2, "0")}</small>
              <span>{question.question_prompt}</span>
            </a>
          ))}
          <a href="#practice-plan">专项练习</a>
        </aside>

        <main className="report-document">
          <section id="report-overview" className="report-overview">
            <div className="report-overview-column strengths">
              <h2><CheckCircle2 size={18} /> 已形成的能力</h2>
              <ul>{report.strengths.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
            <div className="report-overview-column gaps">
              <h2><Target size={18} /> 下一步缺口</h2>
              <ul>{report.gaps.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          </section>

          <section className="question-review" aria-labelledby="question-review-title">
            <header>
              <span>逐题复盘</span>
              <h2 id="question-review-title">逐题证据时间线</h2>
              <p>结论按冻结计划切分；每个引用都只指向本场已确认的原回答。</p>
            </header>
            {report.questions.map((question) => (
              <article id={`question-${question.id}`} className="question-evaluation" key={question.id}>
                <div className="question-number">{String(question.question_sequence).padStart(2, "0")}</div>
                <div className="question-evaluation-body">
                  <div className="question-evaluation-heading">
                    <h3>{question.question_prompt}</h3>
                    <span className={`anchor-badge ${question.anchor}`}>{anchorLabels[question.anchor]}</span>
                  </div>
                  <p>{question.summary}</p>
                  <div className="evidence-links">
                    {question.evidence.map((evidence) => (
                      <button
                        type="button"
                        key={`${question.id}-${evidence.message_id}`}
                        onClick={() => setSelectedEvidence(evidence)}
                      >
                        <Quote size={13} /> 定位原回答 #{messagesById.get(evidence.message_id)?.sequence ?? "—"}
                      </button>
                    ))}
                    {question.evidence.length === 0 && <span>本题没有足够原始证据</span>}
                  </div>
                  <div className="question-findings">
                    <div><strong>缺口</strong><ul>{question.gaps.map((item) => <li key={item}>{item}</li>)}</ul></div>
                    <div><strong>动作</strong><ul>{question.actions.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  </div>
                </div>
              </article>
            ))}
          </section>

          <section id="practice-plan" className="practice-plan">
            <header>
              <ListChecks size={19} />
              <div>
                <span>专项练习</span>
                <h2>专项练习计划</h2>
                <p>先选择你愿意加入的建议；确认后才会写入你的私人训练队列。</p>
              </div>
            </header>
            <ol>
              {report.action_plan.map((action, actionIndex) => {
                const queuedTask = reportTasks.find((task) => task.action_index === actionIndex && task.status !== "dismissed");
                const selected = selectedActionIndices.includes(actionIndex);
                return (
                <li className={queuedTask ? "p0-action-already-queued" : ""} key={action.title}>
                  <span>P{action.priority}</span>
                  <div>
                    <h3>{action.title}</h3>
                    <p>{action.instruction}</p>
                    <small>完成标准：{action.success_criteria}</small>
                  </div>
                  {queuedTask ? (
                    <span className={`p0-task-status ${queuedTask.status}`}>{practiceStatusLabels[queuedTask.status]}</span>
                  ) : (
                    <label className="p0-action-select">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggleAction(actionIndex)}
                      />
                      <span>加入队列</span>
                    </label>
                  )}
                </li>
                );
              })}
            </ol>
            {report.action_plan.length > 0 && (
              <footer className="p0-practice-confirmation">
                <p>加入后会以 10 分钟专项模拟承接训练目标，不会自动写入长期记忆。</p>
                <button
                  type="button"
                  disabled={selectedActionIndices.length === 0 || createPracticeTasks.isPending}
                  onClick={() => createPracticeTasks.mutate()}
                >
                  {createPracticeTasks.isPending ? "正在加入" : `加入训练队列${selectedActionIndices.length ? `（${selectedActionIndices.length}）` : ""}`}
                </button>
              </footer>
            )}
            {createPracticeTasks.isError && (
              <p className="p0-practice-error" role="alert">暂时无法加入训练队列，请确认本地服务已更新后重试。</p>
            )}
          </section>
        </main>

        <aside className="report-analysis">
          <section className="dimension-matrix" aria-labelledby="dimension-title">
            <header><span>能力概览</span><h2 id="dimension-title">能力维度</h2></header>
            {report.dimensions.map((dimension) => (
              <div className="dimension-row" key={dimension.id}>
                <div>
                  <strong>{dimensionLabels[dimension.dimension] ?? dimension.dimension}</strong>
                  <span className={`anchor-dot ${dimension.anchor}`} aria-label={anchorLabels[dimension.anchor]} />
                </div>
                <p>{dimension.gaps[0] || dimension.action}</p>
                <small>证据置信 {Math.round(dimension.confidence * 100)}%</small>
              </div>
            ))}
          </section>
          {trendVisible && (
            <section className="report-trend">
              <span>可比较场次</span>
              <h2>跨场趋势</h2>
              <p>{report.trend_comparison.note}</p>
              <small>{report.trend_comparison.comparable_session_count} 场可比面试</small>
            </section>
          )}
          <CoachPanel reportId={report.id} questions={report.questions} />
        </aside>
      </div>

      <EvidenceDrawer
        evidence={selectedEvidence}
        message={selectedEvidence ? messagesById.get(selectedEvidence.message_id) ?? null : null}
        onClose={() => setSelectedEvidence(null)}
      />
    </article>
  );
}

function StyleProfileBoundary({
  styleProfile,
}: {
  styleProfile: EvaluationReport["style_profile"];
}) {
  const profile = styleProfile ?? {
    snapshot_available: false,
    trust_status: "template" as const,
    version: null,
    evidence_count: 0,
    latest_evidence_at: null,
    source_summaries: [],
  };
  const trustLabel = styleProfileTrustLabels[profile.trust_status];

  return (
    <section
      className={`p0-style-profile-boundary ${profile.snapshot_available ? "" : "missing"}`}
      aria-labelledby="style-profile-boundary-title"
    >
      <ShieldCheck size={18} aria-hidden="true" />
      <div>
        <div className="p0-style-profile-heading">
          <h2 id="style-profile-boundary-title">本场公司画像边界</h2>
          <span>{trustLabel}</span>
        </div>
        {profile.snapshot_available ? (
          <p>
            {profile.version ? `风格包 v${profile.version}` : "风格包版本未记录"}
            {` · ${profile.evidence_count} 条证据`}
            {profile.source_summaries.length > 0
              ? ` · 已冻结 ${profile.source_summaries.length} 条来源摘要`
              : ""}
          </p>
        ) : (
          <p>这份报告创建于画像快照启用前，无法还原本场的版本与证据。</p>
        )}
        <small>画像仅用于模拟提问节奏与追问范围，不代表公司官方面试标准、招聘事实或录用判断。</small>
      </div>
    </section>
  );
}

function TrainingTaskOutcome({ task }: { task: PracticeTask }) {
  const queryClient = useQueryClient();
  const updateTask = useMutation({
    mutationFn: (status: PracticeTask["status"]) => reportApi.updatePracticeTask(task.id, status),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["practice-tasks"] });
    },
  });

  if (task.status === "completed") {
    return (
      <section className="p0-training-outcome completed" aria-label="训练任务已完成">
        <CheckCircle2 size={18} aria-hidden="true" />
        <p>此专项训练已标记完成；它仍只会保留为你的私有训练记录。</p>
      </section>
    );
  }

  return (
    <section className="p0-training-outcome" aria-labelledby="training-outcome-title">
      <ListChecks size={18} aria-hidden="true" />
      <div>
        <h2 id="training-outcome-title">完成这次专项后，要如何处理任务？</h2>
        <p>{task.title} · {task.success_criteria}</p>
      </div>
      <div>
        <button type="button" disabled={updateTask.isPending} onClick={() => updateTask.mutate("pending")}>保留待练</button>
        <button type="button" disabled={updateTask.isPending} onClick={() => updateTask.mutate("completed")}>标记完成</button>
      </div>
    </section>
  );
}

function ReportSkeleton() {
  return (
    <section className="report-skeleton" role="status" aria-label="正在加载评估报告">
      <p>正在读取评估报告…</p>
      <div /><div /><div />
    </section>
  );
}
