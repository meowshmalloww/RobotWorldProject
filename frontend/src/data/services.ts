export interface ServiceRow {
  name: string;
  kind: "core" | "agent" | "integration" | "worker";
  status: "running" | "degraded" | "stopped";
  version: string;
  latency: string;
  uptime: string;
  restarts: number;
  gpu?: string;
}

export const services: ServiceRow[] = [
  { name: "api.gateway", kind: "core", status: "running", version: "v1.24.3", latency: "12 ms", uptime: "21d 4h", restarts: 0 },
  { name: "worldops.source", kind: "core", status: "running", version: "v1.24.3", latency: "86 ms", uptime: "21d 4h", restarts: 1 },
  { name: "ai.failure", kind: "agent", status: "running", version: "v0.9.2", latency: "1.9 s", uptime: "6d 11h", restarts: 2, gpu: "RTX 4080 · 31%" },
  { name: "ai.assets", kind: "agent", status: "degraded", version: "v0.9.2", latency: "4.2 s", uptime: "6d 11h", restarts: 5, gpu: "RTX 4080 · 64%" },
  { name: "ai.training", kind: "agent", status: "running", version: "v0.9.2", latency: "—", uptime: "6d 10h", restarts: 1, gpu: "RTX 4080 · 71%" },
  { name: "usd.compiler", kind: "worker", status: "running", version: "v1.4.0", latency: "2.1 s", uptime: "13d 2h", restarts: 0 },
  { name: "sim.isaac", kind: "worker", status: "running", version: "v4.5.0", latency: "—", uptime: "13d 2h", restarts: 0, gpu: "RTX 4080 · 58%" },
  { name: "brightdata.connector", kind: "integration", status: "running", version: "v2.3.1", latency: "640 ms", uptime: "21d 4h", restarts: 0 },
  { name: "signoz.exporter", kind: "integration", status: "running", version: "v0.111.0", latency: "9 ms", uptime: "21d 4h", restarts: 0 },
  { name: "port.sync", kind: "integration", status: "running", version: "v1.1.7", latency: "210 ms", uptime: "8d 19h", restarts: 1 },
];

export const settingsSections = {
  project: [
    { k: "Project name", v: "Zero Downtime Project" },
    { k: "Project ID", v: "wops_zdp_01" },
    { k: "Region", v: "us-west-2" },
    { k: "Created", v: "May 1, 2025" },
  ],
};
