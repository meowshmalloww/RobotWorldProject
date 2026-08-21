import { useEffect, useMemo, useState } from "react";
import { Card } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, apiUrl, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

interface EvaluationResult {
  runId: string;
  robotId: string;
  worldTemplateId: string;
  worldRuntimeSha256: string;
  policy: string;
  seed: number;
  success: boolean;
  failureCode: string | null;
  failureDetail: string | null;
  durationSeconds: number;
  physicsHz: number;
  controlHz: number;
  phases: { phase: string; reached?: boolean; ticks?: number; finalErrorM?: number; widthM?: number }[];
  trajectory: { phase?: string; step?: number; timeSeconds: number; contactCount: number; gripperWidthM: number; objectPositionM: number[]; finite: boolean; normalizedAction?: number[] }[];
  contactSummary: { sampledPairs: Record<string, number>; samples: number };
  predicate: {
    assetVersionId?: string;
    modelRegistrationId?: string;
    contained: boolean;
    onSupportSurface: boolean;
    settled: boolean;
    released?: boolean;
    targetErrorM: number;
    finalObjectPositionM: number[];
    finalSpeedMps?: number;
    finalLinearSpeedMps?: number;
    policySteps?: number;
  };
  frameHashes: Record<string, Record<string, string>>;
}

interface Evaluation {
  id: string;
  status: string;
  robotId: string;
  worldTemplateId: string;
  policy: string;
  seed: number;
  success: boolean | null;
  failureCode: string | null;
  failureDetail: string | null;
  result: EvaluationResult;
  createdAt: string;
}

interface RobotRegistration { id: string; displayName: string; lifecycleState: string; active: boolean }
interface ModelRegistration { id: string; displayName: string; roles: string[]; lifecycleState: string; healthStatus: string }
interface CommandResponse { commandId: string; result: { evaluation: Evaluation } }

function errorText(value: unknown): string {
  return value instanceof ApiError ? value.message : String(value);
}

function asArray<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object") {
    const candidate = (value as Record<string, unknown>)[key];
    if (Array.isArray(candidate)) return candidate as T[];
  }
  return [];
}

function evaluationBadge(evaluation: Evaluation | null): string {
  if (!evaluation) return "blocked";
  if (["QUEUED", "STARTING", "RUNNING"].includes(evaluation.status)) return "in_progress";
  return evaluation.success ? "passed" : "failed";
}

function evaluationEvidence(evaluation: Evaluation | null): string {
  if (!evaluation?.result) return "Not run; no result is inferred.";
  const steps = evaluation.result.trajectory.length;
  return evaluation.success
    ? `${steps} recorded controller steps · predicates passed`
    : `${steps} recorded controller steps · ${evaluation.failureCode ?? "predicate failure"}`;
}

