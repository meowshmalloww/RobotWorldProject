import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/* ---- Badge ------------------------------------------------------------- */
export type BadgeTone = "green" | "amber" | "red" | "blue" | "purple" | "grey" | "orange" | "outline" | "live" | "teal";

export function Badge({
  tone = "grey",
  children,
  dot,
  icon,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  dot?: boolean;
  icon?: IconName;
}) {
  const cls = tone === "teal" ? "b-grey" : tone;
  return (
    <span className={`badge b-${cls}`} style={tone === "teal" ? { background: "var(--teal-soft)", color: "var(--teal)" } : undefined}>
      {dot && <span className="dot" />}
      {icon && <Icon name={icon} size={11} />}
      {children}
    </span>
  );
}

/* ---- Toggle ---------------------------------------------------------- */
export function Toggle({ checked, onChange, label }: { checked: boolean; onChange?: (v: boolean) => void; label?: string }) {
  return (
    <label className="toggle" title={label}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange?.(e.target.checked)} />
      <span className="track" />
      <span className="thumb" />
    </label>
  );
}

/* ---- Search input ----------------------------------------------------- */
export function SearchBox({
  placeholder = "Search",
  kbd,
  value,
  onChange,
  style,
}: {
  placeholder?: string;
  kbd?: string;
  value?: string;
  onChange?: (v: string) => void;
  style?: React.CSSProperties;
}) {
  return (
    <div className="search-box" style={style}>
      <span className="search-ico"><Icon name="search" size={13} /></span>
      <input className="input" placeholder={placeholder} value={value} onChange={(e) => onChange?.(e.target.value)} />
      {kbd && <kbd>{kbd}</kbd>}
    </div>
  );
}

/* ---- Tabs ------------------------------------------------------------- */
export function Tabs({ items, active, onChange }: { items: string[]; active: string; onChange: (v: string) => void }) {
  return (
    <div className="tabs">
      {items.map((t) => (
        <button key={t} className={t === active ? "on" : ""} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  );
}

/* ---- Segmented control ------------------------------------------------- */
export function Segmented<T extends string>({
  options, value, onChange,
}: {
  options: { value: T; label: ReactNode; icon?: IconName }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} size={12} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---- Pagination ---------------------------------------------------------- */
export function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  const items: (number | "…")[] = [];
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - page) <= 1) items.push(p);
    else if (items[items.length - 1] !== "…") items.push("…");
  }
  return (
    <div className="pagination">
      <button className="pg-btn" disabled={page <= 1} onClick={() => onPage(page - 1)}><Icon name="chevronLeft" size={12} /></button>
      {items.map((it, i) =>
        it === "…" ? (
          <span key={`e${i}`} className="pg-btn" style={{ pointerEvents: "none" }}>…</span>
        ) : (
          <button key={it} className={`pg-btn ${it === page ? "on" : ""}`} onClick={() => onPage(it)}>{it}</button>
        ),
      )}
      <button className="pg-btn" disabled={page >= pages} onClick={() => onPage(page + 1)}><Icon name="chevronRight" size={12} /></button>
    </div>
  );
}

