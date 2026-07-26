import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface SettingsSectionHeadingProps {
  icon: LucideIcon;
  label: string;
  title: string;
  description: string;
  titleId?: string;
  action?: ReactNode;
}

/** A presentational heading shared by settings cards; it intentionally owns no state. */
export function SettingsSectionHeading({
  action,
  description,
  icon: Icon,
  label,
  title,
  titleId,
}: SettingsSectionHeadingProps) {
  return (
    <header className="settings-section-heading">
      <span className="settings-section-heading__icon" aria-hidden="true">
        <Icon size={18} strokeWidth={1.7} />
      </span>
      <div className="settings-section-heading__copy">
        <p>{label}</p>
        <h2 id={titleId}>{title}</h2>
        <span>{description}</span>
      </div>
      {action && <div className="settings-section-heading__action">{action}</div>}
    </header>
  );
}
