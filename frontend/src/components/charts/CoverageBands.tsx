/**
 * Scenario coverage strip — four difficulty bands (Easy / Nominal / Hard /
 * Extreme), each band a split bar: covered portion vs gap.
 * Colors follow the editor convention: green covered, amber partial, red gap.
 */
export function CoverageBands({ bands, height = 8 }: { bands: [number, number, number, number]; height?: number }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 3, flex: 1 }}>
      {bands.map((v, i) => (
        <div key={i} style={{ height, background: "rgba(148,170,220,0.10)", borderRadius: 2.5, overflow: "hidden" }}>
          <div
            style={{
              width: `${v}%`,
              height: "100%",
              borderRadius: 2.5,
              background: v >= 80 ? "var(--green)" : v >= 50 ? "var(--amber)" : v > 15 ? "var(--red)" : "transparent",
            }}
          />
        </div>
      ))}
    </div>
  );
}

/** Horizontal stacked mini-bar used in weakness lists. */
export function ContribBar({ value, max = 35, color = "var(--series-1)" }: { value: number; max?: number; color?: string }) {
  return (
    <div style={{ height: 5, flex: 1, background: "rgba(148,170,220,0.10)", borderRadius: 3, overflow: "hidden" }}>
      <div style={{ width: `${(value / max) * 100}%`, height: "100%", background: color, borderRadius: 3 }} />
    </div>
  );
}
