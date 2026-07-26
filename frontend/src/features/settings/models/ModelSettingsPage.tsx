import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Cpu,
  Database,
  KeyRound,
  LoaderCircle,
  Play,
  Plus,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { type FormEvent, useState } from "react";

import { SettingsTabs } from "../SettingsTabs";
import { SettingsSectionHeading } from "../SettingsSectionHeading";
import { modelConnectionApi } from "./api";
import {
  modelRoles,
  type ConnectionDraft,
  type EmbeddingIndexStatus,
  type LocalCapability,
  type ModelConnection,
  type ModelRole,
  type ProviderType,
  type RoleTarget,
} from "./types";

const roleLabels: Record<ModelRole, string> = {
  interviewer: "面试官",
  evaluator: "评估官",
  planner: "面试规划",
  context_summarizer: "上下文压缩",
  researcher: "公司研究",
  vision_researcher: "视觉资料解析",
  coach: "训练教练",
  embedding: "向量检索",
  transcriber: "语音转写",
};

const initialDraft: ConnectionDraft = {
  name: "",
  provider_type: "openai_compatible",
  base_url: "https://api.openai.com/v1",
  api_key: "",
  model_name: "",
  extra_headers: {},
  context_window_tokens: 128_000,
  max_output_tokens: 4_096,
  tokenizer_type: "estimated",
  supports_prompt_caching: false,
  supports_token_count_endpoint: false,
};

const sharedEmbeddingEndpointKeys = new Set(["multilingual-e5-small", "bge-m3"]);
const rolesRequiringExplicitBinding = new Set<ModelRole>(["vision_researcher"]);

function statusLabel(status: ModelConnection["status"]) {
  return {
    untested: "未测试",
    healthy: "连接正常",
    degraded: "连接异常",
    disabled: "已停用",
  }[status];
}

type LocalCapabilityDisplayStatus = LocalCapability["status"] | "inactive_alternative";

interface LocalCapabilityPresentation {
  displayStatus: LocalCapabilityDisplayStatus;
  compactStatus: string;
  detail: string;
  action: string;
  optionStatus: string;
}

function localLatencyLabel(capability: LocalCapability) {
  return capability.latency_ms === null ? null : `${capability.latency_ms} ms`;
}

function getLocalCapabilityPresentation(
  capability: LocalCapability,
  capabilities: LocalCapability[] | undefined,
): LocalCapabilityPresentation {
  const readyAlternative = capabilities?.find(
    (candidate) =>
      candidate.key !== capability.key
      && candidate.role === "embedding"
      && sharedEmbeddingEndpointKeys.has(candidate.key)
      && candidate.status === "ready",
  );

  if (
    capability.role === "embedding"
    && capability.status === "mismatch"
    && sharedEmbeddingEndpointKeys.has(capability.key)
    && readyAlternative
  ) {
    return {
      displayStatus: "inactive_alternative",
      compactStatus: `当前未启用（${readyAlternative.title} 已就绪）`,
      detail: `与“${readyAlternative.title}”共用本机嵌入服务端口；一次只能运行一种嵌入模型。`,
      action: `如要切换，停止 ${readyAlternative.compose_profile} 后启动 ${capability.compose_profile}，再检查。`,
      optionStatus: "当前未启用（先切换 Docker profile）",
    };
  }

  if (capability.status === "ready") {
    return {
      displayStatus: "ready",
      compactStatus: "服务已就绪",
      detail: capability.summary,
      action: "可以直接绑定到对应 Agent 角色。",
      optionStatus: "已就绪",
    };
  }

  if (capability.status === "unavailable") {
    return {
      displayStatus: "unavailable",
      compactStatus: "待配置",
      detail: "Docker 本地服务尚未启动或当前不可达。",
      action: `可先绑定为预配置；启动 ${capability.compose_profile} 后再检查即可生效。`,
      optionStatus: "待配置（先启动 Docker）",
    };
  }

  return {
    displayStatus: "mismatch",
    compactStatus: "模型校验未通过",
    detail: "本地服务能够响应，但返回的模型或向量维度与此能力不一致。",
    action: `停止当前容器，确认后启动 ${capability.compose_profile}，再重新检查。`,
    optionStatus: "模型不匹配（请修正服务）",
  };
}

