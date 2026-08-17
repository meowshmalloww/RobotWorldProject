import type { AgentInsight, Alert, LogLine, Stat, TraceSpan } from "./types";
import { series } from "./util";

export const obsStats: Stat[] = [
  { label: "Spans per minute", value: "4,812", icon: "observability", tint: "blue", delta: { value: "12.6%", dir: "up", label: "vs 30m ago" }, spark: series(71, 24, 3800, 5000, 20) },
  { label: "Error rate", value: "2.31%", icon: "warning", tint: "red", delta: { value: "0.61pp", dir: "up", label: "vs 30m ago" }, spark: series(72, 24, 1.2, 3.4, 0.02) },
  { label: "Pipeline latency (p95)", value: "18m 42s", icon: "clock", tint: "amber", delta: { value: "2m 31s", dir: "up", label: "vs 30m ago" }, spark: series(73, 24, 12, 19, 0.1) },
  { label: "GPU utilization", value: "71%", icon: "chip", tint: "purple", delta: { value: "6pp", dir: "up", label: "vs 30m ago" }, spark: series(74, 24, 55, 78, 0.4) },
  { label: "Successful repairs", value: "128", icon: "zap", tint: "teal", delta: { value: "18", dir: "up", label: "vs 30m ago" }, spark: series(75, 24, 90, 130, 1.4) },
  { label: "Simulation health", value: "98.2%", icon: "shield", tint: "green", delta: { value: "1.0pp", dir: "up", label: "vs 30m ago" }, spark: series(76, 24, 95, 99, 0.05) },
];

/* One autonomous-loop iteration as a distributed trace (2m 47.213s total) */
export const traceMeta = {
  traceId: "9f3b7e2a4c1b73d9",
  iterationId: "iter_2025-05-11T10:14:22.123Z",
  status: "Error",
  duration: "3m 50.220s",
  durationMs: 230_220,
  startTime: "10:14:22.123 AM",
  spans: 8,
  errors: 1,
};

export const traceSpans: TraceSpan[] = [
  { name: "robot.evaluate", service: "robot", startMs: 0, durationMs: 18_610, status: "ok", icon: "robot", color: "#4C8DFF" },
  { name: "failure.analyze", service: "ai.failure", startMs: 18_900, durationMs: 26_840, status: "ok", icon: "search", color: "#A077F0" },
  { name: "source.query", service: "worldops.source", startMs: 46_300, durationMs: 12_120, status: "ok", icon: "sources", color: "#3BBFC9" },
  { name: "brightdata.scrape", service: "brightdata", startMs: 59_100, durationMs: 48_950, status: "ok", icon: "worlds", color: "#4CC38A" },
  { name: "asset.generate", service: "ai.assets", startMs: 108_400, durationMs: 32_710, status: "error", icon: "cube", color: "#F0564F" },
  { name: "usd.compile", service: "usd.compiler", startMs: 141_500, durationMs: 9_430, status: "ok", icon: "usd", color: "#E5A13D" },
  { name: "training.run", service: "ai.training", startMs: 151_300, durationMs: 64_000, status: "ok", icon: "training", color: "#4CC38A" },
  { name: "evaluate_again", service: "robot", startMs: 215_700, durationMs: 14_520, status: "ok", icon: "gauge", color: "#A077F0" },
];

export const metricsSeries = {
  labels: ["09:45", "09:50", "09:55", "10:00", "10:05", "10:10"],
  latency: series(81, 30, 9, 15, 0.06),
  error: series(82, 30, 1, 3, 0.01),
  gpu: series(83, 30, 58, 76, 0.3),
  throughput: series(84, 30, 6, 11, 0.05),
};

export const logs: LogLine[] = [
  { time: "10:16.45.123", level: "ERROR", service: "ai.assets", message: "Asset validation failed: missing collider" },
  { time: "10:16.45.120", level: "WARN", service: "usd.compiler", message: "Material fallback applied to 3 meshes" },
  { time: "10:16.44.987", level: "INFO", service: "brightdata", message: "Scrape completed: 128 pages, 432 assets" },
  { time: "10:16.44.532", level: "INFO", service: "worldops.source", message: "Query returned 432 candidates" },
  { time: "10:16.44.120", level: "INFO", service: "ai.failure", message: "Failure pattern matched: MissingCollider" },
  { time: "10:16.43.998", level: "ERROR", service: "robot", message: "Grasp failed: collision detected" },
  { time: "10:16.43.210", level: "INFO", service: "robot", message: "Evaluation completed: success=false" },
];

export const alerts: Alert[] = [
  {
    title: "Collector completeness dropped below threshold", severity: "high", service: "Bright Data",
    firingFor: "7m 12s", meta: [["Source", "Bright Data"], ["Threshold", "< 85%"], ["Current", "68%"]], tags: ["data-quality", "collector", "+2"],
  },
  {
    title: "Asset validation failed: missing collider", severity: "high", service: "ai.assets",
    firingFor: "3m 45s", meta: [["Service", "ai.assets"], ["Impact", "High"], ["Occurrences", "24"]], tags: ["validation", "assets", "pipeline"],
  },
  {
    title: "Pipeline latency above baseline", severity: "medium", service: "pipeline",
    firingFor: "4m 01s", pending: true, meta: [["p95 latency", "18m 42s"], ["Baseline", "12m 10s"], ["Δ", "+52%"]], tags: ["performance", "latency"],
  },
];

export const agentInsights: AgentInsight[] = [
  { icon: "collider", title: "Missing collider is the top failure pattern", body: "Occurred 24 times (41%) across 6 worlds. Common in industrial assets from brightdata source." },
  { icon: "sources", title: "Collector completeness is degrading", body: "Scrape success rate dropped to 68%. Failures concentrated in category: industrial_equipment. Consider rotating proxies or widening query." },
  { icon: "training", title: "Training latency increased", body: "training.run p95 is up 52% vs baseline. GPU queueing detected on worker gpuw-05 (utilization 92%)." },
  { icon: "gauge", title: "High impact failures cluster in evaluate_again", body: "87% of iteration errors originate in post-training evaluation. Consider adding stricter pre-train validation." },
];
