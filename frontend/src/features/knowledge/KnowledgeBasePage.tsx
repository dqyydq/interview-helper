import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Pencil,
  FileText,
  ListFilter,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { apiUrl } from "../../lib/api/client";
import { knowledgeApi } from "./api";
import type {
  BackgroundJob,
  Difficulty,
  Question,
  QuestionDraft,
  QuestionSortField,
  QuestionStatus,
  QuestionType,
  QuestionUpdateDraft,
  SortOrder,
} from "./types";

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

const pageSizeOptions = [10, 20, 50];

interface QuestionFormState {
  prompt: string;
  question_type: QuestionType;
  difficulty: Difficulty;
  status: QuestionStatus;
  tagInput: string;
  referencePointsInput: string;
  followUpsInput: string;
  companiesInput: string;
  roundsInput: string;
  sourceNote: string;
  userNote: string;
}

type QuestionFormPayload = QuestionUpdateDraft;

const emptyQuestionForm = (): QuestionFormState => ({
  prompt: "",
  question_type: "project_deep_dive",
  difficulty: "intermediate",
  status: "active",
  tagInput: "",
  referencePointsInput: "",
  followUpsInput: "",
  companiesInput: "",
  roundsInput: "",
  sourceNote: "",
  userNote: "",
});

function toLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function toCommaList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function toOptionalText(value: string): string | null {
  const trimmed = value.trim();
  return trimmed || null;
}

function questionToForm(question: Question): QuestionFormState {
  return {
    prompt: question.prompt,
    question_type: question.question_type,
    difficulty: question.difficulty,
    status: question.status,
    tagInput: question.tags.map((tag) => tag.name).join(", "),
    referencePointsInput: question.reference_points.join("\n"),
    followUpsInput: question.follow_up_suggestions.join("\n"),
    companiesInput: question.applicable_companies.join(", "),
    roundsInput: question.applicable_rounds.join(", "),
    sourceNote: question.source_note ?? "",
    userNote: question.user_note ?? "",
  };
}

function questionFormPayload(form: QuestionFormState): QuestionFormPayload {
  return {
    prompt: form.prompt.trim(),
    question_type: form.question_type,
    difficulty: form.difficulty,
    status: form.status,
    reference_points: toLines(form.referencePointsInput),
    follow_up_suggestions: toLines(form.followUpsInput),
    applicable_companies: toCommaList(form.companiesInput),
    applicable_rounds: toCommaList(form.roundsInput),
    source_note: toOptionalText(form.sourceNote),
    user_note: toOptionalText(form.userNote),
    tag_names: toCommaList(form.tagInput),
  };
}