function embeddingIndexIsActive(status: EmbeddingIndexStatus | undefined) {
  return Boolean(
    (status?.building_profile
      && (!status.job || status.job.status === "queued" || status.job.status === "running"))
    || status?.job?.status === "queued"
    || status?.job?.status === "running",
  );
}

function hasCurrentEmbeddingIndexFailure(status: EmbeddingIndexStatus | undefined) {
  const failed = status?.latest_failed_profile;
  if (!failed) return false;
  const active = status?.active_profile;
  if (!active) return true;
  return Date.parse(failed.created_at) > Date.parse(active.created_at);
}

function embeddingIndexHeadline(status: EmbeddingIndexStatus | undefined) {
  if (status?.interview_active) return "面试进行中，索引任务将暂停";
  if (status?.building_profile && status.job?.phase === "waiting_for_interview") {
    return "已暂停，正在优先保障面试响应";
  }
  if (status?.building_profile) return "正在后台构建语义索引";
  if (hasCurrentEmbeddingIndexFailure(status) && status?.active_profile) {
    return "新索引未构建完成，仍在使用上一版";
  }
  if (hasCurrentEmbeddingIndexFailure(status)) return "上一次构建未完成";
  if (status?.active_profile) return "语义索引已就绪";
  return "尚未建立语义索引";
}

function embeddingIndexDetail(status: EmbeddingIndexStatus | undefined) {
  if (status?.interview_active || status?.job?.phase === "waiting_for_interview") {
    return "后台任务会自动让出资源；当前面试不会等待向量检索或重新嵌入。";
  }
  if (status?.building_profile && status.active_profile) {
    return "旧索引仍在服务本次面试；新索引验证完成后才会原子切换。";
  }
  if (status?.building_profile) {
    return "构建在后台分批进行，期间仍可正常使用关键词检索。";
  }
  if (hasCurrentEmbeddingIndexFailure(status) && status?.active_profile) {
    return `${status.latest_failed_profile?.failure_summary ?? "新索引未能完成构建。"}旧索引仍在服务，可修正配置后重新构建。`;
  }
  if (hasCurrentEmbeddingIndexFailure(status)) {
    return status?.latest_failed_profile?.failure_summary ?? "请检查嵌入模型连接后重新构建。";
  }
  if (status?.active_profile) {
    return "面试仅查询已缓存的向量，不会在对话中调用嵌入模型。";
  }
  return "先为“向量检索”角色绑定 OpenAI-compatible 或本地 Docker 嵌入模型。";
}

