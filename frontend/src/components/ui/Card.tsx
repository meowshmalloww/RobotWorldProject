import type { CSSProperties, ReactNode } from "react";
import { Icon } from "./Icon";

export function Card({
  title,
  info,
  right,
  children,
  flush,
  style,
  className,
  pad,
}: {
  title?: ReactNode;
  info?: boolean;
  right?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  style?: CSSProperties;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section className={`card ${className ?? ""}`} style={style}>
      {title !== undefined && (
        <header className="card-head">
          <span className="card-title">
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
      {flush ? (
        <div className="card-body-flush">{children}</div>
      ) : (
        <div className="card-body" style={pad === false ? { padding: 0 } : undefined}>{children}</div>
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


