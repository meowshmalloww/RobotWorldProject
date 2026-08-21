import { useState } from "react";
import { Card } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { StatusBadge } from "../components/ui/controls";
import { LineChart } from "../components/charts/LineChart";
import { downloadFile } from "../components/ui/Modal";
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
  successCurve: { measured: number[] };
  collisionCurve: { measured: number[] };
  agentDecision: {
    title: string;
    decision: string;
    evidence: string[];
    nextStep: { name: string; meta: string };
    confidence: number;
  } | null;
}

interface LocalVlaStatus {
  available: boolean;
  modelId?: string;
  path: string;
  modelBytes?: number;
  tensorCount?: number;
  checkpoint?: { dtype: string; backbone: string; cameras: string[]; stateSize: number; actionSize: number; actionHorizon: number };
  robotWorldContract?: { compatible: boolean; blockers: string[] };
  runtime?: { resident: boolean; idleUnloadSeconds: number; lerobotInstalledInBackend: boolean; loadAllowed: boolean };
}

export default function Training() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<TrainingData>("/training");
  const { data: localVla, error: localVlaError, loading: localVlaLoading } = useApi<LocalVlaStatus>("/models/vla-jepa/status");
  const { data: robotData } = useApi<{ robots: { id: string; name: string; readiness: { executable: boolean; blockers: string[] } }[] }>("/robots");
  // needed for the "run the agent" button when there is no decision yet
  const { data: skillsData } = useApi<{ skills: Skill[] }>("/skills");
  const [runningAgent, setRunningAgent] = useState(false);

  const runs = data?.runs ?? [];
  const agentDecision = data?.agentDecision ?? null;

  const exportCsv = () => {
    const header = "run_id,name,policy,worlds,duration,delta_success_pp,status,when";
    const body = runs.map((r) => [r.runId, `"${r.name}"`, `"${r.policy}"`, r.worlds, `"${r.duration}"`, r.delta, r.status, `"${r.when}"`].join(","));
    downloadFile("training-runs.csv", [header, ...body].join("\n"), "text/csv");
    toast.push("ok", "Runs exported", `training-runs.csv · ${runs.length} rows`);
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
          <h1 className="page-title">Policy &amp; Evaluation</h1>
          <p className="page-sub">Review measured evaluations and checkpoint readiness. Training is intentionally disabled on this workstation.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary" onClick={exportCsv} disabled={runs.length === 0}><Icon name="download" size={13} /> Export runs</button>
          <button className="btn btn-secondary" disabled title="RobotWorld will not train until a separate training environment is explicitly authorized"><Icon name="lock" size={13} /> Training disabled</button>
        </div>
      </div>

      <Card
        title="Local VLA-JEPA checkpoint"
        right={<StatusBadge status={localVla?.robotWorldContract?.compatible ? "ready" : localVla?.available ? "blocked" : "offline"} />}
        style={{ marginBottom: 10 }}
      >
        {localVlaLoading ? <Skeleton rows={3} /> : localVlaError ? <ErrorState message={localVlaError.message} /> : localVla ? (
          <div className="col" style={{ gap: 8 }}>
            <div className="st-grid">
              <div className="kv-row"><span className="kv-k">Checkpoint</span><span className="kv-v mono">{localVla.modelId ?? "unavailable"}</span></div>
              <div className="kv-row"><span className="kv-k">Weights</span><span className="kv-v mono">{localVla.modelBytes ? `${(localVla.modelBytes / 1e9).toFixed(2)} GB · ${localVla.tensorCount} tensors` : "missing"}</span></div>
              <div className="kv-row"><span className="kv-k">Runtime residency</span><span className="kv-v mono">{localVla.runtime?.resident ? "loaded" : `offloaded · ${localVla.runtime?.idleUnloadSeconds ?? 300}s policy`}</span></div>
              <div className="kv-row"><span className="kv-k">I/O contract</span><span className="kv-v mono">{localVla.checkpoint ? `${localVla.checkpoint.cameras.length} cameras · state ${localVla.checkpoint.stateSize} · action ${localVla.checkpoint.actionSize}×${localVla.checkpoint.actionHorizon}` : "unknown"}</span></div>
            </div>
            {localVla.robotWorldContract && !localVla.robotWorldContract.compatible && (
              <div className="callout callout-warn" style={{ margin: 0 }}>
                <Icon name="warning" size={13} />
                <span><b>Execution blocked by embodiment contract.</b> {localVla.robotWorldContract.blockers.join("; ")}.</span>
              </div>
            )}
          </div>
        ) : null}
      </Card>

      <Card title="Embodiment learning loop" right={<StatusBadge status={robotData?.robots.some((robot) => robot.readiness.executable) && localVla?.robotWorldContract?.compatible ? "ready" : "blocked"} />} style={{ marginBottom: 10 }}>
        <div className="row" style={{ alignItems: "stretch", gap: 6, flexWrap: "wrap" }}>
          {[
            ["1", "Import robot", robotData?.robots.length ? `${robotData.robots.length} inspected` : "URDF / MJCF / OpenUSD required"],
            ["2", "Map observations", "two checkpoint camera keys + robot state"],
            ["3", "Collect demonstrations", "LeRobot dataset; no synthetic success labels"],
            ["4", "Fine-tune adapters", "reinitialize camera/state/action projections"],
            ["5", "Evaluate in physics", "measured collisions and task predicates"],
            ["6", "Promote or repair", "only measured runs feed the next cycle"],
          ].map(([n, title, detail]) => <div className="card grow" style={{ minWidth: 155, padding: 9, background: "var(--bg-panel-2)" }} key={n}><span className="micro t3 mono">STEP {n}</span><div className="small" style={{ fontWeight: 650 }}>{title}</div><div className="micro t3">{detail}</div></div>)}
        </div>
        <div className="callout callout-warn" style={{ margin: "9px 0 0" }}><Icon name="lock" size={13} /><span><b>No training job will start yet.</b> The DROID VLA-JEPA checkpoint has a 7-D action / 8-D state contract and two exterior cameras; an arbitrary uploaded robot requires robot-specific data and fine-tuning.</span></div>
      </Card>

      <div className="tr-stats">
        {loading && !data
          ? Array.from({ length: 5 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : data?.stats.map((s) => <StatCard key={s.label} stat={s} small />)}
      </div>

      {error && <div className="card" style={{ marginBottom: 10 }}><ErrorState message={error.message} onRetry={refetch} /></div>}

      <div className="tr-main">
        <div className="col" style={{ gap: 10, minWidth: 0 }}>
          {/* Run history */}
          <Card title="Run history" flush>
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
                          <td />
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="row" style={{ padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
                  <span className="micro t3">{runs.length} runs</span>
                </div>
              </>
            ) : (
              !error && <EmptyState icon="training">No training runs. Attach a pinned compatible VLA checkpoint, then use policy evaluation.</EmptyState>
            )}
          </Card>

          {/* charts */}
          <div className="tr-charts" style={{ marginTop: 0 }}>
            <Card title="Measured evaluation success history" info>
              {loading && !data ? (
                <Skeleton rows={4} />
              ) : data && data.successCurve.measured.length > 1 ? (
                <>
                  <div className="legend" style={{ marginBottom: 4 }}>
                    <span className="lg"><i style={{ background: "var(--series-2)" }} /> Recorded episode outcome</span>
                  </div>
                  <LineChart
                    series={[
                      { name: "Measured", data: data.successCurve.measured, color: "var(--series-2)", endLabel: `${data.successCurve.measured.at(-1)?.toFixed(1)}%` },
                    ]}
                    height={190}
                    yMin={0}
                    yMax={100}
                    yTicks={4}
                    yFormat={(v) => `${v.toFixed(0)}%`}
                  />
                </>
              ) : (
                <EmptyState icon="chartBar">No success metrics yet — run a real policy evaluation after connecting the VLA.</EmptyState>
              )}
            </Card>
            <Card title="Measured collision history" info>
              {loading && !data ? (
                <Skeleton rows={4} />
              ) : data && data.collisionCurve.measured.length > 1 ? (
                <>
                  <div className="legend" style={{ marginBottom: 4 }}>
                    <span className="lg"><i style={{ background: "var(--series-2)" }} /> Recorded collision count</span>
                  </div>
                  <LineChart
                    series={[
                      { name: "Measured", data: data.collisionCurve.measured, color: "var(--series-2)", endLabel: `${data.collisionCurve.measured.at(-1)?.toFixed(0)}` },
                    ]}
                    height={190}
                    yMin={0}
                    yTicks={4}
                    yFormat={(v) => `${v.toFixed(0)}`}
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
                <div className="card row" style={{ width: "100%", padding: "10px 12px", gap: 10, background: "var(--bg-panel-2)", textAlign: "left" }}>
                  <span className="cell-ico"><Icon name="cabinet" size={14} /></span>
                  <span className="col grow" style={{ gap: 0 }}>
                    <span style={{ fontWeight: 600, fontSize: "var(--fs-body)" }}>{agentDecision.nextStep.name}</span>
                    <span className="micro t3">{agentDecision.nextStep.meta}</span>
                  </span>
                </div>
                <div className="row between" style={{ margin: "12px 0 5px" }}>
                  <span className="section-label">Confidence</span>
                  <span className="small g-green" style={{ fontWeight: 640 }}>{agentDecision.confidence >= 0.75 ? "High" : agentDecision.confidence >= 0.4 ? "Medium" : "Low"}</span>
                </div>
                <div className="row" style={{ gap: 3 }}>
                  {Array.from({ length: 12 }, (_, i) => (
                    <i key={i} style={{ flex: 1, height: 5, borderRadius: 3, background: i / 12 < agentDecision.confidence ? "var(--green)" : "rgba(255,255,255,0.10)" }} />
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
