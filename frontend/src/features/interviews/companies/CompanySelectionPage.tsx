import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronRight,
  Clock3,
  FileSearch,
  Plus,
  ShieldCheck,
  Target,
  X,
} from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { companyApi } from "./api";
import type { CompanyDraft } from "./types";

const defaultDraft: CompanyDraft = {
  name: "",
  description: "",
  style_pack: {
    name: "自定义轮次草案",
    supported_roles: ["llm_application_engineer"],
  },
  rounds: [
    { round_key: "round_1", name: "一面", sequence: 1, duration_minutes: 45 },
    { round_key: "round_2", name: "二面", sequence: 2, duration_minutes: 45 },
    { round_key: "round_3", name: "三面", sequence: 3, duration_minutes: 45 },
  ],
};

function companyMonogram(name: string) {
  return name.replace(/科技|集团|公司/g, "").slice(0, 2).toUpperCase();
}

export function CompanySelectionPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedCompanyId, setSelectedCompanyId] = useState<string>();
  const [selectedRoundId, setSelectedRoundId] = useState<string>();
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState(defaultDraft);

  const companies = useQuery({ queryKey: ["companies"], queryFn: companyApi.list });
  const createCompany = useMutation({
    mutationFn: companyApi.create,
    onSuccess: async (company) => {
      setSelectedCompanyId(company.id);
      setSelectedRoundId(company.latest_style_pack?.rounds[0]?.id);
      setDraft(defaultDraft);
      setFormOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["companies"] });
    },
  });

  const company =
    companies.data?.find((item) => item.id === selectedCompanyId) ?? companies.data?.[0];
  const rounds = company?.latest_style_pack?.rounds ?? [];
  const round = rounds.find((item) => item.id === selectedRoundId) ?? rounds[0];
  const stylePack = company?.latest_style_pack;
  const topicNames = round ? Object.keys(round.topic_weights) : [];

  const submitCompany = (event: FormEvent) => {
    event.preventDefault();
    createCompany.mutate(draft);
  };

  return (
    <section className="company-console" aria-labelledby="company-console-title">
      <aside className="company-rail" aria-label="公司列表">
        <div className="company-rail-heading">
          <div>
            <span>INTERVIEW TARGET</span>
            <h1 id="company-console-title">选择公司</h1>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="添加公司"
            onClick={() => setFormOpen(true)}
          >
            <Plus size={18} aria-hidden="true" />
          </button>
        </div>

        {companies.isLoading && <div className="rail-skeleton" aria-label="正在加载公司" />}
        {companies.isError && (
          <p className="inline-error">公司列表加载失败，请检查本地 API。</p>
        )}
        <div className="company-list">
          {companies.data?.map((item) => (
            <button
              key={item.id}
              className={item.id === company?.id ? "company-option active" : "company-option"}
              type="button"
              onClick={() => {
                setSelectedCompanyId(item.id);
                setSelectedRoundId(item.latest_style_pack?.rounds[0]?.id);
              }}
            >
              <span className="company-monogram" aria-hidden="true">
                {companyMonogram(item.name)}
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>{item.is_system ? "系统骨架" : "我的公司"}</small>
              </span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
          ))}
        </div>
        <button className="add-company-row" type="button" onClick={() => setFormOpen(true)}>
          <Plus size={18} aria-hidden="true" />
          添加公司
        </button>
      </aside>

      <main className="round-workspace">
        <header className="round-workspace-heading">
          <div>
            <span>ROUND PROFILE</span>
            <h2>选择面试轮次</h2>
          </div>
          {stylePack && (
            <span className={`pack-status ${stylePack.status}`}>
              {stylePack.status === "active" ? <Check size={13} /> : <FileSearch size={13} />}
              {stylePack.status === "active" ? "已启用" : "草案"}
            </span>
          )}
        </header>

        {rounds.length > 0 ? (
          <>
            <div className="round-switcher" role="tablist" aria-label="面试轮次">
              {rounds.map((item) => (
                <button
                  key={item.id}
                  className={item.id === round?.id ? "round-tab active" : "round-tab"}
                  type="button"
                  role="tab"
                  aria-selected={item.id === round?.id}
                  onClick={() => setSelectedRoundId(item.id)}
                >
                  <span>{String(item.sequence).padStart(2, "0")}</span>
                  <strong>{item.name}</strong>
                  <small>{item.duration_minutes} 分钟</small>
                </button>
              ))}
            </div>

            <article className="round-detail">
              <div className="round-title-block">
                <span className="round-accent" aria-hidden="true" />
                <div>
                  <h3>{round?.name} · {stylePack?.name}</h3>
                  <p>{round?.opening_style || "这是可编辑的轮次骨架，尚未添加具体开场与公司风格结论。"}</p>
                </div>
              </div>

              <section className="round-facts" aria-label="轮次信息">
                <div>
                  <Clock3 size={19} aria-hidden="true" />
                  <span><small>预计时长</small><strong>{round?.duration_minutes} 分钟</strong></span>
                </div>
                <div>
                  <Target size={19} aria-hidden="true" />
                  <span><small>压力等级</small><strong>{round?.pressure_level} / 5</strong></span>
                </div>
                <div>
                  <ShieldCheck size={19} aria-hidden="true" />
                  <span><small>资料状态</small><strong>{stylePack?.evidence_label}</strong></span>
                </div>
              </section>

              <section className="focus-section">
                <h4>关注维度</h4>
                {topicNames.length > 0 ? (
                  <div className="focus-tags">
                    {topicNames.map((topic) => <span key={topic}>{topic}</span>)}
                  </div>
                ) : (
                  <p>尚未添加有来源的关注维度。后续可在风格包中维护研究证据。</p>
                )}
              </section>

              <section className="follow-up-section">
                <h4>追问模式</h4>
                {round?.follow_up_patterns.length ? (
                  <ol>{round.follow_up_patterns.map((item) => <li key={item}>{item}</li>)}</ol>
                ) : (
                  <p>当前仅提供轮次结构，不推断该公司的追问习惯。</p>
                )}
              </section>
            </article>
          </>
        ) : (
          <div className="console-empty">
            <Building2 size={28} aria-hidden="true" />
            <h3>还没有可用轮次</h3>
            <p>添加一家公司后，系统会先建立一面、二面和三面骨架。</p>
          </div>
        )}
      </main>

      <aside className="style-preview" aria-labelledby="style-preview-title">
        <div className="preview-heading">
          <span>STYLE PREVIEW</span>
          <h2 id="style-preview-title">面试官预览</h2>
        </div>
        <section>
          <h3>沟通方式</h3>
          <p>{round?.opening_style || "待用户补充或后续研究 Agent 生成有来源的草案。"}</p>
        </section>
        <section>
          <h3>可能追问</h3>
          <p>{round?.follow_up_patterns[0] || "当前没有足够证据，不展示推测性结论。"}</p>
        </section>
        <section>
          <h3>信息来源</h3>
          <p>{stylePack?.evidence_label || "尚未选择风格包"}</p>
        </section>
        <div className="preview-caution">
          <AlertTriangle size={17} aria-hidden="true" />
          <p>公司风格仅作为模拟参数。无来源内容会明确标为草案，不代表官方标准。</p>
        </div>
      </aside>

      <footer className="selection-command">
        <div>
          <Building2 size={20} aria-hidden="true" />
          <span>
            <strong>{company?.name || "未选择公司"} · {round?.name || "未选择轮次"}</strong>
            <small>岗位方向：LLM 应用开发</small>
          </span>
        </div>
        <button className="secondary-button" type="button" onClick={() => setFormOpen(true)}>
          调整公司
        </button>
        <button
          className="primary-button"
          type="button"
          disabled={!company || !round}
          onClick={() => navigate(`/interviews/setup?company=${company?.id}&round=${round?.id}`)}
        >
          配置本场模拟
        </button>
      </footer>

      {formOpen && (
        <div className="dialog-backdrop" role="presentation">
          <form className="console-dialog" onSubmit={submitCompany}>
            <div className="dialog-heading">
              <div>
                <span>CUSTOM COMPANY</span>
                <h2>添加公司骨架</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setFormOpen(false)}>
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <label>
              公司名称
              <input
                required
                autoFocus
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="例如：某云计算公司"
              />
            </label>
            <label>
              说明
              <textarea
                value={draft.description}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                placeholder="说明资料范围或目标岗位，不要把传闻写成事实。"
              />
            </label>
            {createCompany.isError && <p className="inline-error">创建失败，名称可能已经存在。</p>}
            <div className="dialog-actions">
              <button className="secondary-button" type="button" onClick={() => setFormOpen(false)}>取消</button>
              <button className="primary-button" type="submit" disabled={createCompany.isPending}>创建轮次骨架</button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}
