import { useEffect, useState } from "react";
import { Icon } from "../ui/Icon";
import { SearchBox } from "../ui/controls";
import { Menu, MenuItem } from "../ui/controls";

export function Topbar() {
  const [query, setQuery] = useState("");

  // ⌘K focuses search — a real shortcut, not decoration
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.querySelector<HTMLInputElement>(".topbar-search input")?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  return (
    <header className="topbar">
      <Menu
        width={220}
        trigger={() => (
          <button className="proj-switch">
            <span className="t3" style={{ fontWeight: 500 }}>Project</span>
            <span className="crumb-sep">/</span>
            Zero Downtime Project
            <Icon name="chevronDown" size={12} style={{ color: "var(--text-3)" }} />
          </button>
        )}
      >
        <div className="menu-label">Projects</div>
        <MenuItem icon="check">Zero Downtime Project</MenuItem>
        <MenuItem icon="worlds">Warehouse Pilot</MenuItem>
        <MenuItem icon="robot">Kitchen Generalization</MenuItem>
        <div className="menu-sep" />
        <MenuItem icon="plus">New project…</MenuItem>
      </Menu>

      <div className="topbar-search">
        <SearchBox placeholder="Search assets, skills, runs, worlds…" kbd="⌘K" value={query} onChange={setQuery} />
      </div>

      <div className="topbar-actions">
        <Menu
          align="right"
          width={300}
          trigger={(open) => (
            <button className="icon-btn" title="Notifications" style={open ? { background: "var(--bg-hover)" } : undefined}>
              <Icon name="bell" size={15} />
              <span className="badge-dot">3</span>
            </button>
          )}
        >
          <div className="menu-label">Notifications</div>
          <MenuItem icon="warning">
            <span className="col" style={{ gap: 1 }}>
              <span>Asset validation failed: missing collider</span>
              <span className="micro t3">ai.assets · 3m ago</span>
            </span>
          </MenuItem>
          <MenuItem icon="check">
            <span className="col" style={{ gap: 1 }}>
              <span>Run 9f2a7c1 completed · +8.7pp</span>
              <span className="micro t3">ai.training · 18m ago</span>
            </span>
          </MenuItem>
          <MenuItem icon="refresh">
            <span className="col" style={{ gap: 1 }}>
              <span>Scraper bd_retailer_us auto-repaired</span>
              <span className="micro t3">brightdata · 1h ago</span>
            </span>
          </MenuItem>
        </Menu>
        <button className="icon-btn" title="Help"><Icon name="help" size={15} /></button>
        <Menu
          align="right"
          width={200}
          trigger={() => (
            <button className="user-chip">
              <span className="avatar">ML</span>
              Morgan Lee
              <Icon name="chevronDown" size={12} style={{ color: "var(--text-3)" }} />
            </button>
          )}
        >
          <MenuItem icon="settings">Account settings</MenuItem>
          <MenuItem icon="chip">API keys</MenuItem>
          <div className="menu-sep" />
          <MenuItem icon="external">Sign out</MenuItem>
        </Menu>
      </div>
    </header>
  );
}

export function StatusBar() {
  return (
    <footer className="statusbar">
      <span>All times in PDT</span>
      <span className="grow" />
      <span className="row" style={{ gap: 6 }}><span className="status-dot" /> System healthy</span>
      <span className="sep" />
      <span className="mono">API v1.24.3</span>
      <span className="sep" />
      <span>© 2025 WorldOps</span>
    </footer>
  );
}
