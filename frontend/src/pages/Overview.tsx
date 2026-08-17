import { useNavigate } from "react-router-dom";
import { Card, CardLink, Progress } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { pctTone } from "../components/ui/helpers";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { Viewport } from "../components/three/Viewport";
import { WarehouseKitchen } from "../components/three/WarehouseKitchen";
import {
  integrations, loopStages, overviewStats, pipelineActivity, readiness, skillGaps, sourceSummary,
} from "../data/overview";

export default function Overview() {
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="ov-stats">
        {overviewStats.map((s) => <StatCard key={s.label} stat={s} />)}
      </div>

      <div className="ov-mid">
        {/* Autonomous loop */}
        <Card title="Autonomous Loop" info style={{ gridColumn: "span 1" }}>
          <div className="pipe-flow">
            {loopStages.map((s, i) => (
              <div key={s.title} style={{ display: "contents" }}>
                <div className="pipe-node">
                  <span className="pipe-ico"><Icon name={s.icon as IconName} size={18} /></span>
                  <span className="pn-title">{s.title}</span>
                  <span className="pn-desc">{s.desc}</span>
                </div>
                {i < loopStages.length - 1 && <span className="pipe-link" />}
              </div>
            ))}
          </div>
        </Card>

        {/* Top skill gaps */}
        <Card title="Top skill gaps" info right={<CardLink onClick={() => nav("/skills")}>View all</CardLink>} flush>
          <table className="table">
            <thead>
              <tr><th>Skill</th><th style={{ textAlign: "right" }}>Success (avg)</th><th style={{ width: 110 }}>Coverage</th></tr>
            </thead>
            <tbody>
              {skillGaps.map((g) => (
                <tr key={g.name} className="rowlink" onClick={() => nav("/skills/open-cabinet")}>
                  <td>
                    <div className="cell-main">
                      <span className="cell-ico"><Icon name={g.icon as IconName} size={13} /></span>
                      <span className="col" style={{ gap: 0 }}>
                        <span style={{ fontWeight: 560 }}>{g.name}</span>
                        <span className="micro t3">{g.family}</span>
                      </span>
                    </div>
                  </td>
                  <td className="mono" style={{ textAlign: "right", color: g.success < 50 ? "var(--red)" : g.success < 70 ? "var(--amber)" : "var(--text-1)", fontWeight: 600 }}>
                    {g.success.toFixed(1)}%
                  </td>
                  <td>
                    <div className="row" style={{ gap: 8 }}>
                      <Progress value={g.coverage} tone={pctTone(g.coverage)} style={{ flex: 1 }} />
                      <span className="mono t2" style={{ fontSize: "var(--fs-small)", width: 30, textAlign: "right" }}>{g.coverage}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row between" style={{ padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
            <span className="micro t3">Success measured on latest evaluation suite</span>
            <span className="micro t3 mono">as of 10:15 AM</span>
          </div>
        </Card>

        {/* Simulation preview — live 3D */}
        <Card
          title="Simulation preview"
          right={
            <span className="row" style={{ gap: 8 }}>
              <Badge tone="live" dot>Live</Badge>
              <button className="icon-btn btn-sm" title="Options"><Icon name="dots" size={13} /></button>
            </span>
          }
          flush
        >
          <div style={{ padding: "10px 12px 12px" }}>
            <Viewport
              camera={{ position: [3.4, 2.3, 1.4], fov: 40 }}
              target={[0.2, 0.9, -2.9]}
              style={{ height: 240 }}
              gizmo={false}
              dpr={[1, 1.5]}
            >
              <WarehouseKitchen />
            </Viewport>
            <div className="row between" style={{ marginTop: 8 }}>
              <span className="small" style={{ fontWeight: 560 }}>Warehouse Kitchen v2</span>
              <span className="row" style={{ gap: 6 }}>
                <Badge tone="green">PhysX</Badge>
                <button className="btn btn-ghost btn-sm" onClick={() => nav("/worlds")}>
                  Open composer <Icon name="arrowRight" size={11} />
                </button>
              </span>
            </div>
          </div>
        </Card>
      </div>

      <div className="ov-bottom">
        {/* Recent pipeline activity */}
        <Card title="Recent pipeline activity" right={<CardLink>View all</CardLink>} flush>
          <table className="table">
            <thead>
              <tr><th>Pipeline</th><th>Stage</th><th>Status</th><th>Started</th><th style={{ textAlign: "right" }}>Duration</th></tr>
            </thead>
            <tbody>
              {pipelineActivity.map((p) => (
                <tr key={p.pipeline}>
                  <td>
                    <div className="cell-main">
                      <span className="cell-ico"><Icon name={p.icon as IconName} size={13} /></span>
                      <span style={{ fontWeight: 550 }}>{p.pipeline}</span>
                    </div>
                  </td>
                  <td className="t-muted">
                    <span className="row" style={{ gap: 6 }}>
                      <Icon name={p.stageIcon as IconName} size={12} style={{ color: "var(--text-3)" }} />
                      {p.stage}
                    </span>
                  </td>
                  <td><StatusBadge status={p.status} /></td>
                  <td className="t-muted mono" style={{ fontSize: "var(--fs-small)" }}>{p.started}</td>
                  <td className="mono t-muted" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{p.duration}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        {/* Sources & scraping */}
        <Card title="Sources & scraping" right={<CardLink onClick={() => nav("/sources")}>View all</CardLink>}>
          <div className="row" style={{ gap: 24, marginBottom: 10 }}>
            <span className="col" style={{ gap: 0 }}>
              <span className="t2 small">Objects found online</span>
              <span className="row" style={{ gap: 8 }}>
                <b style={{ fontSize: 20, letterSpacing: "-0.02em" }}>{sourceSummary.objectsFound}</b>
              </span>
              <DeltaSmall up value={`↑ ${sourceSummary.objectsDelta} vs yesterday`} />
            </span>
            <span className="col" style={{ gap: 0 }}>
              <span className="t2 small">Extraction completeness</span>
              <b style={{ fontSize: 20, letterSpacing: "-0.02em" }}>{sourceSummary.completeness}</b>
              <DeltaSmall up value={`↑ ${sourceSummary.completenessDelta} vs yesterday`} />
            </span>
          </div>
          <div className="col" style={{ gap: 0 }}>
            <div className="row micro t3" style={{ padding: "3px 0 6px", borderBottom: "1px solid var(--border)" }}>
              <span className="grow">Top sources</span><span style={{ width: 64 }}>Objects</span><span style={{ width: 96 }}>Completeness</span>
            </div>
            {sourceSummary.top.map((s) => (
              <div key={s.name} className="row" style={{ padding: "6px 0", gap: 8, borderBottom: "1px solid rgba(148,170,220,0.05)", fontSize: "var(--fs-body)" }}>
                <span className="grow">{s.name}</span>
                <span className="mono t2" style={{ width: 64, fontSize: "var(--fs-small)" }}>{s.objects}</span>
                <span className="row" style={{ width: 96, gap: 7 }}>
                  <Progress value={s.completeness} tone={pctTone(s.completeness)} style={{ flex: 1 }} />
                  <span className="mono t2" style={{ fontSize: "var(--fs-small)", width: 38, textAlign: "right" }}>{s.completeness}%</span>
                </span>
              </div>
            ))}
          </div>
          <div className="row between" style={{ marginTop: 10 }}>
            <span className="micro t3">Last updated 10:10 AM</span>
            <CardLink onClick={() => nav("/sources")}>Manage sources</CardLink>
          </div>
        </Card>

        {/* Simulation readiness */}
        <Card title="Simulation readiness" right={<CardLink onClick={() => nav("/assets")}>View all</CardLink>}>
          <div className="row" style={{ gap: 22, marginBottom: 10 }}>
            <span className="col" style={{ gap: 1 }}>
              <span className="t2 small">Promoted (24h)</span>
              <b className="g-green" style={{ fontSize: 20, letterSpacing: "-0.02em" }}>{readiness.promoted}</b>
              <DeltaSmall up value={`↑ ${readiness.promotedDelta} vs yesterday`} />
            </span>
            <span className="col" style={{ gap: 1 }}>
              <span className="t2 small">Blocked (24h)</span>
              <b className="g-red" style={{ fontSize: 20, letterSpacing: "-0.02em" }}>{readiness.blocked}</b>
              <span className="micro g-red">↓ {readiness.blockedDelta} vs yesterday</span>
            </span>
          </div>
          <div className="col">
            <div className="row micro t3" style={{ padding: "3px 0 6px", borderBottom: "1px solid var(--border)" }}>
              <span className="grow">Recent candidates</span><span>Status</span>
            </div>
            {readiness.recent.map((r) => (
              <div key={r.name} className="row between" style={{ padding: "6px 0", borderBottom: "1px solid rgba(148,170,220,0.05)", fontSize: "var(--fs-body)" }}>
                <span>{r.name}</span>
                <StatusBadge status={r.status} />
              </div>
            ))}
          </div>
          <div className="micro t3" style={{ marginTop: 10 }}>Readiness checks: Physics · Semantics · Visuals · Licensing</div>
        </Card>

        {/* Integrations */}
        <Card title="Integrations" flush pad={false}>
          <div>
            {integrations.map((i) => (
              <div key={i.key} className="intg-row">
                <span className={`brand-ico brand-${i.key}`} style={{ width: 30, height: 30, fontSize: 11 }}>
                  {i.name.slice(0, 1)}
                </span>
                <span className="col grow" style={{ gap: 0 }}>
                  <span style={{ fontWeight: 600, fontSize: "var(--fs-body)" }}>{i.name}</span>
                  <span className="micro t3">{i.desc}</span>
                </span>
                <span className="col" style={{ textAlign: "right", gap: 0 }}>
                  <span className="small g-green" style={{ fontWeight: 620 }}>{i.status}</span>
                  <span className="micro t3 mono">{i.meta}</span>
                </span>
                <Icon name="chevronRight" size={12} style={{ color: "var(--text-3)" }} />
              </div>
            ))}
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
            <CardLink onClick={() => nav("/settings")}>Manage integrations</CardLink>
          </div>
        </Card>
      </div>
    </div>
  );
}

function DeltaSmall({ value, up }: { value: string; up?: boolean }) {
  return <span className={`micro ${up ? "g-green" : "g-red"}`}>{value}</span>;
}
