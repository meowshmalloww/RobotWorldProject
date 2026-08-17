/** Pick a bar tone from a percentage using editor thresholds. */
export function pctTone(v: number): "green" | "amber" | "red" {
  if (v >= 80) return "green";
  if (v >= 50) return "amber";
  return "red";
}
