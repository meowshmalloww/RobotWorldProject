import { useEffect, useMemo, useState } from "react";

type LoadingVariant = "drive" | "dots" | "orbit";

const driveDelays = Array.from({ length: 9 }, (_, index) => {
  const row = Math.floor(index / 3);
  const column = index % 3;
  return (column + Math.abs(row - 1)) * 90;
});
const orbitOrder = [0, 1, 2, 5, 8, 7, 6, 3];

function formatElapsed(startedAt: number, now: number): string {
  const total = Math.max(0, (now - startedAt) / 1000);
  return total < 60 ? `${total.toFixed(1)}s` : `${Math.floor(total / 60)}m ${(total % 60).toFixed(1)}s`;
}

/** A visual indicator for a request that is actually in-flight. */
export function LoadingState({ label, variant = "drive", startedAt }: {
  label: string;
  variant?: LoadingVariant;
  startedAt: number;
}) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  const delays = useMemo(() => variant === "orbit"
    ? Array.from({ length: 9 }, (_, index) => {
      const position = orbitOrder.indexOf(index);
      return position < 0 ? null : position * 110;
    })
    : driveDelays, [variant]);

  return (
    <div className="loading-state" role="status" aria-live="polite">
      <span className={`loading-grid ${variant === "dots" ? "is-dots" : ""}`} aria-hidden="true">
        {delays.map((delay, index) => (
          <i key={index} style={delay === null ? undefined : { animationDelay: `${delay}ms`, animationDuration: `${variant === "orbit" ? 950 : 650}ms` }} />
        ))}
      </span>
      <span className="loading-label">{label}</span>
      <span className="loading-elapsed">{formatElapsed(startedAt, now)}</span>
    </div>
  );
}
