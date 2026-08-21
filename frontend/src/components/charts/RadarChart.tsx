interface RadarDatum {
  label: string;
  value: number;
}

function point(index: number, count: number, radius: number, center: number) {
  const angle = -Math.PI / 2 + (index * Math.PI * 2) / count;
  return [center + Math.cos(angle) * radius, center + Math.sin(angle) * radius] as const;
}

/** Compact evidence radar. Every vertex comes directly from persisted API values. */
export function RadarChart({ data, size = 250 }: { data: RadarDatum[]; size?: number }) {
  if (data.length < 3) return null;
  const center = size / 2;
  const radius = size * 0.31;
  const polygon = (scale: number) => data.map((_, index) => point(index, data.length, radius * scale, center).join(",")).join(" ");
  const values = data.map((item, index) => point(index, data.length, radius * Math.max(0, Math.min(100, item.value)) / 100, center).join(",")).join(" ");

  return (
    <div className="radar-chart" style={{ width: size, maxWidth: "100%" }}>
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Measured skill coverage radar">
        {[1, .75, .5, .25].map((ring) => <polygon key={ring} points={polygon(ring)} className="radar-grid" />)}
        {data.map((_, index) => {
          const [x, y] = point(index, data.length, radius, center);
          return <line key={index} x1={center} y1={center} x2={x} y2={y} className="radar-axis" />;
        })}
        <polygon points={values} className="radar-value" />
        {data.map((item, index) => {
          const [x, y] = point(index, data.length, radius * 1.28, center);
          return <text key={item.label} x={x} y={y} textAnchor="middle" dominantBaseline="middle">{item.label}</text>;
        })}
      </svg>
    </div>
  );
}
