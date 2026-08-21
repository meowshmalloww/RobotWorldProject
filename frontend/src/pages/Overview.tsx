import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardLink, Progress } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { PipelineActivity, RecentCandidate, SkillGap, Stat } from "../data/types";

interface OverviewData {
  stats: Stat[];
  skillGaps: SkillGap[];
  pipelineActivity: PipelineActivity[];
  sourceSummary: { objectsFound: string; completeness: string; top: { name: string; objects: string; completeness: number }[] };
  readiness: { promoted: number; blocked: number; recent: RecentCandidate[] };
  integrations: { key: string; name: string; desc: string; status: string; meta: string }[];
}

const LOCAL_PIPELINE = [
  { icon: "search" as IconName, title: "Source collection", detail: "Bright Data image search selects a traceable source image.", state: "Configured" },
  { icon: "image" as IconName, title: "Foreground extraction", detail: "U²-NetP runs locally before image-to-3D generation.", state: "Local" },
  { icon: "cube" as IconName, title: "Image-to-3D", detail: "DINOv3 conditioning and TRELLIS.2-4B run from local checkpoints.", state: "Local" },
  { icon: "usd" as IconName, title: "Asset composition", detail: "GLB → visual.usdc → asset.usda → world.usda reference chain.", state: "Verified" },
];

const PIPELINE_FLOW = [
  { icon: "search" as IconName, title: "Bright Data", detail: "Traceable source images + descriptions" },
  { icon: "cube" as IconName, title: "TRELLIS.2", detail: "Image → GLB/USD with baked PBR textures" },
  { icon: "grid" as IconName, title: "Segmentation", detail: "Parts, behavior, and evidence from descriptions" },
  { icon: "worlds" as IconName, title: "World build", detail: "Measured placement into OpenUSD scenes" },
  { icon: "robot" as IconName, title: "Train & evaluate", detail: "Oracle + VLA rollouts in MuJoCo physics" },
  { icon: "observability" as IconName, title: "SigNoz telemetry", detail: "OpenTelemetry traces, metrics, and logs" },
];

