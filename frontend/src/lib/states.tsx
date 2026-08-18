import type { CSSProperties, ReactNode } from "react";
import { Icon, type IconName } from "../components/ui/Icon";

/** Loading skeleton block — shimmer bar, composable into cards/tables. */
export function Skeleton({ rows = 3, height = 14, style }: { rows?: number; height?: number; style?: CSSProperties }) {
  return (
    <div className="col" style={{ gap: 10, padding: "14px", ...style }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skl" style={{ height, width: `${100 - (i % 3) * 14}%` }} />
      ))}
    </div>
  );
}

/** Error state with retry — used by every page when the API call fails. */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="empty-note col center" style={{ gap: 8, padding: 28 }}>
      <Icon name="warning" size={18} style={{ color: "var(--amber)" }} />
      <span>{message}</span>
      {onRetry && (
        <button className="btn btn-secondary btn-sm" onClick={onRetry}>
          <Icon name="refresh" size={12} /> Retry
        </button>
      )}
    </div>
  );
}

/** Honest empty state — no fixture stand-ins. */
export function EmptyState({ icon = "box", children }: { icon?: IconName; children: ReactNode }) {
  return (
    <div className="empty-note col center" style={{ gap: 8, padding: 26 }}>
      <Icon name={icon} size={18} style={{ color: "var(--text-3)" }} />
      <span>{children}</span>
    </div>
  );
}
