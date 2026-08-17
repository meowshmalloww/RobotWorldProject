import { Card, CardLink } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { StatusBadge } from "../components/ui/controls";
import { LineChart } from "../components/charts/LineChart";
import { agentDecision, collisionCurve, evalComparison, successCurve, trainingRuns, trainingStats } from "../data/training";
import { fmtPp } from "../data/util";

export default function Training() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Training &amp; Evaluation</h1>
          <p className="page-sub">Train policies, evaluate performance, and improve success across real-world variations.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary"><Icon name="download" size={13} /> Export runs</button>
          <button className="btn btn-primary"><Icon name="plus" size={13} /> New training run</button>
        </div>
      </div>

      <div className="tr-stats">
        {trainingStats.map((s) => <StatCard key={s.label} stat={s} small />)}
      </div>

      <div className="tr-main">
        <div className="col" style={{ gap: 10, minWidth: 0 }}>
          {/* Run history */}
          <Card title="Run history" right={<CardLink>View all runs</CardLink>} flush>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Run</th><th>Policy</th><th style={{ textAlign: "right" }}>Worlds</th>
                    <th style={{ textAlign: "right" }}>Duration</th><th style={{ textAlign: "right" }}>Δ Success</th>
                    <th>Status</th><th style={{ width: 30 }} />
                  </tr>
                </thead>
                <tbody>
                  {trainingRuns.map((r) => (
                    <tr key={r.id} className="rowlink">
                      <td>
                        <div className="cell-main">
                          <span className="cell-ico"><Icon name="play" size={11} /></span>
                          <span className="col" style={{ gap: 0 }}>
                            <span style={{ fontWeight: 580 }}>{r.name}</span>
                            <span className="micro t3 mono">Run ID: {r.runId}</span>
                          </span>
                        </div>
                      </td>
                      <td>
                        <span className="row" style={{ gap: 7 }}>
                          <Icon name="cube" size={13} style={{ color: "var(--text-3)" }} />
                          {r.policy}
                        </span>
                      </td>
                      <td className="mono t2" style={{ textAlign: "right" }}>{r.worlds.toLocaleString()}</td>
                      <td className="mono t2" style={{ textAlign: "right" }}>{r.duration}</td>
                      <td className="mono" style={{ textAlign: "right", fontWeight: 640, color: r.delta >= 0 ? "var(--green)" : "var(--red)" }}>
                        {fmtPp(r.delta)}
                      </td>
                      <td>
                        <span className="col" style={{ gap: 0 }}>
                          <StatusBadge status={r.status} />
                          <span className="micro t3" style={{ marginTop: 2 }}>{r.when}</span>
                        </span>
                      </td>
                      <td><button className="icon-btn btn-sm"><Icon name="dots" size={13} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row between" style={{ padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
              <span className="micro t3">Showing 1–6 of 24 runs</span>
              <CardLink>View all runs</CardLink>
            </div>
          </Card>

          {/* charts */}
          <div className="tr-charts" style={{ marginTop: 0 }}>
            <Card title="Success rate over training iterations" info right={<CardLink>View details</CardLink>}>
              <div className="legend" style={{ marginBottom: 4 }}>
                <span className="lg"><i style={{ background: "var(--series-2)" }} /> Best (Refrigerator v2.1.3)</span>
                <span className="lg"><i style={{ background: "var(--series-1)" }} /> Baseline (Refrigerator v2.0.0)</span>
              </div>
              <LineChart
                series={[
                  { name: "Best", data: successCurve.best, color: "var(--series-2)", endLabel: "98.7%" },
                  { name: "Baseline", data: successCurve.baseline, color: "var(--series-1)", endLabel: "86.3%" },
                ]}
                height={190}
                yMin={0}
                yMax={100}
                yTicks={4}
                yFormat={(v) => `${v.toFixed(0)}%`}
                xLabels={["0", "5K", "10K", "15K", "20K", "25K", "30K", "35K"]}
              />
            </Card>
            <Card title="Collision rate over training iterations" info right={<CardLink>View details</CardLink>}>
              <div className="legend" style={{ marginBottom: 4 }}>
                <span className="lg"><i style={{ background: "var(--series-2)" }} /> Best (Refrigerator v2.1.3)</span>
                <span className="lg"><i style={{ background: "var(--series-1)" }} /> Baseline (Refrigerator v2.0.0)</span>
              </div>
              <LineChart
                series={[
                  { name: "Best", data: collisionCurve.best, color: "var(--series-2)", endLabel: "1.8%" },
                  { name: "Baseline", data: collisionCurve.baseline, color: "var(--series-1)", endLabel: "6.7%" },
                ]}
                height={190}
                yMin={0}
                yMax={22}
                yTicks={4}
                yFormat={(v) => `${v.toFixed(0)}%`}
                xLabels={["0", "5K", "10K", "15K", "20K", "25K", "30K", "35K"]}
              />
            </Card>
          </div>
        </div>

        {/* right rail */}
        <div className="tr-right">
          {/* Agent decision */}
          <Card title={<span className="row" style={{ gap: 7 }}><Icon name="agent" size={14} style={{ color: "var(--purple)" }} /> Agent Decision</span>} info>
            <div className="small t2" style={{ marginBottom: 8 }}>Why we selected the next curriculum</div>
            <div className="card" style={{ background: "var(--bg-panel-2)", padding: "10px 12px" }}>
              <div className="row" style={{ gap: 8 }}>
                <Icon name="check" size={14} style={{ color: "var(--green)", flex: "none", marginTop: 1 }} />
                <span style={{ fontSize: "var(--fs-body)", fontWeight: 550 }}>{agentDecision.decision}</span>
              </div>
            </div>
            <div className="section-label" style={{ margin: "12px 0 5px" }}>Evidence</div>
            <ul className="col" style={{ gap: 4 }}>
              {agentDecision.evidence.map((e) => (
                <li key={e} className="row small t2" style={{ gap: 8 }}>
                  <span style={{ width: 4, height: 4, borderRadius: 2, background: "var(--text-3)", flex: "none" }} />
                  {e}
                </li>
              ))}
            </ul>
            <div className="section-label" style={{ margin: "12px 0 6px" }}>Recommended next step</div>
            <button className="card row clickable" style={{ width: "100%", padding: "10px 12px", gap: 10, background: "var(--bg-panel-2)", textAlign: "left" }}>
              <span className="cell-ico"><Icon name="cabinet" size={14} /></span>
              <span className="col grow" style={{ gap: 0 }}>
                <span style={{ fontWeight: 600, fontSize: "var(--fs-body)" }}>{agentDecision.nextStep.name}</span>
                <span className="micro t3">{agentDecision.nextStep.meta}</span>
              </span>
              <Icon name="chevronRight" size={13} style={{ color: "var(--text-3)" }} />
            </button>
            <div className="row between" style={{ margin: "12px 0 5px" }}>
              <span className="section-label">Confidence</span>
              <span className="small g-green" style={{ fontWeight: 640 }}>High</span>
            </div>
            <div className="row" style={{ gap: 3 }}>
              {Array.from({ length: 12 }, (_, i) => (
                <i key={i} style={{ flex: 1, height: 5, borderRadius: 3, background: i / 12 < agentDecision.confidence ? "var(--green)" : "rgba(148,170,220,0.15)" }} />
              ))}
            </div>
          </Card>

          {/* Before / after evaluation */}
          <Card title={<span className="row" style={{ gap: 7 }}><Icon name="compare" size={14} style={{ color: "var(--accent)" }} /> Before / After Evaluation</span>} info>
            <div className="small t2" style={{ marginBottom: 8 }}>Compare baseline vs candidate policy</div>
            <div className="row" style={{ gap: 8, marginBottom: 8 }}>
              <select className="select grow"><option>Refrigerator v2.0.0</option><option>Cabinet Open v1.3.9</option></select>
              <span className="t3 small">vs</span>
              <select className="select grow"><option>Refrigerator v2.1.3</option><option>Refrigerator v2.1.2</option></select>
            </div>
            <table className="table">
              <thead>
                <tr><th>Task</th><th style={{ textAlign: "right" }}>Baseline</th><th style={{ textAlign: "right" }}>Candidate</th><th style={{ textAlign: "right" }}>Δ (pp)</th></tr>
              </thead>
              <tbody>
                {evalComparison.map((r) => (
                  <tr key={r.task}>
                    <td>
                      <span className="row" style={{ gap: 7 }}>
                        <Icon name={r.icon as IconName} size={12} style={{ color: "var(--text-3)" }} />
                        {r.task}
                      </span>
                    </td>
                    <td className="mono t2" style={{ textAlign: "right" }}>{r.baseline.toFixed(1)}%</td>
                    <td className="mono g-green" style={{ textAlign: "right", fontWeight: 620 }}>{r.candidate.toFixed(1)}%</td>
                    <td className="mono g-green" style={{ textAlign: "right", fontWeight: 620 }}>+{(r.candidate - r.baseline).toFixed(1)}</td>
                  </tr>
                ))}
                <tr>
                  <td style={{ fontWeight: 640 }}>Overall Success</td>
                  <td className="mono t2" style={{ textAlign: "right", fontWeight: 640 }}>86.3%</td>
                  <td className="mono g-green" style={{ textAlign: "right", fontWeight: 700 }}>98.7%</td>
                  <td className="mono g-green" style={{ textAlign: "right", fontWeight: 700 }}>+12.4</td>
                </tr>
              </tbody>
            </table>
            <div style={{ marginTop: 10 }}>
              <CardLink>View full evaluation report <Icon name="external" size={10} /></CardLink>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
