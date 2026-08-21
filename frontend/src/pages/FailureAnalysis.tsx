import { useEffect, useMemo, useState } from "react";
import { Card } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

interface Evaluation {
  id: string;
  status: string;
  robotId: string;
  policy: string;
  success: boolean | null;
  failureCode: string | null;
  createdAt: string;
}

interface FailureEvent {
  id: string;
  evaluationId: string;
  code: string;
  subsystem: string;
  certainty: string;
  classifierRevision: string;
  evidence: {
    policy?: string;
    trajectorySteps?: number;
    nonFiniteSteps?: number;
    oracleCounterpartEvaluationId?: string | null;
    oracleCounterpartPassed?: boolean;
    failureDetail?: string | null;
  };
  recommendedAction: {
    action: string;
    reason: string;
    varyDimensions: string[];
    oracleRequired: boolean;
  };
  eventSha256: string;
  createdAt: string;
}

interface CoverageDimension {
  configuredBins: string[];
  counts: Record<string, number>;
  unknownCount: number;
  coveredBins: number;
  coverageFraction: number;
  underrepresentedBins: string[];
}

interface CoverageState {
  taxonomyRevision: string;
  sampleCount: number;
  uniqueScenarioCount: number;
  successCount: number;
  failureCounts: Record<string, number>;
  dimensions: Record<string, CoverageDimension>;
}

interface RobotList {
  registrations: { id: string; displayName: string; lifecycleState: string; active: boolean }[];
}

interface ModelList {
  models: { id: string; displayName: string; roles: string[]; lifecycleState: string }[];
}

interface CurriculumPlan {
  id: string;
  status: string;
  robotId: string;
  modelId: string | null;
  sourceEvaluationId: string | null;
  scenarioSpecId: string | null;
  analysis: {
    sampleCount: number;
    successCount: number;
    successRate: number | null;
    wilson95: number[] | null;
    topFailureCode: string | null;
    validReusableAssetIds: string[];
  };
  decision: {
    action: string;
    reason: string;
    assetVersionId?: string;
    nextGate?: string;
    scenarioReused: boolean;
  };
  createdAt: string;
}

interface ScenarioSpec {
  id: string;
  lifecycleState: string;
  assetVersionId: string | null;
  scenarioFingerprint: string;
  oracleRequired: boolean;
  specification: {
    variationDimensions?: string[];
    deferredVariationDimensions?: string[];
    targetFailureCode?: string | null;
    placementConstraints?: Record<string, unknown>;
  };
}

interface PlanEnvelope {
  commandId: string;
  result: { plan: CurriculumPlan; scenario: ScenarioSpec | null; coverage: CoverageState };
}

interface ScenarioExecution {
  id: string;
  scenarioId: string;
  stage: string;
  status: string;
  evaluationId: string | null;
  error: string | null;
  finishedAt: string | null;
}

interface ScenarioExecutionEnvelope {
  commandId: string;
  result: {
    scenario: ScenarioSpec;
    execution: ScenarioExecution;
    evaluation: Evaluation;
  };
}

