import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Database,
  FileText,
  Gauge,
  LoaderCircle,
  ShieldCheck,
  Sparkles,
  Target,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { knowledgeApi } from "../../knowledge/api";
import { companyApi } from "../companies/api";
import { planningApi } from "./api";
import { liveInterviewApi } from "../live/api";
import type { InterviewPlan, PlanDraft, PlanJob } from "./types";

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

export function InterviewSetupPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const companyId = searchParams.get("company") ?? "";
  const roundId = searchParams.get("round") ?? "";
  const companies = useQuery({ queryKey: ["companies"], queryFn: companyApi.list });
  const banks = useQuery({ queryKey: ["question-banks"], queryFn: knowledgeApi.listBanks });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: knowledgeApi.listResumes });
  const company = companies.data?.find((item) => item.id === companyId);
  const round = company?.latest_style_pack?.rounds.find((item) => item.id === roundId);
  const eventSourceRef = useRef<EventSource | null>(null);
  const [plan, setPlan] = useState<InterviewPlan>();
  const [job, setJob] = useState<PlanJob>();
  const [form, setForm] = useState<Omit<PlanDraft, "company_id" | "round_profile_id">>({
    role_name: "llm_application_engineer",
    duration_minutes: 45,
    target_question_count: 6,
    question_bank_ids: [],
    resume_id: null,
    source_weights: { manual: 0.4, resume: 0.3, generated: 0.3 },
    preferences: { input_mode: "text" },
  });

  useEffect(() => {
    if (round) {
      setForm((current) => ({ ...current, duration_minutes: round.duration_minutes }));
    }
  }, [round]);

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

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!company || !round) return;
    generate.mutate({ ...form, company_id: company.id, round_profile_id: round.id });
  };

  if (companies.isLoading) {
    return <div className="setup-loading"><LoaderCircle className="spin" /> 正在读取面试配置</div>;
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
    <section className="interview-setup" aria-labelledby="setup-title">
      <header className="setup-header">
        <button className="text-button" type="button" onClick={() => navigate("/interviews")}>
          <ArrowLeft size={15} /> 返回公司选择
        </button>
        <div>
          <span>INTERVIEW SETUP / 02</span>
          <h1 id="setup-title">配置本场模拟</h1>
          <p>{company.name} · {round.name} · {company.latest_style_pack?.evidence_label}</p>
        </div>
        <div className="setup-trust">
          <ShieldCheck size={18} />
          <span>计划只使用你明确选择的资料</span>
        </div>
      </header>

      <form className="setup-grid" onSubmit={submit}>
        <main className="setup-form">
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
                    onChange={(event) => setForm({ ...form, duration_minutes: Number(event.target.value) })}
                  >
                    {[30, 45, 60, 90].map((minutes) => <option key={minutes} value={minutes}>{minutes} 分钟</option>)}
                  </select>
                </label>
                <label>
                  目标题量
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={form.target_question_count}
                    onChange={(event) => setForm({ ...form, target_question_count: Number(event.target.value) })}
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
            <span>EXPLAINABLE PLAN</span>
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
            <span><strong>{company.name} · {round.name}</strong><small>{form.duration_minutes} 分钟 / {form.target_question_count} 题</small></span>
          </div>
          <button className="secondary-button" type="button" onClick={() => navigate("/questions")}>管理资料</button>
          {!isReady ? (
            <button className="primary-button" type="submit" disabled={isPlanning || generate.isPending}>
              {isPlanning ? "正在生成计划" : "生成面试计划"}
            </button>
          ) : (
            <button
              className="primary-button"
              type="button"
              disabled={createSession.isPending}
              onClick={() => plan && createSession.mutate(plan.id)}
            >
              {createSession.isPending ? "正在创建房间" : "开始模拟面试"}
            </button>
          )}
        </footer>
      </form>
    </section>
  );
}
