import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../ui/Logo";
import { Icon } from "../ui/Icon";
import { MangoAvatar } from "../ui/MangoAvatar";
import { Menu, MenuItem, SearchBox } from "../ui/controls";
import { useToast } from "../ui/Toast";
import { downloadFile } from "../ui/Modal";
import { api, ApiError } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { useWs } from "../../lib/useWs";
import type { SkillGap } from "../../data/types";

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

interface WsEvent {
  type: "event";
  kind: string;
  title: string;
  msg: string;
}

const SEARCHABLE: { label: string; path: string; icon: string }[] = [
  { label: "Overview", path: "/", icon: "overview" },
  { label: "Skills & Coverage", path: "/skills", icon: "skills" },
  { label: "Assets", path: "/assets", icon: "assets" },
  { label: "Worlds — Scene Editor", path: "/worlds", icon: "worlds" },
  { label: "Worlds — Live Evaluation", path: "/worlds?mode=live", icon: "play" },
  { label: "Sources", path: "/sources", icon: "sources" },
  { label: "Policy readiness", path: "/training", icon: "training" },
  { label: "Observability", path: "/observability/services", icon: "observability" },
  { label: "Settings", path: "/settings", icon: "settings" },
  { label: "API Keys", path: "/settings?tab=apikeys", icon: "lock" },
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
  const { data: overview } = useApi<{ skillGaps: SkillGap[] }>("/overview");

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

  const runAgent = async () => {
    const gap = overview?.skillGaps[0];
    if (!gap) {
      toast.push("err", "No skill gaps", "The agent needs a skill gap to target — check Overview once data loads");
      return;
    }
    try {
      const { jobId } = await api.post<{ jobId: string }>("/agent/run", { skillId: gap.name.toLowerCase().replace(/\s+/g, "-") });
      toast.push("ok", "Agent iteration started", `Job ${jobId} · targeting ${gap.name}`);
    } catch (e) {
      toast.push("err", "Agent failed to start", e instanceof ApiError ? e.message : String(e));
    }
  };

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
          <MenuItem icon="robot" onClick={runAgent}>Run diagnostics agent</MenuItem>
          <MenuItem icon="refresh" onClick={() => nav("/worlds")}>Open acceptance scenarios</MenuItem>
        </Menu>
        <Menu width={190} trigger={(open) => <button className={`tb-menu-btn ${open ? "open" : ""}`}>Help</button>}>
          <MenuItem icon="book" onClick={() => toast.push("info", "Documentation", "Docs ship with the backend integration")}>Documentation</MenuItem>
          <MenuItem icon="info" onClick={() => nav("/settings?tab=about")}>About RobotWorld</MenuItem>
        </Menu>
      </nav>

      <div className="tb-drag" />

      <div className="tb-search" ref={searchRef} style={{ position: "relative" }}>
        <SearchBox
          placeholder={`Search — ${loc.pathname === "/" ? "everything" : loc.pathname.split("/").filter(Boolean)[0] ?? "everything"}`}
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
        <NotificationsMenu />
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
          <MenuItem icon="chip" onClick={() => nav("/settings?tab=apikeys")}>API keys</MenuItem>
        </Menu>
        <WindowControls />
      </div>
    </header>
  );
}

/* ---- Notifications — fed by the backend event stream ------------------------ */
interface NotifItem {
  id: number;
  icon: "warning" | "check" | "refresh" | "info";
  title: string;
  sub: string;
  tone: "err" | "ok" | "info";
}

function NotificationsMenu() {
  const nav = useNavigate();
  const toast = useToast();
  const [items, setItems] = useState<NotifItem[]>([]);
  const nextId = useRef(1);

  useWs<WsEvent>("/events", {
    onMessage: (ev) => {
      if (ev.type !== "event") return;
      const tone: NotifItem["tone"] = ev.kind === "err" || ev.kind === "alert" ? "err" : ev.kind === "ok" ? "ok" : "info";
      const icon: NotifItem["icon"] = tone === "err" ? "warning" : tone === "ok" ? "check" : "info";
      setItems((xs) => [{ id: nextId.current++, icon, title: ev.title, sub: ev.msg, tone }, ...xs].slice(0, 20));
      toast.push(tone, ev.title, ev.msg);
    },
  });

  const dismiss = (id: number) => setItems((xs) => xs.filter((x) => x.id !== id));

  return (
    <Menu
      align="right"
      width={360}
      trigger={(o) => {
        return (
          <button className="icon-btn" title="Notifications" style={o ? { background: "var(--bg-hover)" } : undefined}>
            <Icon name="bell" size={15} />
            {items.length > 0 && <span className="badge-dot">{items.length}</span>}
          </button>
        );
      }}
    >
      <div className="notif-head">
        <span className="notif-title">Notifications</span>
        <button className="micro t2" onClick={() => { setItems([]); }}>Mark all read</button>
      </div>
      <div className="notif-body">
        {items.length === 0 && <div className="empty-note" style={{ padding: 22 }}>You're all caught up.</div>}
        {items.map((i) => (
          <div key={i.id} className={`notif-item ${i.tone}`} onClick={() => dismiss(i.id)}>
            <span className={`notif-ico ${i.tone}`}><Icon name={i.icon} size={13} /></span>
            <span className="col grow" style={{ gap: 1, minWidth: 0 }}>
              <span className="ellipsis" style={{ fontWeight: 580, fontSize: "var(--fs-body)" }}>{i.title}</span>
              <span className="micro t3 ellipsis">{i.sub}</span>
            </span>
            <Icon name="x" size={11} className="notif-x" />
          </div>
        ))}
      </div>
      <div className="notif-foot" onClick={() => nav("/observability/alerts")}>
        View all in Observability <Icon name="arrowRight" size={11} />
      </div>
    </Menu>
  );
}

export function StatusBar() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const h = await api.get<Health>("/health");
        if (!alive) return;
        setHealth(h);
        setDown(false);
      } catch {
        if (!alive) return;
        setDown(true);
      }
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <footer className="statusbar">
      <span className="sb-item">
        <span className={`status-dot ${down ? "" : "ok"}`} style={down ? { background: "var(--red)" } : undefined} />
        {down ? "Backend offline" : "Connected"}
      </span>
      <span className="sep" />
      <span className="sb-item">Articulated Door Validation Lab</span>
      <span className="grow" />
      <span className="sb-item"><Icon name="cube" size={11} /> Native Vulkan viewport</span>
      <span className="sep" />
      <span className="sb-item"><Icon name="chip" size={11} /> {health ? `${health.simulation.engine} ${health.simulation.version} · ${health.simulation.timestepHz} Hz` : "Simulator offline"}</span>
      <span className="sep" />
      <span className="sb-item mono">API {health ? `v${health.version}` : "—"}</span>
      <span className="sep" />
      <span>{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
    </footer>
  );
}