function errorText(value: unknown): string {
  return value instanceof ApiError ? value.message : String(value);
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

export default function FailureAnalysis() {
  const toast = useToast();
  const evaluations = useApi<{ evaluations: Evaluation[] }>("/evaluations");
  const failures = useApi<{ failureEvents: FailureEvent[] }>("/failure-events");
  const plans = useApi<{ plans: CurriculumPlan[] }>("/curriculum/plans");
  const scenarios = useApi<{ scenarios: ScenarioSpec[] }>("/scenario-specs");
  const scenarioExecutions = useApi<{ executions: ScenarioExecution[] }>("/scenario-executions");
  const robots = useApi<RobotList>("/robots");
  const models = useApi<ModelList>("/models");
  const [evaluationId, setEvaluationId] = useState("");
  const [robotId, setRobotId] = useState("");
  const [modelId, setModelId] = useState("");
  const [targetRate, setTargetRate] = useState(0.8);
  const [minimumAttempts, setMinimumAttempts] = useState(5);
  const [episodeBudget, setEpisodeBudget] = useState(100);
  const [scenarioBudget, setScenarioBudget] = useState(1);
  const [busy, setBusy] = useState<"analyze" | "plan" | "oracle" | null>(null);
  const [latestScenario, setLatestScenario] = useState<ScenarioSpec | null>(null);

  const terminalEvaluations = useMemo(
    () => (evaluations.data?.evaluations ?? []).filter((item) => ["SUCCEEDED", "FAILED", "CANCELLED", "CRASHED"].includes(item.status)),
    [evaluations.data],
  );
  const availableRobots = useMemo(
    () => (robots.data?.registrations ?? []).filter((item) => item.lifecycleState === "AVAILABLE"),
    [robots.data],
  );
  const vlaModels = useMemo(
    () => (models.data?.models ?? []).filter((item) => item.roles.includes("vla_policy")),
    [models.data],
  );
  useEffect(() => {
    if (!evaluationId && terminalEvaluations.length) setEvaluationId(terminalEvaluations[0].id);
  }, [evaluationId, terminalEvaluations]);
  useEffect(() => {
    if (!robotId && availableRobots.length) setRobotId((availableRobots.find((item) => item.active) ?? availableRobots[0]).id);
  }, [availableRobots, robotId]);

  const coveragePath = robotId
    ? `/coverage?robotId=${encodeURIComponent(robotId)}${modelId ? `&modelId=${encodeURIComponent(modelId)}` : ""}&taskFamily=pick_place`
    : null;
  const coverage = useApi<CoverageState>(coveragePath);

  const analyze = async () => {
    if (!evaluationId) return;
    setBusy("analyze");
    try {
      const response = await api.post<{ commandId: string; result: { classification: { outcome: string; failureEvent: FailureEvent | null } } }>(
        `/evaluations/${evaluationId}/analyze`,
        {},
      );
      const classification = response.result.classification;
      toast.push(
        classification.outcome === "SUCCESS" ? "ok" : "info",
        classification.outcome === "SUCCESS" ? "Successful evaluation indexed" : `Failure classified · ${classification.failureEvent?.code}`,
        `${evaluationId} · command ${response.commandId}`,
      );
      await Promise.all([failures.refetch(), coverage.refetch()]);
    } catch (reason) {
      toast.push("err", "Analysis failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const planNext = async () => {
    if (!robotId) return;
    setBusy("plan");
    try {
      const response = await api.post<PlanEnvelope>("/curriculum/plan-next", {
        robotId,
        modelId: modelId || null,
        taskFamily: "pick_place",
        targetSuccessRate: targetRate,
        minimumAttempts,
        maxEvaluationEpisodes: episodeBudget,
        maxNewScenarios: scenarioBudget,
        lookbackLimit: Math.min(500, Math.max(episodeBudget, minimumAttempts)),
        seed: 2301,
      });
      setLatestScenario(response.result.scenario);
      toast.push(
        response.result.plan.status === "PLANNED" ? "ok" : "info",
        `Curriculum decision · ${response.result.plan.status}`,
        `${response.result.plan.decision.action} · command ${response.commandId}`,
      );
      await Promise.all([plans.refetch(), failures.refetch(), coverage.refetch()]);
    } catch (reason) {
      toast.push("err", "Planning failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const latestPlan = plans.data?.plans[0] ?? null;
  const effectiveScenario = latestScenario
    ?? scenarios.data?.scenarios.find((item) => item.id === latestPlan?.scenarioSpecId)
    ?? null;
  const latestExecution = effectiveScenario
    ? scenarioExecutions.data?.executions.find((item) => item.scenarioId === effectiveScenario.id) ?? null
    : null;
  const supportedOracleVariations = new Set(["baseline_policy_evaluation", "object_pose", "orientation", "support_region"]);
  const scenarioVariations = effectiveScenario?.specification.variationDimensions ?? [];
  const oracleExecutable = effectiveScenario?.lifecycleState === "PLANNED"
    && scenarioVariations.length > 0
    && scenarioVariations.every((item) => supportedOracleVariations.has(item))
    && (!scenarioVariations.includes("baseline_policy_evaluation") || scenarioVariations.length === 1);
  const selectedEvaluation = terminalEvaluations.find((item) => item.id === evaluationId) ?? null;

  const executeOracle = async () => {
    if (!effectiveScenario || !oracleExecutable) return;
    setBusy("oracle");
    try {
      const response = await api.post<ScenarioExecutionEnvelope>(`/scenario-specs/${effectiveScenario.id}/oracle`);
      setLatestScenario(response.result.scenario);
      toast.push(
        response.result.evaluation.success ? "ok" : "err",
        response.result.evaluation.success ? "Deterministic oracle passed" : "Deterministic oracle failed",
        `${response.result.evaluation.id} - command ${response.commandId}`,
      );
      await Promise.all([
        scenarios.refetch(),
        scenarioExecutions.refetch(),
        evaluations.refetch(),
        failures.refetch(),
        coverage.refetch(),
        plans.refetch(),
      ]);
    } catch (reason) {
      toast.push("err", "Scenario execution failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Diagnosis · Curriculum</div>
          <h1 className="page-title">Failure Analysis &amp; Curriculum</h1>
          <p className="page-sub">Structured evidence first, then a deterministic budgeted next-scenario decision and an authoritative oracle gate. This page does not launch training or an autonomous loop.</p>
        </div>
        <Badge tone="blue">Plan + oracle gate</Badge>
      </div>

      <div className="callout" style={{ marginBottom: 10, borderColor: "rgba(94,234,212,.25)" }}>
        <Icon name="shield" size={13} style={{ color: "var(--teal)" }} />
        <span><b>Authoritative inputs only.</b> Classifications come from persisted MuJoCo state, contacts, policy actions, task predicates, and terminal run status. Scenario proposals require the deterministic oracle before VLA use.</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 10, alignItems: "start" }}>
        <Card title="Classify one evaluation" right={<StatusBadge status={selectedEvaluation?.success ? "passed" : selectedEvaluation ? "failed" : "pending"} />}>
          {evaluations.loading && !evaluations.data ? <Skeleton rows={3} /> : terminalEvaluations.length === 0 ? <EmptyState icon="warning">No terminal authoritative evaluations.</EmptyState> : (
            <div className="col" style={{ gap: 9 }}>
              <select className="select mono" value={evaluationId} onChange={(event) => setEvaluationId(event.target.value)}>
                {terminalEvaluations.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.policy} · {item.status}</option>)}
              </select>
              {selectedEvaluation && <div className="st-grid"><div className="kv-row"><span className="kv-k">Policy</span><span className="kv-v mono">{selectedEvaluation.policy}</span></div><div className="kv-row"><span className="kv-k">Raw terminal signal</span><span className="kv-v mono">{selectedEvaluation.failureCode ?? (selectedEvaluation.success ? "success" : "none")}</span></div></div>}
              <button className="btn btn-primary" disabled={!evaluationId || busy !== null} onClick={analyze}><Icon name="target" size={12} /> {busy === "analyze" ? "Classifying…" : "Persist failure + coverage evidence"}</button>
            </div>
          )}
        </Card>

        <Card title="Budgeted next-world planner" right={<Badge tone="grey">Persisted decisions</Badge>}>
          <div className="col" style={{ gap: 8 }}>
            <div className="row" style={{ gap: 8 }}>
              <select className="select" value={robotId} onChange={(event) => setRobotId(event.target.value)} style={{ flex: 1 }}>
                {availableRobots.length === 0 ? <option value="">No AVAILABLE robot</option> : availableRobots.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}
              </select>
              <select className="select" value={modelId} onChange={(event) => setModelId(event.target.value)} style={{ flex: 1 }}>
                <option value="">Deterministic oracle coverage</option>
                {vlaModels.map((item) => <option key={item.id} value={item.id}>{item.displayName} · {item.lifecycleState}</option>)}
              </select>
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <label className="field"><span>Target success</span><input className="input mono" type="number" min={0} max={1} step={0.05} value={targetRate} onChange={(event) => setTargetRate(Math.max(0, Math.min(1, Number(event.target.value))))} /></label>
              <label className="field"><span>Minimum attempts</span><input className="input mono" type="number" min={1} value={minimumAttempts} onChange={(event) => setMinimumAttempts(Math.max(1, Number(event.target.value) || 1))} /></label>
              <label className="field"><span>Episode budget</span><input className="input mono" type="number" min={1} value={episodeBudget} onChange={(event) => setEpisodeBudget(Math.max(1, Number(event.target.value) || 1))} /></label>
              <label className="field"><span>New scenarios</span><input className="input mono" type="number" min={0} value={scenarioBudget} onChange={(event) => setScenarioBudget(Math.max(0, Number(event.target.value) || 0))} /></label>
            </div>
            <button className="btn btn-primary" disabled={!robotId || busy !== null} onClick={planNext}><Icon name="workflow" size={12} /> {busy === "plan" ? "Planning…" : "Plan next scenario or stop"}</button>
          </div>
        </Card>
      </div>

      <Card title="Structured failure events" flush style={{ marginTop: 10 }} right={<span className="micro t3">{failures.data?.failureEvents.length ?? 0}</span>}>
        {failures.error ? <ErrorState message={failures.error.message} onRetry={failures.refetch} /> : failures.loading && !failures.data ? <Skeleton rows={4} /> : !failures.data?.failureEvents.length ? <EmptyState icon="warning">No classified failures. Successful evaluations produce coverage observations but no fake failure row.</EmptyState> : (
          <div className="table-scroll"><table className="table"><thead><tr><th>Evaluation</th><th>Classification</th><th>Evidence</th><th>Deterministic route</th><th>Integrity</th></tr></thead><tbody>{failures.data.failureEvents.map((event) => <tr key={event.id}><td><div className="col" style={{ gap: 2 }}><span className="mono">{event.evaluationId}</span><span className="micro t3">{event.evidence.policy}</span></div></td><td><div className="col" style={{ gap: 3 }}><StatusBadge status="failed" /><span className="mono">{event.code}</span><span className="micro t3">{event.subsystem} · {event.certainty}</span></div></td><td className="small t2">{event.evidence.trajectorySteps ?? 0} steps · {event.evidence.nonFiniteSteps ?? 0} non-finite{event.evidence.oracleCounterpartPassed ? <div className="t-green">oracle passed · {event.evidence.oracleCounterpartEvaluationId}</div> : null}</td><td><div className="col" style={{ gap: 3 }}><span className="mono small">{event.recommendedAction.action}</span><span className="micro t3">{event.recommendedAction.varyDimensions.join(" · ") || "repair before variation"}</span><span className="micro t2">{event.recommendedAction.reason}</span></div></td><td><div className="micro mono t3">{event.classifierRevision}</div><div className="micro mono t3" title={event.eventSha256}>{event.eventSha256.slice(0, 16)}…</div></td></tr>)}</tbody></table></div>
        )}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(320px, .8fr)", gap: 10, marginTop: 10, alignItems: "start" }}>
        <Card title="Measured coverage bins" right={<span className="micro mono t3">{coverage.data?.taxonomyRevision ?? "pick-place-coverage-v1"}</span>}>
          {!robotId ? <EmptyState icon="target">Select a robot.</EmptyState> : coverage.loading && !coverage.data ? <Skeleton rows={5} /> : coverage.error ? <ErrorState message={coverage.error.message} onRetry={coverage.refetch} /> : (
            <div className="col" style={{ gap: 9 }}>
              <div className="row" style={{ gap: 6 }}><Badge tone="blue">{coverage.data?.sampleCount ?? 0} episodes</Badge><Badge tone="grey">{coverage.data?.uniqueScenarioCount ?? 0} unique scenarios</Badge><Badge tone="green">{coverage.data?.successCount ?? 0} successes</Badge></div>
              {Object.entries(coverage.data?.dimensions ?? {}).map(([name, dimension]) => <div key={name}><div className="row between"><span className="small">{name}</span><span className="micro mono t3">{dimension.coveredBins}/{dimension.configuredBins.length} bins · {percent(dimension.coverageFraction)}</span></div><div className="row" style={{ gap: 5, marginTop: 5, flexWrap: "wrap" }}>{dimension.configuredBins.map((bin) => <Badge key={bin} tone={dimension.counts[bin] ? "green" : "grey"}>{bin}: {dimension.counts[bin] ?? 0}</Badge>)}{dimension.unknownCount > 0 && <Badge tone="amber">unknown: {dimension.unknownCount}</Badge>}</div></div>)}
            </div>
          )}
        </Card>

        <Card title="Latest persisted decision" right={latestPlan ? <StatusBadge status={latestPlan.status === "PLANNED" ? "ready" : latestPlan.status === "STOPPED" ? "passed" : "blocked"} /> : null}>
          {!latestPlan ? <EmptyState icon="workflow">No canonical curriculum decision yet.</EmptyState> : <div className="col" style={{ gap: 7 }}><div className="kv-row"><span className="kv-k">Plan</span><span className="kv-v mono">{latestPlan.id}</span></div><div className="kv-row"><span className="kv-k">Action</span><span className="kv-v mono">{latestPlan.decision.action}</span></div><div className="kv-row"><span className="kv-k">Reason</span><span className="kv-v mono">{latestPlan.decision.reason}</span></div><div className="kv-row"><span className="kv-k">Measured result</span><span className="kv-v mono">{latestPlan.analysis.successCount}/{latestPlan.analysis.sampleCount} · {percent(latestPlan.analysis.successRate)}</span></div>{latestPlan.analysis.wilson95 && <div className="kv-row"><span className="kv-k">Wilson 95%</span><span className="kv-v mono">{percent(latestPlan.analysis.wilson95[0])}–{percent(latestPlan.analysis.wilson95[1])}</span></div>}<div className="kv-row"><span className="kv-k">Top failure</span><span className="kv-v mono">{latestPlan.analysis.topFailureCode ?? "none"}</span></div>{latestPlan.scenarioSpecId && <div className="kv-row"><span className="kv-k">Scenario</span><span className="kv-v mono">{latestPlan.scenarioSpecId}</span></div>}</div>}
          {effectiveScenario && <div className="callout" style={{ marginTop: 9 }}><Icon name="target" size={12} /><span><b>{effectiveScenario.id}</b> reuses {effectiveScenario.assetVersionId}; materializes {(effectiveScenario.specification.variationDimensions ?? []).join(", ") || "configured gaps"}. State: {effectiveScenario.lifecycleState}.{effectiveScenario.specification.deferredVariationDimensions?.length ? ` Deferred: ${effectiveScenario.specification.deferredVariationDimensions.join(", ")}.` : ""}</span></div>}
          {effectiveScenario && <div className="col" style={{ gap: 6, marginTop: 9 }}>
            <button className="btn btn-primary" disabled={!oracleExecutable || busy !== null} onClick={executeOracle}><Icon name="play" size={12} /> {busy === "oracle" ? "Running real MuJoCo oracle..." : "Materialize + run deterministic oracle"}</button>
            {!oracleExecutable && effectiveScenario.lifecycleState === "PLANNED" && <span className="micro t-amber">This variation is fail-closed because the current placement compiler cannot materialize it.</span>}
            {latestExecution && <span className="micro mono t3">{latestExecution.stage} - {latestExecution.status}{latestExecution.evaluationId ? ` - ${latestExecution.evaluationId}` : ""}</span>}
          </div>}
        </Card>
      </div>
    </div>
  );
}
