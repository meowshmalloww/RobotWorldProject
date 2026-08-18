import { pctTone } from "../ui/helpers";

/**
 * Coverage heatmap — dimensions (rows) × difficulty bands (columns),
 * each cell colored by coverage value. Mirrors the editor convention:
 * green ≥80, amber 50–79, red <50, empty = untested.
 */
export function Heatmap({
  rows,
  cols = ["Easy", "Nominal", "Hard", "Extreme"],
}: {
  rows: { label: string; values: number[] }[];
  cols?: string[];
}) {
  return (
    <div className="heatmap">
      <div className="heatmap-row heatmap-head">
        <span className="heatmap-label" />
        {cols.map((c) => <span key={c} className="heatmap-colhead">{c}</span>)}
      </div>
      {rows.map((r) => (
        <div key={r.label} className="heatmap-row">
          <span className="heatmap-label" title={r.label}>{r.label}</span>
          {r.values.map((v, i) => (
            <span
              key={i}
              className="heatmap-cell"
              style={{
                background: v >= 80 ? "var(--green)" : v >= 50 ? "var(--amber)" : v > 0 ? "var(--red)" : "rgba(255,255,255,0.05)",
                color: v > 0 ? "#0D0F12" : "var(--text-3)",
              }}
              title={`${r.label} · ${cols[i]} · ${v > 0 ? v + "%" : "untested"}`}
            >
              {v > 0 ? v : ""}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Small donut with center label and legend. */
export function DistributionDonut({
  segments,
  size = 120,
  centerLabel,
  centerSub,
}: {
  segments: { label: string; value: number; color: string }[];
  size?: number;
  centerLabel?: string;
  centerSub?: string;
}) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const r = size / 2 - 6;
  const cx = size / 2, cy = size / 2;
  let acc = 0;
  const c = 2 * Math.PI * r;
  return (
    <div className="row" style={{ gap: 14, alignItems: "center" }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: "rotate(-90deg)", flex: "none" }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={10} />
        {segments.map((s) => {
          const len = (s.value / total) * c;
          const dash = `${len.toFixed(1)} ${c.toFixed(1)}`;
          const el = (
            <circle
              key={s.label}
              cx={cx} cy={cy} r={r} fill="none"
              stroke={s.color} strokeWidth={10}
              strokeDasharray={dash}
              strokeDashoffset={-((acc / total) * c).toFixed(1)}
            />
          );
          acc += s.value;
          return el;
        })}
        {centerLabel && (
          <text x={cx} y={cy - 1} textAnchor="middle" fontSize={15} fontWeight={700} fill="var(--text-1)" style={{ transform: "rotate(90deg)", transformOrigin: "center" }} fontFamily="var(--font-mono)">
            {centerLabel}
          </text>
        )}
        {centerSub && (
          <text x={cx} y={cy + 12} textAnchor="middle" fontSize={9} fill="var(--text-3)" style={{ transform: "rotate(90deg)", transformOrigin: "center" }}>
            {centerSub}
          </text>
        )}
      </svg>
      <div className="col" style={{ gap: 6 }}>
        {segments.map((s) => (
          <span key={s.label} className="row small t2" style={{ gap: 7 }}>
            <i style={{ width: 9, height: 9, borderRadius: 2, background: s.color, flex: "none" }} />
            {s.label} <b className="mono t1" style={{ fontWeight: 600 }}>{s.value}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export { pctTone };
