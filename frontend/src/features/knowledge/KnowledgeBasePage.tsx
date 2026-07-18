import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  BookOpen,
  Check,
  FileText,
  Plus,
  Search,
  Upload,
  X,
} from "lucide-react";
import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";

import { apiUrl } from "../../lib/api/client";
import { knowledgeApi } from "./api";
import type { BackgroundJob, Difficulty, QuestionDraft, QuestionType } from "./types";

const typeLabels: Record<QuestionType, string> = {
  open_ended: "开放问答",
  project_deep_dive: "项目深挖",
  system_design: "系统设计",
  code_discussion: "代码讨论",
  scenario: "场景问题",
};

const difficultyLabels: Record<Difficulty, string> = {
  foundational: "基础",
  intermediate: "进阶",
  advanced: "高级",
  expert: "专家",
};

const resumeStatusLabels = {
  pending: "等待解析",
  parsing: "正在解析",
  ready: "解析完成",
  failed: "解析失败",
};

export function KnowledgeBasePage() {
  const queryClient = useQueryClient();
  const eventSources = useRef(new Map<string, EventSource>());
  const [activeTab, setActiveTab] = useState<"questions" | "resumes">("questions");
  const [selectedBankId, setSelectedBankId] = useState<string>();
  const [search, setSearch] = useState("");
  const [questionFormOpen, setQuestionFormOpen] = useState(false);
  const [bankFormOpen, setBankFormOpen] = useState(false);
  const [bankName, setBankName] = useState("");
  const [questionDraft, setQuestionDraft] = useState<Omit<QuestionDraft, "bank_id">>({
    prompt: "",
    question_type: "project_deep_dive",
    difficulty: "intermediate",
    status: "active",
    tag_names: [],
  });
  const [tagInput, setTagInput] = useState("");

  const banks = useQuery({ queryKey: ["question-banks"], queryFn: knowledgeApi.listBanks });
  const activeBankId = selectedBankId ?? banks.data?.[0]?.id;
  const questions = useQuery({
    queryKey: ["questions", activeBankId, search],
    queryFn: () => knowledgeApi.listQuestions(activeBankId, search),
  });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: knowledgeApi.listResumes });

  useEffect(() => {
    const sources = eventSources.current;
    return () => {
      sources.forEach((source) => source.close());
      sources.clear();
    };
  }, []);

  const watchJob = (job: BackgroundJob) => {
    if (typeof EventSource === "undefined" || eventSources.current.has(job.id)) return;
    const source = new EventSource(apiUrl(`/jobs/${job.id}/events`));
    eventSources.current.set(job.id, source);
    source.addEventListener("job", (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      const update = JSON.parse(event.data) as BackgroundJob;
      void queryClient.invalidateQueries({ queryKey: ["resumes"] });
      if (["completed", "failed", "cancelled"].includes(update.status)) {
        source.close();
        eventSources.current.delete(job.id);
      }
    });
    source.onerror = () => {
      void queryClient.invalidateQueries({ queryKey: ["resumes"] });
    };
  };

  const createBank = useMutation({
    mutationFn: knowledgeApi.createBank,
    onSuccess: async (bank) => {
      setSelectedBankId(bank.id);
      setBankName("");
      setBankFormOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["question-banks"] });
    },
  });
  const createQuestion = useMutation({
    mutationFn: knowledgeApi.createQuestion,
    onSuccess: async () => {
      setQuestionDraft({
        prompt: "",
        question_type: "project_deep_dive",
        difficulty: "intermediate",
        status: "active",
        tag_names: [],
      });
      setTagInput("");
      setQuestionFormOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["questions"] }),
        queryClient.invalidateQueries({ queryKey: ["question-banks"] }),
      ]);
    },
  });
  const archiveQuestion = useMutation({
    mutationFn: knowledgeApi.archiveQuestion,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["questions"] }),
        queryClient.invalidateQueries({ queryKey: ["question-banks"] }),
      ]);
    },
  });
  const uploadResume = useMutation({
    mutationFn: knowledgeApi.uploadResume,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["resumes"] });
      if (result.job && !["completed", "failed", "cancelled"].includes(result.job.status)) {
        watchJob(result.job);
      }
    },
  });

  const submitQuestion = (event: FormEvent) => {
    event.preventDefault();
    if (!activeBankId) return;
    createQuestion.mutate({
      ...questionDraft,
      bank_id: activeBankId,
      tag_names: tagInput.split(",").map((tag) => tag.trim()).filter(Boolean),
    });
  };

  const submitBank = (event: FormEvent) => {
    event.preventDefault();
    createBank.mutate(bankName);
  };

  const selectResume = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) uploadResume.mutate(file);
    event.target.value = "";
  };

  const hasLoadError = banks.isError || questions.isError || resumes.isError;

  return (
    <section className="knowledge-console" aria-labelledby="knowledge-title">
      <header className="knowledge-heading">
        <div>
          <span>PERSONAL KNOWLEDGE</span>
          <h1 id="knowledge-title">面试知识库</h1>
          <p>题目与简历由你管理。面试规划只检索当前公司、轮次和岗位真正需要的内容。</p>
        </div>
        <nav className="knowledge-tabs" aria-label="知识库类型">
          <button
            className={activeTab === "questions" ? "active" : ""}
            type="button"
            onClick={() => setActiveTab("questions")}
          >
            <BookOpen size={16} aria-hidden="true" /> 题库
          </button>
          <button
            className={activeTab === "resumes" ? "active" : ""}
            type="button"
            onClick={() => setActiveTab("resumes")}
          >
            <FileText size={16} aria-hidden="true" /> 简历
          </button>
        </nav>
      </header>

      {hasLoadError && <p className="console-error">知识库加载失败，请确认后端服务与数据库已启动。</p>}

      {activeTab === "questions" ? (
        <div className="question-layout">
          <aside className="bank-sidebar">
            <div className="section-heading">
              <h2>我的题库</h2>
              <button className="icon-button" type="button" aria-label="新建题库" onClick={() => setBankFormOpen(true)}>
                <Plus size={17} />
              </button>
            </div>
            {bankFormOpen && (
              <form className="inline-create" onSubmit={submitBank}>
                <label>
                  题库名称
                  <input required autoFocus value={bankName} onChange={(event) => setBankName(event.target.value)} />
                </label>
                <div>
                  <button className="secondary-button" type="button" onClick={() => setBankFormOpen(false)}>取消</button>
                  <button className="primary-button" type="submit" disabled={createBank.isPending}>创建</button>
                </div>
              </form>
            )}
            <div className="bank-list">
              {banks.data?.map((bank) => (
                <button
                  key={bank.id}
                  className={bank.id === activeBankId ? "active" : ""}
                  type="button"
                  onClick={() => setSelectedBankId(bank.id)}
                >
                  <span><strong>{bank.name}</strong><small>{bank.question_count} 道题</small></span>
                  {bank.id === activeBankId && <Check size={15} aria-hidden="true" />}
                </button>
              ))}
            </div>
            {!banks.isLoading && banks.data?.length === 0 && (
              <div className="sidebar-empty">
                <p>还没有题库。</p>
                <button type="button" onClick={() => setBankFormOpen(true)}>创建第一个题库</button>
              </div>
            )}
          </aside>

          <main className="question-panel">
            <div className="question-toolbar">
              <label className="search-field">
                <span className="sr-only">搜索题目</span>
                <Search size={16} aria-hidden="true" />
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索题目、技术关键词" />
              </label>
              <button className="primary-button" type="button" disabled={!activeBankId} onClick={() => setQuestionFormOpen(true)}>
                <Plus size={16} aria-hidden="true" /> 添加题目
              </button>
            </div>

            {questionFormOpen && (
              <form className="question-form" onSubmit={submitQuestion}>
                <div className="form-title">
                  <h2>手工添加题目</h2>
                  <button className="icon-button" type="button" aria-label="关闭题目表单" onClick={() => setQuestionFormOpen(false)}><X size={17} /></button>
                </div>
                <label className="prompt-field">
                  题目内容
                  <textarea required value={questionDraft.prompt} onChange={(event) => setQuestionDraft({ ...questionDraft, prompt: event.target.value })} placeholder="例如：请说明你会如何设计长对话的上下文压缩策略。" />
                </label>
                <div className="form-columns">
                  <label>
                    题型
                    <select value={questionDraft.question_type} onChange={(event) => setQuestionDraft({ ...questionDraft, question_type: event.target.value as QuestionType })}>
                      {Object.entries(typeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </label>
                  <label>
                    难度
                    <select value={questionDraft.difficulty} onChange={(event) => setQuestionDraft({ ...questionDraft, difficulty: event.target.value as Difficulty })}>
                      {Object.entries(difficultyLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </label>
                  <label>
                    标签
                    <input value={tagInput} onChange={(event) => setTagInput(event.target.value)} placeholder="RAG, Agent, 项目深挖" />
                  </label>
                </div>
                {createQuestion.isError && <p className="inline-error">保存失败，题库中可能已经存在相同题目。</p>}
                <div className="form-actions">
                  <button className="secondary-button" type="button" onClick={() => setQuestionFormOpen(false)}>取消</button>
                  <button className="primary-button" type="submit" disabled={createQuestion.isPending}>保存题目</button>
                </div>
              </form>
            )}

            <div className="question-list" aria-live="polite">
              {questions.isLoading && <div className="list-skeleton" aria-label="正在加载题目" />}
              {questions.data?.data.map((question) => (
                <article className="question-row" key={question.id}>
                  <div className="question-main">
                    <div className="question-meta">
                      <span>{typeLabels[question.question_type]}</span>
                      <span>{difficultyLabels[question.difficulty]}</span>
                      <span>{question.status === "active" ? "已启用" : "草案"}</span>
                    </div>
                    <h3>{question.prompt}</h3>
                    <div className="tag-line">
                      {question.tags.map((tag) => <span key={tag.id}>{tag.name}</span>)}
                    </div>
                  </div>
                  <button className="row-icon-button" type="button" aria-label={`归档题目：${question.prompt}`} onClick={() => archiveQuestion.mutate(question.id)}>
                    <Archive size={16} aria-hidden="true" />
                  </button>
                </article>
              ))}
              {!questions.isLoading && questions.data?.count === 0 && (
                <div className="console-empty compact">
                  <BookOpen size={26} aria-hidden="true" />
                  <h3>{search ? "没有匹配的题目" : "这个题库还是空的"}</h3>
                  <p>{search ? "换一个关键词试试。" : "首版支持手工录入，链接导入会在后续版本接入。"}</p>
                </div>
              )}
            </div>
          </main>
        </div>
      ) : (
        <div className="resume-panel">
          <div className="resume-toolbar">
            <div>
              <h2>专项面试简历</h2>
              <p>解析后只在需要时检索相关项目与技能，避免每轮重复发送整份简历。</p>
            </div>
            <label className={`primary-button upload-button ${uploadResume.isPending ? "disabled" : ""}`}>
              <Upload size={16} aria-hidden="true" />
              {uploadResume.isPending ? "正在上传" : "上传简历"}
              <input
                type="file"
                accept=".pdf,.docx,.md,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown"
                disabled={uploadResume.isPending}
                onChange={selectResume}
              />
            </label>
          </div>
          <p className="upload-note">支持 PDF、DOCX、Markdown、TXT，最大 5 MB。本地 worker 负责后台解析。</p>
          {uploadResume.isError && <p className="inline-error">上传失败，请检查文件类型、编码与大小。</p>}
          <div className="resume-list">
            {resumes.data?.map((resume) => (
              <article className="resume-row" key={resume.id}>
                <span className="file-glyph"><FileText size={20} aria-hidden="true" /></span>
                <div>
                  <h3>{resume.filename}</h3>
                  <p>{resume.parse_status === "ready" ? `${resume.sections.length} 个区段 · ${resume.claims.length} 条可检索事实` : "解析完成后会生成结构化区段与事实"}</p>
                </div>
                <span className={`resume-status ${resume.parse_status}`}>{resumeStatusLabels[resume.parse_status]}</span>
              </article>
            ))}
            {!resumes.isLoading && resumes.data?.length === 0 && (
              <div className="console-empty">
                <Upload size={28} aria-hidden="true" />
                <h3>还没有上传简历</h3>
                <p>添加一份简历后，面试规划可以优先追问真实项目与技术取舍。</p>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
