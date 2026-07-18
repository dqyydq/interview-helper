import { ArrowRight, type LucideIcon } from "lucide-react";

interface EmptyStatePageProps {
  eyebrow: string;
  title: string;
  description: string;
  icon: LucideIcon;
}

export function EmptyStatePage({
  eyebrow,
  title,
  description,
  icon: Icon,
}: EmptyStatePageProps) {
  return (
    <section className="empty-state" aria-labelledby="page-title">
      <div className="page-index" aria-hidden="true">
        01
      </div>
      <div className="empty-state-copy">
        <p className="eyebrow">{eyebrow}</p>
        <h1 id="page-title">{title}</h1>
        <p className="description">{description}</p>
        <div className="baseline-status">
          <span className="status-dot" aria-hidden="true" />
          开发基线已就绪
        </div>
      </div>
      <div className="empty-state-action" aria-hidden="true">
        <Icon size={34} strokeWidth={1.3} />
        <span>功能接入中</span>
        <ArrowRight size={18} />
      </div>
    </section>
  );
}
