import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  Check,
  Pencil,
  Pin,
  PinOff,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";

import { SettingsTabs } from "../SettingsTabs";
import { memoryApi } from "./api";
import type { MemoryItem, MemoryStatus, MemoryType } from "./types";

const statusFilters: Array<{ value: MemoryStatus | "all"; label: string }> = [
  { value: "all", label: "全部" },
  { value: "proposed", label: "待确认" },
  { value: "active", label: "已启用" },
  { value: "conflicted", label: "有冲突" },
  { value: "rejected", label: "已忽略" },
];

const typeLabels: Record<MemoryType, string> = {
  stable_skill: "稳定能力",
  recurring_gap: "反复短板",
  preference: "面试偏好",
  target: "求职目标",
  constraint: "明确约束",
  company_context: "公司上下文",
};

const statusLabels: Record<MemoryStatus, string> = {
  proposed: "待确认",
  active: "已启用",
  conflicted: "存在冲突",
  rejected: "已忽略",
  expired: "已过期",
};

export function MemorySettingsPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<MemoryStatus | "all">("all");
  const [editingId, setEditingId] = useState<string>();
  const [editingContent, setEditingContent] = useState("");
  const memories = useQuery({
    queryKey: ["memories", filter],
    queryFn: () => memoryApi.list(filter === "all" ? undefined : filter),
  });
  const settings = useQuery({ queryKey: ["memory-settings"], queryFn: memoryApi.settings });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["memories"] });
  };
  const action = useMutation({
    mutationFn: (operation: () => Promise<unknown>) => operation(),
    onSuccess: refresh,
  });
  const toggleMemory = useMutation({
    mutationFn: memoryApi.updateSettings,
    onSuccess: (next) => queryClient.setQueryData(["memory-settings"], next),
  });

  const beginEdit = (memory: MemoryItem) => {
    setEditingId(memory.id);
    setEditingContent(memory.content);
  };
  const counts = (memories.data ?? []).reduce<Record<string, number>>((result, item) => {
    result[item.status] = (result[item.status] ?? 0) + 1;
    return result;
  }, {});
  const error = [memories.error, settings.error, action.error, toggleMemory.error].find(
    (item) => item instanceof Error,
  );

  return (
    <section className="settings-console memory-console" aria-labelledby="memory-title">
      <header className="settings-intro memory-intro">
        <div>
          <p className="eyebrow">CONTROLLED INTERVIEW MEMORY</p>
          <h1 id="memory-title">长期记忆</h1>
          <p>管理 Agent 跨场使用的稳定信息。每条记忆都保留来源、状态和你的最终控制权。</p>
        </div>
        <label className="memory-master-switch">
          <span>
            <strong>跨场记忆</strong>
            <small>关闭后停止提取与调用，已有内容不会删除</small>
          </span>
          <input
            type="checkbox"
            checked={settings.data?.memory_enabled ?? false}
            disabled={settings.isLoading || toggleMemory.isPending}
            onChange={(event) => toggleMemory.mutate(event.target.checked)}
          />
          <i aria-hidden="true" />
        </label>
      </header>

      <SettingsTabs />
      {error instanceof Error && <p className="settings-error">{error.message}</p>}

      <div className="memory-workspace">
        <aside className="memory-filter" aria-label="记忆状态筛选">
          <span className="memory-filter-label">STATUS INDEX</span>
          {statusFilters.map((item) => (
            <button
              className={filter === item.value ? "active" : ""}
              type="button"
              key={item.value}
              onClick={() => setFilter(item.value)}
            >
              <span>{item.label}</span>
              <small>{item.value === "all" ? memories.data?.length ?? 0 : counts[item.value] ?? "—"}</small>
            </button>
          ))}
          <div className="memory-principle">
            <BrainCircuit size={18} aria-hidden="true" />
            <p><strong>只记稳定事实</strong>临时回答与单次失误不会直接成为长期记忆。</p>
          </div>
        </aside>

        <main className="memory-list" aria-live="polite">
          <div className="memory-list-heading">
            <div><span>MEMORY REGISTER</span><h2>{statusFilters.find((item) => item.value === filter)?.label}</h2></div>
            <small>{memories.data?.length ?? 0} ENTRIES</small>
          </div>

          {memories.isLoading && <div className="memory-empty">正在读取可追溯记忆…</div>}
          {!memories.isLoading && memories.data?.length === 0 && (
            <div className="memory-empty">
              <BrainCircuit size={23} />
              <strong>这里还没有记忆</strong>
              <p>完成面试后，稳定能力、反复短板和明确偏好会先进入待确认状态。</p>
            </div>
          )}
          {memories.data?.map((memory) => (
            <article className={`memory-row ${memory.status}`} key={memory.id}>
              <div className="memory-row-index">{String(memory.memory_version).padStart(2, "0")}</div>
              <div className="memory-row-body">
                <div className="memory-meta">
                  <span>{typeLabels[memory.memory_type]}</span>
                  <span className={`memory-status ${memory.status}`}>{statusLabels[memory.status]}</span>
                  {memory.pinned && <span className="memory-pinned"><Pin size={11} /> 已置顶</span>}
                </div>
                {editingId === memory.id ? (
                  <textarea
                    aria-label="编辑记忆内容"
                    value={editingContent}
                    onChange={(event) => setEditingContent(event.target.value)}
                  />
                ) : <p className="memory-content">{memory.content}</p>}
                <div className="memory-evidence">
                  <span>{memory.sources.length} 个来源</span>
                  <span>置信度 {Math.round(memory.confidence * 100)}%</span>
                  <span>更新于 {new Date(memory.last_verified_at ?? memory.first_observed_at).toLocaleDateString("zh-CN")}</span>
                </div>
                {memory.status === "conflicted" && memory.open_conflicts.length > 0 && (
                  <div className="memory-conflict-note">
                    <ShieldAlert size={15} />
                    <span>发现相互矛盾的信息，确认后才会继续用于面试。</span>
                    <button
                      type="button"
                      onClick={() => action.mutate(() => memoryApi.resolveConflict(memory.open_conflicts[0].id, memory.id))}
                    >保留此版本</button>
                  </div>
                )}
              </div>
              <div className="memory-actions">
                {editingId === memory.id ? (
                  <>
                    <button
                      type="button"
                      aria-label="保存记忆"
                      disabled={!editingContent.trim() || action.isPending}
                      onClick={() => action.mutate(
                        () => memoryApi.update(memory.id, editingContent.trim()),
                        { onSuccess: () => setEditingId(undefined) },
                      )}
                    ><Check size={15} /></button>
                    <button type="button" aria-label="取消编辑" onClick={() => setEditingId(undefined)}><X size={15} /></button>
                  </>
                ) : (
                  <>
                    {memory.status === "proposed" && (
                      <button type="button" aria-label="确认记忆" onClick={() => action.mutate(() => memoryApi.confirm(memory.id))}><Check size={15} /></button>
                    )}
                    {memory.status === "active" && (
                      <button
                        type="button"
                        aria-label={memory.pinned ? "取消置顶" : "置顶记忆"}
                        onClick={() => action.mutate(() => memoryApi.pin(memory.id, !memory.pinned))}
                      >{memory.pinned ? <PinOff size={15} /> : <Pin size={15} />}</button>
                    )}
                    {memory.status !== "conflicted" && (
                      <button type="button" aria-label="编辑记忆" onClick={() => beginEdit(memory)}><Pencil size={15} /></button>
                    )}
                    {memory.status !== "rejected" && (
                      <button type="button" aria-label="忽略记忆" onClick={() => action.mutate(() => memoryApi.reject(memory.id))}><X size={15} /></button>
                    )}
                    <button
                      type="button"
                      aria-label="删除记忆"
                      onClick={() => {
                        if (window.confirm("永久删除这条记忆及其来源记录？")) {
                          action.mutate(() => memoryApi.remove(memory.id));
                        }
                      }}
                    ><Trash2 size={15} /></button>
                  </>
                )}
              </div>
            </article>
          ))}
        </main>
      </div>
    </section>
  );
}
