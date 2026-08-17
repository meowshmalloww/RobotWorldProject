/* Deterministic series generator — charts must render identical data on
   every load (fixtures stand in for the telemetry API). */

export function mulberry(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Smooth random-walk series in [min, max], deterministic per seed. */
export function series(seed: number, n: number, min: number, max: number, drift = 0): number[] {
  const rnd = mulberry(seed);
  const out: number[] = [];
  let v = min + (max - min) * (0.35 + rnd() * 0.3);
  for (let i = 0; i < n; i++) {
    v += (rnd() - 0.5) * (max - min) * 0.09 + drift;
    v = Math.max(min, Math.min(max, v));
    out.push(Number(v.toFixed(2)));
  }
  return out;
}

/** Monotonic-ish learning curve from a→b with noise, n samples. */
export function learningCurve(seed: number, n: number, from: number, to: number, noise = 2.2): number[] {
  const rnd = mulberry(seed);
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    const ease = 1 - Math.pow(1 - t, 2.1); // fast early gains
    const v = from + (to - from) * ease + (rnd() - 0.5) * noise;
    out.push(Number(v.toFixed(2)));
  }
  out[n - 1] = to;
  return out;
}

/** Decay curve (collisions) from a→b. */
export function decayCurve(seed: number, n: number, from: number, to: number, noise = 0.5): number[] {
  const rnd = mulberry(seed);
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    const ease = 1 - Math.pow(1 - t, 1.7);
    const v = from + (to - from) * ease + (rnd() - 0.5) * noise;
    out.push(Number(Math.max(0, v).toFixed(2)));
  }
  out[n - 1] = to;
  return out;
}

export const fmtInt = (n: number) => n.toLocaleString("en-US");
export const fmtPct = (n: number, d = 1) => `${n.toFixed(d)}%`;
export const fmtPp = (n: number, d = 1) => `${n > 0 ? "+" : ""}${n.toFixed(d)}pp`;
