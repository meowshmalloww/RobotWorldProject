import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardLink, Progress } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Delta, Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { CoverageBands } from "../components/charts/CoverageBands";
import { DonutGauge } from "../components/charts/DonutGauge";
import { pctTone } from "../components/ui/helpers";
import { recommendedSkills, scenarioCoverageDims, skillRelations, skills, skillsBand } from "../data/skills";

export default function Skills() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("All categories");
  const [status, setStatus] = useState("All status");
  const [page, setPage] = useState(1);
  const [coverageSkill, setCoverageSkill] = useState("Open Cabinet");

  const filtered = useMemo(
    () =>
      skills.filter(
        (s) =>
          s.name.toLowerCase().includes(q.toLowerCase()) &&
          (category === "All categories" || s.category === category) &&
          (status === "All status" || s.status === status),
      ),
    [q, category, status],
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Skills &amp; Coverage</h1>
          <p className="page-sub">Manage robot skills, track coverage across scenarios, and close capability gaps.</p>
        </div>
      </div>

      <div className="sk-stats">
        {skillsBand.map((s) => (
          <div key={s.label} className="stat-card">
            <div className="stat-meta">
              <div className="stat-label">{s.label}</div>
              <div className="stat-value">{s.value}</div>
              <div className="stat-foot">
                {s.foot.includes("vs") ? (
                  <Delta value={s.foot.split(" ")[0]} dir={s.foot.startsWith("-") ? "down" : "up"} label={s.foot.split(" ").slice(1).join(" ")} />
                ) : (
                  <span className="row" style={{ gap: 5 }}>
                    {s.foot.includes("%") && <Icon name={s.icon as IconName} size={11} style={{ color: "var(--text-3)" }} />}
                    {s.foot}
                  </span>
                )}
              </div>
            </div>
            {s.label === "Avg success rate" ? (
              <span className="stat-spark">
                <DonutGauge value={0.876} size={44} stroke={4.5} />
              </span>
            ) : (
              <span className="stat-spark" style={{ color: "var(--text-3)" }}>
                <Icon name={s.icon as IconName} size={26} strokeWidth={1.1} />
              </span>
            )}
          </div>
        ))}
      </div>

      <div className="sk-main">
        {/* Skills table */}
        <Card
          title="Skills"
          flush
          right={
            <span className="row" style={{ gap: 7 }}>
              <SearchBox placeholder="Search skills" value={q} onChange={(v) => { setQ(v); setPage(1); }} style={{ width: 190 }} />
              <select className="select" style={{ width: 132 }} value={category} onChange={(e) => setCategory(e.target.value)}>
                {["All categories", "Manipulation", "Navigation", "Perception"].map((c) => <option key={c}>{c}</option>)}
              </select>
              <select className="select" style={{ width: 112 }} value={status} onChange={(e) => setStatus(e.target.value)}>
                <option>All status</option>
                <option value="ready">Ready</option>
                <option value="improving">Improving</option>
                <option value="in_training">In Training</option>
                <option value="weak">Weak</option>
                <option value="not_started">Not Started</option>
              </select>
              <button className="btn btn-ghost btn-icon btn-sm" title="Table options"><Icon name="filter" size={13} /></button>
            </span>
          }
        >
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Skill</th><th>Category</th><th>Success</th><th style={{ width: 130 }}>Coverage</th>
                  <th>Last trained</th><th>Status</th><th style={{ width: 30 }} />
                </tr>
              </thead>
              <tbody>
                {filtered.map((s) => (
                  <tr key={s.id} className="rowlink" onClick={() => nav(`/skills/${s.id}`)}>
                    <td>
                      <div className="cell-main">
                        <span className="cell-ico"><Icon name={s.icon as IconName} size={13} /></span>
                        <span className="col" style={{ gap: 0 }}>
                          <span style={{ fontWeight: 580 }}>{s.name}</span>
                          <span className="micro t3 ellipsis" style={{ maxWidth: 190 }}>{s.description}</span>
                        </span>
                      </div>
                    </td>
                    <td className="t-muted">{s.category}</td>
                    <td>
                      <div className="col" style={{ gap: 0 }}>
                        <span className="mono" style={{ fontWeight: 620, color: s.success >= 90 ? "var(--text-1)" : s.success >= 80 ? "var(--text-1)" : "var(--text-1)" }}>
                          {s.success.toFixed(1)}%
                        </span>
                        <Delta value={`${Math.abs(s.successDelta).toFixed(1)}pp`} dir={s.successDelta >= 0 ? "up" : "down"} goodWhen="up" />
                      </div>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 8 }}>
                        <Progress value={s.coverage} tone={pctTone(s.coverage)} style={{ flex: 1 }} />
                        <span className="mono t2" style={{ fontSize: "var(--fs-small)", width: 32, textAlign: "right" }}>{s.coverage}%</span>
                      </div>
                    </td>
                    <td className="t-muted" style={{ fontSize: "var(--fs-small)" }}>{s.lastTrained}</td>
                    <td><StatusBadge status={s.status} /></td>
                    <td>
                      <button className="icon-btn btn-sm" onClick={(e) => e.stopPropagation()}><Icon name="dots" size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row between" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
            <span className="micro t3">Showing {filtered.length} of 142 skills</span>
            <Pagination page={page} pages={24} onPage={setPage} />
          </div>
        </Card>

        {/* Scenario coverage */}
        <div className="sk-right">
          <Card
            title="Scenario Coverage"
            info
            right={
              <select className="select" style={{ width: 150 }} value={coverageSkill} onChange={(e) => setCoverageSkill(e.target.value)}>
                {skills.map((s) => <option key={s.id}>{s.name}</option>)}
              </select>
            }
            flush
          >
            <table className="table">
              <thead>
                <tr>
                  <th>Dimension</th><th>Coverage</th><th>Gaps</th>
                  <th colSpan={4} style={{ minWidth: 280 }}>
                    <span className="row" style={{ justifyContent: "space-between" }}>
                      <span>Easy</span><span>Nominal</span><span>Hard</span><span>Extreme</span>
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {scenarioCoverageDims.map((d) => (
                  <tr key={d.dimension}>
                    <td style={{ fontWeight: 550 }}>{d.dimension}</td>
                    <td className="mono t2">{d.coverage}%</td>
                    <td className="mono t2">{d.gaps}</td>
                    <td colSpan={4} style={{ minWidth: 260 }}>
                      <CoverageBands bands={d.bands} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ gap: 14, padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
              {[
                ["var(--green)", "Covered (≥80%)"],
                ["var(--amber)", "Partial (50–79%)"],
                ["var(--red)", "Gap (<50%)"],
                ["rgba(148,170,220,0.18)", "Not tested"],
              ].map(([c, l]) => (
                <span key={l} className="row micro t2" style={{ gap: 6 }}>
                  <i style={{ width: 9, height: 9, borderRadius: 2.5, background: c }} /> {l}
                </span>
              ))}
            </div>
          </Card>

          <div className="sk-bottom" style={{ marginTop: 0, gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
            {/* Recommended next skills */}
            <Card title="Recommended next skills" info right={<CardLink>View all</CardLink>} flush>
              {recommendedSkills.map((r) => (
                <div key={r.name} className="row" style={{ gap: 10, padding: "9px 14px", borderBottom: "1px solid rgba(148,170,220,0.06)" }}>
                  <span className="cell-ico" style={{ width: 24, height: 24, fontSize: 11, fontWeight: 700, fontFamily: "var(--font-mono)" }}>{r.rank}</span>
                  <span className="col grow" style={{ gap: 0 }}>
                    <span style={{ fontWeight: 580 }}>{r.name}</span>
                    <span className="micro t3">{r.impact} · {r.gaps} scenario gaps</span>
                  </span>
                  <button className="btn btn-secondary btn-sm">Review</button>
                </div>
              ))}
            </Card>

            {/* Skill relationships */}
            <Card title="Skill relationships" info right={<CardLink>View graph</CardLink>}>
              <SkillGraphMini />
              <div className="row" style={{ gap: 14, marginTop: 10 }}>
                <span className="row micro t3" style={{ gap: 6 }}><i style={{ width: 14, height: 0, borderTop: "2px solid var(--text-3)" }} /> Prerequisite</span>
                <span className="row micro t3" style={{ gap: 6 }}><i style={{ width: 14, height: 0, borderTop: "2px dashed var(--text-3)" }} /> Stronger with</span>
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Compact relationship diagram for the selected skill — real SVG layout. */
function SkillGraphMini() {
  const root = skillRelations.root;
  const nodes = skillRelations.edges;
  return (
    <svg viewBox="0 0 300 150" style={{ width: "100%", display: "block", marginTop: 4 }}>
      {/* edges */}
      {nodes.map((n, i) => {
        const y = 28 + i * 46;
        const dashed = n.kind === "stronger";
        return (
          <g key={n.to}>
            <path
              d={`M 108 75 C 150 75, 150 ${y}, 182 ${y}`}
              fill="none"
              stroke="rgba(148,170,220,0.3)"
              strokeWidth={1.3}
              strokeDasharray={dashed ? "4 4" : undefined}
            />
            <path d={`M 182 ${y} l -6 -3 v 6 z`} fill="rgba(148,170,220,0.45)" transform={`translate(6,0)`} />
          </g>
        );
      })}
      {/* root node */}
      <g>
        <rect x={14} y={58} width={96} height={34} rx={7} fill="var(--bg-panel-3)" stroke="rgba(76,195,138,0.5)" />
        <text x={62} y={73} textAnchor="middle" fontSize={10.5} fontWeight={650} fill="var(--text-1)">{root.name}</text>
        <text x={62} y={85} textAnchor="middle" fontSize={8.5} fill="var(--green)">{root.status}</text>
      </g>
      {/* targets */}
      {nodes.map((n, i) => {
        const y = 28 + i * 46;
        const col = n.status === "Weak" ? "var(--red)" : n.status === "Improving" ? "var(--amber)" : "var(--green)";
        return (
          <g key={n.to}>
            <rect x={196} y={y - 15} width={96} height={30} rx={7} fill="var(--bg-panel-3)" stroke="var(--border-strong)" />
            <text x={244} y={y - 2} textAnchor="middle" fontSize={10} fontWeight={600} fill="var(--text-1)">{n.to}</text>
            <text x={244} y={y + 9} textAnchor="middle" fontSize={8.5} fill={col}>{n.status}</text>
          </g>
        );
      })}
    </svg>
  );
}
