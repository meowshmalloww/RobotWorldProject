import { Icon, type IconName } from "../ui/Icon";
import type { TraceSpan } from "../../data/types";

function fmtDur(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)}s`;
  return `${Math.floor(s / 60)}m ${(s % 60).toFixed(1).replace(/\.0$/, "")}s`;
}

/**
 * Distributed-trace waterfall (SigNoz-style). Bars are positioned on a real
 * time axis derived from span start/duration.
 */
export function TraceWaterfall({
  spans, totalMs, height = 30,
}: {
  spans: TraceSpan[];
  totalMs?: number;
  height?: number;
}) {
  const total = totalMs ?? Math.max(...spans.map((s) => s.startMs + s.durationMs));
  const W = 900, rowH = height, padL = 0;
  const px = (ms: number) => padL + (ms / total) * (W - padL);

  // axis ticks: 6 buckets
  const ticks = Array.from({ length: 7 }, (_, i) => (total / 6) * i);
  const fmtTick = (ms: number) => {
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(0)}s`;
    return `${Math.floor(s / 60)}m ${(s % 60).toFixed(0)}s`;
  };

  return (
    <div>
      {/* axis header */}
      <div style={{ display: "grid", gridTemplateColumns: "168px 1fr", gap: 0, marginBottom: 2 }}>
        <div style={{ fontSize: "var(--fs-small)", color: "var(--text-3)", padding: "4px 10px" }}>Span</div>
        <svg viewBox={`0 0 ${W} 18`} style={{ width: "100%", height: 18, display: "block" }}>
          {ticks.map((t) => (
            <text key={t} x={px(t)} y={12} fontSize={9.5} fill="var(--text-3)" fontFamily="var(--font-mono)" textAnchor={t === 0 ? "start" : "middle"}>
              {fmtTick(t)}
            </text>
          ))}
        </svg>
      </div>
      <div>
        {spans.map((s) => (
          <div key={s.name} style={{ display: "grid", gridTemplateColumns: "168px 1fr", alignItems: "center" }}>
            <div className="row" style={{ gap: 7, padding: "0 10px", height: rowH, minWidth: 0 }}>
              <span style={{ color: "var(--text-3)", display: "inline-flex", flex: "none" }}>
                <Icon name={(s.icon as IconName) ?? "box"} size={12} />
              </span>
              <span className="ellipsis" style={{ fontSize: "var(--fs-small)", color: s.status === "error" ? "var(--red)" : "var(--text-1)", fontWeight: 550 }}>{s.name}</span>
            </div>
            <svg viewBox={`0 0 ${W} ${rowH}`} style={{ width: "100%", height: rowH, display: "block" }}>
              {ticks.map((t) => (
                <line key={t} x1={px(t)} x2={px(t)} y1={0} y2={rowH} stroke="rgba(148,170,220,0.07)" strokeWidth={1} />
              ))}
              <rect
                x={px(s.startMs)}
                y={rowH / 2 - 7}
                width={Math.max(6, px(s.startMs + s.durationMs) - px(s.startMs))}
                height={14}
                rx={3.5}
                fill={s.color}
                opacity={s.status === "error" ? 1 : 0.88}
                stroke={s.status === "error" ? "rgba(240,86,79,0.6)" : "transparent"}
              />
              <text
                x={px(s.startMs + s.durationMs) + 8}
                y={rowH / 2 + 3.5}
                fontSize={10}
                fill="var(--text-2)"
                fontFamily="var(--font-mono)"
              >
                {fmtDur(s.durationMs)}
              </text>
              {s.status === "error" && (
                <circle cx={px(s.startMs + s.durationMs) - 1} cy={rowH / 2} r={3} fill="var(--red)" />
              )}
            </svg>
          </div>
        ))}
      </div>
    </div>
  );
}
