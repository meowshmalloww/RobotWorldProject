import { useEffect, useMemo, useState } from "react";
import { Card } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

type RunState = "QUEUED" | "STARTING" | "RUNNING" | "SUCCEEDED" | "STOPPED" | "BLOCKED" | "CANCELLED" | "CRASHED";

interface RobotRegistration {
  id: string;
  displayName: string;
  lifecycleState: string;
  active: boolean;
}

interface ModelRegistration {
  id: string;
  displayName: string;
  roles: string[];
  lifecycleState: string;
  healthStatus: string;
}

interface AssetVersion {
  id: string;
  displayName: string;
  category: string;
  lifecycleState: string;
}

interface RunHistoryEntry {
  iteration?: number;
  phase: string;
  outcome?: string;
  planId?: string;
  scenarioId?: string;
  evaluationId?: string;
  success?: boolean;
  failureCode?: string | null;
  reusedValidatedScenario?: boolean;
  error?: string;
  at?: string;
}

interface AutonomousRun {
  id: string;
  lifecycleState: RunState;
  autonomyMode: string;
  robotId: string;
  modelId: string | null;
  taskFamily: string;
  instruction: string;
  budgets: {
    maxWorlds: number;
    maxScrapeRequests: number;
    maxGpuMinutes: number;
    maxEvaluationEpisodes: number;
    maxRetries: number;
    maxIterations: number;
    maxConsecutiveFailures: number;
  };
  state: {
    phase?: string;
    iteration?: number;
    consumed?: {
      worlds?: number;
      scrapeRequests?: number;
      gpuMinutes?: number;
      evaluationEpisodes?: number;
    };
    current?: { planId?: string; scenarioId?: string; oracleEvaluationId?: string };
    history?: RunHistoryEntry[];
    blockers?: string[];
  };
  cancellationRequested: boolean;
  commandId: string;
  error: string | null;
  stopReason: string | null;
  startedAt: string | null;
  heartbeatAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

interface RunEnvelope {
  commandId: string;
  reused: boolean;
  result: { run: AutonomousRun };
}

const TERMINAL = new Set<RunState>(["SUCCEEDED", "STOPPED", "BLOCKED", "CANCELLED", "CRASHED"]);

function errorText(reason: unknown): string {
  return reason instanceof ApiError ? reason.message : String(reason);
}

function statusForRun(state: RunState): string {
  if (state === "SUCCEEDED") return "passed";
  if (state === "BLOCKED" || state === "CRASHED") return "failed";
  if (state === "CANCELLED" || state === "STOPPED") return "blocked";
  return "running";
}

function valueOrZero(value: number | undefined, decimals = 0): string {
  return Number(value ?? 0).toFixed(decimals);
}

export default function AgentControl() {
  const toast = useToast();
  const robots = useApi<{ registrations: RobotRegistration[] }>("/robots");
  const models = useApi<{ models: ModelRegistration[] }>("/models");
  const assets = useApi<{ assetVersions: AssetVersion[] }>("/asset-versions");
  const runs = useApi<{ runs: AutonomousRun[] }>("/autonomous-runs?limit=100");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [robotId, setRobotId] = useState("");
  const [modelId, setModelId] = useState("");
  const [assetVersionId, setAssetVersionId] = useState("");
  const [autonomyMode, setAutonomyMode] = useState("EXECUTE_WITH_APPROVAL");
  const [instruction, setInstruction] = useState("Pick up the object and place it in the target.");
  const [executeVla, setExecuteVla] = useState(false);
  const [seed, setSeed] = useState(2401);
  const [worldBudget, setWorldBudget] = useState(1);
  const [episodeBudget, setEpisodeBudget] = useState(1);
  const [gpuBudget, setGpuBudget] = useState(0);
  const [iterationBudget, setIterationBudget] = useState(1);
  const [retryBudget, setRetryBudget] = useState(0);
  const [failureBudget, setFailureBudget] = useState(1);
  const [maxPolicySteps, setMaxPolicySteps] = useState(150);
  const [busy, setBusy] = useState<"start" | "cancel" | null>(null);

  const availableRobots = useMemo(
    () => (robots.data?.registrations ?? []).filter((robot) => robot.lifecycleState === "AVAILABLE"),
    [robots.data],
  );
  const vlaModels = useMemo(
    () => (models.data?.models ?? []).filter((model) => model.roles.includes("vla_policy")),
    [models.data],
  );
  const validatedAssets = useMemo(
    () => (assets.data?.assetVersions ?? []).filter((asset) => asset.lifecycleState === "ORACLE_VALIDATED"),
    [assets.data],
  );
  const selectedRun = (runs.data?.runs ?? []).find((run) => run.id === selectedRunId)
    ?? runs.data?.runs[0]
    ?? null;
  const activeRun = (runs.data?.runs ?? []).find((run) => !TERMINAL.has(run.lifecycleState)) ?? null;

  useEffect(() => {
    if (!robotId && availableRobots.length) {
      setRobotId((availableRobots.find((robot) => robot.active) ?? availableRobots[0]).id);
    }
  }, [availableRobots, robotId]);
  useEffect(() => {
    if (!assetVersionId && validatedAssets.length) setAssetVersionId(validatedAssets[0].id);
  }, [assetVersionId, validatedAssets]);
  useEffect(() => {
    if (!selectedRunId && runs.data?.runs.length) setSelectedRunId(runs.data.runs[0].id);
  }, [runs.data, selectedRunId]);
  useEffect(() => {
    if (!activeRun) return;
    const timer = window.setInterval(runs.refetch, 1000);
    return () => window.clearInterval(timer);
  }, [activeRun, runs.refetch]);
  useEffect(() => {
    if (executeVla) {
      setEpisodeBudget((value) => Math.max(2, value));
      setGpuBudget((value) => value > 0 ? value : 1);
      if (!modelId && vlaModels.length) setModelId(vlaModels[0].id);
    }
  }, [executeVla, modelId, vlaModels]);

  const startRun = async () => {
    if (!robotId || !assetVersionId || (executeVla && !modelId)) return;
    setBusy("start");
    try {
      const response = await api.post<RunEnvelope>("/autonomous-runs", {
        autonomyMode,
        robotId,
        modelId: modelId || null,
        taskFamily: "pick_place",
        instruction,
        executeVla,
        allowedAssetVersionIds: [assetVersionId],
        seed,
        maxPolicySteps,
        budgets: {
          maxWorlds: worldBudget,
          maxScrapeRequests: 0,
          maxGpuMinutes: executeVla ? gpuBudget : 0,
          maxEvaluationEpisodes: episodeBudget,
          maxRetries: retryBudget,
          maxIterations: iterationBudget,
          maxConsecutiveFailures: failureBudget,
        },
      });
      setSelectedRunId(response.result.run.id);
      toast.push("ok", "Curriculum run queued", `${response.result.run.id} · command ${response.commandId}`);
      runs.refetch();
    } catch (reason) {
      toast.push("err", "Run was not started", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const cancelRun = async () => {
    if (!selectedRun || TERMINAL.has(selectedRun.lifecycleState)) return;
    setBusy("cancel");
    try {
      const response = await api.post<{ run: AutonomousRun }>(`/autonomous-runs/${selectedRun.id}/cancel`, {});
      toast.push("info", "Kill switch requested", `${response.run.id} will stop at the next activity boundary.`);
      runs.refetch();
    } catch (reason) {
      toast.push("err", "Kill switch failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const loadingInputs = (robots.loading && !robots.data) || (models.loading && !models.data) || (assets.loading && !assets.data);
  const inputError = robots.error ?? models.error ?? assets.error;
  const canStart = Boolean(robotId && assetVersionId && (!executeVla || modelId) && !activeRun && !busy);
  const history = selectedRun?.state.history ?? [];
  const consumed = selectedRun?.state.consumed;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Agent Control</h1>
          <p className="page-sub">Persisted, budget-bounded orchestration over the same typed planner, MuJoCo oracle, VLA, and failure-analysis commands used by the API.</p>
        </div>
        <Badge tone="teal" icon="shield">Authoritative execution</Badge>
      </div>

      <div className="callout" style={{ marginBottom: 10, borderColor: "rgba(94,234,212,.25)" }}>
        <Icon name="info" size={13} style={{ color: "var(--teal)" }} />
        <span><b>No fixture progress.</b> Phase, consumption, IDs, blockers, and terminal state below are read from the internal catalog. VLA execution fails closed when its exact checkpoint contract is unavailable.</span>
      </div>

      {inputError && <div className="card" style={{ marginBottom: 10 }}><ErrorState message={inputError.message} onRetry={() => { robots.refetch(); models.refetch(); assets.refetch(); }} /></div>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(360px, .85fr) minmax(0, 1.15fr)", gap: 10, alignItems: "start" }}>
        <Card title="Start a bounded run" right={<Badge tone={activeRun ? "amber" : "green"}>{activeRun ? "Robot busy" : "Ready for command"}</Badge>}>
          {loadingInputs ? <Skeleton rows={6} /> : (
            <div className="col" style={{ gap: 9 }}>
              <label className="field"><span>Active embodiment</span><select className="select" value={robotId} onChange={(event) => setRobotId(event.target.value)}>{availableRobots.length === 0 ? <option value="">No active AVAILABLE robot</option> : availableRobots.map((robot) => <option key={robot.id} value={robot.id}>{robot.displayName} · {robot.active ? "active" : "available"}</option>)}</select></label>
              <label className="field"><span>Oracle-validated asset</span><select className="select" value={assetVersionId} onChange={(event) => setAssetVersionId(event.target.value)}>{validatedAssets.length === 0 ? <option value="">No ORACLE_VALIDATED asset</option> : validatedAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.displayName} · {asset.category}</option>)}</select></label>
              <label className="field"><span>Autonomy policy</span><select className="select" value={autonomyMode} onChange={(event) => setAutonomyMode(event.target.value)}><option value="EXECUTE_WITH_APPROVAL">Execute with approval</option><option value="AUTONOMOUS_WITH_BUDGETS">Autonomous with budgets</option></select></label>
              <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={executeVla} onChange={(event) => setExecuteVla(event.target.checked)} /><span className="small">Continue past the deterministic gate into the configured VLA policy</span></label>
              {executeVla && <label className="field"><span>VLA policy registration</span><select className="select" value={modelId} onChange={(event) => setModelId(event.target.value)}>{vlaModels.length === 0 ? <option value="">No registered VLA policy</option> : vlaModels.map((model) => <option key={model.id} value={model.id}>{model.displayName} · {model.lifecycleState}/{model.healthStatus}</option>)}</select></label>}
              <label className="field"><span>Instruction</span><textarea className="input" rows={2} value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(90px, 1fr))", gap: 8 }}>
                <label className="field"><span>Seed</span><input className="input mono" type="number" min={0} value={seed} onChange={(event) => setSeed(Math.max(0, Number(event.target.value) || 0))} /></label>
                <label className="field"><span>Worlds</span><input className="input mono" type="number" min={1} max={100} value={worldBudget} onChange={(event) => setWorldBudget(Math.max(1, Number(event.target.value) || 1))} /></label>
                <label className="field"><span>Episodes</span><input className="input mono" type="number" min={executeVla ? 2 : 1} value={episodeBudget} onChange={(event) => setEpisodeBudget(Math.max(executeVla ? 2 : 1, Number(event.target.value) || 1))} /></label>
                <label className="field"><span>Iterations</span><input className="input mono" type="number" min={1} value={iterationBudget} onChange={(event) => setIterationBudget(Math.max(1, Number(event.target.value) || 1))} /></label>
                <label className="field"><span>Retries</span><input className="input mono" type="number" min={0} max={20} value={retryBudget} onChange={(event) => setRetryBudget(Math.max(0, Number(event.target.value) || 0))} /></label>
                <label className="field"><span>Failure stop</span><input className="input mono" type="number" min={1} value={failureBudget} onChange={(event) => setFailureBudget(Math.max(1, Number(event.target.value) || 1))} /></label>
                {executeVla && <><label className="field"><span>GPU minutes</span><input className="input mono" type="number" min={0.1} step={0.1} value={gpuBudget} onChange={(event) => setGpuBudget(Math.max(0.1, Number(event.target.value) || 0.1))} /></label><label className="field"><span>Policy steps</span><input className="input mono" type="number" min={1} max={1000} value={maxPolicySteps} onChange={(event) => setMaxPolicySteps(Math.max(1, Number(event.target.value) || 1))} /></label></>}
              </div>
              <div className="micro t3">Scrape budget is fixed at 0 in this controller revision; evidence acquisition is not silently invoked.</div>
              <button className="btn btn-primary" disabled={!canStart} onClick={startRun}><Icon name="play" size={12} /> {busy === "start" ? "Queuing durable run…" : "Start persisted curriculum run"}</button>
              {activeRun && <div className="micro t-amber">Run {activeRun.id} already owns the selected robot until it reaches a terminal state.</div>}
            </div>
          )}
        </Card>

        <Card title="Run state" right={selectedRun ? <StatusBadge status={statusForRun(selectedRun.lifecycleState)} /> : undefined}>
          {runs.loading && !runs.data ? <Skeleton rows={6} /> : runs.error ? <ErrorState message={runs.error.message} onRetry={runs.refetch} /> : !selectedRun ? <EmptyState icon="agent">No autonomous curriculum runs have been persisted.</EmptyState> : (
            <div className="col" style={{ gap: 10 }}>
              <select className="select mono" value={selectedRun.id} onChange={(event) => setSelectedRunId(event.target.value)}>{(runs.data?.runs ?? []).map((run) => <option key={run.id} value={run.id}>{run.id} · {run.lifecycleState} · {new Date(run.createdAt).toLocaleString()}</option>)}</select>
              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}><StatusBadge status={statusForRun(selectedRun.lifecycleState)} /><Badge tone="blue">phase {selectedRun.state.phase ?? "terminal"}</Badge><Badge tone="grey">iteration {selectedRun.state.iteration ?? 0}</Badge>{selectedRun.cancellationRequested && <Badge tone="amber">kill requested</Badge>}</div>
              <div className="st-grid">
                <div className="kv-row"><span className="kv-k">Run / command</span><span className="kv-v mono">{selectedRun.id} · {selectedRun.commandId}</span></div>
                <div className="kv-row"><span className="kv-k">Robot / model</span><span className="kv-v mono">{selectedRun.robotId} · {selectedRun.modelId ?? "oracle only"}</span></div>
                <div className="kv-row"><span className="kv-k">World consumption</span><span className="kv-v mono">{valueOrZero(consumed?.worlds)} / {selectedRun.budgets.maxWorlds}</span></div>
                <div className="kv-row"><span className="kv-k">Episode consumption</span><span className="kv-v mono">{valueOrZero(consumed?.evaluationEpisodes)} / {selectedRun.budgets.maxEvaluationEpisodes}</span></div>
                <div className="kv-row"><span className="kv-k">GPU consumption</span><span className="kv-v mono">{valueOrZero(consumed?.gpuMinutes, 3)} / {selectedRun.budgets.maxGpuMinutes.toFixed(1)} min</span></div>
                <div className="kv-row"><span className="kv-k">Heartbeat</span><span className="kv-v mono">{selectedRun.heartbeatAt ? new Date(selectedRun.heartbeatAt).toLocaleString() : "not started"}</span></div>
                <div className="kv-row"><span className="kv-k">Terminal reason</span><span className="kv-v mono">{selectedRun.stopReason ?? "—"}</span></div>
              </div>
              {(selectedRun.state.blockers?.length || selectedRun.error) && <div className="callout" style={{ borderColor: "rgba(248,113,113,.3)" }}><Icon name="warning" size={13} style={{ color: "var(--red)" }} /><div className="col" style={{ gap: 4 }}><b>Fail-closed blockers</b>{(selectedRun.state.blockers ?? [selectedRun.error]).filter(Boolean).map((blocker) => <span className="micro mono" key={blocker}>{blocker}</span>)}</div></div>}
              {!TERMINAL.has(selectedRun.lifecycleState) && <button className="btn btn-secondary" disabled={busy !== null || selectedRun.cancellationRequested} onClick={cancelRun}><Icon name="stop" size={12} /> {selectedRun.cancellationRequested ? "Kill switch requested" : busy === "cancel" ? "Requesting stop…" : "Request cooperative kill switch"}</button>}
            </div>
          )}
        </Card>
      </div>

      <Card title="Persisted activity evidence" flush style={{ marginTop: 10 }} right={<Badge tone="grey">{history.length} phase records</Badge>}>
        {!selectedRun || history.length === 0 ? <EmptyState icon="workflow">No completed activity boundary has been recorded for this run.</EmptyState> : <div className="table-scroll"><table className="table"><thead><tr><th>Iteration</th><th>Phase</th><th>Outcome</th><th>Durable IDs</th><th>Evidence</th><th>Recorded</th></tr></thead><tbody>{history.map((entry, index) => <tr key={`${entry.phase}-${entry.iteration ?? 0}-${entry.at ?? index}`}><td className="mono">{entry.iteration ?? 0}</td><td><Badge tone={entry.phase === "ORACLE" ? "teal" : entry.phase === "VLA" ? "purple" : "blue"}>{entry.phase}</Badge></td><td><StatusBadge status={entry.outcome?.includes("EXHAUSTED") || entry.error ? "failed" : entry.success === false ? "failed" : entry.success === true ? "passed" : "ready"} /></td><td><div className="col micro mono t3"><span>{entry.planId ?? ""}</span><span>{entry.scenarioId ?? ""}</span><span>{entry.evaluationId ?? ""}</span></div></td><td className="small t2">{entry.failureCode ?? entry.outcome ?? (entry.reusedValidatedScenario ? "reused validated scenario" : entry.success === true ? "predicate passed" : "state persisted")}{entry.error && <div className="micro t-red">{entry.error}</div>}</td><td className="micro mono t3">{entry.at ? new Date(entry.at).toLocaleString() : "—"}</td></tr>)}</tbody></table></div>}
      </Card>
    </div>
  );
}