/* ---- Menu (popover dropdown) ---------------------------------------------- */
export function Menu({
  trigger, children, align = "left", width,
}: {
  trigger: (open: boolean) => ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div ref={ref} style={{ position: "relative", display: "inline-flex" }}>
      <span onClick={() => setOpen((o) => !o)} style={{ display: "inline-flex" }}>{trigger(open)}</span>
      {open && (
        <div className="menu" style={{ top: "calc(100% + 5px)", [align]: 0, width }} onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ icon, children, onClick }: { icon?: IconName; children: ReactNode; onClick?: () => void }) {
  return (
    <button onClick={onClick}>
      {icon && <Icon name={icon} size={13} style={{ color: "var(--text-3)" }} />}
      {children}
    </button>
  );
}

/* ---- X/Y/Z inspector vector row -------------------------------------------- */
export function VecInput({ values, onChange }: { values: [string, string, string]; onChange?: (axis: number, v: string) => void }) {
  const axes = ["X", "Y", "Z"] as const;
  return (
    <div className="vec-row">
      {axes.map((a, i) => (
        <span key={a} className="vec-cell">
          <span className={`axis-tag axis-${a.toLowerCase()}`}>{a}</span>
          <input className="input" value={values[i]} onChange={(e) => onChange?.(i, e.target.value)} />
        </span>
      ))}
    </div>
  );
}

/* ---- Collapsible inspector section ------------------------------------------ */
export function InspSection({
  title, children, defaultOpen = true, right,
}: {
  title: string; children: ReactNode; defaultOpen?: boolean; right?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="insp-section">
      <div className={`insp-head ${open ? "open" : ""}`} onClick={() => setOpen(!open)}>
        <span className="caret"><Icon name="chevronRight" size={11} /></span>
        {title}
        {right && <span style={{ marginLeft: "auto" }} onClick={(e) => e.stopPropagation()}>{right}</span>}
      </div>
      {open && <div className="insp-body">{children}</div>}
    </div>
  );
}

/* ---- Delta chip ---------------------------------------------------------------- */
export function Delta({ value, dir, goodWhen, label }: { value: string; dir: "up" | "down" | "flat"; goodWhen?: "up" | "down"; label?: string }) {
  let cls: "up" | "down" | "flat" = dir;
  if (goodWhen) cls = dir === goodWhen ? "up" : dir === "flat" ? "flat" : "down";
  return (
    <span className={`delta ${cls}`}>
      {dir === "up" && <Icon name="arrowUp" size={10} />}
      {dir === "down" && <Icon name="arrowDown" size={10} />}
      {value}
      {label && <span style={{ color: "var(--text-3)", fontWeight: 450 }}>{label}</span>}
    </span>
  );
}

/* ---- Status badge mapping used across pages ------------------------------------ */
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { tone: "green" | "amber" | "red" | "blue" | "grey" | "purple" | "orange" | "live"; label: string; icon?: IconName }> = {
    ready: { tone: "green", label: "Ready", icon: "check" },
    healthy: { tone: "green", label: "Healthy", icon: "check" },
    promoted: { tone: "green", label: "Promoted", icon: "check" },
    passed: { tone: "green", label: "Passed", icon: "check" },
    pass: { tone: "green", label: "Pass", icon: "check" },
    success: { tone: "green", label: "Success", icon: "check" },
    completed: { tone: "green", label: "Completed", icon: "check" },
    improving: { tone: "amber", label: "Improving", icon: "clock" },
    in_training: { tone: "amber", label: "In Training", icon: "clock" },
    in_progress: { tone: "blue", label: "In Progress", icon: "refresh" },
    building: { tone: "blue", label: "Building", icon: "refresh" },
    running: { tone: "green", label: "Running", icon: "refresh" },
    testing: { tone: "amber", label: "Testing", icon: "clock" },
    needs_data: { tone: "amber", label: "Needs data", icon: "warning" },
    at_risk: { tone: "orange", label: "At Risk", icon: "warning" },
    warn: { tone: "amber", label: "Warn", icon: "warning" },
    weak: { tone: "red", label: "Weak", icon: "warning" },
    failed: { tone: "red", label: "Failed", icon: "x" },
    fail: { tone: "red", label: "Fail", icon: "x" },
    blocked: { tone: "red", label: "Blocked", icon: "x" },
    degraded: { tone: "orange", label: "Degraded", icon: "warning" },
    repairing: { tone: "blue", label: "Repairing", icon: "refresh" },
    needs_attention: { tone: "red", label: "Needs Attention", icon: "warning" },
    not_started: { tone: "grey", label: "Not Started" },
    pending: { tone: "grey", label: "Pending", icon: "clock" },
    draft: { tone: "grey", label: "Draft" },
    stopped: { tone: "grey", label: "Stopped" },
    error: { tone: "red", label: "Error", icon: "warning" },
    PHYSICS_VALIDATED: { tone: "green", label: "Physics validated", icon: "check" },
    STATIC_VALIDATED: { tone: "blue", label: "Static validated", icon: "check" },
    COMPILED: { tone: "blue", label: "Compiled", icon: "refresh" },
    IMPORTED: { tone: "grey", label: "Imported" },
    REJECTED: { tone: "red", label: "Rejected", icon: "x" },
  };
  const m = map[status] ?? { tone: "grey" as const, label: status };
  return <Badge tone={m.tone} icon={m.icon}>{m.label}</Badge>;
}
