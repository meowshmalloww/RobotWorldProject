import { NavLink, useNavigate } from "react-router-dom";
import { Logo } from "../ui/Logo";
import { Icon, type IconName } from "../ui/Icon";

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Overview", icon: "overview", end: true },
  { to: "/skills", label: "Skills", icon: "skills" },
  { to: "/assets", label: "Assets", icon: "assets" },
  { to: "/worlds", label: "Worlds", icon: "worlds" },
  { to: "/sources", label: "Sources", icon: "sources" },
  { to: "/training", label: "Training", icon: "training" },
  { to: "/observability", label: "Observability", icon: "observability" },
  { to: "/services", label: "Services", icon: "services" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const nav = useNavigate();
  return (
    <aside className="sidebar">
      <div className="sidebar-brand clickable" onClick={() => nav("/")} title="WorldOps">
        <Logo size={22} />
        <span className="wordmark">WorldOps</span>
      </div>
      <nav className="sidebar-nav">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
            title={n.label}
          >
            <span className="nav-ico"><Icon name={n.icon} size={15} /></span>
            <span className="nav-label">{n.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-foot">
        {!collapsed && (
          <button className="workspace-switch" style={{ marginBottom: 6 }}>
            <span className="ws-text grow">
              <span className="ws-label">Workspace</span>
              <br />
              <span className="ws-name">Zero Downtime Project</span>
            </span>
            <Icon name="chevronDown" size={12} />
          </button>
        )}
        <button className="sidebar-collapse" onClick={onToggle} title={collapsed ? "Expand" : "Collapse"}>
          <Icon name={collapsed ? "chevronRight" : "chevronsLeft"} size={14} />
          <span className="nav-label">Collapse</span>
        </button>
      </div>
    </aside>
  );
}