export function ModelSettingsPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(initialDraft);
  const [formOpen, setFormOpen] = useState(false);
  const [checkingCapabilityKey, setCheckingCapabilityKey] = useState<string | null>(null);

  const connections = useQuery({ queryKey: ["model-connections"], queryFn: modelConnectionApi.list });
  const bindings = useQuery({ queryKey: ["model-bindings"], queryFn: modelConnectionApi.listBindings });
  const readiness = useQuery({ queryKey: ["model-readiness"], queryFn: modelConnectionApi.readiness });
  const localCapabilities = useQuery({
    queryKey: ["local-ai-capabilities"],
    queryFn: modelConnectionApi.listLocalCapabilities,
  });
  const embeddingIndex = useQuery({
    queryKey: ["embedding-index"],
    queryFn: modelConnectionApi.embeddingIndexStatus,
    refetchInterval: (query) => embeddingIndexIsActive(query.state.data) ? 1_800 : false,
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["model-connections"] }),
      queryClient.invalidateQueries({ queryKey: ["model-bindings"] }),
      queryClient.invalidateQueries({ queryKey: ["model-readiness"] }),
      queryClient.invalidateQueries({ queryKey: ["local-ai-capabilities"] }),
      queryClient.invalidateQueries({ queryKey: ["embedding-index"] }),
    ]);
  };

  const createConnection = useMutation({
    mutationFn: modelConnectionApi.create,
    onSuccess: async () => {
      setDraft(initialDraft);
      setFormOpen(false);
      await refresh();
    },
  });
  const testConnection = useMutation({
    mutationFn: modelConnectionApi.test,
    onSuccess: refresh,
  });
  const deleteConnection = useMutation({
    mutationFn: modelConnectionApi.remove,
    onSuccess: refresh,
  });
  const redactConnection = useMutation({
    mutationFn: modelConnectionApi.redactCredentials,
    onSuccess: refresh,
  });
  const bindRole = useMutation({
    mutationFn: ({ role, target }: { role: ModelRole; target: RoleTarget }) =>
      modelConnectionApi.bindRole(role, target),
    onSuccess: refresh,
  });
  const unbindRole = useMutation({
    mutationFn: modelConnectionApi.unbindRole,
    onSuccess: refresh,
  });
  const testLocalCapability = useMutation({
    mutationFn: modelConnectionApi.testLocalCapability,
    onMutate: (key) => {
      setCheckingCapabilityKey(key);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["local-ai-capabilities"] });
    },
    onSettled: () => {
      setCheckingCapabilityKey(null);
    },
  });
  const rebuildEmbeddingIndex = useMutation({
    mutationFn: modelConnectionApi.rebuildEmbeddingIndex,
    onSuccess: refresh,
  });

  const updateProvider = (providerType: ProviderType) => {
    setDraft((current) => ({
      ...current,
      provider_type: providerType,
      base_url:
        providerType === "openai_compatible"
          ? "https://api.openai.com/v1"
          : "https://api.anthropic.com/v1",
    }));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    createConnection.mutate(draft);
  };

  const error = [
    connections.error,
    bindings.error,
    readiness.error,
    localCapabilities.error,
    embeddingIndex.error,
    createConnection.error,
    testConnection.error,
    deleteConnection.error,
    redactConnection.error,
    unbindRole.error,
    testLocalCapability.error,
    rebuildEmbeddingIndex.error,
  ].find(
    (item) => item instanceof Error,
  );
  const embeddingBinding = bindings.data?.find((binding) => binding.role === "embedding");
  const boundEmbeddingCapability =
    embeddingBinding?.target_kind === "local_capability"
      ? localCapabilities.data?.find(
          (capability) => capability.key === embeddingBinding.local_capability_key,
        )
      : undefined;
  const embeddingLocalCapabilityReady =
    embeddingBinding?.target_kind !== "local_capability"
    || (boundEmbeddingCapability !== undefined
      && getLocalCapabilityPresentation(boundEmbeddingCapability, localCapabilities.data).displayStatus === "ready");
  const embeddingIndexUnavailableLocalTarget = Boolean(
    embeddingBinding?.target_kind === "local_capability" && !embeddingLocalCapabilityReady,
  );
  const embeddingRebuildTitle = !embeddingBinding
    ? "请先绑定“向量检索”模型"
    : embeddingIndexUnavailableLocalTarget
      ? `请先启动 ${boundEmbeddingCapability?.compose_profile ?? "本地 Docker 嵌入服务"} 并检查服务`
      : undefined;
  const embeddingIndexBusy = embeddingIndexIsActive(embeddingIndex.data);
  const embeddingIndexJob = embeddingIndex.data?.job;
  const embeddingIndexProgress = embeddingIndexJob
    ? Math.round(embeddingIndexJob.progress * 100)
    : 0;
  const indexedSources = embeddingIndexJob
    ? embeddingIndexJob.memory_embeddings + embeddingIndexJob.plan_question_embeddings
    : 0;
  const rebuildLabel = embeddingIndexBusy
    ? "后台构建中"
    : embeddingIndex.data?.active_profile
      ? "重新构建"
      : "建立索引";

  return (
    <section className="settings-console" aria-labelledby="settings-title">
      <header className="settings-intro">
        <div>
          <p className="eyebrow">本地模型与服务</p>
          <h1 id="settings-title">系统设置</h1>
          <p>
            每个 Agent 角色可使用不同模型。密钥只在本机后端加密保存，不会回传到浏览器。
          </p>
        </div>
        <div className={`readiness ${readiness.data?.ready ? "ready" : "pending"}`}>
          {readiness.data?.ready ? <ShieldCheck aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <span>
            <strong>{readiness.data?.ready ? "面试链路已就绪" : "仍需完成必要绑定"}</strong>
            <small>面试与评估链路</small>
          </span>
        </div>
      </header>

      <SettingsTabs />

      {error instanceof Error && <p className="settings-error">{error.message}</p>}

      <div className="settings-grid">
        <section className="connection-panel" aria-labelledby="connections-title">
          <SettingsSectionHeading
            icon={Cpu}
            label="连接管理"
            title="模型连接"
            titleId="connections-title"
            description="添加并维护面试、评估、检索等任务使用的模型服务。"
            action={(
              <button className="primary-button" type="button" onClick={() => setFormOpen(!formOpen)}>
                <Plus size={16} aria-hidden="true" /> 新建连接
              </button>
            )}
          />

          {formOpen && (
            <form className="connection-form" onSubmit={submit}>
              <div className="provider-switch" aria-label="模型协议">
                {(["openai_compatible", "anthropic_compatible"] as ProviderType[]).map((type) => (
                  <button
                    key={type}
                    className={draft.provider_type === type ? "selected" : ""}
                    type="button"
                    onClick={() => updateProvider(type)}
                  >
                    {type === "openai_compatible" ? "OpenAI 兼容协议" : "Anthropic 兼容协议"}
                  </button>
                ))}
              </div>
              <label>
                连接名称
                <input
                  required
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  placeholder="例如：主面试模型"
                />
              </label>
              <label>
                服务地址（Base URL）
                <input
                  required
                  type="url"
                  value={draft.base_url}
                  onChange={(event) => setDraft({ ...draft, base_url: event.target.value })}
                />
              </label>
              <label>
                模型名称
                <input
                  required
                  value={draft.model_name}
                  onChange={(event) => setDraft({ ...draft, model_name: event.target.value })}
                  placeholder="模型服务中的 model id"
                />
              </label>
              <label>
                API 密钥
                <input
                  required
                  type="password"
                  autoComplete="off"
                  value={draft.api_key}
                  onChange={(event) => setDraft({ ...draft, api_key: event.target.value })}
                  placeholder="仅发送到本地后端"
                />
              </label>
              <div className="compact-fields">
                <label>
                  上下文窗口
                  <input
                    min={1024}
                    type="number"
                    value={draft.context_window_tokens}
                    onChange={(event) =>
                      setDraft({ ...draft, context_window_tokens: Number(event.target.value) })
                    }
                  />
                </label>
                <label>
                  最大输出 Token
                  <input
                    min={1}
                    type="number"
                    value={draft.max_output_tokens}
                    onChange={(event) =>
                      setDraft({ ...draft, max_output_tokens: Number(event.target.value) })
                    }
                  />
                </label>
              </div>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={draft.supports_prompt_caching}
                  onChange={(event) =>
                    setDraft({ ...draft, supports_prompt_caching: event.target.checked })
                  }
                />
                支持提示词缓存
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={draft.supports_token_count_endpoint}
                  onChange={(event) =>
                    setDraft({ ...draft, supports_token_count_endpoint: event.target.checked })
                  }
                />
                支持官方 Token 计数接口
              </label>
              <div className="form-actions">
                <button className="quiet-button" type="button" onClick={() => setFormOpen(false)}>
                  取消
                </button>
                <button className="primary-button" type="submit" disabled={createConnection.isPending}>
                  保存加密连接
                </button>
              </div>
            </form>
          )}

          <div className="connection-list">
            {connections.data?.map((connection) => (
              <article className="connection-row" key={connection.id}>
                <span className="provider-glyph"><Cpu size={18} aria-hidden="true" /></span>
                <div className="connection-identity">
                  <strong>{connection.name}</strong>
                  <span>{connection.model_name}</span>
                </div>
                <span className="protocol-label">
                  {connection.provider_type === "openai_compatible" ? "OpenAI 协议" : "Anthropic 协议"}
                </span>
                <span className={`connection-status ${connection.status}`}>
                  {connection.status === "healthy" && <Check size={13} aria-hidden="true" />}
                  {statusLabel(connection.status)}
                </span>
                <div className="row-actions">
                  <button
                    type="button"
                    aria-label={`测试 ${connection.name}`}
                    title="测试连接"
                    onClick={() => testConnection.mutate(connection.id)}
                  >
                    <Play size={15} aria-hidden="true" />
                  </button>
                  {connection.has_api_key && (
                    <button
                      type="button"
                      aria-label={`清除 ${connection.name} 的密钥并停用`}
                      title="清除密钥并停用"
                      disabled={redactConnection.isPending}
                      onClick={() => {
                        if (
                          window.confirm(
                            `清除“${connection.name}”的 API 密钥和额外请求头，并停用该连接？此操作不可撤销。`,
                          )
                        ) {
                          redactConnection.mutate(connection.id);
                        }
                      }}
                    >
                      <KeyRound size={15} aria-hidden="true" />
                    </button>
                  )}
                  <button
                    type="button"
                    aria-label={`删除 ${connection.name}`}
                    title="删除连接"
                    onClick={() => {
                      if (window.confirm(`删除“${connection.name}”及其本地密钥？`)) {
                        deleteConnection.mutate(connection.id);
                      }
                    }}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                  </button>
                </div>
              </article>
            ))}
            {!connections.isLoading && connections.data?.length === 0 && (
              <div className="connection-empty">尚未配置模型连接。先添加至少一个面试模型。</div>
            )}
          </div>

          <div className="local-capability-list" aria-labelledby="local-capabilities-title">
            <SettingsSectionHeading
              icon={Cpu}
              label="本地 Docker 服务"
              title="本地 AI 服务"
              titleId="local-capabilities-title"
              description="仅检查固定的本机服务；不会下载模型、启动容器或读取 API 密钥。"
            />
            {localCapabilities.data?.map((capability) => {
              const presentation = getLocalCapabilityPresentation(capability, localCapabilities.data);
              const latency = localLatencyLabel(capability);
              const isChecking = testLocalCapability.isPending
                && checkingCapabilityKey === capability.key;
              const visibleStatus = isChecking ? "正在检查本地服务…" : presentation.compactStatus;
              return (
                <article
                  className={`connection-row local-capability-card ${presentation.displayStatus}`}
                  key={capability.key}
                >
                  <span className="provider-glyph"><Cpu size={18} aria-hidden="true" /></span>
                  <div className="connection-identity local-capability-identity">
                    <strong>{capability.title}</strong>
                    <span>{capability.compose_profile} · {capability.model_name}</span>
                    <span className={`local-card-mobile-status ${presentation.displayStatus}`}>
                      {visibleStatus}{latency && !isChecking ? ` · ${latency}` : ""}
                    </span>
                  </div>
                  <span className="protocol-label">{capability.runtime}</span>
                  <span className={`connection-status ${presentation.displayStatus}`} aria-hidden="true">
                    {capability.status === "ready" && !isChecking && <Check size={13} aria-hidden="true" />}
                    {isChecking && <LoaderCircle className="local-capability-spinner" size={13} aria-hidden="true" />}
                    {visibleStatus}{latency && !isChecking ? ` · ${latency}` : ""}
                  </span>
                  <div className="row-actions">
                    <button
                      type="button"
                      aria-busy={isChecking}
                      aria-label={isChecking ? `正在检查 ${capability.title}` : `检查 ${capability.title}`}
                      disabled={isChecking}
                      onClick={() => testLocalCapability.mutate(capability.key)}
                    >
                      {isChecking ? (
                        <LoaderCircle className="local-capability-spinner" size={15} aria-hidden="true" />
                      ) : (
                        <Play size={15} aria-hidden="true" />
                      )}
                    </button>
                  </div>
                  <p
                    className={`local-capability-feedback ${presentation.displayStatus}`}
                    role="status"
                    aria-atomic="true"
                    aria-live="polite"
                  >
                    <strong>
                      {isChecking ? "正在检查…" : visibleStatus}
                      {latency && !isChecking ? ` · ${latency}` : ""}
                    </strong>
                    <span>
                      {isChecking
                        ? "正在验证固定的本机服务地址；检查不会启动容器、下载模型或改动当前配置。"
                        : `${presentation.detail} ${presentation.action}`}
                    </span>
                  </p>
                </article>
              );
            })}
            {!localCapabilities.isLoading && !localCapabilities.data?.length && (
              <div className="connection-empty">本地 Docker 能力目录暂不可用。</div>
            )}
          </div>

          <section className="embedding-index-panel" aria-labelledby="embedding-index-title">
            <SettingsSectionHeading
              icon={Database}
              label="语义检索"
              title="语义索引"
              titleId="embedding-index-title"
              description="索引在后台增量构建，面试时会自动让出资源。"
              action={(
                <button
                  className="secondary-button embedding-index-action"
                  type="button"
                  aria-busy={rebuildEmbeddingIndex.isPending}
                  disabled={
                    !embeddingBinding
                    || embeddingIndexUnavailableLocalTarget
                    || embeddingIndexBusy
                    || rebuildEmbeddingIndex.isPending
                  }
                  title={embeddingRebuildTitle}
                  onClick={() => rebuildEmbeddingIndex.mutate()}
                >
                  {rebuildEmbeddingIndex.isPending || embeddingIndexBusy ? (
                    <LoaderCircle className="local-capability-spinner" size={15} aria-hidden="true" />
                  ) : (
                    <Database size={15} aria-hidden="true" />
                  )}
                  {rebuildLabel}
                </button>
              )}
            />
            <article
              className={`embedding-index-status ${embeddingIndexBusy ? "building" : ""}`}
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <div>
                <strong>{embeddingIndexHeadline(embeddingIndex.data)}</strong>
                <span>{embeddingIndexDetail(embeddingIndex.data)}</span>
              </div>
              {embeddingIndexJob && (
                <div className="embedding-index-progress" aria-label={`索引进度 ${embeddingIndexProgress}%`}>
                  <span className="embedding-index-progress-track" aria-hidden="true">
                    <span style={{ width: `${embeddingIndexProgress}%` }} />
                  </span>
                  <small>
                    {embeddingIndexProgress}% · 已写入 {indexedSources} 条缓存
                    {embeddingIndexJob.vector_dimensions
                      ? ` · ${embeddingIndexJob.vector_dimensions} 维`
                      : ""}
                  </small>
                </div>
              )}
              {!embeddingIndexJob && embeddingIndex.data?.active_profile && (
                <small className="embedding-index-active-model">
                  {embeddingIndex.data.active_profile.model_name}
                  {embeddingIndex.data.active_profile.vector_dimensions
                    ? ` · ${embeddingIndex.data.active_profile.vector_dimensions} 维`
                    : ""}
                </small>
              )}
            </article>
            <p className="embedding-index-note">
              索引只由后台任务更新。面试进行时会自动暂停，检索始终优先使用已验证的缓存；
              未建立索引时安全回退到关键词检索。
            </p>
            {embeddingIndexUnavailableLocalTarget && (
              <p className="embedding-index-note embedding-index-blocked">
                已绑定 {boundEmbeddingCapability?.title ?? "本地 Docker 嵌入服务"}；请先启动 {boundEmbeddingCapability?.compose_profile ?? "对应服务"}
                并点击“检查”，确认就绪后再建立索引。
              </p>
            )}
          </section>
        </section>

        <aside className="routing-panel" aria-labelledby="routing-title">
          <SettingsSectionHeading
            icon={ShieldCheck}
            label="任务分配"
            title="Agent 角色路由"
            titleId="routing-title"
            description="面试官与评估官需显式绑定；未配置的辅助角色会使用安全回退策略。"
          />
          <div className="role-list">
            {modelRoles.map((role) => {
              const binding = bindings.data?.find((item) => item.role === role);
              const required = role === "interviewer" || role === "evaluator";
              const requiresExplicitBinding = rolesRequiringExplicitBinding.has(role);
              const roleConnections = role === "transcriber" || role === "embedding"
                ? connections.data?.filter(
                    (connection) => connection.provider_type === "openai_compatible",
                  )
                : connections.data;
              const localRoleCapabilities = localCapabilities.data?.filter((item) => item.role === role);
              const selectedTarget = binding?.target_kind === "local_capability"
                ? `local:${binding.local_capability_key}`
                : binding?.connection_id ?? "";
              const selectedLocalCapability = binding?.target_kind === "local_capability"
                ? localRoleCapabilities?.find((item) => item.key === binding.local_capability_key)
                : undefined;
              const selectedLocalPresentation = selectedLocalCapability
                ? getLocalCapabilityPresentation(selectedLocalCapability, localCapabilities.data)
                : undefined;
              const isLocalPreconfiguration = selectedLocalPresentation
                && selectedLocalPresentation.displayStatus !== "ready";
              return (
                <label className="role-row" key={role}>
                  <span>
                    <strong>{roleLabels[role]}</strong>
                    <small>{required ? "必需" : requiresExplicitBinding ? "按需" : "可选"}</small>
                  </span>
                  <span className="role-target-control">
                    <select
                      aria-label={`${roleLabels[role]}模型`}
                      value={selectedTarget}
                      onChange={(event) => {
                        const target = event.target.value;
                        if (target.startsWith("local:")) {
                          bindRole.mutate({
                            role,
                            target: { local_capability_key: target.slice("local:".length) },
                          });
                        } else if (target) {
                          bindRole.mutate({ role, target: { connection_id: target } });
                        } else {
                          unbindRole.mutate(role);
                        }
                      }}
                    >
                      <option value="">
                        {required || requiresExplicitBinding ? "请选择连接" : "使用回退策略"}
                      </option>
                      {roleConnections?.map((connection) => (
                        <option key={connection.id} value={connection.id}>
                          {connection.name} · {connection.model_name}
                        </option>
                      ))}
                      {!!localRoleCapabilities?.length && (
                        <optgroup label="本地 Docker（可先绑定，启动后生效）">
                          {localRoleCapabilities.map((capability) => {
                            const presentation = getLocalCapabilityPresentation(
                              capability,
                              localCapabilities.data,
                            );
                            return (
                              <option key={capability.key} value={`local:${capability.key}`}>
                                {capability.title} · {presentation.optionStatus}
                              </option>
                            );
                          })}
                        </optgroup>
                      )}
                    </select>
                    {isLocalPreconfiguration && (
                      <small className="role-preconfiguration">
                        已保存为预配置：{selectedLocalPresentation.compactStatus}。服务就绪前不会自动改用云端模型。
                      </small>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        </aside>
      </div>
    </section>
  );
}
