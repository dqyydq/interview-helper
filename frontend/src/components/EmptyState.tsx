import type { LucideIcon } from "lucide-react";
import { useId, type ReactNode } from "react";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  action: ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  const titleId = useId();
  const classes = ["empty-state", "empty-state--guided", className].filter(Boolean).join(" ");

  return (
    <section className={classes} aria-labelledby={titleId}>
      <div className="empty-state__icon" aria-hidden="true">
        <Icon size={28} strokeWidth={1.6} />
      </div>
      <div className="empty-state__copy">
        <h2 id={titleId}>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="empty-state__action">{action}</div>
    </section>
  );
}
