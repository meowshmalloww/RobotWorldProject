import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Card, CardLink } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { Badge, SearchBox, StatusBadge } from "../components/ui/controls";
import { LineChart } from "../components/charts/LineChart";
import { TraceWaterfall } from "../components/charts/TraceWaterfall";
import { useToast } from "../components/ui/Toast";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { AgentInsight, Alert, LogLine, ServiceRow, Stat, TraceSpan } from "../data/types";

const TABS: { id: string; label: string; icon: IconName }[] = [
  { id: "services", label: "Services", icon: "services" },
  { id: "traces", label: "Traces", icon: "workflow" },
  { id: "metrics", label: "Metrics", icon: "chartBar" },
  { id: "logs", label: "Logs", icon: "terminal" },
  { id: "alerts", label: "Alerts", icon: "bell" },
];

export interface TraceMeta {
  traceId: string;
  iterationId: string;
  status: string;
  duration: string;
  durationMs: number;
  startTime: string;
  spans: number;
  errors: number;
}

interface TraceDetail {
  meta: TraceMeta;
  spans: TraceSpan[];
  insights: AgentInsight[];
}

/**
 * Observability — modelled on the SigNoz open-source console
 * (Services / Traces / Metrics / Logs / Alerts over one telemetry store).
 */
export default function Observability() {
  const { tab = "services" } = useParams();
  const nav = useNavigate();
  const active = TABS.some((t) => t.id === tab) ? tab : "services";
  const { data: alerts } = useApi<Alert[]>("/observability/alerts", []);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Observability</h1>
          <p className="page-sub">Measured OpenTelemetry data from the durable local store, with optional export to self-hosted SigNoz.</p>
        </div>
      </div>

      <div className="page-tabs">
        {TABS.map((t) => (
          <button key={t.id} className={active === t.id ? "on" : ""} onClick={() => nav(`/observability/${t.id}`)}>
            <Icon name={t.icon} size={13} />
            {t.label}
            {t.id === "alerts" && alerts && alerts.length > 0 && <span className="tab-count">{alerts.length}</span>}
          </button>
        ))}
      </div>

      {active === "services" && <ServicesTab onOpenTraces={() => nav("/observability/traces")} />}
      {active === "traces" && <TracesTab />}
      {active === "metrics" && <MetricsTab />}
      {active === "logs" && <LogsTab />}
      {active === "alerts" && <AlertsTab />}
    </div>
  );
}