export default function Simulation() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<unknown>("/evaluations");
  const { data: robots } = useApi<unknown>("/robots");
  const { data: models } = useApi<unknown>("/models");
  const evaluations = useMemo(() => asArray<Evaluation>(data, "evaluations"), [data]);
  const registrations = useMemo(() => asArray<RobotRegistration>(robots, "registrations"), [robots]);
  const modelRegistrations = useMemo(() => asArray<ModelRegistration>(models, "models"), [models]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [phase, setPhase] = useState("settle");
  const [seed, setSeed] = useState(0);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!selectedId && evaluations.length) setSelectedId(evaluations[0].id);
  }, [evaluations, selectedId]);
  const selected = evaluations.find((evaluation) => evaluation.id === selectedId) ?? evaluations[0] ?? null;
  const activeRobot = registrations.find((robot) => robot.active && robot.lifecycleState === "AVAILABLE") ?? null;
  const vla = modelRegistrations.find((model) => model.roles.includes("vla_policy")) ?? null;
  const phases = useMemo(() => selected?.result?.frameHashes ? Object.keys(selected.result.frameHashes) : [], [selected]);
  const selectedIsVla = selected?.policy.startsWith("vla-jepa:") ?? false;
  const selectedAssetVersionId = selected?.result?.predicate?.assetVersionId;
  const counterpart = selectedAssetVersionId
    ? evaluations.find((evaluation) => (
      evaluation.id !== selected?.id
      && evaluation.result?.predicate?.assetVersionId === selectedAssetVersionId
      && evaluation.policy.startsWith("vla-jepa:") !== selectedIsVla
    )) ?? null
    : null;
  const oracleEvaluation = selectedIsVla ? counterpart : selected;
  const vlaEvaluation = selectedIsVla ? selected : counterpart;
  const evaluatedVlaModel = modelRegistrations.find(
    (model) => model.id === vlaEvaluation?.result?.predicate?.modelRegistrationId,
  ) ?? vla;
  const selectedFinalSpeed = selected?.result?.predicate?.finalLinearSpeedMps
    ?? selected?.result?.predicate?.finalSpeedMps;

  useEffect(() => {
    if (phases.length && !phases.includes(phase)) setPhase(phases.at(-1) ?? phases[0]);
  }, [phase, phases]);

  const runOracle = async () => {
    if (!activeRobot) return;
    setRunning(true);
    try {
      const response = await api.post<CommandResponse>("/evaluations/oracle/pick-place", { robotId: activeRobot.id, seed });
      setSelectedId(response.result.evaluation.id);
      setPhase("settle");
      toast.push(response.result.evaluation.success ? "ok" : "err", response.result.evaluation.success ? "Oracle task succeeded" : "Oracle task failed", `${response.result.evaluation.id} · command ${response.commandId}`);
      refetch();
    } catch (reason) {
      toast.push("err", "Evaluation command failed", errorText(reason));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><div className="page-eyebrow">Physics · Evaluation</div><h1 className="page-title">Simulation &amp; Evaluation</h1><p className="page-sub">Recorded authoritative MuJoCo state, contacts, observations, actions, and predicates. The frontend never synthesizes motion or success.</p></div>
        <div className="head-actions"><div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 7 }}><label>Seed</label><input className="input mono" type="number" min={0} value={seed} onChange={(event) => setSeed(Math.max(0, Number(event.target.value) || 0))} style={{ width: 90 }} /></div><button className="btn btn-primary" onClick={runOracle} disabled={!activeRobot || running}><Icon name={running ? "refresh" : "play"} size={13} /> {running ? "Running physics…" : "Run pick/place oracle"}</button></div>
      </div>

      <div className="callout" style={{ margin: "0 0 10px", borderColor: "rgba(94,234,212,.25)" }}><Icon name="check" size={13} style={{ color: "var(--teal)" }} /><span><b>Mode: authoritative physics / recorded result.</b> MuJoCo state and the evaluator's fixed controller rate are persisted by run ID. This page contains no fixture episodes.</span></div>

      {!activeRobot && <div className="callout callout-warn" style={{ margin: "0 0 10px" }}><Icon name="warning" size={13} /><span>Register and select a physics-validated Franka on the Robots page before running the oracle.</span></div>}
      {error && <Card style={{ marginBottom: 10 }}><ErrorState message={error.message} onRetry={refetch} /></Card>}

      <div style={{ display: "grid", gridTemplateColumns: "310px minmax(0, 1fr)", gap: 10, alignItems: "start" }}>
        <Card title="Evaluation runs" flush right={<span className="micro t3">{evaluations.length}</span>}>
          {loading && !data ? <Skeleton rows={6} /> : !evaluations.length ? <EmptyState icon="play">No recorded oracle or VLA evaluations.</EmptyState> : <div>{evaluations.map((evaluation) => <button key={evaluation.id} onClick={() => setSelectedId(evaluation.id)} style={{ width: "100%", textAlign: "left", padding: "10px 12px", borderBottom: "1px solid var(--border)", background: selected?.id === evaluation.id ? "var(--accent-soft)" : "transparent" }}><div className="row" style={{ gap: 7 }}><StatusBadge status={evaluation.status === "SUCCEEDED" ? "passed" : evaluation.status === "FAILED" ? "failed" : "in_progress"} /><span className="mono small">{evaluation.id}</span></div><div className="micro t3" style={{ marginTop: 4 }}>{evaluation.policy} · seed {evaluation.seed}</div><div className="micro t3">{new Date(evaluation.createdAt).toLocaleString()}</div></button>)}</div>}
        </Card>

        {selected?.result ? <div className="col" style={{ gap: 10 }}>
          <Card title={`Run ${selected.id}`} right={<div className="row" style={{ gap: 6 }}><Badge tone="blue">Recorded</Badge><StatusBadge status={selected.success ? "passed" : "failed"} /></div>}>
            <div className="st-grid">
              <div className="kv-row"><span className="kv-k">Robot revision</span><span className="kv-v mono">{selected.robotId}</span></div>
              <div className="kv-row"><span className="kv-k">World template</span><span className="kv-v mono">{selected.worldTemplateId}</span></div>
              <div className="kv-row"><span className="kv-k">Runtime hash</span><span className="kv-v mono">{selected.result.worldRuntimeSha256.slice(0, 18)}…</span></div>
              <div className="kv-row"><span className="kv-k">Timing</span><span className="kv-v mono">{selected.result.physicsHz} Hz physics · {selected.result.controlHz} Hz control</span></div>
              <div className="kv-row"><span className="kv-k">Measured duration</span><span className="kv-v mono">{selected.result.durationSeconds.toFixed(3)} s wall time</span></div>
              <div className="kv-row"><span className="kv-k">Seed</span><span className="kv-v mono">{selected.seed}</span></div>
            </div>
            {selected.failureDetail && <div className="callout callout-warn" style={{ marginTop: 9 }}><Icon name="warning" size={12} /><span>{selected.failureCode}: {selected.failureDetail}</span></div>}
          </Card>

          <Card title="Recorded camera observations" right={<div className="segmented">{phases.map((value) => <button className={phase === value ? "on" : ""} onClick={() => setPhase(value)} key={value}>{value.replaceAll("_", " ")}</button>)}</div>} flush>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, background: "var(--border)" }}>{(["front", "wrist"] as const).map((camera) => <div key={camera} style={{ background: "var(--bg-panel)" }}><div className="micro t2" style={{ padding: "7px 10px", textTransform: "uppercase" }}>{camera} RGB · recorded</div><img src={apiUrl(`/evaluations/${selected.id}/frames/${phase}/${camera}.png`)} alt={`${phase} ${camera} recorded observation`} style={{ display: "block", width: "100%", aspectRatio: "1 / 1", maxHeight: 390, objectFit: "contain", background: "#000" }} /></div>)}</div>
          </Card>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <Card title="Task predicates" right={<StatusBadge status={selected.result.success ? "passed" : "failed"} />}><div className="col" style={{ gap: 7 }}>{[["Contained", selected.result.predicate.contained], ["On support surface", selected.result.predicate.onSupportSurface], ["Settled", selected.result.predicate.settled], ...(typeof selected.result.predicate.released === "boolean" ? [["Released", selected.result.predicate.released] as [string, boolean]] : [])].map(([name, passed]) => <div className="row" key={String(name)} style={{ gap: 7 }}><Icon name={passed ? "check" : "x"} size={12} style={{ color: passed ? "var(--green)" : "var(--red)" }} /><span className="small">{String(name)}</span></div>)}<div className="kv-row"><span className="kv-k">Target error</span><span className="kv-v mono">{(selected.result.predicate.targetErrorM * 1000).toFixed(2)} mm</span></div><div className="kv-row"><span className="kv-k">Final speed</span><span className="kv-v mono">{selectedFinalSpeed === undefined ? "—" : `${selectedFinalSpeed.toExponential(2)} m/s`}</span></div></div></Card>
            <Card title="Contact evidence" right={<span className="micro t3">{selected.result.contactSummary.samples.toLocaleString()} sampled contacts</span>}><div className="col" style={{ gap: 7 }}>{Object.entries(selected.result.contactSummary.sampledPairs).map(([pair, count]) => <div className="kv-row" key={pair}><span className="kv-k mono">{pair.replace("|", " ↔ ")}</span><span className="kv-v mono">{count.toLocaleString()}</span></div>)}</div></Card>
          </div>

          <Card title="Oracle vs learned policy" flush><div className="table-scroll"><table className="table"><thead><tr><th>Evaluator</th><th>Connection</th><th>Result</th><th>Evidence</th></tr></thead><tbody><tr><td>Deterministic IK oracle</td><td>MuJoCo differential IK actuator controls</td><td><StatusBadge status={evaluationBadge(oracleEvaluation)} /></td><td className="small t2">{evaluationEvidence(oracleEvaluation)}</td></tr><tr><td>VLA-JEPA</td><td>{evaluatedVlaModel ? `${evaluatedVlaModel.displayName} · ${evaluatedVlaModel.lifecycleState}/${evaluatedVlaModel.healthStatus}` : "not registered"}</td><td><StatusBadge status={evaluationBadge(vlaEvaluation)} /></td><td className="small t2">{evaluationEvidence(vlaEvaluation)}</td></tr></tbody></table></div></Card>
        </div> : <Card><EmptyState icon="play">Select a recorded evaluation.</EmptyState></Card>}
      </div>
    </div>
  );
}
