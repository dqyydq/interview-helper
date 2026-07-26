import { useMutation } from "@tanstack/react-query";
import { BookOpenCheck, Dumbbell, LoaderCircle, MessageSquareText, Sparkles } from "lucide-react";
import { useState } from "react";

import { reportApi } from "./api";
import type { CoachMode, QuestionEvaluation } from "./types";

interface CoachPanelProps {
  reportId: string;
  questions: QuestionEvaluation[];
}

const modeCopy: Record<CoachMode, { label: string; icon: typeof Sparkles }> = {
  explain: { label: "解释评级", icon: MessageSquareText },
  rewrite: { label: "示范重答", icon: BookOpenCheck },
  practice: { label: "专项练习", icon: Dumbbell },
};

export function CoachPanel({ reportId, questions }: CoachPanelProps) {
  const [mode, setMode] = useState<CoachMode>("explain");
  const [questionId, setQuestionId] = useState(questions[0]?.id ?? "");
  const coach = useMutation({
    mutationFn: () =>
      reportApi.coach({
        reportId,
        mode,
        questionEvaluationId: questionId || undefined,
      }),
  });

  return (
    <section className="coach-panel" aria-labelledby="coach-title">
      <header>
        <Sparkles size={17} aria-hidden="true" />
        <div>
          <span>答题复盘与练习</span>
          <h2 id="coach-title">复盘教练</h2>
        </div>
      </header>
      <div className="coach-modes" aria-label="教练模式">
        {(Object.entries(modeCopy) as Array<[CoachMode, (typeof modeCopy)[CoachMode]]>).map(
          ([value, item]) => {
            const Icon = item.icon;
            return (
              <button
                type="button"
                className={mode === value ? "active" : ""}
                aria-pressed={mode === value}
                key={value}
                onClick={() => setMode(value)}
              >
                <Icon size={14} /> {item.label}
              </button>
            );
          },
        )}
      </div>
      <label>
        针对题目
        <select value={questionId} onChange={(event) => setQuestionId(event.target.value)}>
          {questions.map((question) => (
            <option value={question.id} key={question.id}>
              第 {question.question_sequence} 题 · {question.question_prompt}
            </option>
          ))}
        </select>
      </label>
      <button
        className="coach-run"
        type="button"
        disabled={coach.isPending || (mode === "rewrite" && !questionId)}
        onClick={() => coach.mutate()}
      >
        {coach.isPending ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
        {coach.isPending ? "正在生成" : modeCopy[mode].label}
      </button>
      {coach.isError && (
        <p className="coach-error" role="alert">
          教练暂时不可用。请确认已绑定 Coach 模型后重试。
        </p>
      )}
      {coach.data && (
        <article className="coach-result" aria-live="polite">
          <h3>{coach.data.title}</h3>
          <p>{coach.data.explanation}</p>
          {coach.data.original_answer && (
            <div className="coach-answer original">
              <strong>用户原回答</strong>
              <p>{coach.data.original_answer}</p>
            </div>
          )}
          {coach.data.suggested_answer && (
            <div className="coach-answer suggested">
              <strong>建议答案</strong>
              <p>{coach.data.suggested_answer}</p>
            </div>
          )}
          {coach.data.practice_prompts.length > 0 && (
            <ol>
              {coach.data.practice_prompts.map((prompt) => (
                <li key={prompt}>{prompt}</li>
              ))}
            </ol>
          )}
        </article>
      )}
    </section>
  );
}
