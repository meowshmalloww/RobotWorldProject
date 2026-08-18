/* Number formatting helpers shared by pages. */

export const fmtInt = (n: number) => n.toLocaleString("en-US");
export const fmtPct = (n: number, d = 1) => `${n.toFixed(d)}%`;
export const fmtPp = (n: number, d = 1) => `${n > 0 ? "+" : ""}${n.toFixed(d)}pp`;