export function KnowledgeBasePage() {
  const queryClient = useQueryClient();
  const eventSources = useRef(new Map<string, EventSource>());
  const [activeTab, setActiveTab] = useState<"questions" | "resumes">("questions");
  const [selectedBankId, setSelectedBankId] = useState<string>();
  const [search, setSearch] = useState("");
  const [questionStatus, setQuestionStatus] = useState<QuestionStatus | "">("");
  const [questionType, setQuestionType] = useState<QuestionType | "">("");
  const [questionDifficulty, setQuestionDifficulty] = useState<Difficulty | "">("");
  const [tagFilter, setTagFilter] = useState("");
  const [sortBy, setSortBy] = useState<QuestionSortField>("updated_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(20);
  const [selectedQuestionIds, setSelectedQuestionIds] = useState<string[]>([]);
  const [questionFormOpen, setQuestionFormOpen] = useState(false);
  const [editingQuestion, setEditingQuestion] = useState<Question | null>(null);
  const [bankFormOpen, setBankFormOpen] = useState(false);
  const [bankName, setBankName] = useState("");
  const [questionDraft, setQuestionDraft] = useState<QuestionFormState>(emptyQuestionForm);
  const [variantPrompt, setVariantPrompt] = useState("");
  const [variantType, setVariantType] = useState("paraphrase");

  const banks = useQuery({ queryKey: ["question-banks"], queryFn: knowledgeApi.listBanks });
  const activeBankId = selectedBankId ?? banks.data?.[0]?.id;
  const questions = useQuery({
    queryKey: [
      "questions",
      activeBankId,
      search,
      questionStatus,
      questionType,
      questionDifficulty,
      tagFilter,
      sortBy,
      sortOrder,
      offset,
      limit,
    ],
    queryFn: () => knowledgeApi.listQuestions({
      bankId: activeBankId,
      search,
      status: questionStatus || undefined,
      questionType: questionType || undefined,
      difficulty: questionDifficulty || undefined,
      tag: tagFilter || undefined,
      sortBy,
      sortOrder,
      offset,
      limit,
    }),
    enabled: Boolean(activeBankId),
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
      setQuestionDraft(emptyQuestionForm());
      setQuestionFormOpen(false);
      setOffset(0);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["questions"] }),
        queryClient.invalidateQueries({ queryKey: ["question-banks"] }),
      ]);
    },
  });
  const updateQuestion = useMutation({
    mutationFn: ({ questionId, draft }: { questionId: string; draft: QuestionUpdateDraft }) =>
      knowledgeApi.updateQuestion(questionId, draft),
    onSuccess: async () => {
      setQuestionFormOpen(false);
      setEditingQuestion(null);
      setQuestionDraft(emptyQuestionForm());
      await queryClient.invalidateQueries({ queryKey: ["questions"] });
    },
  });
  const archiveQuestion = useMutation({
    mutationFn: knowledgeApi.archiveQuestion,
    onSuccess: async () => {
      setSelectedQuestionIds([]);
      setOffset(0);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["questions"] }),
        queryClient.invalidateQueries({ queryKey: ["question-banks"] }),
      ]);
    },
  });
  const bulkArchiveQuestions = useMutation({
    mutationFn: knowledgeApi.bulkArchiveQuestions,
    onSuccess: async () => {
      setSelectedQuestionIds([]);
      setOffset(0);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["questions"] }),
        queryClient.invalidateQueries({ queryKey: ["question-banks"] }),
      ]);
    },
  });
  const createQuestionVariant = useMutation({
    mutationFn: ({ questionId, prompt, variantKind }: { questionId: string; prompt: string; variantKind: string }) =>
      knowledgeApi.createQuestionVariant(questionId, prompt, variantKind),
    onSuccess: async (variant) => {
      setVariantPrompt("");
      setEditingQuestion((current) => current ? {
        ...current,
        variants: [...current.variants, variant],
      } : current);
      await queryClient.invalidateQueries({ queryKey: ["questions"] });
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
  const retryResumeParse = useMutation({
    mutationFn: knowledgeApi.retryResumeParse,
    onSuccess: async (job) => {
      await queryClient.invalidateQueries({ queryKey: ["resumes"] });
      if (!["completed", "failed", "cancelled"].includes(job.status)) {
        watchJob(job);
      }
    },
  });
  const deleteResume = useMutation({
    mutationFn: knowledgeApi.deleteResume,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["resumes"] });
    },
  });

  const submitQuestion = (event: FormEvent) => {
    event.preventDefault();
    const payload = questionFormPayload(questionDraft);
    if (editingQuestion) {
      updateQuestion.mutate({ questionId: editingQuestion.id, draft: payload });
      return;
    }
    if (!activeBankId) return;
    createQuestion.mutate({
      ...payload,
      bank_id: activeBankId,
      status: payload.status === "archived" ? "draft" : payload.status,
    } satisfies QuestionDraft);
  };

  const openQuestionCreate = () => {
    setEditingQuestion(null);
    setQuestionDraft(emptyQuestionForm());
    setVariantPrompt("");
    setQuestionFormOpen(true);
  };

  const openQuestionEdit = (question: Question) => {
    setEditingQuestion(question);
    setQuestionDraft(questionToForm(question));
    setVariantPrompt("");
    setQuestionFormOpen(true);
  };

  const closeQuestionForm = () => {
    setQuestionFormOpen(false);
    setEditingQuestion(null);
    setQuestionDraft(emptyQuestionForm());
    setVariantPrompt("");
  };

  const resetQuestionView = () => {
    setSearch("");
    setQuestionStatus("");
    setQuestionType("");
    setQuestionDifficulty("");
    setTagFilter("");
    setSortBy("updated_at");
    setSortOrder("desc");
    setOffset(0);
  };

  const changeQuestionView = (action: () => void) => {
    action();
    setOffset(0);
    setSelectedQuestionIds([]);
  };

  const toggleQuestionSelection = (questionId: string) => {
    setSelectedQuestionIds((current) => current.includes(questionId)
      ? current.filter((item) => item !== questionId)
      : [...current, questionId]);
  };

  const togglePageSelection = () => {
    const pageQuestionIds = questions.data?.data.map((question) => question.id) ?? [];
    const pageIsSelected = pageQuestionIds.length > 0
      && pageQuestionIds.every((questionId) => selectedQuestionIds.includes(questionId));
    setSelectedQuestionIds((current) => pageIsSelected
      ? current.filter((questionId) => !pageQuestionIds.includes(questionId))
      : [...new Set([...current, ...pageQuestionIds])]);
  };

  const archiveSelectedQuestions = () => {
    if (selectedQuestionIds.length === 0) return;
    if (window.confirm(`归档已选择的 ${selectedQuestionIds.length} 道题目？`)) {
      bulkArchiveQuestions.mutate(selectedQuestionIds);
    }
  };

  const addVariant = () => {
    const prompt = variantPrompt.trim();
    if (!editingQuestion || !prompt) return;
    createQuestionVariant.mutate({
      questionId: editingQuestion.id,
      prompt,
      variantKind: variantType.trim() || "paraphrase",
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
  const pageQuestions = questions.data?.data ?? [];
  const pageIsFullySelected = pageQuestions.length > 0
    && pageQuestions.every((question) => selectedQuestionIds.includes(question.id));
  const hasQuestionFilters = Boolean(
    search || questionStatus || questionType || questionDifficulty || tagFilter,
  );
  const canBulkArchive = questionStatus !== "archived";
  const pageStart = questions.data?.count ? offset + 1 : 0;
  const pageEnd = Math.min(offset + limit, questions.data?.count ?? 0);
  const hasPreviousPage = offset > 0;
  const hasNextPage = offset + limit < (questions.data?.count ?? 0);

  return (
    <section className="knowledge-console" aria-labelledby="knowledge-title">
      <header className="knowledge-heading">
        <div>
          <span>个人知识库</span>
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
                  onClick={() => {
                    setSelectedBankId(bank.id);
                    setOffset(0);
                    setSelectedQuestionIds([]);
                    closeQuestionForm();
                  }}
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
                <input
                  value={search}
                  onChange={(event) => changeQuestionView(() => setSearch(event.target.value))}
                  placeholder="搜索题目、技术关键词"
                />
              </label>
              <Link className="secondary-button" to="/questions/discover">
                <Search size={16} aria-hidden="true" /> 发现题目
              </Link>
              <button className="primary-button" type="button" disabled={!activeBankId} onClick={openQuestionCreate}>
                <Plus size={16} aria-hidden="true" /> 添加题目
              </button>
            </div>

            <section className="question-controls" aria-label="题库筛选与排序">
              <div className="question-controls-heading">
                <span><ListFilter size={15} aria-hidden="true" /> 筛选与排序</span>
                {hasQuestionFilters && (
                  <button className="text-button" type="button" onClick={resetQuestionView}>清除筛选</button>
                )}
              </div>
              <div className="question-control-fields">
                <label>
                  状态
                  <select value={questionStatus} onChange={(event) => changeQuestionView(() => setQuestionStatus(event.target.value as QuestionStatus | ""))}>
                    <option value="">全部状态</option>
                    <option value="active">已启用</option>
                    <option value="draft">草稿</option>
                    <option value="archived">已归档</option>
                  </select>
                </label>
                <label>
                  题型
                  <select value={questionType} onChange={(event) => changeQuestionView(() => setQuestionType(event.target.value as QuestionType | ""))}>
                    <option value="">全部题型</option>
                    {Object.entries(typeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label>
                  难度
                  <select value={questionDifficulty} onChange={(event) => changeQuestionView(() => setQuestionDifficulty(event.target.value as Difficulty | ""))}>
                    <option value="">全部难度</option>
                    {Object.entries(difficultyLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label>
                  标签筛选
                  <input value={tagFilter} onChange={(event) => changeQuestionView(() => setTagFilter(event.target.value))} placeholder="按标签筛选" />
                </label>
                <label>
                  排序
                  <select value={sortBy} onChange={(event) => changeQuestionView(() => setSortBy(event.target.value as QuestionSortField))}>
                    <option value="updated_at">最近更新</option>
                    <option value="created_at">创建时间</option>
                    <option value="difficulty">难度</option>
                    <option value="times_used">使用次数</option>
                  </select>
                </label>
                <label>
                  顺序
                  <select value={sortOrder} onChange={(event) => changeQuestionView(() => setSortOrder(event.target.value as SortOrder))}>
                    <option value="desc">从新到旧 / 高到低</option>
                    <option value="asc">从旧到新 / 低到高</option>
                  </select>
                </label>
              </div>
            </section>

            {questionFormOpen && (
              <form className="question-form" onSubmit={submitQuestion}>
                <div className="form-title">
                  <div>
                    <h2>{editingQuestion ? "编辑题目" : "手工添加题目"}</h2>
                    <p>{editingQuestion ? "更新后会立即用于后续题目检索。" : "仅支持手动录入；本版本不提供 URL 导入。"}</p>
                  </div>
                  <button className="icon-button" type="button" aria-label="关闭题目表单" onClick={closeQuestionForm}><X size={17} /></button>
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
                    状态
                    <select value={questionDraft.status} onChange={(event) => setQuestionDraft({ ...questionDraft, status: event.target.value as QuestionStatus })}>
                      <option value="active">已启用</option>
                      <option value="draft">草稿</option>
                      {editingQuestion && <option value="archived">已归档</option>}
                    </select>
                  </label>
                </div>
                <div className="form-columns">
                  <label>
                    标签
                    <input value={questionDraft.tagInput} onChange={(event) => setQuestionDraft({ ...questionDraft, tagInput: event.target.value })} placeholder="RAG, Agent, 项目深挖" />
                  </label>
                  <label>
                    适用公司
                    <input value={questionDraft.companiesInput} onChange={(event) => setQuestionDraft({ ...questionDraft, companiesInput: event.target.value })} placeholder="字节跳动, 自定义公司" />
                  </label>
                  <label>
                    适用轮次
                    <input value={questionDraft.roundsInput} onChange={(event) => setQuestionDraft({ ...questionDraft, roundsInput: event.target.value })} placeholder="二面, 技术深挖" />
                  </label>
                </div>
                <div className="question-form-details">
                  <label>
                    来源说明
                    <textarea value={questionDraft.sourceNote} onChange={(event) => setQuestionDraft({ ...questionDraft, sourceNote: event.target.value })} placeholder="记录题目出处或自己的整理背景，不会自动抓取链接。" />
                  </label>
                  <label>
                    参考要点
                    <textarea value={questionDraft.referencePointsInput} onChange={(event) => setQuestionDraft({ ...questionDraft, referencePointsInput: event.target.value })} placeholder="一行一条，例如：说明上下文窗口与预算策略。" />
                  </label>
                  <label>
                    建议追问
                    <textarea value={questionDraft.followUpsInput} onChange={(event) => setQuestionDraft({ ...questionDraft, followUpsInput: event.target.value })} placeholder="一行一条，例如：如果上下文继续增长如何处理？" />
                  </label>
                  <label>
                    个人备注
                    <textarea value={questionDraft.userNote} onChange={(event) => setQuestionDraft({ ...questionDraft, userNote: event.target.value })} placeholder="只供自己整理与复盘使用。" />
                  </label>
                </div>
                {editingQuestion && (
                  <section className="question-variants" aria-labelledby="question-variants-title">
                    <div className="question-variants-heading">
                      <h3 id="question-variants-title"><ClipboardList size={15} aria-hidden="true" /> 题目变体</h3>
                      <span>{editingQuestion.variants.length} 条</span>
                    </div>
                    {editingQuestion.variants.length > 0 && (
                      <ul>
                        {editingQuestion.variants.map((variant) => (
                          <li key={variant.id}><span>{variant.variant_type}</span>{variant.prompt}</li>
                        ))}
                      </ul>
                    )}
                    <div className="question-variant-create">
                      <input aria-label="变体内容" value={variantPrompt} onChange={(event) => setVariantPrompt(event.target.value)} placeholder="添加一个改写或变式问法" />
                      <input aria-label="变体类型" value={variantType} onChange={(event) => setVariantType(event.target.value)} placeholder="例如：paraphrase" />
                      <button className="secondary-button" type="button" disabled={!variantPrompt.trim() || createQuestionVariant.isPending} onClick={addVariant}>添加变体</button>
                    </div>
                  </section>
                )}
                {(createQuestion.isError || updateQuestion.isError || createQuestionVariant.isError) && <p className="inline-error">保存失败，请检查字段；同一题库内不允许存在完全相同的题目。</p>}
                <div className="form-actions">
                  <button className="secondary-button" type="button" onClick={closeQuestionForm}>取消</button>
                  <button className="primary-button" type="submit" disabled={createQuestion.isPending || updateQuestion.isPending}>
                    {editingQuestion ? "保存修改" : "保存题目"}
                  </button>
                </div>
              </form>
            )}

            <div className="question-list" aria-live="polite">
              <div className="question-list-header">
                <label className="question-select-all">
                  <input aria-label="选择当前页全部题目" type="checkbox" checked={pageIsFullySelected} disabled={pageQuestions.length === 0 || !canBulkArchive} onChange={togglePageSelection} />
                  <span>{questions.data ? `${pageStart}–${pageEnd} / ${questions.data.count} 道题目` : "题目列表"}</span>
                </label>
                {canBulkArchive && selectedQuestionIds.length > 0 && (
                  <button className="bulk-archive-button" type="button" disabled={bulkArchiveQuestions.isPending} onClick={archiveSelectedQuestions}>
                    <Archive size={15} aria-hidden="true" /> 归档已选 {selectedQuestionIds.length} 道
                  </button>
                )}
              </div>
              {questions.isLoading && <div className="list-skeleton" aria-label="正在加载题目" />}
              {pageQuestions.map((question) => (
                <article className="question-row" key={question.id}>
                  <label className="question-select">
                    <input
                      aria-label={`选择题目：${question.prompt}`}
                      type="checkbox"
                      checked={selectedQuestionIds.includes(question.id)}
                      disabled={!canBulkArchive}
                      onChange={() => toggleQuestionSelection(question.id)}
                    />
                  </label>
                  <div className="question-main">
                    <div className="question-meta">
                      <span>{typeLabels[question.question_type]}</span>
                      <span>{difficultyLabels[question.difficulty]}</span>
                      <span>{question.status === "active" ? "已启用" : question.status === "draft" ? "草稿" : "已归档"}</span>
                      <span>已使用 {question.times_used} 次</span>
                    </div>
                    <h3>{question.prompt}</h3>
                    <div className="tag-line">
                      {question.tags.map((tag) => <span key={tag.id}>{tag.name}</span>)}
                    </div>
                    {(question.source_note || question.reference_points.length > 0 || question.variants.length > 0) && (
                      <details className="question-evidence">
                        <summary>查看来源、参考点与变体</summary>
                        {question.source_note && <p><strong>来源：</strong>{question.source_note}</p>}
                        {question.reference_points.length > 0 && <p><strong>参考点：</strong>{question.reference_points.join(" · ")}</p>}
                        {question.variants.length > 0 && <p><strong>变体：</strong>{question.variants.map((variant) => variant.prompt).join(" · ")}</p>}
                      </details>
                    )}
                  </div>
                  <div className="question-row-actions">
                    <button className="row-icon-button" type="button" aria-label={`编辑题目：${question.prompt}`} onClick={() => openQuestionEdit(question)}>
                      <Pencil size={15} aria-hidden="true" />
                    </button>
                    {question.status !== "archived" && (
                      <button className="row-icon-button" type="button" aria-label={`归档题目：${question.prompt}`} onClick={() => archiveQuestion.mutate(question.id)}>
                        <Archive size={16} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </article>
              ))}
              {!questions.isLoading && questions.data?.count === 0 && (
                <div className="console-empty compact">
                  <BookOpen size={26} aria-hidden="true" />
                  <h3>{hasQuestionFilters ? "没有匹配的题目" : "这个题库还是空的"}</h3>
                  <p>{hasQuestionFilters ? "调整筛选条件或清除筛选后重试。" : "首版只支持手工录入和编辑；不会显示不可用的链接导入入口。"}</p>
                </div>
              )}
              {questions.data && questions.data.count > 0 && (
                <nav className="question-pagination" aria-label="题目分页">
                  <label>
                    每页
                    <select value={limit} onChange={(event) => changeQuestionView(() => setLimit(Number(event.target.value)))}>
                      {pageSizeOptions.map((size) => <option key={size} value={size}>{size}</option>)}
                    </select>
                  </label>
                  <div>
                    <button className="row-icon-button" type="button" aria-label="上一页" disabled={!hasPreviousPage} onClick={() => setOffset((current) => Math.max(0, current - limit))}><ChevronLeft size={16} /></button>
                    <span>{pageStart}–{pageEnd} / {questions.data.count}</span>
                    <button className="row-icon-button" type="button" aria-label="下一页" disabled={!hasNextPage} onClick={() => setOffset((current) => current + limit)}><ChevronRight size={16} /></button>
                  </div>
                </nav>
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
          {(retryResumeParse.isError || deleteResume.isError) && (
            <p className="inline-error">简历操作失败，请稍后重试。</p>
          )}
          <div className="resume-list">
            {resumes.data?.map((resume) => (
              <article className="resume-row" key={resume.id}>
                <span className="file-glyph"><FileText size={20} aria-hidden="true" /></span>
                <div>
                  <h3>{resume.filename}</h3>
                  <p>{resume.parse_status === "ready" ? `${resume.sections.length} 个区段 · ${resume.claims.length} 条可检索事实` : "解析完成后会生成结构化区段与事实"}</p>
                </div>
                <span className={`resume-status ${resume.parse_status}`}>{resumeStatusLabels[resume.parse_status]}</span>
                <div className="resume-actions">
                  {resume.parse_status === "failed" && (
                    <button
                      className="resume-action retry"
                      type="button"
                      disabled={retryResumeParse.isPending || deleteResume.isPending}
                      onClick={() => retryResumeParse.mutate(resume.id)}
                    >
                      <RefreshCw size={14} aria-hidden="true" />
                      {retryResumeParse.isPending ? "正在重试" : "重新解析"}
                    </button>
                  )}
                  <button
                    className="resume-action delete"
                    type="button"
                    aria-label={`删除简历 ${resume.filename}`}
                    disabled={deleteResume.isPending || retryResumeParse.isPending}
                    onClick={() => {
                      if (window.confirm(`删除“${resume.filename}”及其本地文件？`)) {
                        deleteResume.mutate(resume.id);
                      }
                    }}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                  </button>
                </div>
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
