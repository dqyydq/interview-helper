import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Flame,
  KeyRound,
  Play,
  Plus,
  SearchCheck,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { type FormEvent, useState } from "react";

import { discoveryApi } from "../../discovery/api";
import type {
  DiscoveryConnector,
  DiscoveryConnectorCreate,
  DiscoveryConnectorUpdate,
  DiscoveryProviderType,
} from "../../discovery/types";
import { SettingsTabs } from "../SettingsTabs";

type ConnectorFormMode = "create" | "edit" | null;

interface ConnectorDraft {
  providerType: DiscoveryProviderType;
  name: string;
  apiKey: string;
  enabled: boolean;
  defaultCountry: string;
}

const emptyDraft: ConnectorDraft = {
  providerType: "tavily",
  name: "",
  apiKey: "",
  enabled: true,
  defaultCountry: "",
};

const discoveryProviderTypes = ["tavily", "firecrawl"] as const satisfies readonly DiscoveryProviderType[];
const connectorsPerProviderLimit = 3;

const providerLabels: Record<DiscoveryProviderType, string> = {
  tavily: "Tavily",
  firecrawl: "Firecrawl",
};

function providerKeyLabel(providerType: DiscoveryProviderType) {
  return `${providerLabels[providerType]} API Key`;
}

const statusLabels: Record<DiscoveryConnector["status"], string> = {
  untested: "未测试",
  healthy: "连接正常",
  degraded: "需要检查",
  disabled: "已停用",
};

function displayError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function toDraft(connector: DiscoveryConnector): ConnectorDraft {
  return {
    providerType: connector.provider_type,
    name: connector.name,
    apiKey: "",
    enabled: connector.enabled,
    defaultCountry: connector.configuration.default_country ?? "",
  };
}

function toConfiguration(defaultCountry: string) {
  const normalizedCountry = defaultCountry.trim();
  return { default_country: normalizedCountry || null };
}

