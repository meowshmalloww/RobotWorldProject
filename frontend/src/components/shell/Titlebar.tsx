import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../ui/Logo";
import { Icon } from "../ui/Icon";
import { MangoAvatar } from "../ui/MangoAvatar";
import { Menu, MenuItem, SearchBox } from "../ui/controls";
import { useToast } from "../ui/Toast";
import { downloadFile } from "../ui/Modal";
import { api, ApiError } from "../../lib/api";

const isElectron = typeof window !== "undefined" && !!window.robotworld?.isElectron;

interface Health {
  status: string;
  version: string;
  uptimeS: number;
  database: string;
  signoz: string;
  brightdata: string;
  port: string;
  openai: string;
  simulation: { engine: string; version: string; timestepHz: number };
}

interface Performance {
  cpuPercent: number | null;
  memory: { usedGb: number; totalGb: number; percent: number };
  gpu: { available: boolean; name?: string; memoryUsedMb?: number; memoryTotalMb?: number; utilizationPercent?: number };
}

interface WorldFrameMetric {
  fps: number;
  latencyMs: number | null;
  active: boolean;
  at: number;
}

const SEARCHABLE: { label: string; path: string; icon: string }[] = [
  { label: "Overview", path: "/", icon: "overview" },
  { label: "Skills & Coverage", path: "/skills", icon: "skills" },
  { label: "Assets", path: "/assets", icon: "assets" },
  { label: "Worlds - Scene Editor", path: "/worlds", icon: "worlds" },
  { label: "Failure Analysis & Curriculum", path: "/failure-analysis", icon: "warning" },
  { label: "Agent Control", path: "/agent-control", icon: "agent" },
  { label: "Sources", path: "/sources", icon: "sources" },
  { label: "Scraper Repair", path: "/scraper-repair", icon: "refresh" },
  { label: "Policy readiness", path: "/training", icon: "training" },
  { label: "Observability", path: "/observability/services", icon: "observability" },
  { label: "Settings", path: "/settings", icon: "settings" },
];

function WindowControls() {
  const [maxed, setMaxed] = useState(false);
  if (!isElectron) return null;
  const rw = window.robotworld!;
  void rw.isMaximized().then((m) => setMaxed(m));
  void rw.onMaximizedChange(setMaxed);
  return (
    <div className="tb-win">
      <button onClick={() => rw.minimize()} title="Minimize"><Icon name="minimize" size={13} /></button>
      <button onClick={() => rw.toggleMaximize()} title={maxed ? "Restore" : "Maximize"}>
        <Icon name={maxed ? "restoreWin" : "maximizeWin"} size={12} />
      </button>
      <button className="tb-close" onClick={() => rw.close()} title="Close"><Icon name="x" size={14} /></button>
    </div>
  );
}

