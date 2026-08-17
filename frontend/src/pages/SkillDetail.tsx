import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Card, CardLink, Progress } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, Menu, MenuItem, SearchBox, StatusBadge } from "../components/ui/controls";
import { LineChart } from "../components/charts/LineChart";
import { Sparkline } from "../components/charts/Sparkline";
import { ContribBar } from "../components/charts/CoverageBands";
import { openCabinetDetail } from "../data/skills";
import { fmtInt } from "../data/util";
import { Viewport } from "../components/three/Viewport";
import { WarehouseKitchen } from "../components/three/WarehouseKitchen";

const IMPACT_TONE = { high: ["var(--red-soft)", "var(--red)"], medium: ["var(--amber-soft)", "var(--amber)"], low: ["var(--green-soft)", "var(--green)"] } as const;

export default function SkillDetail() {
  useParams(); // route param reserved for the API-backed lookup
  const nav = useNavigate();
  const d = openCabinetDetail; // fixture: every route renders the documented skill for now
  const [familyQ, setFamilyQ] = useState("");
  const [familyStatus, setFamilyStatus] = useState("All statuses");

  const families = d.families.filter(
    (f) =>
      f.family.toLowerCase().includes(familyQ.toLowerCase()) &&
      (familyStatus === "All statuses" || f.status === familyStatus),
  );

  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/skills">Skills</Link> <Icon name="chevronRight" size={10} /> <span>{d.category}</span> <Icon name="chevronRight" size={10} /> <span className="crumb-cur">{d.name}</span>
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title row" style={{ gap: 10 }}>
            {d.name}
            {d.promoted && <Badge tone="green">Promoted</Badge>}
          </h1>
          <p className="page-sub">{d.description}</p>
        </div>
        <div className="head-actions">
          <Menu trigger={() => <button className="btn btn-secondary">Skill actions <Icon name="chevronDown" size={12} /></button>} align="right" width={210}>
            <MenuItem icon="play">Run evaluation</MenuItem>
            <MenuItem icon="target">Set as skill target</MenuItem>
            <MenuItem icon="edit">Edit skill definition</MenuItem>
            <div className="menu-sep" />
            <MenuItem icon="download">Export coverage report</MenuItem>
          </Menu>
          <button className="btn btn-primary" onClick={() => nav("/worlds")}>
            <Icon name="spark" size={13} /> Generate Missing Worlds
          </button>
        </div>
      </div>

      {/* stat band */}
      <div className="sd-stats">
        {[
          { label: "Current success rate", value: `${d.success.toFixed(1)}%`, sub: `↓ ${Math.abs(d.successDelta)}pp vs 24h ago`, subTone: "var(--red)", spark: d.successTrend, color: "var(--series-6)" },
          { label: "Target success", value: `${d.target.toFixed(1)}%`, sub: "On track", subTone: "var(--green)", spark: undefined },
          { label: "Scenario coverage", value: `${d.coverage}%`, sub: d.scenarioCount, subTone: "var(--text-3)", spark: d.coverageTrend, color: "var(--series-1)" },
          { label: "Average collisions", value: d.avgCollisions.toFixed(2), sub: `↓ ${d.collisionsDelta} vs 24h ago`, subTone: "var(--green)", spark: d.collisionTrend, color: "var(--orange)" },
          { label: "Last training gain", value: d.lastGain, sub: "10:15 AM PDT", subTone: "var(--text-3)", spark: d.successTrend.map((v) => v / 10), color: "var(--series-2)" },
        ].map((s) => (
          <div key={s.label} className="stat-card" style={{ alignItems: "flex-start" }}>
            <div className="stat-meta">
              <div className="stat-label">{s.label}</div>
              <div className="stat-value" style={s.label === "Current success rate" ? { color: "var(--orange)" } : s.label === "Last training gain" ? { color: "var(--green)" } : undefined}>
                {s.value}
              </div>
              <div className="stat-foot" style={{ color: s.subTone }}>{s.sub}</div>
            </div>
            {s.spark && <span className="stat-spark"><Sparkline data={s.spark} color={s.color!} width={86} height={34} /></span>}
          </div>
        ))}
      </div>

      <div className="sd-main">
        {/* Weakness analysis */}
        <Card title="Weakness Analysis" info flush>
          <div className="row micro t3" style={{ padding: "8px 14px 4px", gap: 8 }}>
            <span style={{ width: 210 }}>Failure mode</span>
            <span style={{ width: 48 }} />
            <span className="grow">Contribution</span>
            <span style={{ width: 56, textAlign: "right" }}>Examples</span>
          </div>
          {d.weaknesses.map((w) => (
            <div key={w.mode} className="row" style={{ gap: 8, padding: "7px 14px", borderTop: "1px solid rgba(148,170,220,0.05)" }}>
              <span className="col" style={{ width: 210, gap: 0, flex: "none" }}>
                <span style={{ fontWeight: 580, fontSize: "var(--fs-body)" }}>{w.mode}</span>
                {w.detail && <span className="micro t3 ellipsis">{w.detail}</span>}
              </span>
              <span className="row grow" style={{ gap: 8 }}>
                <span className="mono" style={{ width: 40, textAlign: "right", fontSize: "var(--fs-small)", color: "var(--text-1)", fontWeight: 600 }}>{w.contribution.toFixed(1)}%</span>
                <ContribBar value={w.contribution} color={w.contribution > 20 ? "var(--orange)" : "var(--series-1)"} />
              </span>
              <span className="mono t2" style={{ width: 56, textAlign: "right", fontSize: "var(--fs-small)" }}>{fmtInt(w.examples)}</span>
            </div>
          ))}
          <div className="row between" style={{ padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
            <span className="small t2">Total failures analyzed</span>
            <b className="mono">{fmtInt(7492)}</b>
          </div>
        </Card>

        {/* Curriculum plan */}
        <Card title="Curriculum Plan" info right={<CardLink>View plan</CardLink>} flush>
          <div style={{ padding: "6px 14px 4px" }} className="micro t3">Recommended next scenario families</div>
          {d.curriculum.map((c) => (
            <div key={c.rank} className="row" style={{ gap: 10, padding: "8px 14px", borderTop: "1px solid rgba(148,170,220,0.05)" }}>
              <span className="cell-ico" style={{ width: 22, height: 22, fontSize: 10.5, fontWeight: 700, fontFamily: "var(--font-mono)" }}>{c.rank}</span>
              <span className="col grow" style={{ gap: 1 }}>
                <span className="row" style={{ gap: 7 }}>
                  <span style={{ fontWeight: 580 }}>{c.name}</span>
                  <span className="badge" style={{ background: IMPACT_TONE[c.impact][0], color: IMPACT_TONE[c.impact][1], height: 17, fontSize: "var(--fs-micro)", padding: "0 6px" }}>
                    {c.impact} impact
                  </span>
                </span>
                <span className="micro t3">{c.desc}</span>
              </span>
              <span className="col" style={{ textAlign: "right", gap: 0 }}>
                <span className="mono g-green" style={{ fontWeight: 640, fontSize: "var(--fs-small)" }}>+{c.scenarios}</span>
                <span className="micro t3">scenarios</span>
              </span>
            </div>
          ))}
          <div className="row between" style={{ padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
            <span className="small t2">Total recommended</span>
            <b className="mono g-green">+1,140 scenarios</b>
          </div>
        </Card>

        {/* Before vs After */}
        <Card
          title="Before vs After"
          info
          right={
            <select className="select" style={{ width: 150 }}>
              <option>Last 5 training cycles</option>
              <option>Last 10 training cycles</option>
            </select>
          }
        >
          <div className="legend" style={{ marginBottom: 6 }}>
            <span className="lg"><i style={{ background: "var(--series-muted)" }} className="dashed" /> Before</span>
            <span className="lg"><i style={{ background: "var(--series-1)" }} /> After</span>
          </div>
          <LineChart
            series={[
              { name: "Before", data: d.beforeAfter.before, color: "#525E78", dashed: true },
              { name: "After", data: d.beforeAfter.after, color: "var(--series-1)" },
            ]}
            height={172}
            xLabels={d.beforeAfter.labels}
            yMin={0}
            yMax={100}
            yFormat={(v) => `${v.toFixed(0)}`}
            endBadges={false}
          />
          <div className="row" style={{ gap: 0, marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
            {[
              ["Success rate", "+11.6pp", "var(--green)"],
              ["Coverage", "+9.8pp", "var(--green)"],
              ["Collisions", "-0.16", "var(--green)"],
              ["Examples", "+4.2K", "var(--green)"],
            ].map(([k, v, c]) => (
              <span key={k} className="col grow" style={{ gap: 0 }}>
                <span className="micro t3">{k}</span>
                <b className="mono" style={{ color: c, fontSize: "var(--fs-body)" }}>{v}</b>
              </span>
            ))}
          </div>
          <div style={{ marginTop: 10 }}>
            <CardLink onClick={() => nav("/training")}>Open training analytics</CardLink>
          </div>
        </Card>

        {/* Simulation preview + rollouts */}
        <div className="sd-right">
          <Card
            title="Simulation Preview"
            info
            flush
            right={
              <span className="row" style={{ gap: 6 }}>
                <Badge tone="live" dot>Live</Badge>
                <button className="icon-btn btn-sm"><Icon name="dots" size={13} /></button>
              </span>
            }
          >
            <div style={{ padding: 10 }}>
              <Viewport
                camera={{ position: [2.9, 2.0, 0.6], fov: 42 }}
                target={[0.1, 1.0, -3.0]}
                style={{ height: 210 }}
                gizmo={false}
                dpr={[1, 1.4]}
              >
                <WarehouseKitchen cabinetDoorOpen={{ left: 0.7 }} />
              </Viewport>
            </div>
            <div className="col" style={{ padding: "0 14px 12px", gap: 5 }}>
              {[
                ["Environment", "Warehouse Kitchen v2"],
                ["Task", "Open upper right cabinet"],
                ["Domain randomization", "High"],
                ["Physics", "PhysX"],
              ].map(([k, v]) => (
                <div key={k} className="row between" style={{ fontSize: "var(--fs-small)" }}>
                  <span className="t3">{k}</span><span className="t1" style={{ fontWeight: 550 }}>{v}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Recent Evaluation Rollouts" right={<CardLink>View all</CardLink>}>
            <RolloutStrip />
            <div className="col" style={{ gap: 6, marginTop: 10 }}>
              <div className="row between small">
                <span className="t2">Success 41% (18 / 44)</span>
              </div>
              <Progress value={41} tone="amber" />
              <div className="row between small">
                <span className="t2">Avg. collisions</span>
                <span className="mono">0.64</span>
              </div>
              <Progress value={26} tone="orange" />
            </div>
            <div style={{ marginTop: 10 }}>
              <CardLink onClick={() => nav("/worlds/live")}>Open rollout viewer</CardLink>
            </div>
          </Card>
        </div>
      </div>

      {/* Scenario families table */}
      <div className="sd-bottom">
        <Card
          title={
            <span className="row" style={{ gap: 8 }}>
              Scenario Families
              <span className="t3" style={{ fontWeight: 500, fontSize: "var(--fs-small)" }}>1,112 total</span>
            </span>
          }
          info
          flush
          right={
            <span className="row" style={{ gap: 7 }}>
              <SearchBox placeholder="Search families…" value={familyQ} onChange={setFamilyQ} style={{ width: 200 }} />
              <select className="select" style={{ width: 130 }} value={familyStatus} onChange={(e) => setFamilyStatus(e.target.value)}>
                <option>All statuses</option>
                <option value="promoted">Promoted</option>
                <option value="needs_data">Needs data</option>
                <option value="in_progress">In progress</option>
              </select>
              <button className="btn btn-secondary btn-sm"><Icon name="download" size={12} /> Export</button>
            </span>
          }
        >
          <table className="table">
            <thead>
              <tr>
                <th>Family</th><th style={{ textAlign: "right" }}>Count</th><th style={{ width: 170 }}>Success</th>
                <th style={{ width: 150 }}>Coverage</th><th>Source</th><th>Status</th><th>Updated</th><th style={{ width: 30 }} />
              </tr>
            </thead>
            <tbody>
              {families.map((f) => (
                <tr key={f.id}>
                  <td style={{ fontWeight: 550 }}>{f.family}</td>
                  <td className="mono t2" style={{ textAlign: "right" }}>{f.count}</td>
                  <td>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="mono" style={{ width: 44, fontSize: "var(--fs-small)", fontWeight: 600 }}>{f.success.toFixed(1)}%</span>
                      <Progress value={f.success} tone={f.success > 50 ? "green" : "orange"} style={{ flex: 1 }} />
                    </div>
                  </td>
                  <td>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="mono t2" style={{ width: 36, fontSize: "var(--fs-small)" }}>{f.coverage}%</span>
                      <Progress value={f.coverage} tone="blue" style={{ flex: 1 }} />
                    </div>
                  </td>
                  <td className="t-muted">{f.source}</td>
                  <td><StatusBadge status={f.status} /></td>
                  <td className="t-muted mono" style={{ fontSize: "var(--fs-small)" }}>{f.updated}</td>
                  <td><button className="icon-btn btn-sm"><Icon name="dots" size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

/** Rollout thumbnails — live mini renders of the world with outcome marks. */
function RolloutStrip() {
  const rollouts = [
    { id: 1, ok: true, cam: [2.4, 1.8, 0.2] as [number, number, number], door: 0.9 },
    { id: 2, ok: true, cam: [1.6, 1.5, -0.4] as [number, number, number], door: 0.4 },
    { id: 3, ok: false, cam: [3.2, 1.6, -1.2] as [number, number, number], door: 0.1 },
  ];
  return (
    <div className="row" style={{ gap: 7 }}>
      {rollouts.map((r) => (
        <div key={r.id} className="thumb clickable grow" style={{ height: 74 }}>
          <Viewport
            camera={{ position: r.cam, fov: 46 }}
            target={[0.3, 1.1, -3.1]}
            style={{ height: "100%", borderRadius: 0 }}
            gizmo={false}
            controls={false}
            shadows={false}
            dpr={[0.6, 0.9]}
          >
            <WarehouseKitchen cabinetDoorOpen={{ left: r.door }} />
          </Viewport>
          <span
            style={{
              position: "absolute", right: 5, bottom: 5, width: 16, height: 16, borderRadius: "50%",
              background: r.ok ? "var(--green)" : "var(--red)", display: "grid", placeItems: "center", color: "#fff",
            }}
          >
            <Icon name={r.ok ? "check" : "x"} size={10} strokeWidth={2.2} />
          </span>
          <span style={{ position: "absolute", left: 5, bottom: 5, color: "rgba(233,237,245,0.85)", display: "inline-flex" }}>
            <Icon name="play" size={11} />
          </span>
        </div>
      ))}
    </div>
  );
}
