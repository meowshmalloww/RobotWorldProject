import { useMemo, useRef, useState } from "react";

export interface LineSeries {
  name: string;
  data: number[];
  color: string;
  dashed?: boolean;
  /** emphasize end point with a value chip */
  endLabel?: string;
}

/**
 * Multi-series line chart with grid, axes labels, hover crosshair.
 * Pure SVG, deterministic rendering, sized to container via viewBox.
 */
export function LineChart({
  series,
  height = 200,
  xLabels,
  yMin,
  yMax,
  yTicks = 4,
  yFormat = (v: number) => `${v}`,
  xLabel,
  endBadges = true,
}: {
  series: LineSeries[];
  height?: number;
  xLabels?: string[];
  yMin?: number;
  yMax?: number;
  yTicks?: number;
  yFormat?: (v: number) => string;
  xLabel?: string;
  endBadges?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const W = 640;
  const H = height;
  const padL = 34, padR = 52, padT = 12, padB = xLabels || xLabel ? 26 : 10;
  const iw = W - padL - padR;
  const ih = H - padT - padB;

  const all = series.flatMap((s) => s.data);
  const lo = yMin ?? Math.min(...all);
  const hi = yMax ?? Math.max(...all);
  const range = hi - lo || 1;
  const n = Math.max(...series.map((s) => s.data.length));

  const px = (i: number) => padL + (i / (n - 1)) * iw;
  const py = (v: number) => padT + ih - ((v - lo) / range) * ih;

  const ticks = useMemo(
    () => Array.from({ length: yTicks + 1 }, (_, i) => lo + (range * i) / yTicks),
    [lo, range, yTicks],
  );

  const paths = series.map((s) => ({
    ...s,
    d: s.data.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" "),
  }));

  const onMove = (e: React.MouseEvent) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((x - padL) / iw) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };

  return (
    <div ref={ref} style={{ position: "relative", width: "100%" }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }}>
        {/* grid */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={py(t)} y2={py(t)} stroke="rgba(148,170,220,0.09)" strokeDasharray={t === lo ? "" : "3 4"} strokeWidth={1} />
            <text x={padL - 7} y={py(t) + 3} textAnchor="end" fontSize={9.5} fill="var(--text-3)" fontFamily="var(--font-mono)">
              {yFormat(t)}
            </text>
          </g>
        ))}
        {/* x labels */}
        {xLabels?.map((l, i) => (
          <text key={l} x={padL + (i / (xLabels.length - 1)) * iw} y={H - 9} textAnchor="middle" fontSize={9.5} fill="var(--text-3)">
            {l}
          </text>
        ))}
        {xLabel && !xLabels && (
          <text x={padL + iw / 2} y={H - 9} textAnchor="middle" fontSize={9.5} fill="var(--text-3)">{xLabel}</text>
        )}
        {/* series */}
        {paths.map((s) => (
          <g key={s.name}>
            <path
              d={s.d}
              fill="none"
              stroke={s.color}
              strokeWidth={s.dashed ? 1.4 : 1.9}
              strokeDasharray={s.dashed ? "5 4" : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity={s.dashed ? 0.85 : 1}
            />
            {!s.dashed && (
              <circle cx={px(s.data.length - 1)} cy={py(s.data[s.data.length - 1])} r={3} fill={s.color} stroke="#0D1017" strokeWidth={1.4} />
            )}
            {endBadges && s.endLabel && (
              <g transform={`translate(${W - padR + 8}, ${py(s.data[s.data.length - 1]) - 9})`}>
                <rect width={44} height={18} rx={4.5} fill={s.dashed ? "#283042" : s.color} opacity={s.dashed ? 0.9 : 1} />
                <text x={22} y={12.5} textAnchor="middle" fontSize={10} fontWeight={650} fill={s.dashed ? "var(--text-2)" : "#fff"} fontFamily="var(--font-mono)">
                  {s.endLabel}
                </text>
              </g>
            )}
          </g>
        ))}
        {/* hover crosshair */}
        {hover !== null && (
          <g>
            <line x1={px(hover)} x2={px(hover)} y1={padT} y2={H - padB} stroke="rgba(148,170,220,0.3)" strokeWidth={1} />
            {paths.map((s) => (
              <circle key={s.name} cx={px(hover)} cy={py(s.data[hover])} r={3.4} fill={s.color} stroke="#0D1017" strokeWidth={1.6} />
            ))}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={{ left: `${(px(hover) / W) * 100}%`, top: 0, transform: "translateX(-50%)" }}>
          {paths.map((s) => (
            <div key={s.name} className="row" style={{ gap: 7 }}>
              <i style={{ width: 8, height: 8, borderRadius: 2, background: s.color, display: "block" }} />
              <span className="t2">{s.name}</span>
              <b className="mono">{yFormat(s.data[hover])}</b>
            </div>
          ))}
          {xLabels && <div className="micro t3" style={{ marginTop: 3 }}>{xLabels[hover]}</div>}
        </div>
      )}
    </div>
  );
}
