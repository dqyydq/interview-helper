import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Cpu,
  Play,
  Plus,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { type FormEvent, useState } from "react";

import { SettingsTabs } from "../SettingsTabs";
import { modelConnectionApi } from "./api";
import {
  modelRoles,
  type ConnectionDraft,
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

function statusLabel(status: ModelConnection["status"]) {
  return {
    untested: "未测试",
    healthy: "连接正常",
    degraded: "连接异常",
    disabled: "已停用",
  }[status];
}

function localStatusLabel(status: LocalCapability["status"]) {
  return {
    ready: "本地服务已就绪",
    unavailable: "服务未启动或不可达",
    mismatch: "运行中的模型不匹配",
  }[status];
}

export function ModelSettingsPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(initialDraft);
  const [formOpen, setFormOpen] = useState(false);

  const connections = useQuery({ queryKey: ["model-connections"], queryFn: modelConnectionApi.list });
  const bindings = useQuery({ queryKey: ["model-bindings"], queryFn: modelConnectionApi.listBindings });
  const readiness = useQuery({ queryKey: ["model-readiness"], queryFn: modelConnectionApi.readiness });
  const localCapabilities = useQuery({
    queryKey: ["local-ai-capabilities"],
    queryFn: modelConnectionApi.listLocalCapabilities,
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["model-connections"] }),
      queryClient.invalidateQueries({ queryKey: ["model-bindings"] }),
      queryClient.invalidateQueries({ queryKey: ["model-readiness"] }),
      queryClient.invalidateQueries({ queryKey: ["local-ai-capabilities"] }),
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
  const bindRole = useMutation({
    mutationFn: ({ role, target }: { role: ModelRole; target: RoleTarget }) =>
      modelConnectionApi.bindRole(role, target),
    onSuccess: refresh,
  });
  const testLocalCapability = useMutation({
    mutationFn: modelConnectionApi.testLocalCapability,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["local-ai-capabilities"] });
    },
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
    createConnection.error,
    testLocalCapability.error,
  ].find(
    (item) => item instanceof Error,
  );

  return (
    <section className="settings-console" aria-labelledby="settings-title">
      <header className="settings-intro">
        <div>
          <p className="eyebrow">LOCAL MODEL CONTROL</p>
          <h1 id="settings-title">系统设置</h1>
          <p>
            每个 Agent 角色可使用不同模型。密钥只在本机后端加密保存，不会回传到浏览器。
          </p>
        </div>
        <div className={`readiness ${readiness.data?.ready ? "ready" : "pending"}`}>
          {readiness.data?.ready ? <ShieldCheck aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <span>
            <strong>{readiness.data?.ready ? "面试链路已就绪" : "仍需完成必要绑定"}</strong>
            <small>Interview / Evaluation routing</small>
          </span>
        </div>
      </header>

      <SettingsTabs />

      {error instanceof Error && <p className="settings-error">{error.message}</p>}

      <div className="settings-grid">
        <section className="connection-panel" aria-labelledby="connections-title">
          <div className="panel-heading">
            <div>
              <span>01 / CONNECTIONS</span>
              <h2 id="connections-title">模型连接</h2>
            </div>
            <button className="primary-button" type="button" onClick={() => setFormOpen(!formOpen)}>
              <Plus size={16} aria-hidden="true" /> 新建连接
            </button>
          </div>

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
                    {type === "openai_compatible" ? "OpenAI-compatible" : "Anthropic-compatible"}
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
                Base URL
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
                API Key
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
                  Context Window
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
                  Max Output
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
                支持 Prompt Caching
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={draft.supports_token_count_endpoint}
                  onChange={(event) =>
                    setDraft({ ...draft, supports_token_count_endpoint: event.target.checked })
                  }
                />
                支持官方 Token Count
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
                  {connection.provider_type === "openai_compatible" ? "OPENAI" : "ANTHROPIC"}
                </span>
                <span className={`connection-status ${connection.status}`}>
                  {connection.status === "healthy" && <Check size={13} aria-hidden="true" />}
                  {statusLabel(connection.status)}
                </span>
                <div className="row-actions">
                  <button
                    type="button"
                    aria-label={`测试 ${connection.name}`}
                    onClick={() => testConnection.mutate(connection.id)}
                  >
                    <Play size={15} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    aria-label={`删除 ${connection.name}`}
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
            <div className="panel-heading">
              <div>
                <span>03 / LOCAL DOCKER</span>
                <h2 id="local-capabilities-title">本地 AI 服务</h2>
              </div>
            </div>
            <p className="routing-note">
              先在终端安装已校验模型并启动对应 Docker profile；这里只检查固定的 loopback 服务，既不需要 API Key，也不会自动下载或启动容器。
            </p>
            {localCapabilities.data?.map((capability) => (
              <article className="connection-row" key={capability.key}>
                <span className="provider-glyph"><Cpu size={18} aria-hidden="true" /></span>
                <div className="connection-identity">
                  <strong>{capability.title}</strong>
                  <span>{capability.compose_profile} · {capability.model_name}</span>
                </div>
                <span className="protocol-label">{capability.runtime.toUpperCase()}</span>
                <span className={`connection-status ${capability.status}`}>
                  {capability.status === "ready" && <Check size={13} aria-hidden="true" />}
                  {localStatusLabel(capability.status)}
                </span>
                <div className="row-actions">
                  <button
                    type="button"
                    aria-label={`检查 ${capability.title}`}
                    disabled={testLocalCapability.isPending}
                    onClick={() => testLocalCapability.mutate(capability.key)}
                  >
                    <Play size={15} aria-hidden="true" />
                  </button>
                </div>
              </article>
            ))}
            {!localCapabilities.isLoading && !localCapabilities.data?.length && (
              <div className="connection-empty">本地 Docker 能力目录暂不可用。</div>
            )}
          </div>
        </section>

        <aside className="routing-panel" aria-labelledby="routing-title">
          <div className="panel-heading">
            <div>
              <span>02 / AGENT ROUTING</span>
              <h2 id="routing-title">Agent 角色路由</h2>
            </div>
          </div>
          <p className="routing-note">面试官与评估官必须显式绑定；上下文压缩未绑定时回退到规划模型。</p>
          <div className="role-list">
            {modelRoles.map((role) => {
              const binding = bindings.data?.find((item) => item.role === role);
              const required = role === "interviewer" || role === "evaluator";
              const roleConnections = role === "transcriber"
                ? connections.data?.filter(
                    (connection) => connection.provider_type === "openai_compatible",
                  )
                : connections.data;
              const localRoleCapabilities = localCapabilities.data?.filter((item) => item.role === role);
              const selectedTarget = binding?.target_kind === "local_capability"
                ? `local:${binding.local_capability_key}`
                : binding?.connection_id ?? "";
              return (
                <label className="role-row" key={role}>
                  <span>
                    <strong>{roleLabels[role]}</strong>
                    <small>{required ? "REQUIRED" : "OPTIONAL"}</small>
                  </span>
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
                      }
                    }}
                  >
                    <option value="">{required ? "请选择连接" : "使用回退策略"}</option>
                    {roleConnections?.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.name} · {connection.model_name}
                      </option>
                    ))}
                    {!!localRoleCapabilities?.length && (
                      <optgroup label="本地 Docker">
                        {localRoleCapabilities.map((capability) => (
                          <option key={capability.key} value={`local:${capability.key}`}>
                            {capability.title} · {localStatusLabel(capability.status)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </label>
              );
            })}
          </div>
        </aside>
      </div>
    </section>
  );
}
