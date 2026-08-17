import { useId } from "react";

/** Circular gauge ring (0..1). Center content rendered by caller. */
export function DonutGauge({
  value, size = 56, stroke = 5, color = "var(--green)", track = "rgba(148,170,220,0.13)", children,
}: {
  value: number;
  size?: number;
  stroke?: number;
  color?: string;
  track?: string;
  children?: React.ReactNode;
}) {
  const id = useId();
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <div style={{ position: "relative", width: size, height: size, flex: "none" }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)", display: "block" }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        <circle
          key={id}
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${(v * c).toFixed(1)} ${c.toFixed(1)}`}
        />
      </svg>
      {children && (
        <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>{children}</div>
      )}
    </div>
  );
}