export default function Overview() {
  const nav = useNavigate();
  const { data, error, loading, refetch } = useApi<OverviewData>("/overview");

  useEffect(() => {
    const id = window.setInterval(refetch, 12_000);
    return () => window.clearInterval(id);
  }, [refetch]);

  return (
    <div className="page overview-page" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="hero-band rise">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="page-eyebrow">RobotWorld / Local Asset Pipeline</div>
          <div className="hero-title">Build real assets. Keep failed validation visible.</div>
          <p className="hero-sub">Local TRELLIS.2 generation, OpenUSD composition, native Vulkan inspection, and separate physical validation — from Bright Data sources to SigNoz telemetry.</p>
          <div className="hero-chips">
            <Badge tone="grey">No Docker</Badge>
            <Badge tone="teal">TRELLIS.2 local</Badge>
            <Badge tone="green">OpenUSD verified</Badge>
          </div>
        </div>
        <div className="head-actions row" style={{ gap: 8, flex: "none" }}>
          <button className="btn btn-secondary btn-sm" onClick={() => nav("/assets")}><Icon name="cube" size={13} /> Asset library</button>
          <button className="btn btn-primary btn-sm" onClick={() => nav("/worlds")}><Icon name="worlds" size={13} /> Scene editor</button>
        </div>
      </div>

      {error && <div className="card"><ErrorState message={error.message} onRetry={refetch} /></div>}

      <div className="ov-stats rise rise-1" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
        {loading && !data
          ? Array.from({ length: 5 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : data?.stats.map((stat) => <StatCard key={stat.label} stat={stat} />)}
      </div>

      <div className="rise rise-2">
        <div className="section-head"><span className="section-title">End-to-end pipeline</span><span className="section-line" /><span className="section-meta">source → generation → world → policy → telemetry</span></div>
        <div className="flow-strip">
          {PIPELINE_FLOW.map((step, index) => (
            <div key={step.title} className="flow-step">
              <span className="flow-n">{String(index + 1).padStart(2, "0")}</span>
              <span className="flow-title"><span className="flow-ico"><Icon name={step.icon} size={13} /></span>{step.title}</span>
              <span className="flow-detail">{step.detail}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="rise rise-3" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.35fr) minmax(300px, 0.8fr)", gap: 12 }}>
        <Card title="Local production path" right={<Badge tone="grey">No Docker</Badge>} flush>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {LOCAL_PIPELINE.map((step, index) => (
              <div key={step.title} style={{ padding: 15, borderRight: index % 2 === 0 ? "1px solid var(--border)" : undefined, borderBottom: index < 2 ? "1px solid var(--border)" : undefined }}>
                <div className="row between" style={{ marginBottom: 9 }}>
                  <span className="cell-ico"><Icon name={step.icon} size={14} /></span>
                  <Badge tone={step.state === "Verified" ? "green" : "grey"}>{step.state}</Badge>
                </div>
                <div style={{ fontWeight: 650, fontSize: 12.5, marginBottom: 5 }}>{step.title}</div>
                <p className="micro t3" style={{ margin: 0, lineHeight: 1.45 }}>{step.detail}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Promotion gate" flush>
          <div style={{ padding: 14 }}>
            <div className="row" style={{ alignItems: "flex-start", gap: 10, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
              <Icon name="warning" size={17} style={{ color: "var(--amber)", marginTop: 1 }} />
              <div>
                <div style={{ fontSize: 12, fontWeight: 650 }}>Visual mesh ≠ robot-ready asset</div>
                <p className="micro t3" style={{ margin: "5px 0 0", lineHeight: 1.45 }}>Physical measurements, collision geometry, articulation, and task rollout must pass separately. RobotWorld does not auto-promote from one image.</p>
              </div>
            </div>
            <div className="row" style={{ gap: 25, paddingTop: 13 }}>
              <div><div className="micro t3">Promoted</div><b className="g-green" style={{ fontSize: 22 }}>{data?.readiness.promoted ?? "—"}</b></div>
              <div><div className="micro t3">Blocked for evidence</div><b className="g-red" style={{ fontSize: 22 }}>{data?.readiness.blocked ?? "—"}</b></div>
              <button className="btn btn-ghost btn-sm" onClick={() => nav("/assets")} style={{ marginLeft: "auto" }}>Review assets <Icon name="arrowRight" size={11} /></button>
            </div>
          </div>
        </Card>
      </div>

      <div className="rise rise-4" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(300px, 0.9fr) minmax(270px, 0.75fr)", gap: 12 }}>
        <Card title="Recent pipeline activity" right={<CardLink onClick={() => nav("/assets")}>Open assets</CardLink>} flush style={{ minHeight: 260 }}>
          {loading && !data ? <div style={{ padding: 12 }}><Skeleton rows={5} /></div> : data?.pipelineActivity.length ? (
            <div className="activity-list">
              {data.pipelineActivity.map((item, index) => (
                <div key={`${item.pipeline}-${item.started}-${index}`} className="activity-row" onClick={() => nav("/assets")}>
                  <span className="cell-ico"><Icon name={item.icon as IconName} size={13} /></span>
                  <span className="col grow" style={{ gap: 2, minWidth: 0 }}>
                    <span className="row" style={{ gap: 6 }}><span style={{ fontWeight: 600, fontSize: 12 }}>{item.pipeline}</span><StatusBadge status={item.status} /></span>
                    <span className="micro t3">{item.stage} · {item.started} · {item.duration}</span>
                  </span>
                  <Icon name="chevronRight" size={12} style={{ color: "var(--text-3)" }} />
                </div>
              ))}
            </div>
          ) : <EmptyState icon="workflow">No persisted pipeline activity yet.</EmptyState>}
        </Card>

        <Card title="Target skill evidence" right={<CardLink onClick={() => nav("/skills")}>Skills</CardLink>} flush style={{ minHeight: 260 }}>
          {loading && !data ? <div style={{ padding: 12 }}><Skeleton rows={5} /></div> : data?.skillGaps.length ? (
            <div style={{ padding: "4px 0" }}>
              {data.skillGaps.slice(0, 5).map((gap) => (
                <div key={gap.name} style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
                  <div className="row between" style={{ marginBottom: 6 }}><span style={{ fontSize: 12, fontWeight: 600 }}>{gap.name}</span><span className="micro mono">{gap.success.toFixed(1)}%</span></div>
                  <Progress value={gap.coverage} tone={gap.coverage >= 70 ? "green" : "amber"} style={{ height: 5 }} />
                </div>
              ))}
            </div>
          ) : <EmptyState icon="skills">No measured skill evidence yet.</EmptyState>}
        </Card>

        <Card title="Connected services" right={<CardLink onClick={() => nav("/settings")}>Settings</CardLink>} flush style={{ minHeight: 260 }}>
          {loading && !data ? <div style={{ padding: 12 }}><Skeleton rows={3} /></div> : data?.integrations.length ? data.integrations.map((integration) => (
            <div key={integration.key} className="intg-row" style={{ padding: "11px 12px", borderBottom: "1px solid var(--border)" }}>
              <span className={`brand-ico brand-${integration.key}`} style={{ width: 25, height: 25, fontSize: 10 }}>{integration.name.slice(0, 1)}</span>
              <span className="col grow" style={{ gap: 1 }}><span style={{ fontWeight: 600, fontSize: 12 }}>{integration.name}</span><span className="micro t3">{integration.desc}</span></span>
              <span className="micro mono t3" style={{ textAlign: "right" }}>{integration.status}<br />{integration.meta}</span>
            </div>
          )) : <EmptyState icon="link">No service state is available.</EmptyState>}
        </Card>
      </div>
    </div>
  );
}
