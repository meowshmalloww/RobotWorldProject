import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardLink, Progress } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { Viewport } from "../components/three/Viewport";
import { pctTone } from "../components/ui/helpers";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { PipelineActivity, RecentCandidate, SkillGap, Stat } from "../data/types";

interface OverviewData {
  stats: Stat[];
  loopStages: { icon: string; title: string; desc: string }[];
  skillGaps: SkillGap[];
  pipelineActivity: PipelineActivity[];
  sourceSummary: {
    objectsFound: string;
    objectsDelta: string;
    completeness: string;
    completenessDelta: string;
    top: { name: string; objects: string; completeness: number }[];
  };
  readiness: {
    promoted: number;
    promotedDelta: string;
    blocked: number;
    blockedDelta: string;
    recent: RecentCandidate[];
  };
  integrations: { key: string; name: string; desc: string; status: string; meta: string }[];
}

export default function Overview() {
  const nav = useNavigate();
  const { data, error, loading, refetch } = useApi<OverviewData>("/overview");

  // pipeline activity poll every 5s
  useEffect(() => {
    const id = setInterval(refetch, 5000);
    return () => clearInterval(id);
  }, [refetch]);

  return (
    <div className="page overview-page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-sub">World generation, policy readiness, and measured evaluation status. Training is disabled.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-ghost btn-sm" onClick={refetch} title="Refresh"><Icon name="refresh" size={13} /> Refresh</button>
        </div>
      </div>

      {error && (
        <div className="card"><ErrorState message={error.message} onRetry={refetch} /></div>
      )}

      <div className="ov-stats">
        {loading && !data
          ? Array.from({ length: 6 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : data?.stats.map((s) => <StatCard key={s.label} stat={s} />)}
      </div>

      <div className="ov-mid" style={{ gridTemplateColumns: "minmax(0, 1.5fr) 380px" }}>
        {/* Autonomous loop — clean stepper */}
        <Card title="Autonomous Loop" info flush style={{ flex: 1, minHeight: 0 }}>
          {loading && !data ? (
            <Skeleton rows={4} />
          ) : data && data.loopStages.length > 0 ? (
            <div className="loop-stepper">
              {data.loopStages.map((s, i) => (
                <div key={s.title} className={`loop-step ${i === 1 ? "active" : ""}`}>
                  <span className="loop-step-num">{i + 1}</span>
                  <span className="loop-step-title row" style={{ gap: 6 }}>
                    <Icon name={s.icon as IconName} size={13} style={{ color: i === 1 ? "var(--accent)" : "var(--text-3)" }} />
                    {s.title}
                  </span>
                  <span className="loop-step-desc">{s.desc}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState icon="refresh">No pipeline stages reported by the backend yet.</EmptyState>
          )}
        </Card>

        {/* Scene preview — visualization only; measured runs live under Worlds. */}
        <Card
          title="World preview"
          right={<Badge tone="grey">Preview</Badge>}
          flush
          style={{ flex: 1, minHeight: 0 }}
        >
          <div style={{ padding: "8px 10px 10px", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <Viewport
              camera={{ position: [3.4, 2.3, 1.4], fov: 40 }}
              target={[0.2, 0.9, -2.9]}
              style={{ flex: 1, minHeight: 120 }}
              gizmo={false}
              dpr={[1, 1.5]}
            />
            <div className="row between" style={{ marginTop: 8 }}>
              <span className="small" style={{ fontWeight: 560 }}>Articulated Door Validation Lab</span>
              <span className="row" style={{ gap: 6 }}>
                <Badge tone="grey">Native Vulkan</Badge>
                <button className="btn btn-ghost btn-sm" onClick={() => nav("/worlds")}>
                  Open composer <Icon name="arrowRight" size={11} />
                </button>
              </span>
            </div>
          </div>
        </Card>
      </div>

      <div className="ov-bottom" style={{ gridTemplateColumns: "minmax(0, 1.35fr) minmax(0, 1fr) minmax(0, 1fr)" }}>
        {/* Recent pipeline activity — compact list */}
        <Card title="Recent Pipeline Activity" right={<CardLink onClick={() => nav("/training")}>View all</CardLink>} flush style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          {loading && !data ? (
            <Skeleton rows={5} />
          ) : data && data.pipelineActivity.length > 0 ? (
            <div className="activity-list">
              {data.pipelineActivity.map((p) => (
                <div key={p.pipeline} className="activity-row" onClick={() => nav("/worlds")}>
                  <span className="cell-ico"><Icon name={p.icon as IconName} size={13} /></span>
                  <span className="col grow" style={{ gap: 0, minWidth: 0 }}>
                    <span className="row" style={{ gap: 7 }}>
                      <span style={{ fontWeight: 580, fontSize: "var(--fs-body)" }}>{p.pipeline}</span>
                      <StatusBadge status={p.status} />
                    </span>
                    <span className="micro t3 row" style={{ gap: 5 }}>
                      <Icon name={p.stageIcon as IconName} size={10} /> {p.stage} · {p.started} · {p.duration}
                    </span>
                  </span>
                  <Icon name="chevronRight" size={13} style={{ color: "var(--text-3)" }} />
                </div>
              ))}
            </div>
          ) : (
            <EmptyState icon="workflow">No pipeline activity yet — runs will appear here once the agent starts working.</EmptyState>
          )}
        </Card>

        {/* Skill gaps + sources merged */}
        <Card title="Top Skill Gaps" info right={<CardLink onClick={() => nav("/skills")}>View all</CardLink>} flush style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          {loading && !data ? (
            <Skeleton rows={4} />
          ) : data && data.skillGaps.length > 0 ? (
            <table className="table">
              <thead>
                <tr><th>Skill</th><th style={{ textAlign: "right" }}>Success</th><th style={{ width: 120 }}>Coverage</th></tr>
              </thead>
              <tbody>
                {data.skillGaps.map((g) => (
                  <tr key={g.name} className="rowlink" onClick={() => nav(`/skills/${g.name.toLowerCase().replace(/\s+/g, "-")}`)}>
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
          ) : (
            <EmptyState icon="skills">No skill gaps detected yet.</EmptyState>
          )}
        </Card>

        {/* Readiness + integrations */}
        <div className="col" style={{ gap: 10, minWidth: 0 }}>
          <Card title="Simulation readiness" right={<CardLink onClick={() => nav("/assets")}>View all</CardLink>} style={{ flex: 1 }}>
            {loading && !data ? (
              <Skeleton rows={4} />
            ) : data ? (
              <>
                <div className="row" style={{ gap: 20, marginBottom: 8 }}>
                  <span className="col" style={{ gap: 1 }}>
                    <span className="t2 small">Promoted (24h)</span>
                    <b className="g-green" style={{ fontSize: 19, letterSpacing: "-0.02em" }}>{data.readiness.promoted}</b>
                  </span>
                  <span className="col" style={{ gap: 1 }}>
                    <span className="t2 small">Blocked (24h)</span>
                    <b className="g-red" style={{ fontSize: 19, letterSpacing: "-0.02em" }}>{data.readiness.blocked}</b>
                  </span>
                  <span className="col grow" style={{ gap: 1 }}>
                    <span className="t2 small">Objects found online</span>
                    <b style={{ fontSize: 19, letterSpacing: "-0.02em" }}>{data.sourceSummary.objectsFound}</b>
                  </span>
                </div>
                <div className="col">
                  {data.readiness.recent.length > 0 ? (
                    data.readiness.recent.slice(0, 3).map((r) => (
                      <div key={r.name} className="row between" style={{ padding: "5px 0", borderBottom: "1px solid rgba(255,255,255,0.05)", fontSize: "var(--fs-body)" }}>
                        <span className="ellipsis">{r.name}</span>
                        <StatusBadge status={r.status} />
                      </div>
                    ))
                  ) : (
                    <EmptyState icon="cube">No promotion candidates yet.</EmptyState>
                  )}
                </div>
              </>
            ) : null}
          </Card>

          <Card title="Integrations" right={<CardLink onClick={() => nav("/settings")}>Manage</CardLink>} flush style={{ flex: "none" }}>
            {loading && !data ? (
              <Skeleton rows={3} />
            ) : data && data.integrations.length > 0 ? (
              <div>
                {data.integrations.map((i) => (
                  <div key={i.key} className="intg-row" style={{ padding: "8px 14px" }}>
                    <span className={`brand-ico brand-${i.key}`} style={{ width: 26, height: 26, fontSize: 10.5 }}>
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
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon="link">No integrations configured — set them up in Settings.</EmptyState>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
