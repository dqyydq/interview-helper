import { BrainCircuit, Cpu } from "lucide-react";
import { NavLink } from "react-router-dom";

export function SettingsTabs() {
  return (
    <nav className="settings-tabs" aria-label="系统设置分类">
      <NavLink end to="/settings">
        <Cpu size={15} aria-hidden="true" />
        模型与 Agent
      </NavLink>
      <NavLink to="/settings/memory">
        <BrainCircuit size={15} aria-hidden="true" />
        长期记忆
      </NavLink>
    </nav>
  );
}
