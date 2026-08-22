import { NavLink } from "react-router-dom";
import { Icon, type IconName } from "../ui/Icon";

interface NavEntry { to: string; label: string; icon: IconName; end?: boolean }
interface NavGroup { section: string; items: NavEntry[] }

const NAV: NavGroup[] = [
  {
    section: "Build",
    items: [
      { to: "/", label: "Overview", icon: "overview", end: true },
      { to: "/skills", label: "Skills", icon: "skills" },
      { to: "/assets", label: "Assets", icon: "cube" },
      { to: "/worlds", label: "Worlds", icon: "worlds" },
    ],
  },
  {
    section: "Data",
    items: [
      { to: "/evidence", label: "Evidence", icon: "book" },
      { to: "/scraper-repair", label: "Scraper Repair", icon: "refresh" },
      { to: "/sources", label: "Sources", icon: "sources" },
    ],
  },
  {
    section: "Operate",
    items: [
      { to: "/models", label: "Models", icon: "hardDrive" },
      { to: "/robots", label: "Robots & Embodiments", icon: "robot" },
      { to: "/failure-analysis", label: "Failure Analysis", icon: "warning" },
      { to: "/agent-control", label: "Agent Control", icon: "agent" },
      { to: "/training", label: "Policy & Evaluation", icon: "training" },
      { to: "/observability/services", label: "Observability", icon: "observability" },
    ],
  },
];

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <aside className="sidebar">
      <div style={{ height: 8, flex: "none" }} />

      <nav className="sidebar-nav">
        {NAV.map((g) => (
          <div key={g.section}>
            {!collapsed && <div className="nav-section">{g.section}</div>}
            {g.items.map((n) => (
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
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <NavLink to="/settings" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title="Settings" style={{ marginBottom: 2 }}>
          <span className="nav-ico"><Icon name="settings" size={15} /></span>
          <span className="nav-label">Settings</span>
        </NavLink>
        <button className="sidebar-collapse" onClick={onToggle} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          <Icon name={collapsed ? "chevronRight" : "panelLeft"} size={14} />
          <span className="nav-label">Collapse</span>
        </button>
      </div>
    </aside>
  );
}