/* ---- Services (SigNoz APM list) ------------------------------------------- */
function ServicesTab({ onOpenTraces }: { onOpenTraces: () => void }) {
  const [q, setQ] = useState("");
  const { data: stats, error: statsError, loading: statsLoading, refetch: refetchStats } = useApi<Stat[]>("/observability/stats");
  const { data: services, error, loading, refetch } = useApi<ServiceRow[]>("/observability/services");
  const rows = useMemo(() => (services ?? []).filter((s) => s.name.includes(q.toLowerCase())), [services, q]);

  return (
    <div className="col" style={{ gap: 10 }}>
      {statsError ? (
        <div className="card"><ErrorState message={statsError.message} onRetry={refetchStats} /></div>
      ) : (
        <div className="ov-stats" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          {statsLoading && !stats
            ? Array.from({ length: 4 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
            : (stats ?? []).map((s) => <StatCard key={s.label} stat={s} small />)}
        </div>
      )}

      <Card
        title="Services"
        flush
        right={<SearchBox placeholder="Search services…" value={q} onChange={setQ} style={{ width: 210 }} />}
      >
        {error ? (
          <ErrorState message={error.message} onRetry={refetch} />
        ) : loading && !services ? (
          <Skeleton rows={6} />
        ) : rows.length > 0 ? (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Service</th><th>Kind</th><th>Status</th><th>Version</th>
                  <th style={{ textAlign: "right" }}>Latency</th><th style={{ textAlign: "right" }}>Uptime</th>
                  <th style={{ textAlign: "right" }}>Restarts</th><th>GPU</th><th style={{ width: 30 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.name} className="rowlink" onClick={onOpenTraces} title="Open traces for this service">
                    <td>
                      <div className="cell-main">
                        <span className="cell-ico"><Icon name={s.kind === "integration" ? "link" : s.kind === "agent" ? "agent" : s.kind === "worker" ? "chip" : "services"} size={13} /></span>
                        <span className="mono" style={{ fontWeight: 580, fontSize: "var(--fs-small)" }}>{s.name}</span>
                      </div>
                    </td>
                    <td><Badge tone={s.kind === "core" ? "blue" : s.kind === "agent" ? "purple" : s.kind === "integration" ? "teal" : "grey"}>{s.kind}</Badge></td>
                    <td><StatusBadge status={s.status} /></td>
                    <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{s.version}</td>
                    <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{s.latency}</td>
                    <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{s.uptime}</td>
                    <td className="mono" style={{ textAlign: "right", fontSize: "var(--fs-small)", color: s.restarts > 2 ? "var(--amber)" : "var(--text-2)" }}>{s.restarts}</td>
                    <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{s.gpu ?? "—"}</td>
                    <td><button className="icon-btn btn-sm" onClick={(e) => e.stopPropagation()}><Icon name="dots" size={13} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon="services">No services registered — the backend catalog is empty.</EmptyState>
        )}
      </Card>

      {services && services.length > 0 && (
        <Card title="Pipeline Health" info>
          <ServiceMap services={services} />
        </Card>
      )}
    </div>
  );
}

/** Compact horizontal pipeline strip — services as chips with status dots. */
function ServiceMap({ services }: { services: ServiceRow[] }) {
  const tone: Record<string, string> = { core: "var(--accent)", agent: "var(--purple)", integration: "var(--teal)", worker: "var(--text-2)" };
  return (
    <div className="pipe-health">
      {services.map((s, i) => (
        <span key={s.name} className="pipe-health-row">
          <span className={`pipe-health-chip ${s.status === "running" ? "" : s.status}`}>
            <span className="pipe-health-dot" style={{ background: s.status === "degraded" ? "var(--amber)" : s.status === "stopped" ? "var(--red)" : "var(--green)" }} />
            <span style={{ color: tone[s.kind] }}>{s.name}</span>
          </span>
          {i < services.length - 1 && <Icon name="arrowRight" size={10} style={{ color: "var(--text-3)" }} />}
        </span>
      ))}
    </div>
  );
}

/* ---- Traces (waterfall) ------------------------------------------------------ */
function TracesTab() {
  const toast = useToast();
  const { data: settings } = useApi<{ integrations: { signoz: { queryEndpoint: string } } }>("/settings");
  const { data: list, error, loading, refetch } = useApi<{ traces: TraceMeta[] }>("/observability/traces");
  const [selected, setSelected] = useState<string | null>(null);
  const traces = list?.traces ?? [];
  const activeId = selected ?? traces[0]?.traceId ?? null;
  const { data: detail, error: detailError, loading: detailLoading } = useApi<TraceDetail>(
    activeId ? `/observability/traces/${activeId}` : null,
  );
  const openSigNoz = () => {
    const url = settings?.integrations.signoz.queryEndpoint?.replace(/\/$/, "");
    if (!url) {
      toast.push("info", "SigNoz is not configured", "Set the local Community UI endpoint under Settings → Integrations.");
      return;
    }
    if (window.robotworld?.openExternal) void window.robotworld.openExternal(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  };

  if (loading && !list) return <div className="card"><Skeleton rows={6} /></div>;
  if (error) return <div className="card"><ErrorState message={error.message} onRetry={refetch} /></div>;
  if (traces.length === 0) {
    return (
      <div className="card">
        <EmptyState icon="workflow">No traces yet — agent iterations will stream spans here once the pipeline runs.</EmptyState>
      </div>
    );
  }

  return (
    <div className="col" style={{ gap: 10 }}>
      <Card title="Traces" flush>
        <div className="table-scroll" style={{ maxHeight: 220, overflowY: "auto" }}>
          <table className="table">
            <thead>
              <tr><th>Trace ID</th><th>Iteration</th><th>Status</th><th style={{ textAlign: "right" }}>Duration</th><th style={{ textAlign: "right" }}>Spans</th><th style={{ textAlign: "right" }}>Errors</th><th>Start</th></tr>
            </thead>
            <tbody>
              {traces.map((t) => (
                <tr key={t.traceId} className={`rowlink ${activeId === t.traceId ? "selected" : ""}`} onClick={() => setSelected(t.traceId)}>
                  <td className="mono" style={{ fontSize: "var(--fs-small)", fontWeight: 580 }}>{t.traceId}</td>
                  <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{t.iterationId}</td>
                  <td><span style={{ fontWeight: 620, color: t.errors > 0 ? "var(--red)" : "var(--green)" }}>{t.status}</span></td>
                  <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{t.duration}</td>
                  <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{t.spans}</td>
                  <td className="mono" style={{ textAlign: "right", fontSize: "var(--fs-small)", color: t.errors > 0 ? "var(--red)" : "var(--text-2)" }}>{t.errors}</td>
                  <td className="t-muted mono" style={{ fontSize: "var(--fs-small)" }}>{t.startTime}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {detailLoading && !detail ? (
        <div className="card"><Skeleton rows={6} /></div>
      ) : detailError ? (
        <div className="card"><ErrorState message={detailError.message} onRetry={() => setSelected(null)} /></div>
      ) : detail ? (
        <>
          <Card
            title={
              <span className="row" style={{ gap: 8 }}>
                Trace — Autonomous Loop Iteration
                <span className="mono micro t3" style={{ fontWeight: 400 }}>{detail.meta.traceId}</span>
                <button
                  className="icon-btn btn-sm"
                  title="Copy trace ID"
                  onClick={() => {
                    navigator.clipboard?.writeText(detail.meta.traceId).catch(() => {});
                    toast.push("ok", "Copied", "Trace ID on clipboard");
                  }}
                >
                  <Icon name="copy" size={11} />
                </button>
              </span>
            }
            flush
            right={<button className="btn btn-ghost btn-sm" onClick={openSigNoz}>Open SigNoz Community <Icon name="external" size={11} /></button>}
          >
            <div className="row" style={{ gap: 14, padding: "8px 14px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
              <span className="micro t3">Iteration ID <span className="mono t2">{detail.meta.iterationId}</span></span>
              <span className="micro t3">Status <span style={{ fontWeight: 620, color: detail.meta.errors > 0 ? "var(--red)" : "var(--green)" }}>{detail.meta.status}</span></span>
              <span className="micro t3">Duration <span className="mono t2">{detail.meta.duration}</span></span>
              <span className="micro t3">Start <span className="mono t2">{detail.meta.startTime}</span></span>
            </div>
            <div style={{ padding: "8px 0 4px", overflowX: "auto" }}>
              {detail.spans.length > 0 ? <TraceWaterfall spans={detail.spans} /> : <EmptyState icon="workflow">No spans recorded for this trace.</EmptyState>}
            </div>
            <div className="row" style={{ gap: 14, padding: "8px 14px", borderTop: "1px solid var(--border)" }}>
              <span className="micro t3">Total duration <b className="mono t2">{detail.meta.duration}</b></span>
              <span className="micro t3">·</span>
              <span className="micro t3"><b className="mono t2">{detail.meta.spans} spans</b></span>
              <span className="micro t3">·</span>
              <span className="micro t3"><b className={`${detail.meta.errors > 0 ? "g-red" : "g-green"} mono`}>{detail.meta.errors} error{detail.meta.errors === 1 ? "" : "s"}</b></span>
            </div>
          </Card>

          <Card title={<span className="row" style={{ gap: 7 }}><Icon name="agent" size={14} style={{ color: "var(--purple)" }} /> Agent Insights</span>}>
            {detail.insights.length > 0 ? (
              <div className="col" style={{ gap: 10 }}>
                {detail.insights.map((i) => (
                  <div key={i.title} className="row" style={{ gap: 9, alignItems: "flex-start" }}>
                    <span className="cell-ico" style={{ width: 22, height: 22, flex: "none" }}>
                      <Icon name={i.icon as IconName} size={12} />
                    </span>
                    <span className="col" style={{ gap: 2 }}>
                      <span style={{ fontSize: "var(--fs-body)", fontWeight: 600 }}>{i.title}</span>
                      <span className="small t2">{i.body}</span>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon="agent">No agent insights for this trace.</EmptyState>
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}

/* ---- Metrics ------------------------------------------------------------------ */
interface MetricsData {
  series: { name: string; labels: string[]; values: number[]; count: number; latest: number | null; minimum: number | null; maximum: number | null }[];
  pointCount: number;
  store: string;
  signozExporting: boolean;
}

function MetricsTab() {
  const { data: stats } = useApi<Stat[]>("/observability/stats");
  const { data: m, error, loading, refetch } = useApi<MetricsData>("/observability/metrics");

  if (loading && !m) return <div className="card"><Skeleton rows={6} /></div>;
  if (error || !m) return <div className="card"><ErrorState message={error?.message ?? "Failed to load metrics"} onRetry={refetch} /></div>;

  const empty = m.series.length === 0;

  return (
    <div className="col" style={{ gap: 10 }}>
      {stats && stats.length > 0 && (
        <div className="ob-stats">
          {stats.map((s) => <StatCard key={s.label} stat={s} small />)}
        </div>
      )}
      <Card title="Pipeline metrics" right={<span className="row"><Badge tone={m.signozExporting ? "teal" : "grey"}>{m.signozExporting ? "SigNoz exporting" : "Local store"}</Badge><CardLink onClick={refetch}>Refresh</CardLink></span>}>
        {empty ? (
          <EmptyState icon="chartBar">No metric series yet — charts appear once the pipeline emits telemetry.</EmptyState>
        ) : (
          <div className="ob-metric-grid">
            {m.series.map((series, index) => <section className="ob-metric" key={series.name}>
              <div className="row between"><div className="col" style={{ gap: 2 }}><b className="mono small">{series.name}</b><span className="micro t3">{series.count} recorded points</span></div><span className="mono" style={{ fontSize: 18 }}>{series.latest === null ? "—" : Number(series.latest.toPrecision(4))}</span></div>
              <LineChart series={[{ name: series.name, data: series.values, color: `var(--series-${index % 6 + 1})` }]} height={112} yTicks={3} xLabels={series.labels} />
              <div className="row between micro t3"><span>min {series.minimum === null ? "—" : Number(series.minimum.toPrecision(4))}</span><span>max {series.maximum === null ? "—" : Number(series.maximum.toPrecision(4))}</span></div>
            </section>)}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ---- Logs ------------------------------------------------------------------------ */
function LogsTab() {
  const [logQ, setLogQ] = useState("");
  const [level, setLevel] = useState("All levels");
  const [paused, setPaused] = useState(false);
  const levelParam = level === "All levels" ? "" : `?level=${level}`;
  const { data: logs, error, loading, refetch } = useApi<LogLine[]>(`/observability/logs${levelParam}`);

  // 3s streaming poll unless paused
  useEffect(() => {
    if (paused) return;
    const id = setInterval(refetch, 3000);
    return () => clearInterval(id);
  }, [paused, refetch]);

  const filtered = (logs ?? []).filter(
    (l) => l.message.toLowerCase().includes(logQ.toLowerCase()) || l.service.includes(logQ),
  );

  return (
    <Card
      title="Logs"
      flush
      right={
        <span className="row" style={{ gap: 6 }}>
          <SearchBox placeholder="Search logs…" value={logQ} onChange={setLogQ} style={{ width: 200 }} />
          <select className="select" style={{ width: 110 }} value={level} onChange={(e) => setLevel(e.target.value)}>
            {["All levels", "INFO", "WARN", "ERROR", "DEBUG"].map((l) => <option key={l}>{l}</option>)}
          </select>
          <button className={`btn btn-sm ${paused ? "btn-ghost" : "btn-secondary"}`} onClick={() => setPaused(!paused)}>
            {paused ? <><Icon name="play" size={11} /> Resume</> : <><Icon name="pause" size={11} /> Pause</>}
          </button>
        </span>
      }
    >
      <div className="row micro t3" style={{ padding: "4px 12px", gap: 10, borderBottom: "1px solid var(--border)" }}>
        <span style={{ width: 86 }}>Time</span><span style={{ width: 52 }}>Level</span><span style={{ width: 130 }}>Service</span><span className="grow">Event</span>
      </div>
      <div className="log-list" style={{ maxHeight: 480, overflowY: "auto" }}>
        {error ? (
          <ErrorState message={error.message} onRetry={refetch} />
        ) : loading && !logs ? (
          <Skeleton rows={8} height={11} />
        ) : (
          <>
            {filtered.map((l, i) => (
              <div key={i} className="log-row">
                <span className="t3">{l.time}</span>
                <span className={`log-lvl ${l.level}`}>{l.level}</span>
                <span className="t2 ellipsis">{l.service}</span>
                <span className="ellipsis" style={{ color: l.level === "ERROR" ? "var(--red)" : "var(--text-1)" }}>{l.message}</span>
                <Icon name="chevronRight" size={11} style={{ color: "var(--text-3)" }} />
              </div>
            ))}
            {filtered.length === 0 && <div className="empty-note">No log lines match the current filter.</div>}
          </>
        )}
      </div>
      <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)" }}>
        <span className="micro t3">{filtered.length} lines · streamed from the telemetry exporter{paused ? " (paused)" : ""}</span>
      </div>
    </Card>
  );
}

/* ---- Alerts ------------------------------------------------------------------------- */
function AlertsTab() {
  const { data: alerts, error, loading, refetch } = useApi<Alert[]>("/observability/alerts");
  return (
    <div className="col" style={{ gap: 10 }}>
      <Card title="Alert rules" flush>
        {error ? (
          <ErrorState message={error.message} onRetry={refetch} />
        ) : loading && !alerts ? (
          <Skeleton rows={4} />
        ) : alerts && alerts.length > 0 ? (
          <div>
            {alerts.map((a) => (
              <div key={a.title} style={{ padding: "11px 14px", borderBottom: "1px solid var(--border)" }}>
                <div className="row" style={{ gap: 8 }}>
                  <span className="health-dot" style={{ background: a.severity === "high" ? "var(--red)" : "var(--amber)" }} />
                  <span style={{ fontWeight: 620, fontSize: "var(--fs-body)" }} className="ellipsis grow">{a.title}</span>
                  <span className={`micro ${a.pending ? "g-amber" : "g-red"}`} style={{ fontWeight: 640, whiteSpace: "nowrap" }}>
                    {a.pending ? "PENDING" : "FIRING"} · {a.firingFor}
                  </span>
                </div>
                <div className="micro t2" style={{ margin: "5px 0 7px" }}>
                  {a.meta.map(([k, v]) => `${k}: ${v}`).join("  ·  ")}
                </div>
                <div className="row" style={{ gap: 5 }}>
                  {a.tags.map((t) => <span key={t} className="tag" style={{ height: 19, fontSize: "var(--fs-micro)" }}>{t}</span>)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState icon="bell">No alerts firing — all pipelines within thresholds.</EmptyState>
        )}
      </Card>
    </div>
  );
}
