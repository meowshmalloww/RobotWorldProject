import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Progress } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Delta, Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { useToast } from "../components/ui/Toast";
import { CoverageBands } from "../components/charts/CoverageBands";
import { DonutGauge } from "../components/charts/DonutGauge";
import { DistributionDonut } from "../components/charts/Heatmap";
import { RadarChart } from "../components/charts/RadarChart";
import { pctTone } from "../components/ui/helpers";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { CoverageDimension, Skill } from "../data/types";

const PAGE_SIZE = 20;

interface SkillsData {
  skills: Skill[];
  band: { label: string; value: string; foot: string; icon: string; tint: string }[];
  recommended: { rank: number; name: string; impact: string; gaps: number }[];
  relations: { root: { name: string; status: string }; edges: { to: string; status: string; kind: string }[] };
  coverageDims: CoverageDimension[];
  curves: { best: number[]; baseline: number[] };
}

export default function Skills() {
  const nav = useNavigate();
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<SkillsData>("/skills");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("All categories");
  const [status, setStatus] = useState("All status");
  const [page, setPage] = useState(1);
  const [coverageSkill, setCoverageSkill] = useState("");

  const allSkills = useMemo(() => data?.skills ?? [], [data]);
  const filtered = useMemo(
    () =>
      allSkills.filter(
        (s) =>
          s.name.toLowerCase().includes(q.toLowerCase()) &&
          (category === "All categories" || s.category === category) &&
          (status === "All status" || s.status === status),
      ),
    [allSkills, q, category, status],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const distribution = useMemo(() => {
    const count = (pred: (s: Skill) => boolean) => allSkills.filter(pred).length;
    return [
      { label: "Ready", value: count((s) => s.status === "ready"), color: "var(--series-2)" },
      { label: "Improving", value: count((s) => s.status === "improving" || s.status === "in_training"), color: "var(--series-3)" },
      { label: "Weak", value: count((s) => s.status === "weak"), color: "var(--series-6)" },
      { label: "Not started", value: count((s) => s.status === "not_started"), color: "var(--series-muted)" },
    ];
  }, [allSkills]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Skills &amp; Coverage</h1>
          <p className="page-sub">Manage robot skills, track coverage across scenarios, and close capability gaps.</p>
        </div>
      </div>

      {error && <div className="card" style={{ marginBottom: 10 }}><ErrorState message={error.message} onRetry={refetch} /></div>}

      <div className="sk-stats">
        {loading && !data
          ? Array.from({ length: 5 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : data?.band.map((s) => (
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
                    <DonutGauge value={parseFloat(s.value) / 100 || 0} size={44} stroke={4.5} />
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
            </span>
          }
        >
          {loading && !data ? (
            <Skeleton rows={6} />
          ) : paged.length > 0 ? (
            <>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Skill</th><th>Category</th><th>Success</th><th style={{ width: 130 }}>Coverage</th>
                      <th>Last trained</th><th>Status</th><th style={{ width: 30 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((s) => (
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
                            <span className="mono" style={{ fontWeight: 620, color: "var(--text-1)" }}>
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
                <span className="micro t3">Showing {paged.length} of {allSkills.length} skills</span>
                <Pagination page={page} pages={pages} onPage={setPage} />
              </div>
            </>
          ) : (
            !error && <EmptyState icon="skills">No skills yet — the curriculum agent creates them as training runs complete.</EmptyState>
          )}
        </Card>

        {/* Scenario coverage */}
        <div className="sk-right">
          <Card
            title="Scenario Coverage"
            info
            right={
              <select className="select" style={{ width: 150 }} value={coverageSkill} onChange={(e) => setCoverageSkill(e.target.value)}>
                {allSkills.length === 0 && <option value="">All skills</option>}
                {allSkills.map((s) => <option key={s.id}>{s.name}</option>)}
              </select>
            }
            flush
          >
            {loading && !data ? (
              <Skeleton rows={5} />
            ) : data && data.coverageDims.length > 0 ? (
              <>
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
                    {data.coverageDims.map((d) => (
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
              </>
            ) : (
              <EmptyState icon="gauge">No coverage dimensions reported yet.</EmptyState>
            )}
          </Card>

          <Card title="Measured Capability Profile" info collapsible>
            {data && data.coverageDims.length >= 3 ? (
              <div className="center" style={{ padding: "4px 0" }}>
                <RadarChart data={data.coverageDims.map((d) => ({ label: d.dimension, value: d.coverage }))} />
              </div>
            ) : loading && !data ? (
              <Skeleton rows={4} />
            ) : (
              <EmptyState icon="gauge">A radar appears after at least three evaluated capability dimensions exist.</EmptyState>
            )}
          </Card>

          {/* Skill distribution */}
          <Card title="Skill Distribution">
            {allSkills.length > 0 ? (
              <DistributionDonut
                size={132}
                centerLabel={String(allSkills.length)}
                centerSub="skills"
                segments={distribution}
              />
            ) : loading && !data ? (
              <Skeleton rows={3} />
            ) : (
              <EmptyState icon="skills">No skills to chart yet.</EmptyState>
            )}
          </Card>

          <div className="sk-bottom" style={{ marginTop: 0, gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
            {/* Recommended next skills */}
            <Card title="Recommended next skills" info flush>
              {loading && !data ? (
                <Skeleton rows={3} />
              ) : data && data.recommended.length > 0 ? (
                data.recommended.map((r) => (
                  <div key={r.name} className="row" style={{ gap: 10, padding: "9px 14px", borderBottom: "1px solid rgba(148,170,220,0.06)" }}>
                    <span className="cell-ico" style={{ width: 24, height: 24, fontSize: 11, fontWeight: 700, fontFamily: "var(--font-mono)" }}>{r.rank}</span>
                    <span className="col grow" style={{ gap: 0 }}>
                      <span style={{ fontWeight: 580 }}>{r.name}</span>
                      <span className="micro t3">{r.impact} · {r.gaps} scenario gaps</span>
                    </span>
                    <button className="btn btn-secondary btn-sm" onClick={() => { nav(`/skills/${r.name.toLowerCase().replace(/\s+/g, "-")}`); toast.push("info", "Opening skill", r.name); }}>Review</button>
                  </div>
                ))
              ) : (
                <EmptyState icon="spark">No recommendations yet — the agent proposes skills after evaluation runs.</EmptyState>
              )}
            </Card>

            {/* Skill relationships — compact tree */}
            <Card title="Skill Relationships" info>
              {loading && !data ? (
                <Skeleton rows={4} />
              ) : data && (data.relations.root.name || data.relations.edges.length > 0) ? (
                <div className="skill-tree">
                  <div className="skill-tree-node root">
                    <span className="cell-ico" style={{ width: 22, height: 22 }}><Icon name="skills" size={12} /></span>
                    <span className="col grow" style={{ gap: 0 }}>
                      <span style={{ fontWeight: 600, fontSize: "var(--fs-body)" }}>{data.relations.root.name}</span>
                      <span className="micro g-green">{data.relations.root.status}</span>
                    </span>
                  </div>
                  {data.relations.edges.map((e) => (
                    <div key={e.to} className="skill-tree-row">
                      <span className="skill-tree-rail" style={{ borderTop: e.kind === "prereq" ? "1px solid var(--border-strong)" : "1px dashed var(--border-strong)" }} />
                      <span className="cell-ico" style={{ width: 20, height: 20 }}><Icon name="skills" size={11} /></span>
                      <span className="col grow" style={{ gap: 0 }}>
                        <span style={{ fontWeight: 560, fontSize: "var(--fs-small)" }}>{e.to}</span>
                        <span className={`micro ${e.status === "Weak" ? "g-red" : "g-amber"}`}>{e.status} · {e.kind === "prereq" ? "prerequisite" : "stronger with"}</span>
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState icon="workflow">No skill relationships mapped yet.</EmptyState>
              )}
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
