import { useRef, useState } from "react";
import { Card, CardLink } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { StatusBadge } from "../components/ui/controls";
import { LineChart } from "../components/charts/LineChart";
import { Modal, downloadFile } from "../components/ui/Modal";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import { fmtPp } from "../lib/format";
import type { EvalComparisonRow, Skill, Stat, TrainingRun } from "../data/types";

interface TrainingData {
  stats: Stat[];
  runs: TrainingRun[];
  evalComparison: EvalComparisonRow[];
  successCurve: { best: number[]; baseline: number[] };
  collisionCurve: { best: number[]; baseline: number[] };
  agentDecision: {
    title: string;
    decision: string;
    evidence: string[];
    nextStep: { name: string; meta: string };
    confidence: number;
  } | null;
}

export default function Training() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<TrainingData>("/training");
  // needed for the "run the agent" button when there is no decision yet
  const { data: skillsData } = useApi<{ skills: Skill[] }>("/skills");
  const [newRun, setNewRun] = useState(false);
  const [queuing, setQueuing] = useState(false);
  const [runningAgent, setRunningAgent] = useState(false);

  // New run form
  const [policy, setPolicy] = useState("Refrigerator v2.1.3 (latest)");
  const [curriculum, setCurriculum] = useState("Left-Hinge Heavy (recommended by agent)");
  const worldsRef = useRef<HTMLInputElement>(null);
  const itersRef = useRef<HTMLInputElement>(null);

  const runs = data?.runs ?? [];
  const agentDecision = data?.agentDecision ?? null;

  const exportCsv = () => {
    const header = "run_id,name,policy,worlds,duration,delta_success_pp,status,when";
    const body = runs.map((r) => [r.runId, `"${r.name}"`, `"${r.policy}"`, r.worlds, `"${r.duration}"`, r.delta, r.status, `"${r.when}"`].join(","));
    downloadFile("training-runs.csv", [header, ...body].join("\n"), "text/csv");
    toast.push("ok", "Runs exported", `training-runs.csv · ${runs.length} rows`);
  };

  const queueRun = async () => {
    setQueuing(true);
    try {
      const { runId } = await api.post<{ runId: string }>("/training/runs", {
        policy,
        curriculum,
        worlds: Number(worldsRef.current?.value.replace(/\D/g, "")) || 0,
        iterations: Number(itersRef.current?.value.replace(/\D/g, "")) || 0,
      });
      setNewRun(false);
      toast.push("ok", "Training run queued", `Run ${runId} · ${policy}`);
      refetch();
    } catch (e) {
      toast.push("err", "Could not queue run", e instanceof ApiError ? e.message : String(e));
    } finally {
      setQueuing(false);
    }
  };

  const runAgent = async () => {
    const skillId = skillsData?.skills[0]?.id;
    if (!skillId) {
      toast.push("err", "No skills available", "The agent needs at least one skill to analyze");
      return;
    }
    setRunningAgent(true);
    try {
      const { jobId } = await api.post<{ jobId: string }>("/agent/run", { skillId });
      toast.push("ok", "Agent iteration started", `Job ${jobId} · analyzing ${skillId}`);
      setTimeout(refetch, 5000);
    } catch (e) {
      toast.push("err", "Agent failed to start", e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunningAgent(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Training &amp; Evaluation</h1>
          <p className="page-sub">Train policies, evaluate performance, and improve success across real-world variations.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary" onClick={exportCsv} disabled={runs.length === 0}><Icon name="download" size={13} /> Export runs</button>
          <button className="btn btn-primary" onClick={() => setNewRun(true)}><Icon name="plus" size={13} /> New training run</button>
        </div>
      </div>

      {newRun && (
        <Modal
          title="New training run"
          onClose={() => setNewRun(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setNewRun(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={queueRun} disabled={queuing}>{queuing ? "Queuing…" : "Queue run"}</button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="field">
              <label>Policy</label>
              <select className="select" value={policy} onChange={(e) => setPolicy(e.target.value)}>
                <option>Refrigerator v2.1.3 (latest)</option><option>Cabinet Open v1.4.2</option><option>Trash Sort v1.0.9</option>
              </select>
            </div>
            <div className="field">
              <label>Curriculum</label>
              <select className="select" value={curriculum} onChange={(e) => setCurriculum(e.target.value)}>
                <option>Left-Hinge Heavy (recommended by agent)</option><option>Open Cabinet Curriculum 13</option><option>Custom scenario set…</option>
              </select>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow"><label>Worlds</label><input ref={worldsRef} className="input mono" defaultValue="640" /></div>
              <div className="field grow"><label>Iterations</label><input ref={itersRef} className="input mono" defaultValue="35,000" /></div>
            </div>
          </div>
        </Modal>
      )}

      <div className="tr-stats">
        {loading && !data
          ? Array.from({ length: 5 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : data?.stats.map((s) => <StatCard key={s.label} stat={s} small />)}
      </div>

      {error && <div className="card" style={{ marginBottom: 10 }}><ErrorState message={error.message} onRetry={refetch} /></div>}

      <div className="tr-main">
        <div className="col" style={{ gap: 10, minWidth: 0 }}>
          {/* Run history */}
          <Card title="Run history" right={<CardLink>View all runs</CardLink>} flush>
            {loading && !data ? (
              <Skeleton rows={6} />
            ) : runs.length > 0 ? (
              <>
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
                      {runs.map((r) => (
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
                  <span className="micro t3">{runs.length} runs</span>
                  <CardLink>View all runs</CardLink>
                </div>
              </>
            ) : (
              !error && <EmptyState icon="training">No training runs yet — queue one with <b>New training run</b>.</EmptyState>
            )}
          </Card>

          {/* charts */}
          <div className="tr-charts" style={{ marginTop: 0 }}>
            <Card title="Success rate over training iterations" info right={<CardLink>View details</CardLink>}>
              {loading && !data ? (
                <Skeleton rows={4} />
              ) : data && data.successCurve.best.length > 1 ? (
                <>
                  <div className="legend" style={{ marginBottom: 4 }}>
                    <span className="lg"><i style={{ background: "var(--series-2)" }} /> Best</span>
                    <span className="lg"><i style={{ background: "var(--series-1)" }} /> Baseline</span>
                  </div>
                  <LineChart
                    series={[
                      { name: "Best", data: data.successCurve.best, color: "var(--series-2)", endLabel: `${data.successCurve.best.at(-1)?.toFixed(1)}%` },
                      { name: "Baseline", data: data.successCurve.baseline, color: "var(--series-1)", endLabel: `${data.successCurve.baseline.at(-1)?.toFixed(1)}%` },
                    ]}
                    height={190}
                    yMin={0}
                    yMax={100}
                    yTicks={4}
                    yFormat={(v) => `${v.toFixed(0)}%`}
                  />
                </>
              ) : (
                <EmptyState icon="chartBar">No success metrics yet — they appear after the first training runs.</EmptyState>
              )}
            </Card>
            <Card title="Collision rate over training iterations" info right={<CardLink>View details</CardLink>}>
              {loading && !data ? (
                <Skeleton rows={4} />
              ) : data && data.collisionCurve.best.length > 1 ? (
                <>
                  <div className="legend" style={{ marginBottom: 4 }}>
                    <span className="lg"><i style={{ background: "var(--series-2)" }} /> Best</span>
                    <span className="lg"><i style={{ background: "var(--series-1)" }} /> Baseline</span>
                  </div>
                  <LineChart
                    series={[
                      { name: "Best", data: data.collisionCurve.best, color: "var(--series-2)", endLabel: `${data.collisionCurve.best.at(-1)?.toFixed(1)}%` },
                      { name: "Baseline", data: data.collisionCurve.baseline, color: "var(--series-1)", endLabel: `${data.collisionCurve.baseline.at(-1)?.toFixed(1)}%` },
                    ]}
                    height={190}
                    yMin={0}
                    yTicks={4}
                    yFormat={(v) => `${v.toFixed(0)}%`}
                  />
                </>
              ) : (
                <EmptyState icon="chartBar">No collision metrics yet.</EmptyState>
              )}
            </Card>
          </div>
        </div>

        {/* right rail */}
        <div className="tr-right">
          {/* Agent decision */}
          <Card title={<span className="row" style={{ gap: 7 }}><Icon name="agent" size={14} style={{ color: "var(--purple)" }} /> Agent Decision</span>} info>
            {loading && !data ? (
              <Skeleton rows={4} />
            ) : agentDecision ? (
              <>
                <div className="small t2" style={{ marginBottom: 8 }}>{agentDecision.title}</div>
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
                  <span className="small g-green" style={{ fontWeight: 640 }}>{agentDecision.confidence >= 0.75 ? "High" : agentDecision.confidence >= 0.4 ? "Medium" : "Low"}</span>
                </div>
                <div className="row" style={{ gap: 3 }}>
                  {Array.from({ length: 12 }, (_, i) => (
                    <i key={i} style={{ flex: 1, height: 5, borderRadius: 3, background: i / 12 < agentDecision.confidence ? "var(--green)" : "rgba(148,170,220,0.15)" }} />
                  ))}
                </div>
              </>
            ) : (
              <div className="empty-note col center" style={{ gap: 8, padding: 18 }}>
                <Icon name="agent" size={18} style={{ color: "var(--purple)" }} />
                <span>No agent decision yet — run the agent to analyze skill gaps.</span>
                <button className="btn btn-primary btn-sm" onClick={runAgent} disabled={runningAgent}>
                  <Icon name="play" size={12} /> {runningAgent ? "Starting…" : "Run the agent"}
                </button>
              </div>
            )}
          </Card>

          {/* Before / after evaluation */}
          <Card title={<span className="row" style={{ gap: 7 }}><Icon name="compare" size={14} style={{ color: "var(--accent)" }} /> Before / After Evaluation</span>} info>
            <div className="small t2" style={{ marginBottom: 8 }}>Compare baseline vs candidate policy</div>
            {loading && !data ? (
              <Skeleton rows={4} />
            ) : data && data.evalComparison.length > 0 ? (
              <>
                <table className="table">
                  <thead>
                    <tr><th>Task</th><th style={{ textAlign: "right" }}>Baseline</th><th style={{ textAlign: "right" }}>Candidate</th><th style={{ textAlign: "right" }}>Δ (pp)</th></tr>
                  </thead>
                  <tbody>
                    {data.evalComparison.map((r) => (
                      <tr key={r.task}>
                        <td>
                          <span className="row" style={{ gap: 7 }}>
                            <Icon name={r.icon as IconName} size={12} style={{ color: "var(--text-3)" }} />
                            {r.task}
                          </span>
                        </td>
                        <td className="mono t2" style={{ textAlign: "right" }}>{r.baseline.toFixed(1)}%</td>
                        <td className="mono g-green" style={{ textAlign: "right", fontWeight: 620 }}>{r.candidate.toFixed(1)}%</td>
                        <td className="mono" style={{ textAlign: "right", fontWeight: 620, color: r.candidate >= r.baseline ? "var(--green)" : "var(--red)" }}>
                          {r.candidate >= r.baseline ? "+" : ""}{(r.candidate - r.baseline).toFixed(1)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ marginTop: 10 }}>
                  <CardLink>View full evaluation report <Icon name="external" size={10} /></CardLink>
                </div>
              </>
            ) : (
              <EmptyState icon="compare">No evaluation comparisons yet — promote a candidate policy to compare.</EmptyState>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
