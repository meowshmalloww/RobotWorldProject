import { useId } from "react";

export function Sparkline({
  data, width = 84, height = 30, color = "var(--series-1)", fill = true, strokeWidth = 1.6,
}: {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
  strokeWidth?: number;
}) {
  const id = useId();
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const px = (i: number) => (i / (data.length - 1)) * width;
  const py = (v: number) => height - 3 - ((v - min) / range) * (height - 6);
  const line = data.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block", overflow: "visible" }}>
      {fill && (
        <>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.22" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${line} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} stroke="none" />
        </>
      )}
      <path d={line} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={px(data.length - 1)} cy={py(data[data.length - 1])} r={2.2} fill={color} />
    </svg>
  );
}
