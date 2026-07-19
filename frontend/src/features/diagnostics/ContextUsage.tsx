import { useQuery } from "@tanstack/react-query";
import { Activity, Database, Layers3 } from "lucide-react";

import { liveInterviewApi } from "../interviews/live/api";

export function ContextUsage({ sessionId }: { sessionId: string }) {
  const diagnostics = useQuery({
    queryKey: ["context-diagnostics", sessionId],
    queryFn: () => liveInterviewApi.diagnostics(sessionId),
    enabled: Boolean(sessionId),
    refetchInterval: 10_000,
    retry: false,
  });
  const latest = diagnostics.data?.snapshots[0];
  const layers = latest?.token_by_layer;
  const budget = Number(layers?.effective_input_budget ?? 0);
  const selected = Number(layers?.selected_input_tokens ?? latest?.input_tokens ?? 0);
  const retained = Number(layers?.compression_ratio ?? 1);
  const retrievalIncluded = Number(layers?.retrieval_included_count ?? 0);
  const retrievalCandidates = Number(layers?.retrieval_candidate_count ?? 0);

  return (
    <section className="context-usage" aria-labelledby="context-usage-title">
      <div className="context-usage-heading">
        <Activity size={15} aria-hidden="true" />
        <h2 id="context-usage-title">上下文诊断</h2>
        <span>L{latest?.compaction_level ?? 0}</span>
      </div>
      {!latest ? (
        <p className="context-usage-empty">首轮回答后显示 Token 分层与压缩情况。</p>
      ) : (
        <>
          <div className="context-budget-line">
            <span style={{ width: `${Math.min(100, budget ? (selected / budget) * 100 : 0)}%` }} />
          </div>
          <dl>
            <div><dt><Layers3 size={11} />输入预算</dt><dd>{selected.toLocaleString()} / {budget.toLocaleString()}</dd></div>
            <div><dt>压缩保留</dt><dd>{Math.round(retained * 100)}%</dd></div>
            <div><dt><Database size={11} />长期记忆</dt><dd>{retrievalIncluded} / {retrievalCandidates}</dd></div>
            <div><dt>计数方式</dt><dd title={latest.count_method}>{latest.count_method.replace("conservative_", "")}</dd></div>
          </dl>
          <small>仅展示计数、层级和引用数量，不记录回答正文。</small>
        </>
      )}
    </section>
  );
}
