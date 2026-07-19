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

import { reportApi } from "../features/reports/api";
import { CoachPanel } from "../features/reports/CoachPanel";
import { EvidenceDrawer } from "../features/reports/EvidenceDrawer";
import type {
  EvaluationAnchor,
  EvaluationReport,
  EvaluationJob,
  EvidenceReference,
} from "../features/reports/types";

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
    return <ReportIndex isLoading={list.isLoading} isError={list.isError} reports={list.data ?? []} />;
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
}: {
  isLoading: boolean;
  isError: boolean;
  reports: Awaited<ReturnType<typeof reportApi.list>>;
}) {
  return (
    <section className="report-index" aria-labelledby="report-index-title">
      <header>
        <div>
          <span>EVIDENCE REPORT REGISTER</span>
          <h1 id="report-index-title">面试评估报告</h1>
          <p>每条结论都能回到本场原回答。摘要用于恢复对话，不参与评分。</p>
        </div>
        <ShieldCheck size={30} aria-hidden="true" />
      </header>
      {isLoading && <div className="report-index-skeleton" aria-label="正在加载报告" />}
      {isError && <p className="report-index-error">报告列表加载失败，请确认后端服务已启动。</p>}
      {!isLoading && !isError && reports.length === 0 && (
        <div className="report-index-empty">
          <FileBarChart size={27} />
          <h2>还没有评估报告</h2>
          <p>完成一场模拟面试后，Evaluator 会在后台生成可追溯报告。</p>
          <Link to="/interviews">开始模拟面试 <ArrowRight size={15} /></Link>
        </div>
      )}
      <div className="report-register">
        {reports.map((item, index) => (
          <Link to={`/reports/${item.report_id}`} className="report-register-row" key={item.report_id}>
            <span className="report-register-index">{String(index + 1).padStart(2, "0")}</span>
            <div>
              <div className="report-register-meta">
                <span>{item.company_name}</span>
                <span>{item.round_name}</span>
                <span>{item.role_name}</span>
              </div>
              <h2>{item.overview || "报告正在生成，结论稍后可见"}</h2>
              <small>{new Date(item.updated_at).toLocaleString("zh-CN")}</small>
            </div>
            <span className={`anchor-badge ${item.overall_anchor}`}>
              {item.status === "completed" ? anchorLabels[item.overall_anchor] : "生成中"}
            </span>
            <ChevronRight size={17} aria-hidden="true" />
          </Link>
        ))}
      </div>
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
      <span>{failed ? "EVALUATION PAUSED" : "EVALUATION IN PROGRESS"}</span>
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
  const messagesById = useMemo(
    () => new Map(report.evidence_messages.map((message) => [message.id, message])),
    [report.evidence_messages],
  );
  const trendVisible = (report.trend_comparison.comparable_session_count ?? 0) >= 2;

  return (
    <article className="interview-report" aria-labelledby="report-title">
      <header className="report-hero">
        <div>
          <Link to="/reports"><ArrowLeft size={14} /> 全部报告</Link>
          <span>EVIDENCE-BASED INTERVIEW REVIEW</span>
          <h1 id="report-title">本场能力结论</h1>
          <p>{report.overview}</p>
        </div>
        <div className="report-verdict">
          <small>OVERALL ANCHOR</small>
          <strong>{anchorLabels[report.overall_anchor]}</strong>
          <span>{report.questions.length} 道题 · {report.evidence_messages.length} 条原始证据</span>
        </div>
      </header>

      <div className="report-layout">
        <aside className="report-outline" aria-label="报告目录">
          <span>REPORT INDEX</span>
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
              <span>QUESTION-BY-QUESTION</span>
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
              <div><span>NEXT PRACTICE</span><h2>专项练习计划</h2></div>
            </header>
            <ol>
              {report.action_plan.map((action) => (
                <li key={action.title}>
                  <span>P{action.priority}</span>
                  <div>
                    <h3>{action.title}</h3>
                    <p>{action.instruction}</p>
                    <small>完成标准：{action.success_criteria}</small>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </main>

        <aside className="report-analysis">
          <section className="dimension-matrix" aria-labelledby="dimension-title">
            <header><span>CAPABILITY MATRIX</span><h2 id="dimension-title">能力维度</h2></header>
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
              <span>COMPARABLE SESSIONS</span>
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

function ReportSkeleton() {
  return (
    <section className="report-skeleton" aria-label="正在加载评估报告">
      <div /><div /><div />
    </section>
  );
}
