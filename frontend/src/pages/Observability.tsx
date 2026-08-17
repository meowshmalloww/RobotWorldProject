import { useState } from "react";
import { Card, CardLink } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { SearchBox } from "../components/ui/controls";
import { LineChart } from "../components/charts/LineChart";
import { TraceWaterfall } from "../components/charts/TraceWaterfall";
import { agentInsights, alerts, logs, metricsSeries, obsStats, traceMeta, traceSpans } from "../data/observability";

export default function Observability() {
  const [live, setLive] = useState(true);
  const [logQ, setLogQ] = useState("");
  const filteredLogs = logs.filter((l) => l.message.toLowerCase().includes(logQ.toLowerCase()) || l.service.includes(logQ));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Observability</h1>
          <p className="page-sub">Pipeline telemetry, distributed traces, and agent-facing failure signals — backed by SigNoz.</p>
        </div>
        <div className="head-actions">
          <select className="select" style={{ width: 140 }}>
            <option>Last 30 minutes</option>
            <option>Last 15 minutes</option>
            <option>Last 1 hour</option>
            <option>Last 6 hours</option>
          </select>
          <button className={`btn btn-sm ${live ? "btn-secondary" : "btn-ghost"}`} onClick={() => setLive(true)} style={live ? { color: "var(--green)" } : undefined}>
            <span className="health-dot live" /> Live
          </button>
          <button className={`btn btn-sm ${!live ? "btn-secondary" : "btn-ghost"}`} onClick={() => setLive(false)}>
            <Icon name="refresh" size={12} /> Auto
          </button>
        </div>
      </div>

      <div className="ob-stats">
        {obsStats.map((s) => <StatCard key={s.label} stat={s} small />)}
      </div>

      <div className="ob-main">
        {/* Trace waterfall */}
        <Card
          title={
            <span className="row" style={{ gap: 8 }}>
              Trace — Autonomous Loop Iteration
              <span className="mono micro t3" style={{ fontWeight: 400 }}>Trace ID {traceMeta.traceId}</span>
              <button className="icon-btn btn-sm" title="Copy trace ID"><Icon name="copy" size={11} /></button>
            </span>
          }
          flush
          right={<button className="btn btn-ghost btn-sm">View in SigNoz <Icon name="external" size={11} /></button>}
        >
          <div className="row" style={{ gap: 14, padding: "8px 14px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
            <span className="micro t3">Iteration ID <span className="mono t2">{traceMeta.iterationId}</span></span>
            <span className="micro t3">Status <span className="g-red" style={{ fontWeight: 620 }}>Error</span></span>
            <span className="micro t3">Duration <span className="mono t2">{traceMeta.duration}</span></span>
            <span className="micro t3">Start Time <span className="mono t2">{traceMeta.startTime}</span></span>
          </div>
          <div style={{ padding: "8px 0 4px", overflowX: "auto" }}>
            <TraceWaterfall spans={traceSpans} />
          </div>
          <div className="row" style={{ gap: 14, padding: "8px 14px", borderTop: "1px solid var(--border)" }}>
            <span className="micro t3">Total duration <b className="mono t2">{traceMeta.duration}</b></span>
            <span className="micro t3">·</span>
            <span className="micro t3"><b className="mono t2">{traceMeta.spans} spans</b></span>
            <span className="micro t3">·</span>
            <span className="micro t3"><b className="g-red mono">{traceMeta.errors} error</b></span>
            <span className="grow" />
            <select className="select" style={{ width: 110, height: 24, fontSize: "var(--fs-small)" }}><option>Services (8)</option></select>
            <select className="select" style={{ width: 96, height: 24, fontSize: "var(--fs-small)" }}><option>Depth (2)</option></select>
          </div>
        </Card>

        {/* Metrics */}
        <Card title="Metrics" right={<CardLink>View all metrics</CardLink>}>
          <div className="legend" style={{ marginBottom: 6 }}>
            <span className="lg"><i style={{ background: "var(--series-1)" }} /> p95 latency</span>
            <span className="lg"><i style={{ background: "var(--series-6)" }} /> Error rate</span>
            <span className="lg"><i style={{ background: "var(--series-2)" }} /> GPU utilization</span>
            <span className="lg"><i style={{ background: "var(--series-4)" }} /> Throughput (spans/min)</span>
          </div>
          <LineChart
            series={[
              { name: "p95 latency (m)", data: metricsSeries.latency, color: "var(--series-1)" },
              { name: "Error rate (%)", data: metricsSeries.error, color: "var(--series-6)" },
              { name: "GPU (%)", data: metricsSeries.gpu.map((v) => v / 5.4), color: "var(--series-2)" },
              { name: "Throughput (k spans/min)", data: metricsSeries.throughput, color: "var(--series-4)" },
            ]}
            height={252}
            yMin={0}
            yMax={16}
            yTicks={4}
            yFormat={(v) => `${v.toFixed(0)}`}
            xLabels={metricsSeries.labels}
          />
        </Card>
      </div>

      <div className="ob-bottom">
        {/* Logs */}
        <Card
          title="Logs"
          flush
          right={
            <span className="row" style={{ gap: 6 }}>
              <SearchBox placeholder="Search logs…" value={logQ} onChange={setLogQ} style={{ width: 170 }} />
              <button className="icon-btn btn-sm" title="Stream settings"><Icon name="settings" size={12} /></button>
              <button className="icon-btn btn-sm" title="Filter"><Icon name="filter" size={12} /></button>
              <button className={`btn btn-sm ${live ? "btn-secondary" : "btn-ghost"}`} onClick={() => setLive(!live)} style={live ? { color: "var(--green)" } : undefined}>
                <span className="health-dot live" /> Live
              </button>
            </span>
          }
        >
          <div className="row micro t3" style={{ padding: "4px 12px", gap: 10, borderBottom: "1px solid var(--border)" }}>
            <span style={{ width: 74 }}>Time</span><span style={{ width: 52 }}>Level</span><span style={{ width: 120 }}>Service</span><span className="grow">Event</span>
          </div>
          <div className="log-list" style={{ maxHeight: 262, overflowY: "auto" }}>
            {filteredLogs.map((l, i) => (
              <div key={i} className="log-row">
                <span className="t3">{l.time}</span>
                <span className={`log-lvl ${l.level}`}>{l.level}</span>
                <span className="t2 ellipsis">{l.service}</span>
                <span className="ellipsis" style={{ color: l.level === "ERROR" ? "var(--red)" : "var(--text-1)" }}>{l.message}</span>
                <Icon name="chevronRight" size={11} style={{ color: "var(--text-3)" }} />
              </div>
            ))}
          </div>
          <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)" }}>
            <CardLink>View all logs in SigNoz <Icon name="external" size={10} /></CardLink>
          </div>
        </Card>

        {/* Alerts */}
        <Card title="Alerts" right={<CardLink>View all alerts</CardLink>} flush>
          <div style={{ maxHeight: 300, overflowY: "auto" }}>
            {alerts.map((a) => (
              <div key={a.title} style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
                <div className="row" style={{ gap: 8 }}>
                  <span className="health-dot" style={{ background: a.severity === "high" ? "var(--red)" : "var(--amber)" }} />
                  <span style={{ fontWeight: 620, fontSize: "var(--fs-body)" }} className="ellipsis grow">{a.title}</span>
                  <span className={`micro ${a.pending ? "g-amber" : "g-red"}`} style={{ fontWeight: 640, whiteSpace: "nowrap" }}>
                    {a.pending ? "PENDING" : "FIRING"} for {a.firingFor}
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
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
            <CardLink>Manage alert policies <Icon name="external" size={10} /></CardLink>
          </div>
        </Card>

        {/* Agent insights */}
        <Card
          title={<span className="row" style={{ gap: 7 }}><Icon name="agent" size={14} style={{ color: "var(--purple)" }} /> Agent Insights</span>}
          right={<span className="micro t3">Generated 1m ago</span>}
        >
          <p className="small t2" style={{ marginBottom: 10 }}>From the last 30 minutes of telemetry, here are the top failure patterns and signals.</p>
          <div className="col" style={{ gap: 10 }}>
            {agentInsights.map((i) => (
              <div key={i.title} className="row" style={{ gap: 9, alignItems: "flex-start" }}>
                <span className="cell-ico" style={{ width: 22, height: 22, flex: "none" }}>
                  <Icon name={i.icon as IconName} size={12} />
                </span>
                <span className="col" style={{ gap: 2 }}>
                  <span style={{ fontSize: "var(--fs-body)", fontWeight: 600 }}>
                    <b style={{ color: "var(--accent)" }}>{i.title.split(" ").slice(0, 3).join(" ")}</b>
                    {i.title.split(" ").slice(3).join(" ")}
                  </span>
                  <span className="small t2">{i.body}</span>
                </span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12 }}>
            <CardLink>Ask the agent a question <Icon name="external" size={10} /></CardLink>
          </div>
        </Card>
      </div>
    </div>
  );
}