export function Titlebar() {
  const nav = useNavigate();
  const loc = useLocation();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!searchOpen) return;
    const close = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) setSearchOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [searchOpen]);

  const searchResults = query.trim()
    ? SEARCHABLE.filter((s) => s.label.toLowerCase().includes(query.trim().toLowerCase()))
    : SEARCHABLE;

  return (
    <header className="titlebar">
      <div className="tb-brand clickable" onClick={() => nav("/")}>
        <Logo size={17} />
        RobotWorld
        <span className="tb-ver">v1.0.0</span>
      </div>

      <nav className="tb-menus">
        <Menu width={220} trigger={(open) => <button className={`tb-menu-btn ${open ? "open" : ""}`}>File</button>}>
          <MenuItem icon="plus" onClick={() => nav("/worlds")}>New world</MenuItem>
          <div className="menu-sep" />
          <MenuItem icon="download" onClick={async () => {
            try {
              const data = await api.get<{ assets: unknown[] }>("/assets");
              downloadFile("robotworld-catalog.json", JSON.stringify({ exported: new Date().toISOString(), assets: data.assets }, null, 2));
              toast.push("ok", "Catalog exported", "robotworld-catalog.json");
            } catch (e) {
              toast.push("err", "Export failed", e instanceof ApiError ? e.message : String(e));
            }
          }}>Export catalog</MenuItem>
          <div className="menu-sep" />
          <MenuItem icon="x" onClick={() => (window.robotworld ? window.robotworld.close() : toast.push("info", "Desktop only", "Exit is available in the RobotWorld desktop app"))}>Exit</MenuItem>
        </Menu>
        <Menu width={210} trigger={(open) => <button className={`tb-menu-btn ${open ? "open" : ""}`}>View</button>}>
          <MenuItem icon="overview" onClick={() => nav("/")}>Overview</MenuItem>
          <MenuItem icon="worlds" onClick={() => nav("/worlds")}>Scene composer</MenuItem>
          <MenuItem icon="observability" onClick={() => nav("/observability/services")}>Observability</MenuItem>
          <div className="menu-sep" />
          <MenuItem icon="grid" onClick={() => {
            ["robotworld.worlds.leftW", "robotworld.worlds.rightW", "robotworld.worlds.shelfH"].forEach((key) => window.localStorage.removeItem(key));
            window.dispatchEvent(new Event("robotworld:reset-layout"));
            toast.push("ok", "Layout reset", "Hierarchy, viewport, console, and inspector restored");
          }}>Reset layout</MenuItem>
        </Menu>
        <Menu width={220} trigger={(open) => <button className={`tb-menu-btn ${open ? "open" : ""}`}>Run</button>}>
          <MenuItem icon="play" onClick={() => nav("/worlds?mode=live")}>Start live evaluation</MenuItem>
          <MenuItem icon="workflow" onClick={() => nav("/agent-control")}>Open agent control</MenuItem>
          <MenuItem icon="refresh" onClick={() => nav("/worlds")}>Open scene editor</MenuItem>
        </Menu>
          <Menu width={190} trigger={(open) => <button className={`tb-menu-btn ${open ? "open" : ""}`}>Help</button>}>
            <MenuItem icon="book" onClick={() => toast.push("info", "Documentation", "Docs ship with the backend integration")}>Documentation</MenuItem>
            <MenuItem icon="info" onClick={() => nav("/settings?tab=about")}>About RobotWorld</MenuItem>
        </Menu>
      </nav>

      <div className="tb-drag" />

      <div className="tb-search" ref={searchRef} style={{ position: "relative" }}>
        <SearchBox
          placeholder={`Search - ${loc.pathname === "/" ? "everything" : loc.pathname.split("/").filter(Boolean)[0] ?? "everything"}`}
          value={query}
          onChange={(v) => {
            setQuery(v);
            setSearchOpen(true);
          }}
        />
        {searchOpen && searchResults.length > 0 && (
          <div className="menu" style={{ top: "calc(100% + 5px)", left: 0, right: 0, width: "auto", position: "absolute" }}>
            <div className="menu-label">Go to</div>
            {searchResults.map((r) => (
              <MenuItem key={r.path} icon={r.icon as never} onClick={() => { nav(r.path); setSearchOpen(false); setQuery(""); }}>
                {r.label}
              </MenuItem>
            ))}
          </div>
        )}
      </div>

      <div className="tb-actions">
        <Menu
          align="right"
          width={180}
          trigger={() => (
            <button className="tb-user">
              <MangoAvatar size={22} />
              <Icon name="chevronDown" size={11} style={{ color: "var(--text-3)" }} />
            </button>
          )}
        >
          <div className="menu-label">Mango</div>
        <MenuItem icon="settings" onClick={() => nav("/settings")}>Settings</MenuItem>
        </Menu>
        <WindowControls />
      </div>
    </header>
  );
}

export function StatusBar() {
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [down, setDown] = useState(false);
  const [worldFrame, setWorldFrame] = useState<WorldFrameMetric | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const [, metrics] = await Promise.all([api.get<Health>("/health"), api.get<Performance>("/system/performance")]);
        if (!alive) return;
        setPerformance(metrics);
        setDown(false);
      } catch {
        if (!alive) return;
        setDown(true);
      }
    };
    poll();
    const id = setInterval(poll, 2_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const onWorldFrame = (event: Event) => {
      const detail = (event as CustomEvent<Omit<WorldFrameMetric, "at">>).detail;
      if (!detail || typeof detail.fps !== "number") return;
      setWorldFrame({ ...detail, at: window.performance.now() });
    };
    window.addEventListener("robotworld:world-frame", onWorldFrame);
    const stale = window.setInterval(() => {
      setWorldFrame((current) => current && window.performance.now() - current.at > 1500 ? null : current);
    }, 1000);
    return () => {
      window.removeEventListener("robotworld:world-frame", onWorldFrame);
      window.clearInterval(stale);
    };
  }, []);

  return (
    <footer className="statusbar" style={{ display: "flex", alignItems: "center", height: 24, padding: "0 10px", background: "var(--bg-panel-1)", borderTop: "1px solid var(--border)", fontSize: 11, color: "var(--text-3)" }}>
      <span className="sb-item">RobotWorld 1.0.0 {down ? "· backend offline" : ""}</span>
      <span className="sep" />
      <span className="sb-item">CPU {performance?.cpuPercent ?? "—"}{performance?.cpuPercent !== null && performance?.cpuPercent !== undefined ? "%" : ""}</span>
      <span className="sep" />
      <span className="sb-item">RAM {performance ? `${performance.memory.usedGb}/${performance.memory.totalGb} GB` : "—"}</span>
      <span className="sep" />
      <span className="sb-item" title="Measured frames rendered by the interactive world viewport">Editor {worldFrame?.active && worldFrame.fps > 0 ? `${worldFrame.fps} FPS` : "— FPS"}</span>
      <span className="grow" />
      <span className="sb-item">GPU {performance?.gpu.available ? `${performance.gpu.utilizationPercent}% · ${performance.gpu.memoryUsedMb}/${performance.gpu.memoryTotalMb} MB` : "unavailable"}</span>
    </footer>
  );
}