export function DiscoverySettingsPage() {
  const queryClient = useQueryClient();
  const [formMode, setFormMode] = useState<ConnectorFormMode>(null);
  const [editingConnector, setEditingConnector] = useState<DiscoveryConnector>();
  const [draft, setDraft] = useState<ConnectorDraft>(emptyDraft);
  const [actionError, setActionError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const connectors = useQuery({
    queryKey: ["discovery-connectors"],
    queryFn: discoveryApi.listConnectors,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["discovery-connectors"] });
  };

  const closeForm = () => {
    setFormMode(null);
    setEditingConnector(undefined);
    setDraft(emptyDraft);
  };

  const createConnector = useMutation({
    mutationFn: discoveryApi.createConnector,
    onSuccess: async (connector) => {
      setNotice(`${providerLabels[connector.provider_type]} 连接器已保存，请先完成连接测试。`);
      setActionError(undefined);
      closeForm();
      await refresh();
    },
    onError: (error) => setActionError(displayError(error, "无法保存连接器。")),
  });

  const updateConnector = useMutation({
    mutationFn: ({ connectorId, payload }: { connectorId: string; payload: DiscoveryConnectorUpdate }) =>
      discoveryApi.updateConnector(connectorId, payload),
    onSuccess: async () => {
      setNotice("连接器设置已更新。");
      setActionError(undefined);
      closeForm();
      await refresh();
    },
    onError: (error) => setActionError(displayError(error, "无法更新连接器。")),
  });

  const toggleConnector = useMutation({
    mutationFn: ({ connectorId, enabled }: { connectorId: string; enabled: boolean }) =>
      discoveryApi.updateConnector(connectorId, { enabled }),
    onSuccess: async (connector) => {
      setNotice(connector.enabled ? "连接器已启用，请在发起任务前测试。" : "连接器已停用。");
      setActionError(undefined);
      await refresh();
    },
    onError: (error) => setActionError(displayError(error, "无法修改连接器状态。")),
  });

  const testConnector = useMutation({
    mutationFn: discoveryApi.testConnector,
    onSuccess: async (result) => {
      setNotice(
        result.status === "healthy"
          ? `连接测试通过，耗时 ${result.latency_ms} ms。`
          : result.error_code
            ? `测试未通过：${result.error_code}`
            : "测试未通过，请检查连接器配置。",
      );
      setActionError(undefined);
      await refresh();
    },
    onError: (error) => setActionError(displayError(error, "无法测试连接器。")),
  });

  const removeConnector = useMutation({
    mutationFn: discoveryApi.removeConnector,
    onSuccess: async () => {
      setNotice("连接器已删除，本地凭据已清除。");
      setActionError(undefined);
      await refresh();
    },
    onError: (error) => setActionError(displayError(error, "无法删除连接器。")),
  });

  const openCreate = (providerType: DiscoveryProviderType) => {
    if (isProviderAtLimit(providerType)) {
      setActionError(`${providerLabels[providerType]} 已达到 ${connectorsPerProviderLimit} 个连接器上限。`);
      return;
    }
    setActionError(undefined);
    setNotice(undefined);
    setEditingConnector(undefined);
    setDraft({ ...emptyDraft, providerType });
    setFormMode("create");
  };

  const openEdit = (connector: DiscoveryConnector) => {
    setActionError(undefined);
    setNotice(undefined);
    setEditingConnector(connector);
    setDraft(toDraft(connector));
    setFormMode("edit");
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActionError(undefined);
    setNotice(undefined);

    if (formMode === "create") {
      if (isProviderAtLimit(draft.providerType)) {
        setActionError(`${providerLabels[draft.providerType]} 已达到 ${connectorsPerProviderLimit} 个连接器上限。`);
        return;
      }
      const payload: DiscoveryConnectorCreate = {
        name: draft.name.trim(),
        provider_type: draft.providerType,
        api_key: draft.apiKey.trim(),
        enabled: draft.enabled,
        configuration: toConfiguration(draft.defaultCountry),
      };
      createConnector.mutate(payload);
      return;
    }

    if (formMode === "edit" && editingConnector) {
      const payload: DiscoveryConnectorUpdate = {
        name: draft.name.trim(),
        enabled: draft.enabled,
        configuration: toConfiguration(draft.defaultCountry),
      };
      const apiKey = draft.apiKey.trim();
      if (apiKey) {
        payload.api_key = apiKey;
      }
      updateConnector.mutate({ connectorId: editingConnector.id, payload });
    }
  };

  const isSaving = createConnector.isPending || updateConnector.isPending;
  const error = actionError ?? (connectors.error instanceof Error ? connectors.error.message : undefined);
  const isWorking = toggleConnector.isPending || testConnector.isPending || removeConnector.isPending;
  const providerCount = (providerType: DiscoveryProviderType) =>
    connectors.data?.filter((connector) => connector.provider_type === providerType).length ?? 0;
  const isProviderAtLimit = (providerType: DiscoveryProviderType) =>
    providerCount(providerType) >= connectorsPerProviderLimit;
  const activeProvider = editingConnector?.provider_type ?? draft.providerType;

  return (
    <section className="settings-console" aria-labelledby="discovery-settings-title">
      <header className="settings-intro">
        <div>
          <p className="eyebrow">SOURCE-AWARE QUESTION DISCOVERY</p>
          <h1 id="discovery-settings-title">题目发现连接器</h1>
          <p>
            配置题目发现服务来检索公开面试资料。每种服务最多保存 3 个自有连接器；凭据只会由本地后端加密保存。
          </p>
        </div>
        <div className={`readiness ${connectors.data?.some((connector) => connector.enabled && connector.status === "healthy") ? "ready" : "pending"}`}>
          {connectors.data?.some((connector) => connector.enabled && connector.status === "healthy") ? (
            <ShieldCheck aria-hidden="true" />
          ) : (
            <AlertTriangle aria-hidden="true" />
          )}
          <span>
            <strong>
              {connectors.data?.some((connector) => connector.enabled && connector.status === "healthy")
                ? "题目发现已就绪"
                : "需要一个已测试的连接器"}
            </strong>
            <small>DISCOVERY / LOCAL CREDENTIAL STORE</small>
          </span>
        </div>
      </header>

      <SettingsTabs />

      {error && <p className="settings-error" role="alert">{error}</p>}
      {!error && notice && <p className="form-hint" role="status">{notice}</p>}

      <div className="settings-grid">
        <section className="connection-panel" aria-labelledby="discovery-connectors-title">
          <div className="panel-heading">
            <div>
              <span>
                01 / DISCOVERY CONNECTORS · TAVILY {providerCount("tavily")}/{connectorsPerProviderLimit} · FIRECRAWL {providerCount("firecrawl")}/{connectorsPerProviderLimit}
              </span>
              <h2 id="discovery-connectors-title">发现连接器</h2>
            </div>
          </div>

          {formMode && (
            <form className="connection-form" onSubmit={submit}>
              <div className="provider-switch" aria-label="发现服务商">
                {formMode === "create" ? discoveryProviderTypes.map((providerType) => (
                  <button
                    key={providerType}
                    className={draft.providerType === providerType ? "selected" : ""}
                    type="button"
                    aria-pressed={draft.providerType === providerType}
                    disabled={isProviderAtLimit(providerType)}
                    onClick={() => setDraft((current) => ({ ...current, providerType }))}
                  >
                    {providerLabels[providerType]} · {providerCount(providerType)}/{connectorsPerProviderLimit}
                  </button>
                )) : (
                  <button className="selected" type="button" aria-pressed="true" disabled>
                    {providerLabels[activeProvider]} · 连接类型固定
                  </button>
                )}
              </div>
              <label>
                连接器名称
                <input
                  required
                  autoFocus
                  maxLength={120}
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  placeholder="例如：主检索连接器"
                />
              </label>
              <label>
                默认国家或地区
                <input
                  maxLength={64}
                  value={draft.defaultCountry}
                  onChange={(event) => setDraft({ ...draft, defaultCountry: event.target.value })}
                  placeholder="可选，例如：cn"
                />
              </label>
              <label>
                {formMode === "create" ? providerKeyLabel(activeProvider) : `轮换 ${providerKeyLabel(activeProvider)}`}
                <input
                  required={formMode === "create"}
                  type="password"
                  autoComplete="off"
                  value={draft.apiKey}
                  onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
                  placeholder={formMode === "create" ? "仅发送到本地后端" : "留空则保留当前密钥"}
                />
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
                />
                保存后启用此连接器
              </label>
              <div className="form-actions">
                <button className="quiet-button" type="button" onClick={closeForm} disabled={isSaving}>
                  取消
                </button>
                <button
                  className="primary-button"
                  type="submit"
                  disabled={isSaving || (formMode === "create" && isProviderAtLimit(draft.providerType))}
                >
                  <KeyRound size={15} aria-hidden="true" />
                  {formMode === "create" ? "保存连接器" : "保存修改"}
                </button>
              </div>
            </form>
          )}

          {connectors.isLoading && <div className="connection-empty">正在读取连接器。</div>}
          {!connectors.isLoading && discoveryProviderTypes.map((providerType) => {
            const providerConnectors = connectors.data?.filter((connector) => connector.provider_type === providerType) ?? [];
            const atLimit = isProviderAtLimit(providerType);
            const providerId = `discovery-${providerType}-connectors-title`;

            return (
              <section className="connection-provider-group" key={providerType} aria-labelledby={providerId}>
                <div className="panel-heading">
                  <div>
                    <span>{providerLabels[providerType].toUpperCase()} · 已保存 {providerConnectors.length}/{connectorsPerProviderLimit}</span>
                    <h2 id={providerId}>{providerLabels[providerType]} 连接器</h2>
                  </div>
                  <button
                    className="primary-button"
                    type="button"
                    aria-label={`新建 ${providerLabels[providerType]} 连接器`}
                    disabled={atLimit}
                    title={atLimit ? `已达到 ${connectorsPerProviderLimit} 个连接器上限` : `新建 ${providerLabels[providerType]} 连接器`}
                    onClick={() => openCreate(providerType)}
                  >
                    <Plus size={16} aria-hidden="true" /> 新建
                  </button>
                </div>
                <div className="connection-list" aria-live="polite">
                  {providerConnectors.map((connector) => (
                    <article className="connection-row" key={connector.id}>
                      <span className="provider-glyph">
                        {connector.provider_type === "tavily"
                          ? <SearchCheck size={18} aria-hidden="true" />
                          : <Flame size={18} aria-hidden="true" />}
                      </span>
                      <div className="connection-identity">
                        <strong>{connector.name}</strong>
                        <span>
                          {connector.has_api_key ? "凭据已本地保存" : "需要配置凭据"}
                          {connector.configuration.default_country ? ` / ${connector.configuration.default_country}` : ""}
                        </span>
                      </div>
                      <span className="protocol-label">{providerLabels[connector.provider_type].toUpperCase()}</span>
                      <span className={`connection-status ${connector.status}`}>
                        {connector.status === "healthy" && <Check size={13} aria-hidden="true" />}
                        {statusLabels[connector.status]}
                      </span>
                      <div className="row-actions">
                        <label title={connector.enabled ? "停用连接器" : "启用连接器"}>
                          <input
                            type="checkbox"
                            aria-label={connector.enabled ? `停用 ${connector.name}` : `启用 ${connector.name}`}
                            checked={connector.enabled}
                            disabled={isWorking}
                            onChange={(event) => {
                              setActionError(undefined);
                              setNotice(undefined);
                              toggleConnector.mutate({ connectorId: connector.id, enabled: event.target.checked });
                            }}
                          />
                        </label>
                        <button
                          type="button"
                          aria-label={`编辑 ${connector.name}`}
                          title="编辑连接器或轮换凭据"
                          disabled={isWorking}
                          onClick={() => openEdit(connector)}
                        >
                          <KeyRound size={15} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          aria-label={`测试 ${connector.name}`}
                          title="测试连接器"
                          disabled={!connector.enabled || isWorking}
                          onClick={() => {
                            setActionError(undefined);
                            setNotice(undefined);
                            testConnector.mutate(connector.id);
                          }}
                        >
                          <Play size={15} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          aria-label={`删除 ${connector.name}`}
                          title="删除连接器"
                          disabled={isWorking}
                          onClick={() => {
                            if (window.confirm(`删除“${connector.name}”并清除其本地凭据？`)) {
                              setActionError(undefined);
                              setNotice(undefined);
                              removeConnector.mutate(connector.id);
                            }
                          }}
                        >
                          <Trash2 size={15} aria-hidden="true" />
                        </button>
                      </div>
                    </article>
                  ))}
                  {providerConnectors.length === 0 && (
                    <div className="connection-empty">尚未添加 {providerLabels[providerType]} 连接器。</div>
                  )}
                </div>
              </section>
            );
          })}
        </section>

        <aside className="routing-panel" aria-labelledby="discovery-boundaries-title">
          <div className="panel-heading">
            <div>
              <span>02 / CONNECTOR BOUNDARIES</span>
              <h2 id="discovery-boundaries-title">来源控制</h2>
            </div>
          </div>
          <p className="routing-note">
            连接器使用固定服务端点。这里不会接收自定义端点、代理、任意请求头或回调地址；每种服务最多保存 3 个连接器，停用项仍计入数量。
          </p>
          <div className="role-list">
            <div className="role-row">
              <span><strong>凭据</strong><small>仅本地</small></span>
              <span>加密静态保存，永不返回给浏览器。</span>
            </div>
            <div className="role-row">
              <span><strong>连接测试</strong><small>健康检查</small></span>
              <span>仅保存安全状态和错误代码，不保存上游细节。</span>
            </div>
            <div className="role-row">
              <span><strong>来源审核</strong><small>必须</small></span>
              <span>搜索结果会先成为草稿候选题，再进入题库。</span>
            </div>
            <div className="role-row">
              <span><strong>删除</strong><small>不可撤销</small></span>
              <span>删除连接器会立即清除保存的凭据。</span>
            </div>
          </div>
          {connectors.data?.some((connector) => connector.last_error_code) && (
            <p className="routing-note" role="status">
              最近记录的错误：{connectors.data.find((connector) => connector.last_error_code)?.last_error_code}
            </p>
          )}
          {formMode === "edit" && editingConnector && !editingConnector.has_api_key && (
            <p className="routing-note">
              <X size={14} aria-hidden="true" />此连接器没有保存的凭据，请先添加后再测试。
            </p>
          )}
        </aside>
      </div>
    </section>
  );
}
