import { useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "./Icon";

/**
 * Panel card. `collapsible` adds a chevron that expands/shrinks the body
 * (animated grid-rows collapse, content unmounted visually but state kept).
 */
export function Card({
  title,
  info,
  right,
  children,
  flush,
  style,
  className,
  pad,
  collapsible = false,
  defaultCollapsed = false,
  onCollapse,
}: {
  title?: ReactNode;
  info?: boolean;
  right?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  style?: CSSProperties;
  className?: string;
  pad?: boolean;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  onCollapse?: (collapsed: boolean) => void;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    onCollapse?.(next);
  };

  const body = flush ? (
    <div className="card-body-flush">{children}</div>
  ) : (
    <div className="card-body" style={pad === false ? { padding: 0 } : undefined}>{children}</div>
  );

  return (
    <section className={`card ${collapsible ? "collapsible" : ""} ${collapsed ? "is-collapsed" : ""} ${className ?? ""}`} style={style}>
      {title !== undefined && (
        <header className="card-head">
          {collapsible && (
            <button className="card-chevron" onClick={toggle} title={collapsed ? "Expand" : "Collapse"} aria-expanded={!collapsed}>
              <Icon name="chevronDown" size={13} />
            </button>
          )}
          <span className="card-title" onDoubleClick={collapsible ? toggle : undefined} style={collapsible ? { cursor: "default" } : undefined}>
            {title}
            {info && (
              <span className="info-dot">
                <Icon name="help" size={12} />
              </span>
            )}
          </span>
          {right && <span className="head-right">{right}</span>}
        </header>
      )}
      {collapsible ? (
        <div className="card-collapse">
          <div className="cc-inner">{body}</div>
        </div>
      ) : (
        body
      )}
    </section>
  );
}

export function CardLink({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <a className="card-link" onClick={onClick} style={{ cursor: "pointer" }}>
      {children} <Icon name="arrowRight" size={12} />
    </a>
  );
}

export function Progress({
  value,
  tone,
  tall,
  style,
}: {
  value: number; // 0..100
  tone?: "green" | "amber" | "red" | "orange" | "blue";
  tall?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div className={`pbar ${tone ? `p-${tone}` : ""} ${tall ? "p-tall" : ""}`} style={style}>
      <i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}
