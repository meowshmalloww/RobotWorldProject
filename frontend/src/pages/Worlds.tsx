import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, StatusBadge } from "../components/ui/controls";
import { Tree } from "../components/ui/Tree";
import { PanelRail, ResizeHandle, usePanelSize } from "../components/ui/Resizable";
import { useToast } from "../components/ui/Toast";
import { Modal } from "../components/ui/Modal";
import { api, ApiError, uploadBinary, websocketUrl } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import { WorldEditorCanvas, type AuthoringRobotGeometry, type EditorTool } from "../components/three/WorldEditorCanvas";
import { AuthoritativeSimulationCanvas, type RuntimeGeometry } from "../components/three/AuthoritativeSimulationCanvas";
import type { Asset, PhysicsCheck, ScenarioVariant, SceneNode } from "../data/types";

interface SceneData {
  worldId?: string;
  worldName?: string;
  sceneTree: SceneNode[];
  placedAssets?: string[];
  placements?: WorldPlacement[];
  assembly?: { file: string; available: boolean };
  variants: ScenarioVariant[];
  physicsChecks: PhysicsCheck[];
  taskSteps: { name: string; state: "done" | "active" | "pending" | "failed" }[];
  successConditions: { name: string; state: string; value: string }[];
  eventTimeline: { t: number; time: string; name: string; sub: string; state: string }[];
  robotSpawn?: { positionM: number[]; quaternionWxyz: number[]; source: string; validatedForExecution: boolean };
}

interface AuthoringRobotPreview {
  robotId: string;
  worldId: string;
  robotRuntimeSha256: string;
  poseSource: string;
  mountSource: string;
  mountValidatedForExecution: boolean;
  geometries: AuthoringRobotGeometry[];
}

interface AcceptanceScenario {
  id: "kitchen-juice" | "factory-sort";
  name: string;
  world: "kitchen" | "factory";
  description: string;
  disclosure: string;
  hierarchy: SceneNode[];
  steps: string[];
  successPredicates: string[];
}

interface AcceptanceCatalog {
  scenarios: AcceptanceScenario[];
  readiness: {
    vulkan: { available: boolean; backend?: string; device?: string; error?: string };
    policyConfigured: boolean;
    brightDataConfigured: boolean;
    sigNozConfigured: boolean;
    trainingEnabled: false;
  };
}

interface AcceptanceJob {
  id: string;
  status: "pending" | "running" | "success" | "failed" | "blocked";
  detail: {
    scenarioId: string;
    stages: { name: string; status: "passed" | "blocked" | "failed"; detail: string; at: string }[];
    error?: string;
    result?: { outcome: string; taskSuccess: boolean | null; message: string; seed: number; manifestSha256?: string; mjcfSha256?: string };
  };
}

type WorldBackend = "mujoco" | "isaac_sim";
type WorldController = "oracle" | "vla_jepa" | "agent";
type WorldTask = "auto" | "pick_place" | "drop_off_table" | "open_drawer";
type WorldViewport = "editor" | "live";

interface ModelSummary {
  id: string;
  displayName: string;
  roles: string[];
  lifecycleState: string;
  healthStatus: string;
}

interface PhysicalAssetVersion {
  id: string;
  displayName: string;
  lifecycleState: string;
  assetId: string;
  version: number;
}

interface WorldEvaluation {
  id: string;
  status: string;
  success: boolean;
  failureCode?: string | null;
  failureDetail?: string | null;
  policy: string;
  seed: number;
  worldTemplateId: string;
  result: { frameHashes?: Record<string, Record<string, string>>; predicate?: Record<string, unknown> };
}

interface AutonomousRunSummary {
  id: string;
  lifecycleState: string;
  stopReason?: string | null;
  state?: Record<string, unknown>;
}

interface WorldOperationResult {
  kind: "oracle_evaluation" | "vla_evaluation" | "autonomous_run" | "isaac_evaluation";
  commandId?: string;
  commandStatus?: string;
  evaluation?: WorldEvaluation;
  run?: AutonomousRunSummary;
  runtime?: { installed: boolean; ready: boolean; blockers: string[] };
}

interface FrankaLiveSession {
  sessionId: string;
  lifecycleState: string;
  authoritative: true;
  backend: "mujoco";
  mode: "oracle" | "manual";
  physicsHz: number;
  controlHz: number;
  streamHz: number;
  operation?: {
    executionScope?: "validation_bench" | "active_world";
    worldId?: string | null;
    instruction?: string;
    task?: WorldTask;
    authoredScene?: {
      sourcePlacement?: { assetId?: string };
      targetPlacement?: { assetId?: string } | null;
      counterPlacement?: { assetId?: string };
    };
  };
  frameCount: number;
  evaluation?: WorldEvaluation | null;
  error?: string | null;
}

interface FrankaLiveFrame {
  type: "frame";
  sequence: number;
  authoritative: true;
  simTimeSeconds: number;
  phase: string;
  jpegBase64: string;
  state: {
    jointPosition?: number[];
    gripperWidthM?: number;
    endEffectorPositionM?: number[];
    objectPositionM?: number[];
    contactCount?: number;
    finite?: boolean;
    renderGeometries?: RuntimeGeometry[];
  };
}

export default function Worlds() {
  const [params] = useSearchParams();
  const assetId = params.get("asset") ?? "";

  return (
    <div className="page unity-worlds-page" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", padding: 0, gap: 0 }}>
      {/* Top Unity Header Subbar */}
      <div className="unity-subbar" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 14px", background: "var(--bg-panel-1)", borderBottom: "1px solid var(--border)", flex: "none" }}>
        <div className="row" style={{ gap: 10, alignItems: "center" }}>
          <span className="row" style={{ gap: 6, alignItems: "center", fontWeight: 650, fontSize: 13, color: "var(--text-1)" }}>
            <Icon name="worlds" size={15} style={{ color: "var(--accent)" }} />
            <span>RobotWorld Scene Editor</span>
          </span>
        </div>
        <div className="head-actions"><Badge tone="grey">Scene Editor</Badge></div>
      </div>
      <SceneComposer assetId={assetId} />
    </div>
  );
}

/* ========================================================================== */

function SceneComposer({ assetId }: { assetId: string }) {
  const toast = useToast();
  const { data: scene, error, loading, refetch } = useApi<SceneData>("/worlds/scene");
  const [activePlacedAssetId, setActivePlacedAssetId] = useState(assetId);
  const placedAssetId = assetId || activePlacedAssetId || scene?.placedAssets?.[0] || "";
  const { data: generatedAsset } = useApi<Asset>(placedAssetId ? `/assets/${placedAssetId}` : null);
  const { data: acceptance } = useApi<AcceptanceCatalog>("/demo-scenarios");
  const [acceptanceId, setAcceptanceId] = useState<AcceptanceScenario["id"]>("kitchen-juice");
  const [acceptanceJob, setAcceptanceJob] = useState<AcceptanceJob | null>(null);
  const [startingAcceptance, setStartingAcceptance] = useState(false);
  const [selected, setSelected] = useState<string | null>("cabinet-02");
  const [selectedName, setSelectedName] = useState("Kitchen Cabinet 02");
  // This seed is the persisted authored-kitchen acceptance seed.  The result
  // always exposes it; additional seeds belong to the robustness run rather
  // than an invisible retry loop.
  const [seed, setSeed] = useState("1048577");
  const [variant, setVariant] = useState("");
  const [shadingVariant, setShadingVariant] = useState<"rgb" | "seg" | "depth">("rgb");
  const [gizmoMode, setGizmoMode] = useState<EditorTool>("camera");
  const [inspTab, setInspTab] = useState<"Components" | "Physics" | "Provenance" | "Agent" | "Robots">("Agent");
  const [shelfTab, setShelfTab] = useState<"Console" | "Checks" | "Variants" | "Diagnostics">("Console");
  const [saved, setSaved] = useState("never");
  const [saving, setSaving] = useState(false);
  const [checks, setChecks] = useState<PhysicsCheck[] | null>(null);
  const [checksRunning, setChecksRunning] = useState(false);
  const [newVariantOpen, setNewVariantOpen] = useState(false);
  const [creatingVariant, setCreatingVariant] = useState(false);
  const [treeSearch, setTreeSearch] = useState("");
  const variantNameRef = useRef<HTMLInputElement>(null);
  const variantDescRef = useRef<HTMLInputElement>(null);
  const { data: robotData, refetch: refetchRobots } = useApi<{ robots: RobotManifest[] }>("/robots");
  const { data: modelData } = useApi<{ models: ModelSummary[] }>("/models");
  const { data: physicalAssets } = useApi<{ assetVersions: PhysicalAssetVersion[] }>("/asset-versions");
  const [robotId, setRobotId] = useState("");
  const { data: authoringRobot } = useApi<AuthoringRobotPreview>(robotId ? `/worlds/scene/robot-preview?robot_id=${encodeURIComponent(robotId)}` : null);
  const [instruction, setInstruction] = useState("Pick up the apple and place it on top of the blender.");
  const command: WorldCommandResult | null = null;
  const [operation, setOperation] = useState<WorldOperationResult | null>(null);
  const [activeRun, setActiveRun] = useState<AutonomousRunSummary | null>(null);
  const [backend, setBackend] = useState<WorldBackend>("mujoco");
  const [controller, setController] = useState<WorldController>("oracle");
  const task: WorldTask = "auto";
  const [assetVersionId, setAssetVersionId] = useState("");
  const [modelId, setModelId] = useState("");
  const [viewport, setViewport] = useState<WorldViewport>("editor");
  const [liveSession, setLiveSession] = useState<FrankaLiveSession | null>(null);
  const [liveFrame, setLiveFrame] = useState<FrankaLiveFrame | null>(null);
  const [liveStatus, setLiveStatus] = useState<"idle" | "connecting" | "running" | "finished" | "failed">("idle");
  const liveSocketRef = useRef<WebSocket | null>(null);
  const [planning, setPlanning] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const [importingRobot, setImportingRobot] = useState(false);
  const [arranging, setArranging] = useState(false);
  const worldRobots = useMemo(() => {
    const ranked = [...(robotData?.robots ?? [])].sort((left, right) => Number(Boolean(right.physicsReady)) - Number(Boolean(left.physicsReady)));
    const seen = new Set<string>();
    return ranked.filter((item) => {
      const key = `${item.format}:${item.name.trim().toLowerCase()}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [robotData?.robots]);

  // panel state - resizable + collapsible
  const [leftW, setLeftW] = usePanelSize(260, 150, 560, "robotworld.worlds.leftW");
  const [rightW, setRightW] = usePanelSize(360, 240, 720, "robotworld.worlds.rightW");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [shelfH, setShelfH] = usePanelSize(210, 100, 560, "robotworld.worlds.shelfH", "row");
  const [shelfOpen, setShelfOpen] = useState(true);

  const activeAcceptance = acceptance?.scenarios.find((item) => item.id === acceptanceId);
  const generatedTree = useMemo<SceneNode[] | null>(() => {
    if (!generatedAsset) return null;
    const toNode = (part: Asset["parts"][number]): SceneNode => ({
      id: part.id,
      name: part.name,
      icon: "cube",
      tag: part.joint ?? "mesh",
      children: part.children?.map(toNode),
    });
    return [{ id: generatedAsset.id, name: generatedAsset.name, icon: "cube", tag: "OpenUSD generated asset", children: generatedAsset.parts.map(toNode) }];
  }, [generatedAsset]);
  const sceneTree = useMemo(() => scene?.sceneTree?.length ? scene.sceneTree : generatedTree ?? [], [generatedTree, scene]);
  const visibleSceneTree = useMemo(() => {
    const query = treeSearch.trim().toLowerCase();
    if (!query) return sceneTree;
    const filter = (nodes: SceneNode[]): SceneNode[] => nodes.flatMap((node) => {
      const children = node.children ? filter(node.children) : [];
      const matches = node.name.toLowerCase().includes(query) || (node.tag ?? "").toLowerCase().includes(query);
      return matches || children.length ? [{ ...node, children }] : [];
    });
    return filter(sceneTree);
  }, [sceneTree, treeSearch]);
  const assetByNodeId = useMemo(() => {
    const result = new Map<string, string>();
    const visit = (nodes: SceneNode[], inherited = "") => {
      nodes.forEach((node) => {
        const owner = node.assetId || inherited;
        if (owner) result.set(node.id, owner);
        if (node.children) visit(node.children, owner);
      });
    };
    visit(sceneTree);
    return result;
  }, [sceneTree]);
  const variantCards = useMemo(() => scene?.variants ?? [], [scene]);
  const physicsChecks = checks ?? scene?.physicsChecks ?? [];
  // Older saved scenes do not contain every newer transform field. Normalize
  // at the API boundary so stale HMR data cannot crash render or drag paths.
  const activePlacement = useMemo(() => {
    const placement = scene?.placements?.find((item) => item.assetId === placedAssetId);
    return placement ? normalizeWorldPlacement(placement) : undefined;
  }, [placedAssetId, scene?.placements]);
  const normalizedPlacements = useMemo(
    () => (scene?.placements ?? []).map(normalizeWorldPlacement),
    [scene?.placements],
  );
  const hasPlacedWorldAssets = Boolean(scene?.placedAssets?.length);

  useEffect(() => { if (!robotId && worldRobots[0]) setRobotId(worldRobots[0].id); }, [robotId, worldRobots]);
  useEffect(() => {
    const policies = modelData?.models.filter((item) => item.roles.includes("vla_policy")) ?? [];
    if (!modelId && policies.length) setModelId((policies.find((item) => item.lifecycleState === "LOADED") ?? policies[0]).id);
  }, [modelData, modelId]);

  useEffect(() => () => liveSocketRef.current?.close(), []);

  const startLiveOracle = async () => {
    setPlanning(true);
    setLiveStatus("connecting");
    setLiveFrame(null);
    liveSocketRef.current?.close();
    try {
      const session = await api.post<FrankaLiveSession>("/worlds/live-sessions", {
        robotId,
        instruction,
        backend,
        controller,
        task,
        assetVersionId: assetVersionId || null,
        seed: Number(seed),
        maxPolicySteps: 150,
        executionScope: hasPlacedWorldAssets ? "active_world" : "validation_bench",
        worldId: hasPlacedWorldAssets ? scene?.worldId : null,
      });
      setLiveSession(session);
      setViewport("live");
      let terminal = false;
      const socket = new WebSocket(websocketUrl(`/worlds/live/${session.sessionId}`));
      liveSocketRef.current = socket;
      socket.onopen = () => setLiveStatus("running");
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as Record<string, unknown>;
        if (message.type === "frame") {
          setLiveFrame(message as unknown as FrankaLiveFrame);
          setLiveStatus("running");
          return;
        }
        if (message.type === "end") {
          terminal = true;
          const evaluation = message.evaluation as WorldEvaluation;
          const nextSession = (message.session as FrankaLiveSession | undefined) ?? session;
          setLiveSession(nextSession);
          setOperation({ kind: "oracle_evaluation", evaluation });
          setLiveStatus(evaluation.success ? "finished" : "failed");
          setPlanning(false);
          toast.push(evaluation.success ? "ok" : "err", evaluation.success ? "Live task passed" : "Live task failed", evaluation.id);
          socket.close();
          return;
        }
        if (message.type === "error") {
          terminal = true;
          setLiveStatus("failed");
          setPlanning(false);
          toast.push("err", "Live simulation failed", String(message.message ?? "Unknown simulation error"));
          socket.close();
        }
      };
      socket.onerror = () => {
        setLiveStatus("failed");
        setPlanning(false);
      };
      socket.onclose = () => {
        if (!terminal) {
          setLiveStatus("failed");
          setPlanning(false);
        }
      };
    } catch (e) {
      setLiveStatus("failed");
      setPlanning(false);
      toast.push("err", "Could not start live simulation", e instanceof ApiError ? e.message : String(e));
    }
  };

  const startManualControl = async () => {
    setPlanning(true);
    setLiveStatus("connecting");
    setLiveFrame(null);
    liveSocketRef.current?.close();
    try {
      const session = await api.post<FrankaLiveSession>("/worlds/manual-sessions", {
        robotId,
        instruction,
        backend: "mujoco",
        controller: "oracle",
        task: "pick_place",
        seed: Number(seed),
        maxPolicySteps: 150,
        executionScope: "active_world",
        worldId: scene?.worldId,
      });
      setLiveSession(session);
      setViewport("live");
      const socket = new WebSocket(websocketUrl(`/worlds/live/${session.sessionId}`));
      liveSocketRef.current = socket;
      socket.onopen = () => { setLiveStatus("running"); setPlanning(false); };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as Record<string, unknown>;
        if (message.type === "frame") setLiveFrame(message as unknown as FrankaLiveFrame);
        if (message.type === "error") {
          setLiveStatus("failed");
          toast.push("err", "Manual simulation failed", String(message.message ?? "Unknown simulation error"));
        }
      };
      socket.onerror = () => { setLiveStatus("failed"); setPlanning(false); };
    } catch (e) {
      setLiveStatus("failed");
      setPlanning(false);
      toast.push("err", "Could not start manual control", e instanceof ApiError ? e.message : String(e));
    }
  };

  const manualControl = async (kind: "jog" | "open" | "close", deltaM?: number[]) => {
    if (!liveSession || liveSession.mode !== "manual") return;
    setManualBusy(true);
    try {
      if (kind === "jog") await api.post(`/worlds/manual-sessions/${liveSession.sessionId}/jog`, { deltaM });
      else await api.post(`/worlds/manual-sessions/${liveSession.sessionId}/gripper`, { command: kind });
    } catch (e) {
      toast.push("err", "Manual command rejected", e instanceof ApiError ? e.message : String(e));
    } finally { setManualBusy(false); }
  };

  const leaveLiveViewport = async () => {
    const session = liveSession;
    setViewport("editor");
    if (session?.mode === "manual") {
      liveSocketRef.current?.close();
      try { await api.del(`/worlds/manual-sessions/${session.sessionId}`); } catch { /* session may already be closed */ }
      setLiveSession(null);
    }
  };

  const planCommand = async () => {
      if (backend === "mujoco" && controller === "oracle" && ["auto", "pick_place", "drop_off_table"].includes(task)) {
        await startLiveOracle();
        return;
      }
      setPlanning(true);
      try {
        const result = await api.post<WorldOperationResult>("/worlds/operate", {
          robotId,
          instruction,
          backend,
          controller,
          task,
          assetVersionId: assetVersionId || null,
          modelId: controller === "oracle" ? null : modelId || null,
          seed: Number(seed),
          maxPolicySteps: 150,
          executionScope: hasPlacedWorldAssets ? "active_world" : "validation_bench",
          worldId: hasPlacedWorldAssets ? scene?.worldId : null,
        });
        setOperation(result);
        setActiveRun(result.run ?? null);
        setInspTab("Agent");
        setRightOpen(true);
        setShelfTab("Console");
        setShelfOpen(true);
        const evidence = result.evaluation?.id ?? result.run?.id ?? result.commandId ?? "recorded result";
        toast.push(result.evaluation?.success === false ? "err" : "ok", result.run ? "Autonomous loop started" : "World execution recorded", evidence);
      } catch (e) {
        toast.push("err", "Execution failed", e instanceof ApiError ? e.message : String(e));
      } finally { setPlanning(false); }
  };

  useEffect(() => {
    if (!activeRun || ["SUCCEEDED", "FAILED", "CANCELLED", "CRASHED", "EXHAUSTED", "STOPPED"].includes(activeRun.lifecycleState)) return;
    const timer = window.setInterval(() => {
      api.get<{ run: AutonomousRunSummary }>(`/autonomous-runs/${activeRun.id}`).then(({ run }) => {
        setActiveRun(run);
        if (["SUCCEEDED", "FAILED", "CANCELLED", "CRASHED", "EXHAUSTED", "STOPPED"].includes(run.lifecycleState)) {
          toast.push(run.lifecycleState === "SUCCEEDED" ? "ok" : "info", "Autonomous loop finished", `${run.id} · ${run.lifecycleState}${run.stopReason ? ` · ${run.stopReason}` : ""}`);
        }
      }).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [activeRun, toast]);

  const importRobot = async (file?: File) => {
    if (!file) return;
    setImportingRobot(true);
    try {
      const robot = await uploadBinary<RobotManifest>("/robots/import", file);
      setRobotId(robot.id);
      await refetchRobots();
      setInspTab("Robots");
      setRightOpen(true);
      toast.push(robot.readiness.executable ? "ok" : "info", "Robot inspected", `${robot.name} · ${robot.joints} joints · ${robot.readiness.blockers.length} readiness gates`);
    } catch (e) { toast.push("err", "Robot import failed", e instanceof ApiError ? e.message : String(e)); }
    finally { setImportingRobot(false); }
  };

  const updatePlacement = async (asset: string, patch: { translation?: number[]; rotationZDeg?: number; scaleMultiplier?: number[]; visible?: boolean; mobility?: "movable" | "fixed" }) => {
    try { await api.patch(`/worlds/placements/${asset}`, patch); await refetch(); }
    catch (e) { toast.push("err", "Placement update failed", e instanceof ApiError ? e.message : String(e)); }
  };

  const updateRobotSpawn = async (patch: { positionM: number[]; quaternionWxyz: number[] }) => {
    try {
      await api.patch("/worlds/robot-spawn", patch);
      await refetch();
      toast.push("ok", "Franka mount saved", "The next authoritative run will compile this persisted base translation.");
    } catch (e) {
      toast.push("err", "Franka mount rejected", e instanceof ApiError ? e.message : String(e));
      await refetch();
    }
  };

  const autoLayout = async () => {
    setArranging(true);
    try {
      const result = await api.post<{ placements: number; provenance: string }>("/worlds/layout", {});
      await refetch();
      toast.push("ok", "Constraint layout saved", `${result.placements} measured assets · ${result.provenance}`);
    } catch (e) { toast.push("err", "Auto-layout failed", e instanceof ApiError ? e.message : String(e)); }
    finally { setArranging(false); }
  };

  // select active variant once loaded
  useEffect(() => {
    if (scene && !variant) {
      const active = scene.variants.find((v) => v.active) ?? scene.variants[0];
      if (active) setVariant(active.id);
    }
  }, [scene, variant]);

  useEffect(() => {
    if (!generatedAsset) return;
    setSelected(generatedAsset.id);
    setSelectedName(generatedAsset.name);
  }, [generatedAsset]);

  useEffect(() => {
    if (assetId) setActivePlacedAssetId(assetId);
    else if (!activePlacedAssetId && scene?.placedAssets?.[0]) setActivePlacedAssetId(scene.placedAssets[0]);
  }, [activePlacedAssetId, assetId, scene]);

  useEffect(() => {
    const reset = () => {
      setLeftW(260);
      setRightW(340);
      setShelfH(210);
      setLeftOpen(true);
      setRightOpen(true);
      setShelfOpen(true);
    };
    window.addEventListener("robotworld:reset-layout", reset);
    return () => window.removeEventListener("robotworld:reset-layout", reset);
  }, [setLeftW, setRightW, setShelfH]);

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const key = event.key.toLowerCase();
      if (gizmoMode === "camera") return;
      if (key === "w") setGizmoMode("translate");
      if (key === "e") setGizmoMode("rotate");
      if (key === "r") setGizmoMode("scale");
      if (key === "q") setGizmoMode("camera");
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [gizmoMode]);

  useEffect(() => {
    if (!acceptanceJob || ["success", "failed", "blocked"].includes(acceptanceJob.status)) return;
    const timer = window.setInterval(() => {
      api.get<AcceptanceJob>(`/jobs/${acceptanceJob.id}`)
        .then((job) => {
          setAcceptanceJob(job);
          if (job.status === "blocked") toast.push("info", "Environment verified; VLA gateway required", job.detail.result?.message ?? "Physical rollout passed; learned policy gate blocked honestly.");
          if (job.status === "failed") toast.push("err", "Acceptance run failed", job.detail.error ?? "See the console for evidence.");
        })
        .catch(() => undefined);
    }, 750);
    return () => window.clearInterval(timer);
  }, [acceptanceJob, toast]);

  const runAcceptance = async (id: AcceptanceScenario["id"]) => {
    setAcceptanceId(id);
    setSelected(id === "kitchen-juice" ? "blender" : "parcel-set");
    setSelectedName(id === "kitchen-juice" ? "Blender Assembly" : "Randomized parcel set");
    setShelfTab("Console");
    setShelfOpen(true);
    setStartingAcceptance(true);
    try {
      const response = await api.post<{ jobId: string }>(`/demo-scenarios/${id}/runs`, { seed: Number(seed) });
      setAcceptanceJob({ id: response.jobId, status: "pending", detail: { scenarioId: id, stages: [] } });
      toast.push("ok", "Acceptance run started", `Seed ${seed} - compiling MuJoCo physics & OpenUSD scene`);
    } catch (e) {
      toast.push("err", "Could not start acceptance run", e instanceof ApiError ? e.message : String(e));
    } finally {
      setStartingAcceptance(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/worlds/scene", { sceneTree, variants: variantCards });
      setSaved("just now");
      toast.push("ok", "Scene saved", `Saved ${variantCards.length} scenario variants`);
    } catch (e) {
      toast.push("err", "Scene save failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const rerunChecks = async () => {
    setShelfTab("Checks");
    setShelfOpen(true);
    setChecksRunning(true);
    try {
      const r = await api.post<{ physicsChecks: PhysicsCheck[] }>("/worlds/checks/run");
      setChecks(r.physicsChecks);
      const warns = r.physicsChecks.filter((c) => c.status === "warn").length;
      const fails = r.physicsChecks.filter((c) => c.status === "fail").length;
      toast.push(fails ? "err" : warns ? "info" : "ok", "Placement & physics checks complete", `${r.physicsChecks.length} checks - ${fails} failed - ${warns} warnings`);
    } catch (e) {
      toast.push("err", "Checks failed to run", e instanceof ApiError ? e.message : String(e));
    } finally {
      setChecksRunning(false);
    }
  };

  const activateVariant = async (id: string) => {
    setVariant(id);
    try {
      await api.post(`/worlds/variants/${id}/activate`);
      refetch();
    } catch (e) {
      toast.push("err", "Could not activate variant", e instanceof ApiError ? e.message : String(e));
    }
  };

  const createVariant = async () => {
    const name = variantNameRef.current?.value.trim();
    if (!name) {
      toast.push("err", "Name required", "Give the variant a name, e.g. High Clutter");
      return;
    }
    setCreatingVariant(true);
    try {
      await api.post<ScenarioVariant>("/worlds/variants", { name, desc: variantDescRef.current?.value.trim() ?? "" });
      setNewVariantOpen(false);
      toast.push("ok", "Variant created", `${name} added to the validation lab`);
      refetch();
    } catch (e) {
      toast.push("err", "Could not create variant", e instanceof ApiError ? e.message : String(e));
    } finally {
      setCreatingVariant(false);
    }
  };

  return (
    <div className="world-editor col" style={{ flex: 1, minHeight: 0, gap: 0, background: "var(--bg-app)" }}>
      {/* Unity Engine Transport & Tool Dock */}
      <div className="unity-dockbar" style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 12px", background: "var(--bg-panel-1)", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
        {hasPlacedWorldAssets ? (
          <>
            <span className="row" style={{ gap: 6, alignItems: "center", minWidth: 0 }}>
              <Icon name="cube" size={13} style={{ color: "var(--accent)" }} />
              <span className="small ellipsis" style={{ fontWeight: 620 }}>{scene?.worldName ?? "OpenUSD World"}</span>
              <span className="micro t3 mono">{scene?.placedAssets?.length ?? 0} placed assets</span>
            </span>
            <select className="select" style={{ width: 112, height: 26, fontSize: 11 }} value={viewport} onChange={(event) => setViewport(event.target.value as WorldViewport)}>
              <option value="editor">3D Editor</option>
              <option value="live" disabled={!liveSession}>Live simulation</option>
            </select>
            <span className="v-divider" />
            <div className="unity-group row" style={{ gap: 2, background: "var(--bg-panel-2)", padding: "2px 4px", borderRadius: 4, border: "1px solid var(--border)" }}>
              <button className={`btn btn-sm ${gizmoMode === "translate" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("translate")} title="Move selected object (W)"><Icon name="move" size={12} /> Move</button>
              <button className={`btn btn-sm ${gizmoMode === "rotate" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("rotate")} title="Rotate selected object around world Z (E)"><Icon name="refresh" size={12} /> Rotate</button>
              <button className={`btn btn-sm ${gizmoMode === "scale" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("scale")} title="Scale selected object (R)"><Icon name="scale" size={12} /> Scale</button>
              <button className={`btn btn-sm ${gizmoMode === "camera" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("camera")} title="Free camera orbit (Q)"><Icon name="camera" size={12} /> Camera</button>
            </div>
            <span className="v-divider" />
            <span className="micro t3">Camera: WASD + Q/E · drag orbit · right drag pan</span>
            <span className="grow" />
            <button className={`btn btn-ghost btn-sm btn-icon ${leftOpen ? "active" : ""}`} title="Toggle Hierarchy" onClick={() => setLeftOpen(!leftOpen)} style={{ height: 26, width: 26 }}>
              <Icon name="panelLeft" size={12} />
            </button>
            <button className={`btn btn-ghost btn-sm btn-icon ${rightOpen ? "active" : ""}`} title="Toggle Inspector" onClick={() => setRightOpen(!rightOpen)} style={{ height: 26, width: 26 }}>
              <Icon name="panelRight" size={12} />
            </button>
            <button className={`btn btn-ghost btn-sm btn-icon ${shelfOpen ? "active" : ""}`} title="Toggle Console" onClick={() => setShelfOpen(!shelfOpen)} style={{ height: 26, width: 26 }}>
              <Icon name="panelBottom" size={12} />
            </button>
            <span className="v-divider" />
            <span className="micro t3 mono">Placement autosaved</span>
            <button className="btn btn-secondary btn-sm" disabled={arranging} onClick={autoLayout}><Icon name="spark" size={11} /> {arranging ? "Solving..." : "Auto-layout"}</button>
          </>
        ) : <>
        {/* Transform Tools: Translate / Rotate / Scale */}
        <div className="unity-group row" style={{ gap: 2, background: "var(--bg-panel-2)", padding: "2px 4px", borderRadius: 4, border: "1px solid var(--border)" }}>
          <button
            className={`btn btn-sm btn-icon ${gizmoMode === "translate" ? "btn-secondary" : "btn-ghost"}`}
            onClick={() => setGizmoMode("translate")}
            title="Translate Tool (W)"
            style={{ height: 26, width: 26 }}
          >
            <Icon name="move" size={12} />
          </button>
          <button
            className={`btn btn-sm btn-icon ${gizmoMode === "rotate" ? "btn-secondary" : "btn-ghost"}`}
            onClick={() => setGizmoMode("rotate")}
            title="Rotate Tool (E)"
            style={{ height: 26, width: 26 }}
          >
            <Icon name="refresh" size={12} />
          </button>
          <button
            className={`btn btn-sm btn-icon ${gizmoMode === "scale" ? "btn-secondary" : "btn-ghost"}`}
            onClick={() => setGizmoMode("scale")}
            title="Scale Tool (R)"
            style={{ height: 26, width: 26 }}
          >
            <Icon name="scale" size={12} />
          </button>
          <button
            className={`btn btn-sm btn-icon ${gizmoMode === "camera" ? "btn-secondary" : "btn-ghost"}`}
            onClick={() => setGizmoMode("camera")}
            title="Free Camera (Q)"
            style={{ height: 26, width: 26 }}
          >
            <Icon name="camera" size={11} />
          </button>
        </div>

        <span className="v-divider" />

        {/* Shading View Mode */}
        <div className="row" style={{ gap: 5, alignItems: "center" }}>
          <span className="micro t3">Shading:</span>
          <select
            className="select"
            style={{ width: 110, height: 26, fontSize: 11 }}
            value={shadingVariant}
            onChange={(e) => setShadingVariant(e.target.value as "rgb" | "seg" | "depth")}
          >
            <option value="rgb">Lit RGB</option>
            <option value="seg">Segmentation ID</option>
            <option value="depth">Depth (Sensor)</option>
          </select>
        </div>

        <span className="v-divider" />

        {/* Scenario & Seed */}
        <button
          className={`btn btn-sm ${acceptanceId === "kitchen-juice" ? "btn-secondary" : "btn-ghost"}`}
          disabled={startingAcceptance || acceptanceJob?.status === "running"}
          onClick={() => runAcceptance("kitchen-juice")}
          title="Compile and run kitchen juice station acceptance world"
          style={{ height: 26, fontSize: 11 }}
        >
          <Icon name="play" size={11} /> Kitchen World
        </button>
        <button
          className={`btn btn-sm ${acceptanceId === "factory-sort" ? "btn-secondary" : "btn-ghost"}`}
          disabled={startingAcceptance || acceptanceJob?.status === "running"}
          onClick={() => runAcceptance("factory-sort")}
          title="Compile and run factory parcel sorting acceptance world"
          style={{ height: 26, fontSize: 11 }}
        >
          <Icon name="play" size={11} /> Logistics World
        </button>

        <div className="row" style={{ gap: 4, alignItems: "center" }}>
          <span className="micro t3">Seed:</span>
          <input
            className="input mono"
            style={{ width: 75, height: 26, fontSize: 11 }}
            value={seed}
            onChange={(e) => setSeed(e.target.value.replace(/\D/g, ""))}
          />
          <button
            className="btn btn-ghost btn-sm btn-icon"
            title="Randomize seed"
            onClick={() => setSeed(String(Math.floor(Math.random() * 9_000_000 + 1_000_000)))}
            style={{ height: 26, width: 26 }}
          >
            <Icon name="refresh" size={11} />
          </button>
        </div>

        <span className="grow" />

        {/* Panel Toggles */}
        <button className={`btn btn-ghost btn-sm btn-icon ${leftOpen ? "active" : ""}`} title="Toggle Hierarchy" onClick={() => setLeftOpen(!leftOpen)} style={{ height: 26, width: 26 }}>
          <Icon name="panelLeft" size={12} />
        </button>
        <button className={`btn btn-ghost btn-sm btn-icon ${shelfOpen ? "active" : ""}`} title="Toggle Console / Bottom Shelf" onClick={() => setShelfOpen(!shelfOpen)} style={{ height: 26, width: 26 }}>
          <Icon name="panelBottom" size={12} />
        </button>
        <button className={`btn btn-ghost btn-sm btn-icon ${rightOpen ? "active" : ""}`} title="Toggle Inspector" onClick={() => setRightOpen(!rightOpen)} style={{ height: 26, width: 26 }}>
          <Icon name="panelRight" size={12} />
        </button>

        <span className="v-divider" />
        <span className="micro t3 mono" style={{ color: "var(--text-3)" }}>Saved: {saved}</span>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving} style={{ height: 26, fontSize: 11 }}>
          <Icon name="save" size={11} /> {saving ? "Saving..." : "Save Stage"}
        </button>
        </>}
      </div>

      {/* Main Unity Dock Layout */}
      <div className="row" style={{ flex: 1, minHeight: 0, gap: 0, alignItems: "stretch" }}>
        {/* Left: Unity Hierarchy Panel */}
        {leftOpen ? (
          <>
            <div className="card unity-panel" style={{ width: leftW, flex: "none", display: "flex", flexDirection: "column", minHeight: 0, borderRadius: 0, borderTop: 0, borderBottom: 0, borderLeft: 0 }}>
              <header className="card-head" style={{ minHeight: 32, padding: "0 10px", background: "var(--bg-panel-2)", borderBottom: "1px solid var(--border)" }}>
                <span className="row" style={{ gap: 6, alignItems: "center" }}>
                  <Icon name="grid" size={12} style={{ color: "var(--accent)" }} />
                  <span className="card-title" style={{ fontSize: 12, fontWeight: 650 }}>Hierarchy</span>
                </span>
                <span className="micro t3 mono">{sceneTree.length} nodes</span>
              </header>
              <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)" }}>
                <div className="search-box">
                  <span className="search-ico"><Icon name="search" size={11} /></span>
                  <input
                    className="input"
                    placeholder="Search Hierarchy..."
                    value={treeSearch}
                    onChange={(e) => setTreeSearch(e.target.value)}
                    style={{ height: 24, fontSize: 11 }}
                  />
                </div>
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: "4px 4px 8px" }}>
                {error ? (
                  <ErrorState message={error.message} onRetry={refetch} />
                ) : loading && !scene ? (
                  <Skeleton rows={8} height={11} />
                ) : visibleSceneTree.length > 0 ? (
                  <Tree
                    nodes={visibleSceneTree as never}
                    selected={selected}
                    onSelect={(id, name) => {
                      setSelected(id);
                      setSelectedName(name);
                      const owner = assetByNodeId.get(id);
                      if (owner) setActivePlacedAssetId(owner);
                    }}
                    onVisibilityChange={(id, visible) => {
                      const owner = assetByNodeId.get(id);
                      if (owner) void updatePlacement(owner, { visible });
                    }}
                  />
                ) : (
                  <EmptyState icon="worlds">No scene nodes loaded.</EmptyState>
                )}
              </div>
            </div>
            <ResizeHandle dir="col" onDrag={(d) => setLeftW((prev) => prev + d)} onReset={() => setLeftW(260)} />
          </>
        ) : (
          <PanelRail label="Hierarchy" side="left" onExpand={() => setLeftOpen(true)} />
        )}

        {/* Center: Full-Bleed 3D Viewport + Bottom Shelf */}
        <div className="col" style={{ flex: 1, minWidth: 0, minHeight: 0, gap: 0 }}>
          <div className="card unity-viewport-container" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", borderRadius: 0, border: 0 }}>
            {viewport === "live" && liveSession ? (
              <LiveWorldResult session={liveSession} frame={liveFrame} status={liveStatus} placements={normalizedPlacements} manualBusy={manualBusy} onManual={manualControl} onBack={() => void leaveLiveViewport()} />
            ) : hasPlacedWorldAssets ? (
              <GeneratedWorldView
                placements={normalizedPlacements}
                robotGeometries={authoringRobot?.geometries ?? []}
                robotSpawn={scene?.robotSpawn}
                selectedAssetId={selected === "robot-spawn" ? "robot-spawn" : placedAssetId}
                mode={gizmoMode}
                onSelect={(id, name) => { if (id !== "robot-spawn") setActivePlacedAssetId(id); setSelected(id); setSelectedName(name); }}
                onCommit={updatePlacement}
                onRobotCommit={updateRobotSpawn}
              />
            ) : (
              <div className="center col" style={{ flex: 1, minHeight: 0, gap: 10, padding: 28, color: "var(--text-3)", textAlign: "center" }}>
                <Icon name="cube" size={28} />
                <span className="small" style={{ color: "var(--text-2)" }}>No composed generated asset is selected.</span>
                <span className="micro">Open a real GLB from Assets to place its OpenUSD world here. RobotWorld does not show a procedural stand-in.</span>
              </div>
            )}
          </div>

          {/* Bottom Dockable Shelf */}
          {shelfOpen && (
            <>
              <ResizeHandle dir="row" onDrag={(d) => setShelfH((prev) => prev - d)} onReset={() => setShelfH(210)} />
              <div className="card unity-shelf" style={{ height: shelfH, flex: "none", display: "flex", flexDirection: "column", minHeight: 0, borderRadius: 0, borderRight: 0, borderLeft: 0, borderBottom: 0 }}>
                <header className="card-head" style={{ minHeight: 30, padding: "0 8px 0 10px", background: "var(--bg-panel-2)", borderBottom: "1px solid var(--border)" }}>
                  <span className="tabs scrollable" style={{ border: 0, gap: 4 }}>
                    {(["Console", "Diagnostics", "Checks", "Variants"] as const).map((t) => (
                      <button
                        key={t}
                        className={shelfTab === t ? "on" : ""}
                        style={{ height: 24, fontSize: 11, padding: "0 10px", borderRadius: 3 }}
                        onClick={() => setShelfTab(t)}
                      >
                        {t === "Checks" ? "Problems" : t}
                      </button>
                    ))}
                  </span>
                  <span className="head-right">
                    {shelfTab === "Checks" ? (
                      <button className="btn btn-ghost btn-sm" onClick={rerunChecks} disabled={checksRunning} style={{ height: 22, fontSize: 10 }}>
                        <Icon name="refresh" size={10} className={checksRunning ? "spin" : undefined} /> {checksRunning ? "Evaluating..." : "Run real checks"}
                      </button>
                    ) : generatedAsset ? (
                      <Badge tone="grey">runtime evidence</Badge>
                    ) : shelfTab === "Variants" ? (
                      <button className="btn btn-ghost btn-sm" onClick={() => setNewVariantOpen(true)} style={{ height: 22, fontSize: 10 }}>
                        <Icon name="plus" size={10} /> New Variant
                      </button>
                    ) : (
                      <StatusBadge status={acceptanceJob?.status ?? "idle"} />
                    )}
                  </span>
                </header>

                <div style={{ flex: 1, overflowY: "auto", minHeight: 0, background: "var(--bg-panel-1)" }}>
                  {shelfTab === "Diagnostics" ? (
                    <RuntimeDiagnosticsPanel />
                  ) : viewport === "live" && shelfTab === "Console" && liveSession ? (
                    <LiveRuntimeShelf session={liveSession} frame={liveFrame} status={liveStatus} evaluation={operation?.evaluation} />
                  ) : generatedAsset ? (
                    <GeneratedAssetShelf asset={generatedAsset} tab={shelfTab as "Console" | "Checks" | "Variants"} assembly={scene?.assembly} checks={physicsChecks} checksRunning={checksRunning} />
                  ) : shelfTab === "Console" ? (
                    <AcceptanceConsole scenario={activeAcceptance} catalog={acceptance ?? undefined} job={acceptanceJob} />
                  ) : shelfTab === "Checks" ? (
                    <div className="table-scroll">
                      {checksRunning && <div className="busy-bar" style={{ margin: "6px 10px 0" }}><i /></div>}
                      {physicsChecks.length > 0 ? (
                        <table className="table" style={{ fontSize: 11.5 }}>
                          <thead>
                            <tr>
                              <th>Check</th><th>Status</th><th>Details</th><th>Impacted Elements</th><th>Severity</th>
                            </tr>
                          </thead>
                          <tbody>
                            {physicsChecks.map((c) => (
                              <tr key={c.check}>
                                <td style={{ fontWeight: 600 }}>{c.check}</td>
                                <td><StatusBadge status={c.status} /></td>
                                <td className="t-muted">{c.details}</td>
                                <td className="t-muted mono">{c.impacted}</td>
                                <td><span className={`sev ${c.severity.toLowerCase()}`}>{c.severity}</span></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ) : (
                        <EmptyState icon="scale">No checks recorded yet - press Re-test Physics.</EmptyState>
                      )}
                    </div>
                  ) : (
                    <div style={{ padding: 10, overflowX: "auto" }}>
                      <div className="variant-row" style={{ display: "flex", gap: 10 }}>
                        {variantCards.map((v) => (
                          <div
                            key={v.id}
                            className={`variant-card ${variant === v.id ? "active" : ""}`}
                            onClick={() => activateVariant(v.id)}
                            style={{
                              width: 180,
                              background: variant === v.id ? "rgba(96,165,250,0.12)" : "var(--bg-panel-2)",
                              border: `1px solid ${variant === v.id ? "var(--accent)" : "var(--border)"}`,
                              borderRadius: 6,
                              padding: 10,
                              cursor: "pointer",
                              transition: "all 0.15s ease",
                            }}
                          >
                            <div className="row between" style={{ marginBottom: 4 }}>
                              <span style={{ fontWeight: 650, fontSize: 12 }}>{v.name}</span>
                              {v.active && <Badge tone="blue">Active</Badge>}
                            </div>
                            <p className="micro t3" style={{ lineHeight: 1.3, margin: 0 }}>{v.desc}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Right: Unity Component Inspector */}
        {rightOpen ? (
          <>
            <ResizeHandle dir="col" onDrag={(d) => setRightW((prev) => prev - d)} onReset={() => setRightW(360)} />
            <div className="card unity-inspector" style={{ width: rightW, flex: "none", display: "flex", flexDirection: "column", minHeight: 0, borderRadius: 0, borderTop: 0, borderBottom: 0, borderRight: 0 }}>
              <header className="card-head" style={{ minHeight: 32, padding: "0 10px", background: "var(--bg-panel-2)", borderBottom: "1px solid var(--border)" }}>
                <span className="row" style={{ gap: 6, alignItems: "center", minWidth: 0 }}>
                  <Icon name="cube" size={13} style={{ color: "var(--accent)" }} />
                  <span className="ellipsis" style={{ fontWeight: 650, fontSize: 12 }}>{selected ? selectedName : "Inspector"}</span>
                </span>
              </header>

              <div className="tabs scrollable" style={{ padding: "0 8px", background: "var(--bg-panel-1)", borderBottom: "1px solid var(--border)" }}>
                {(["Components", "Physics", "Provenance", "Agent", "Robots"] as const).map((t) => (
                  <button key={t} className={inspTab === t ? "on" : ""} onClick={() => setInspTab(t)} style={{ height: 26, fontSize: 11 }}>
                    {t}
                  </button>
                ))}
              </div>

              <div style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "8px 10px" }}>
                {inspTab === "Agent" || inspTab === "Robots" ? (
                  <RobotAgentPanel
                    tab={inspTab}
                    robots={worldRobots}
                    robotId={robotId}
                    setRobotId={setRobotId}
                    instruction={instruction}
                    setInstruction={setInstruction}
                    command={command}
                    operation={operation}
                    activeRun={activeRun}
                    planning={planning}
                    onPlan={planCommand}
                    onImport={importRobot}
                    importing={importingRobot}
                    onRobotsChanged={refetchRobots}
                    backend={backend}
                    setBackend={setBackend}
                    controller={controller}
                    setController={setController}
                    task={task}
                    assets={physicalAssets?.assetVersions ?? []}
                    assetVersionId={assetVersionId}
                    setAssetVersionId={setAssetVersionId}
                    models={modelData?.models.filter((item) => item.roles.includes("vla_policy")) ?? []}
                    modelId={modelId}
                    setModelId={setModelId}
                    hasPlacedWorldAssets={hasPlacedWorldAssets}
                    onManual={startManualControl}
                  />
                ) : generatedAsset ? (
                  <GeneratedAssetInspector asset={generatedAsset} placement={activePlacement} tab={inspTab} onTransform={(translation) => updatePlacement(generatedAsset.id, { translation })} onRotation={(rotationZDeg) => updatePlacement(generatedAsset.id, { rotationZDeg })} onScale={(scaleMultiplier) => updatePlacement(generatedAsset.id, { scaleMultiplier })} onMobility={(mobility) => updatePlacement(generatedAsset.id, { mobility })} />
                ) : selected && ["Components", "Physics", "Provenance"].includes(inspTab) ? (
                  <EmptyState icon="shield">This scene node has no canonical physical manifest. Select a generated asset or run a recorded evaluation; RobotWorld will not invent dimensions, joints, or provenance.</EmptyState>
                ) : (
                  <div className="empty-note center" style={{ padding: 24, textAlign: "center", color: "var(--text-3)" }}>
                    Select an object in the Hierarchy or 3D Viewport to inspect components.
                  </div>
                )}
              </div>
            </div>
          </>
        ) : (
          <PanelRail label="Inspector" side="right" onExpand={() => setRightOpen(true)} />
        )}
      </div>

      {/* New Variant Modal */}
      {newVariantOpen && (
        <Modal
          title="Create Scenario Variation"
          onClose={() => setNewVariantOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setNewVariantOpen(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={createVariant} disabled={creatingVariant}>
                {creatingVariant ? "Compiling..." : "Create Variation"}
              </button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="field"><label>Variant Name</label><input ref={variantNameRef} className="input" placeholder="e.g., Heavy Left Door + Friction 0.45" autoFocus /></div>
            <div className="field"><label>Physical Description</label><input ref={variantDescRef} className="input" placeholder="Mass 18.2kg, horizontal handle, revolute damping 2.4" /></div>
          </div>
        </Modal>
      )}
    </div>
  );
}

interface RuntimeDiagnostics {
  status: "healthy" | "degraded";
  uptimeSeconds: number;
  events: { time: string; level: string; service: string; message: string }[];
}

function diagnosticMessage(raw: string): string {
  try {
    const parsed = JSON.parse(raw) as { message?: string; route?: string };
    if (parsed && typeof parsed === "object" && parsed.message) {
      return `${parsed.message}${parsed.route ? ` · ${parsed.route}` : ""}`;
    }
  } catch { /* plain backend log */ }
  return raw;
}

function LiveRuntimeShelf({ session, frame, status, evaluation }: { session: FrankaLiveSession; frame: FrankaLiveFrame | null; status: string; evaluation?: WorldEvaluation }) {
  const predicate = evaluation?.result?.predicate ?? {};
  const predicateEntries = Object.entries(predicate).filter(([, value]) => typeof value === "boolean" || typeof value === "number").slice(0, 8);
  return <div className="acceptance-log mono" style={{ padding: "6px 10px", maxHeight: "none" }}>
    <div className="console-line"><span className="console-time">{evaluation ? (evaluation.success ? "PASSED" : "FAILED") : status.toUpperCase()}</span><span>{session.mode === "manual" ? "Operator-controlled Panda session" : `Authoritative ${session.operation?.task?.replaceAll("_", " ") ?? "physics task"}`} · {session.sessionId}</span></div>
    <div className="console-line"><span className="console-time">STATE</span><span>frame {frame?.sequence ?? 0} · sim {(frame?.simTimeSeconds ?? 0).toFixed(2)} s · {frame?.phase?.replaceAll("_", " ") ?? "initializing"} · finite {String(frame?.state.finite !== false)}</span></div>
    {evaluation && <div className="console-line"><span className="console-time">EVAL</span><span>{evaluation.id} · {evaluation.policy} · seed {evaluation.seed}</span></div>}
    {evaluation?.failureCode && <div className="console-line"><span className="console-time">ERROR</span><span>{evaluation.failureCode} · {evaluation.failureDetail}</span></div>}
    {predicateEntries.map(([key, value]) => <div className="console-line" key={key}><span className="console-time">PRED</span><span>{key} = {typeof value === "number" ? value.toFixed(6) : String(value)}</span></div>)}
  </div>;
}

function RuntimeDiagnosticsPanel() {
  const { data, error, loading, refetch } = useApi<RuntimeDiagnostics>("/diagnostics/runtime");
  useEffect(() => {
    const timer = window.setInterval(refetch, 3000);
    return () => window.clearInterval(timer);
  }, [refetch]);
  return <div className="col" style={{ minHeight: "100%", gap: 0 }}>
    <div className="row" style={{ padding: "7px 10px", borderBottom: "1px solid var(--border)" }}>
      <StatusBadge status={error ? "failed" : data?.status ?? (loading ? "running" : "idle")} />
      <span className="micro t3">API uptime {finiteNumber(data?.uptimeSeconds).toFixed(1)} s · auto-refresh 3 s</span>
      <span className="grow" />
      <button className="btn btn-ghost btn-sm" onClick={refetch}><Icon name="refresh" size={10} /> Refresh</button>
    </div>
    {error ? <ErrorState message={error.message} onRetry={refetch} /> : data?.events.length ? <div className="acceptance-log mono" style={{ padding: "6px 10px", maxHeight: "none" }}>
      {data.events.map((event, index) => <div className="console-line" key={`${event.time}-${event.service}-${index}`}>
        <span className="console-time">{event.time}</span>
        <span className={event.level === "ERROR" ? "g-red" : "t2"}>[{event.level}] {event.service} · {diagnosticMessage(event.message)}</span>
      </div>)}
    </div> : <EmptyState icon="shield">No recent runtime errors or warnings.</EmptyState>}
  </div>;
}

function RobotAgentPanel({ tab, robots, robotId, setRobotId, instruction, setInstruction, command, operation, activeRun, planning, onPlan, onImport, importing, onRobotsChanged, backend, setBackend, controller, setController, task, assets, assetVersionId, setAssetVersionId, models, modelId, setModelId, hasPlacedWorldAssets, onManual }: {
  tab: "Agent" | "Robots"; robots: RobotManifest[]; robotId: string; setRobotId: (id: string) => void;
  instruction: string; setInstruction: (value: string) => void; command: WorldCommandResult | null; planning: boolean;
  operation: WorldOperationResult | null; activeRun: AutonomousRunSummary | null;
  onPlan: () => void; onImport: (file?: File) => void; importing: boolean;
  onRobotsChanged: () => void;
  backend: WorldBackend; setBackend: (value: WorldBackend) => void;
  controller: WorldController; setController: (value: WorldController) => void;
  task: WorldTask;
  assets: PhysicalAssetVersion[]; assetVersionId: string; setAssetVersionId: (value: string) => void;
  models: ModelSummary[]; modelId: string; setModelId: (value: string) => void;
  hasPlacedWorldAssets: boolean;
  onManual: () => void;
}) {
  const toast = useToast();
  const [registeringFranka, setRegisteringFranka] = useState(false);
  const robot = robots.find((item) => item.id === robotId);
  const addFranka = async () => {
    setRegisteringFranka(true);
    try {
      const command = await api.post<RobotCommandResponse>("/robots/franka/mujoco", {});
      const value = command.result.robot;
      if (!value) throw new Error("Franka registration command returned no robot manifest.");
      setRobotId(value.id);
      onRobotsChanged();
      toast.push(value.physicsReady ? "ok" : "info", "Franka Panda registered", value.physicsReady ? "Pinned MuJoCo model, gripper, front camera, and wrist camera passed validation." : value.readiness.blockers[0] ?? "Physics readiness gates remain.");
    } catch (e) { toast.push("err", "Franka registration failed", e instanceof ApiError ? e.message : String(e)); }
    finally { setRegisteringFranka(false); }
  };
  const mapCamera = async (key: string, value: string) => {
    if (!robot) return;
    try {
      await api.put(`/robots/${robot.id}`, { cameraMappings: { ...robot.cameraMappings, [key]: value } });
      onRobotsChanged();
      toast.push("ok", "Camera mapping saved", `${key} → ${value}`);
    } catch (e) { toast.push("err", "Camera mapping failed", e instanceof ApiError ? e.message : String(e)); }
  };
  if (tab === "Robots") return <div className="col inspector-agent" style={{ minHeight: "100%", gap: 10 }}>
    <div className="col" style={{ gap: 7 }}>
      <select className="select" value={robotId} onChange={(e) => setRobotId(e.target.value)}><option value="">Select robot</option>{robots.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.format}</option>)}</select>
      <div className="row" style={{ flexWrap: "wrap" }}><button className="btn btn-secondary btn-sm grow" disabled={registeringFranka} onClick={() => void addFranka()}><Icon name="plus" size={11} /> {registeringFranka ? "Validating MuJoCo..." : "Register Franka Panda"}</button></div>
      <label className="empty-note center col" style={{ marginTop: 8, minHeight: 78, borderStyle: "dashed", cursor: "pointer" }} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); void onImport(e.dataTransfer.files[0]); }}>
        <Icon name="upload" size={17} /><span>{importing ? "Inspecting source..." : "Drop URDF, MJCF, OpenUSD, or GLB"}</span><input hidden type="file" accept=".urdf,.xml,.mjcf,.usd,.usda,.usdc,.glb" onChange={(e) => void onImport(e.target.files?.[0])} />
      </label>
    </div>
    <div className="grow col" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      {!robot ? <EmptyState icon="robot">Import a robot; RobotWorld will inspect it without assuming it is executable.</EmptyState> : <>
        <div className="col" style={{ gap: 5 }}><b>{robot.name}</b><div className="row" style={{ flexWrap: "wrap" }}><Badge tone="grey">{robot.format}</Badge><Badge tone={robot.readiness.executable ? "teal" : "amber"}>{robot.joints} joints · {robot.readiness.executable ? "executable" : "gated"}</Badge></div></div>
        <div className="col" style={{ gap: 8 }}>
          {(["observation.images.exterior_1_left", "observation.images.exterior_2_left"] as const).map((key) => <label className="field" key={key}><span className="micro t3">{key}</span><select className="select" value={robot.cameraMappings[key] ?? ""} onChange={(e) => void mapCamera(key, e.target.value)}><option value="">Map camera...</option>{robot.cameraNames.map((name) => <option value={name} key={name}>{name}</option>)}</select></label>)}
        </div>
        {robot.readiness.blockers.map((value) => <div className="console-line" key={value}><span className="console-time">BLOCK</span><span>{value}</span></div>)}
      </>}
    </div>
  </div>;
  const selectedModel = models.find((item) => item.id === modelId);
  const selectedAsset = assets.find((item) => item.id === assetVersionId);
  const modelReady = controller === "oracle" || (Boolean(selectedModel) && selectedModel?.lifecycleState === "LOADED" && selectedModel.healthStatus === "healthy");
  const assetReady = hasPlacedWorldAssets || task === "open_drawer" || controller === "oracle" || selectedAsset?.lifecycleState === "ORACLE_VALIDATED";
  const canExecute = Boolean(robotId && instruction.trim().length >= 2 && modelReady && assetReady);
  const run = activeRun ?? operation?.run ?? null;
  const history = Array.isArray(run?.state?.history) ? run.state.history.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
  return <div className="col inspector-agent" style={{ minHeight: "100%", gap: 9 }}>
    <div className="col" style={{ gap: 7 }}>
      <div className="row" style={{ gap: 6 }}>
        <label className="field grow"><span className="micro t3">Backend</span><select className="select" value={backend} onChange={(e) => { const value = e.target.value as WorldBackend; setBackend(value); if (value === "isaac_sim") { setController("oracle"); const isaacRobot = robots.find((item) => item.format === "isaac-openusd-reference"); if (isaacRobot) setRobotId(isaacRobot.id); } else { const mujocoRobot = robots.find((item) => item.format === "mjcf" && item.physicsReady); if (mujocoRobot) setRobotId(mujocoRobot.id); } }}><option value="mujoco">MuJoCo</option><option value="isaac_sim">NVIDIA Isaac Sim + Isaac Lab</option></select></label>
        <div className="field grow"><span className="micro t3">Instruction compiler</span><div className="select" style={{ display: "flex", alignItems: "center" }}>Automatic · active world</div></div>
      </div>
      <label className="field"><span className="micro t3">Robot in this world</span><select className="select" value={robotId} onChange={(e) => setRobotId(e.target.value)}><option value="">No robot selected</option>{robots.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <div className="field"><span className="micro t3">Execution</span><div className="select" style={{ display: "flex", alignItems: "center" }}>{backend === "mujoco" ? "Deterministic Panda physics" : "Isaac worker · license gated"}</div></div>
      {task === "pick_place" && !hasPlacedWorldAssets && <label className="field"><span className="micro t3">Validation asset</span><select className="select" value={assetVersionId} onChange={(e) => setAssetVersionId(e.target.value)}><option value="">Known-good cube</option>{assets.map((item) => <option key={item.id} value={item.id}>{item.displayName} · {item.lifecycleState}</option>)}</select></label>}
      {controller !== "oracle" && <label className="field"><span className="micro t3">Policy brain</span><select className="select" value={modelId} onChange={(e) => setModelId(e.target.value)}><option value="">Select loaded VLA</option>{models.map((item) => <option key={item.id} value={item.id}>{item.displayName} · {item.lifecycleState}/{item.healthStatus}</option>)}</select></label>}
      <textarea className="input" style={{ minHeight: 82, padding: 9, resize: "vertical" }} value={instruction} onChange={(e) => setInstruction(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !planning && canExecute) void onPlan(); }} placeholder="Put the apple inside the sink; drop the banana off the table; place the apple on top of the orange." />
      <span className="micro t3">One action grounds the named objects, compiles the relation, checks physical readiness, and starts the authoritative run. Unknown or ambiguous instructions stop before physics.</span>
      <button className="btn btn-primary btn-sm" disabled={planning || !canExecute} onClick={() => void onPlan()} title={canExecute ? "Compile and run against the active world" : "Select a ready robot and instruction"}><Icon name="play" size={12} /> {planning ? "Compiling and running..." : "Run instruction"}</button>
      {hasPlacedWorldAssets && backend === "mujoco" && controller === "oracle" && ["auto", "pick_place"].includes(task) && <button className="btn btn-secondary btn-sm" disabled={planning || !canExecute} onClick={onManual}><Icon name="robot" size={12} /> Control Panda in this world</button>}
      {operation?.evaluation && <div className={`world-operation-result ${operation.evaluation.success ? "passed" : "failed"}`}><b>{operation.evaluation.success ? "Task predicate passed" : operation.evaluation.failureCode ?? operation.evaluation.status}</b><span className="micro mono">{operation.evaluation.id} · {operation.evaluation.worldTemplateId} · seed {operation.evaluation.seed}</span>{operation.evaluation.failureDetail && <span className="micro t3">{operation.evaluation.failureDetail}</span>}</div>}
      {run && <div className="world-operation-result"><b>Agent {run.lifecycleState}</b><span className="micro mono">{run.id}{run.stopReason ? ` · ${run.stopReason}` : ""}</span>{history.slice(-3).map((item, index) => <span className="micro t3" key={`${String(item.phase ?? item.kind ?? "step")}-${index}`}>{String(item.phase ?? item.kind ?? "step")} · {String(item.failureCode ?? item.reason ?? item.planId ?? "completed")}</span>)}</div>}
      {command && <><div className="small"><b>{command.plan.summary}</b> <span className="micro t3 mono">{command.plannerProvenance}</span></div>{command.plan.steps.map((step, i) => <div className="console-line" key={`${i}-${step}`}><span className="console-time">{String(i + 1).padStart(2, "0")}</span><span>{step}</span></div>)}</>}
      {!command && !operation && <div className="empty-note">{hasPlacedWorldAssets ? "The instruction compiler uses the registered Panda and active editor placements. A movable source must have a validated physical version; visual meshes are never silently substituted as colliders." : "Run instruction opens one continuous authoritative MuJoCo view with synchronized front and wrist observations."}</div>}
    </div>
    <div className="col" style={{ borderTop: "1px solid var(--border)", paddingTop: 9, gap: 5 }}><b className="small">Readiness</b>{backend === "isaac_sim" && <span className="micro t3">Isaac execution will return its exact runtime/EULA blocker if the native process cannot launch.</span>}{hasPlacedWorldAssets && <span className="micro t3">Oracle and VLA use the same compiled apple/blender/counter/Panda runtime. Agent curriculum remains validation-world only.</span>}{!modelReady && <span className="micro t3">Load a healthy VLA policy before VLA or autonomous execution.</span>}{!assetReady && !hasPlacedWorldAssets && <span className="micro t3">The selected asset must pass the deterministic oracle before VLA execution.</span>}{robot?.readiness.blockers.map((value) => <span className="micro t3" key={value}>{value}</span>)}</div>
  </div>;
}

function GeneratedWorldView({ placements, robotGeometries, robotSpawn, selectedAssetId, mode, onSelect, onCommit, onRobotCommit }: {
  placements: WorldPlacement[];
  robotGeometries: AuthoringRobotGeometry[];
  robotSpawn?: { positionM: number[]; quaternionWxyz: number[] };
  selectedAssetId: string;
  mode: EditorTool;
  onSelect: (assetId: string, name: string) => void;
  onCommit: (assetId: string, patch: { translation?: number[]; rotationZDeg?: number; scaleMultiplier?: number[] }) => void;
  onRobotCommit: (patch: { positionM: number[]; quaternionWxyz: number[] }) => void;
}) {
  return (
    <WorldEditorCanvas
      placements={placements}
      robotGeometries={robotGeometries}
      robotSpawn={robotSpawn}
      selectedAssetId={selectedAssetId}
      tool={mode}
      onSelect={onSelect}
      onCommit={onCommit}
      onRobotCommit={onRobotCommit}
      onFrame={({ fps, latencyMs }) => window.dispatchEvent(new CustomEvent("robotworld:world-frame", { detail: { fps, latencyMs, active: true } }))}
    />
  );
}

function LiveWorldResult({ session, frame, status, placements, manualBusy, onManual, onBack }: { session: FrankaLiveSession; frame: FrankaLiveFrame | null; status: string; placements: WorldPlacement[]; manualBusy: boolean; onManual: (kind: "jog" | "open" | "close", deltaM?: number[]) => void; onBack: () => void }) {
  const activeWorld = session.operation?.executionScope === "active_world";
  const sourceAssetId = session.operation?.authoredScene?.sourcePlacement?.assetId;
  const contextPlacements = activeWorld
    ? placements.filter((entry) => entry.assetId !== sourceAssetId).map((entry) => ({
        assetId: entry.assetId,
        name: entry.name,
        translation: entry.translation,
        rotationZDeg: entry.rotationZDeg,
        scale: entry.scale,
      }))
    : [];
  const runtimeGeometries = (frame?.state.renderGeometries ?? []).filter((entry) => (
    !activeWorld || !["workspace_surface", "target_support_collision", "target_marker"].includes(entry.name)
  ));
  return <div className="live-world-result">
    <header><span className="col" style={{ gap: 1 }}><b>{activeWorld ? "Active editor world · authoritative physics" : "Authoritative validation physics"}</b><span className="micro t3 mono">{session.sessionId} · MuJoCo {session.physicsHz} Hz · transforms {session.streamHz} Hz{activeWorld ? " · textured editor assets retained" : ""}</span></span><button className="btn btn-secondary btn-sm" onClick={onBack}><Icon name="cube" size={11} /> Edit placements</button></header>
    {runtimeGeometries.length ? <div style={{ position: "relative", flex: 1, minHeight: 0 }}><AuthoritativeSimulationCanvas geometries={runtimeGeometries} contextPlacements={contextPlacements} /><img src={`data:image/jpeg;base64,${frame?.jpegBase64}`} alt="Authoritative front and wrist RGB observations" style={{ position: "absolute", right: 12, top: 12, width: 250, maxWidth: "32%", height: "auto", border: "1px solid var(--border-strong)", borderRadius: 4, boxShadow: "0 8px 24px rgba(0,0,0,.45)" }} />{session.mode === "manual" && <div className="manual-panda-controls"><b>Cartesian jog · 2 cm</b><div className="manual-jog-grid"><button disabled={manualBusy} onClick={() => onManual("jog", [0.02, 0, 0])}>X+</button><button disabled={manualBusy} onClick={() => onManual("jog", [-0.02, 0, 0])}>X−</button><button disabled={manualBusy} onClick={() => onManual("jog", [0, 0.02, 0])}>Y+</button><button disabled={manualBusy} onClick={() => onManual("jog", [0, -0.02, 0])}>Y−</button><button disabled={manualBusy} onClick={() => onManual("jog", [0, 0, 0.02])}>Z+</button><button disabled={manualBusy} onClick={() => onManual("jog", [0, 0, -0.02])}>Z−</button></div><div className="row"><button disabled={manualBusy} onClick={() => onManual("open")}>Open gripper</button><button disabled={manualBusy} onClick={() => onManual("close")}>Close gripper</button></div><span className="micro">Commands actuate MuJoCo; workspace and joint limits reject unsafe targets.</span></div>}</div> : <div className="center col grow" style={{ gap: 8 }}><Icon name="camera" size={22} /><span className="small">Connecting to the running Franka simulation...</span></div>}
    <footer><span className="mono">{status} · frame {frame?.sequence ?? 0} · sim {(frame?.simTimeSeconds ?? 0).toFixed(2)} s</span><span>{frame?.phase?.replaceAll("_", " ") ?? "initializing"} · contacts {frame?.state.contactCount ?? 0} · {frame?.state.finite === false ? "invalid state" : "finite physics"}</span></footer>
  </div>;
}

interface RobotManifest {
  id: string; name: string; format: string; joints: number; cameras: number; cameraNames: string[];
  cameraMappings: Record<string, string>; policyAdapter?: string | null;
  physicsReady?: boolean;
  readiness: { executable: boolean; blockers: string[] };
}

interface RobotCommandResponse {
  commandId: string;
  status: string;
  result: { robot?: RobotManifest };
}

interface WorldCommandResult {
  instruction: string; executionAllowed: boolean; plannerProvenance: string; blockers: string[];
  plan: { summary: string; steps: string[]; referencedObjectIds?: string[]; assumptions?: string[] };
}

interface WorldPlacement {
  assetId: string;
  name: string;
  translation: number[];
  rotationZDeg?: number;
  baseScale?: number[];
  scaleMultiplier?: number[];
  scale: number[];
  rawBounds: number[][];
  rawExtents: number[];
  targetDimensions: number[];
  worldBounds: number[][];
  dimensionSource: string;
  dimensionConfidence: number;
  anchor: { mode: string; surface: string; gap_m: number };
  mobility?: "movable" | "fixed";
  massKg?: number;
  massSource?: string;
  collisionApproximation?: string;
  physicalStatus: string;
}

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function finiteVector(value: unknown, length = 3, fallback = 0): number[] {
  if (!Array.isArray(value)) return Array.from({ length }, () => fallback);
  return Array.from({ length }, (_, index) => finiteNumber(value[index], fallback));
}

function normalizeWorldPlacement(placement: WorldPlacement): WorldPlacement {
  const bounds = Array.isArray(placement.worldBounds) ? placement.worldBounds : [];
  const anchor = placement.anchor && typeof placement.anchor === "object"
    ? placement.anchor
    : { mode: "unanchored", surface: "unknown", gap_m: 0 };
  return {
    ...placement,
    translation: finiteVector(placement.translation),
    rotationZDeg: finiteNumber(placement.rotationZDeg),
    baseScale: finiteVector(placement.baseScale ?? placement.scale, 3, 1),
    scaleMultiplier: finiteVector(placement.scaleMultiplier, 3, 1),
    scale: finiteVector(placement.scale, 3, 1),
    rawBounds: [finiteVector(placement.rawBounds?.[0]), finiteVector(placement.rawBounds?.[1])],
    rawExtents: finiteVector(placement.rawExtents),
    targetDimensions: finiteVector(placement.targetDimensions),
    worldBounds: [finiteVector(bounds[0]), finiteVector(bounds[1])],
    dimensionSource: placement.dimensionSource || "unknown",
    dimensionConfidence: finiteNumber(placement.dimensionConfidence),
    anchor: {
      mode: anchor.mode || "unanchored",
      surface: anchor.surface || "unknown",
      gap_m: finiteNumber(anchor.gap_m),
    },
    mobility: placement.mobility === "movable" ? "movable" : "fixed",
    massKg: finiteNumber(placement.massKg, 1),
    massSource: placement.massSource || "unknown",
    collisionApproximation: placement.collisionApproximation || "unknown",
    physicalStatus: placement.physicalStatus || "unknown",
  };
}

function GeneratedAssetShelf({ asset, tab, assembly, checks, checksRunning }: { asset: Asset; tab: "Console" | "Checks" | "Variants"; assembly?: SceneData["assembly"]; checks: PhysicsCheck[]; checksRunning: boolean }) {
  const required = ["model.glb", "visual.usdc", "asset.usda", "world.usda", "spec.json"];
  const present = new Set(asset.artifacts.map((artifact) => artifact.file));
  const missing = required.filter((file) => !present.has(file));

  if (tab === "Checks") {
    return (
      <div style={{ padding: "8px 12px" }}>
        {checksRunning && <div className="busy-bar" style={{ marginBottom: 8 }}><i /></div>}
        {checks.map((check) => <div className="console-line" key={check.check}>
          <span className="console-time">{check.status.toUpperCase()}</span>
          <span><b>{check.check}</b> - {check.details} [{check.impacted}]</span>
        </div>)}
        {missing.length === 0 ? (
          <div className="console-line"><span className="console-time">OK</span><span>No missing required visual-pipeline artifacts.</span></div>
        ) : missing.map((file) => (
          <div className="console-line" key={file}><span className="console-time">ERROR</span><span>{file} is missing from asset {asset.id}.</span></div>
        ))}
        {asset.lastEvalResult !== "passed" && (
          <div className="console-line"><span className="console-time">OPEN</span><span>Physical task readiness is {asset.lastEvalResult}; visual generation does not prove articulation or manipulation validity.</span></div>
        )}
      </div>
    );
  }

  if (tab === "Variants") {
    return (
      <div className="acceptance-log mono" style={{ padding: "8px 12px", maxHeight: "none" }}>
        <div className="console-line"><span className="console-time">USD</span><span>world.usda references visual.usdc</span></div>
        {assembly?.available && <div className="console-line"><span className="console-time">STAGE</span><a href="/api/worlds/files/stage.usda">active stage.usda references every placed asset</a></div>}
        <div className="console-line"><span className="console-time">GLB</span><span>/api/assets/{asset.id}/files/model.glb</span></div>
        <div className="console-line"><span className="console-time">API</span><span>/api/assets/{asset.id}/render/vulkan</span></div>
      </div>
    );
  }

  return (
    <div className="acceptance-log mono" style={{ padding: "8px 12px", maxHeight: "none" }}>
      {asset.lastEvalResult === "failed" && <div className="console-line"><span className="console-time">ERROR</span><span>Asset evaluation failed. Open Asset Detail for the persisted failing stage and evidence.</span></div>}
      {asset.lastEvalResult === "pending" && <div className="console-line"><span className="console-time">OPEN</span><span>Physical evaluation is pending; visual generation is not treated as policy readiness.</span></div>}
      {asset.compile.map((stage) => (
        <div className="console-line" key={stage.name}>
          <span className="console-time">{stage.status.toUpperCase()}</span>
          <span>{stage.name} · {stage.duration}</span>
        </div>
      ))}
      {asset.artifacts.map((artifact) => (
        <div className="console-line" key={artifact.file}>
          <span className="console-time">FILE</span>
          <span>{artifact.file} · {artifact.size} · {artifact.generated}</span>
        </div>
      ))}
    </div>
  );
}

function GeneratedAssetInspector({ asset, placement, tab, onTransform, onRotation, onScale, onMobility }: { asset: Asset; placement?: WorldPlacement; tab: "Components" | "Physics" | "Provenance"; onTransform: (translation: number[]) => void; onRotation: (rotation: number) => void; onScale: (scale: number[]) => void; onMobility: (mobility: "movable" | "fixed") => void }) {
  const files = new Set(asset.artifacts.map((artifact) => artifact.file));
  const vec = (values: number[] | undefined) => values ? values.map((value) => finiteNumber(value).toFixed(4)).join(", ") : "unavailable";
  if (placement && tab === "Components") return <div className="col" style={{ gap: 10 }}>
    <InspSection title="Transform" defaultOpen={true}><div className="kv">
      <div className="kv-row"><span className="kv-k">Position XYZ (m)</span><span className="kv-v mono">{vec(placement.translation)}</span></div>
      <div className="row" style={{ gap: 5 }}>{placement.translation.map((value, index) => <label className="field grow" key={index}><span className="micro t3">{["X", "Y", "Z"][index]} m</span><input className="input mono" type="number" step="0.01" value={value} onChange={(event) => { const next = [...placement.translation]; next[index] = Number(event.target.value) || 0; onTransform(next); }} /></label>)}</div>
      <div className="kv-row"><span className="kv-k">Rotation Z</span><span className="kv-v"><input className="input mono" type="number" step="1" value={finiteNumber(placement.rotationZDeg)} onChange={(event) => onRotation(Number(event.target.value) || 0)} /></span></div>
      <div className="kv-row"><span className="kv-k">Measured base scale</span><span className="kv-v mono">{vec(placement.baseScale)}</span></div>
      <div className="row" style={{ gap: 5 }}>{(placement.scaleMultiplier ?? [1, 1, 1]).map((value, index) => <label className="field grow" key={index}><span className="micro t3">Scale {['X', 'Y', 'Z'][index]}</span><input className="input mono" type="number" min="0.02" max="100" step="0.05" value={value} onChange={(event) => { const next = [...(placement.scaleMultiplier ?? [1, 1, 1])]; next[index] = Math.max(.02, Number(event.target.value) || .02); onScale(next); }} /></label>)}</div>
      <div className="kv-row"><span className="kv-k">World AABB min</span><span className="kv-v mono">{vec(placement.worldBounds[0])}</span></div>
      <div className="kv-row"><span className="kv-k">World AABB max</span><span className="kv-v mono">{vec(placement.worldBounds[1])}</span></div>
    </div></InspSection>
    <InspSection title="Physical dimensions" defaultOpen={true}><div className="kv">
      <div className="kv-row"><span className="kv-k">Target W/D/H (m)</span><span className="kv-v mono">{vec(placement.targetDimensions)}</span></div>
      <div className="kv-row"><span className="kv-k">Raw GLB X/Y/Z</span><span className="kv-v mono">{vec(placement.rawExtents)}</span></div>
      <div className="kv-row"><span className="kv-k">Evidence</span><span className="kv-v">{placement.dimensionSource} ({placement.dimensionConfidence.toFixed(2)})</span></div>
    </div></InspSection>
  </div>;
  if (placement && tab === "Physics") return <div className="col" style={{ gap: 10 }}>
    <InspSection title="Support relationship" defaultOpen={true}><div className="kv">
      <div className="kv-row"><span className="kv-k">Body mode</span><span className="kv-v"><select className="select" value={placement.mobility ?? "fixed"} onChange={(event) => onMobility(event.target.value as "movable" | "fixed")}><option value="movable">Movable · gravity + grasp</option><option value="fixed">Fixed · static fixture</option></select></span></div>
      <div className="kv-row"><span className="kv-k">Anchor</span><span className="kv-v mono">{placement.anchor.mode}</span></div>
      <div className="kv-row"><span className="kv-k">Surface</span><span className="kv-v">{placement.anchor.surface}</span></div>
      <div className="kv-row"><span className="kv-k">Authored gap</span><span className="kv-v mono">{(placement.anchor.gap_m * 1000).toFixed(1)} mm</span></div>
      <div className="kv-row"><span className="kv-k">Status</span><span className="kv-v">{placement.physicalStatus.replaceAll("_", " ")}</span></div>
      <div className="kv-row"><span className="kv-k">Collision</span><span className="kv-v mono">{placement.collisionApproximation}</span></div>
      <div className="kv-row"><span className="kv-k">Mass</span><span className="kv-v mono">{finiteNumber(placement.massKg).toFixed(3)} kg · {placement.massSource}</span></div>
    </div></InspSection>
    <p className="micro t3">OpenUSD collision and rigid-body metadata are authored into stage.usda; the active MuJoCo runtime compiler must separately validate contacts and grasp/drop behavior. Estimated mass remains visibly labeled and does not count as measured evidence.</p>
  </div>;
  if (placement && tab === "Provenance") return <div className="col" style={{ gap: 10 }}>
    <InspSection title="Generated artifact chain" defaultOpen={true}><div className="kv">
      <div className="kv-row"><span className="kv-k">Application asset</span><span className="kv-v mono">{placement.assetId}</span></div>
      <div className="kv-row"><span className="kv-k">GLB</span><span className="kv-v mono">{files.has("model.glb") ? "model.glb present" : "missing"}</span></div>
      <div className="kv-row"><span className="kv-k">OpenUSD</span><span className="kv-v mono">{files.has("visual.usdc") && files.has("asset.usda") ? "visual.usdc -> asset.usda -> stage.usda" : "incomplete"}</span></div>
      <div className="kv-row"><span className="kv-k">Scale method</span><span className="kv-v">occupied GLB bounds fitted to target dimensions</span></div>
    </div></InspSection>
    <InspSection title="Bright Data collection trace" defaultOpen={true}><div className="col" style={{ gap: 5 }}>
      {asset.collectionTrace ? <>
        <div className="kv-row"><span className="kv-k">Provider</span><span className="kv-v">{asset.collectionTrace.provider}</span></div>
        <div className="kv-row"><span className="kv-k">Input</span><span className="kv-v mono">{asset.collectionTrace.inputQuery}</span></div>
        {asset.collectionTrace.requests.map((request, index) => <div className="console-line" key={`${request.tool}-${index}`}><span className="console-time">QUERY</span><span><b>{request.tool}</b> · {request.query} · {request.purpose}</span></div>)}
        {asset.collectionTrace.results.map((result, index) => <div className="console-line" key={`${result.value}-${index}`}><span className="console-time">RESULT</span><span>{result.type} · {result.title || result.domain || result.value}</span></div>)}
      </> : <span className="micro t3">This legacy asset predates collection tracing. Rebuild it to persist the exact queries and sanitized results.</span>}
    </div></InspSection>
  </div>;
  return (
    <div className="col" style={{ gap: 10 }}>
      <InspSection title="Generated mesh" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Source</span><span className="kv-v">TRELLIS.2 generated GLB</span></div>
          <div className="kv-row"><span className="kv-k">GLB</span><span className="kv-v mono">{files.has("model.glb") ? "model.glb present" : "missing"}</span></div>
          <div className="kv-row"><span className="kv-k">OpenUSD visual</span><span className="kv-v mono">{files.has("visual.usdc") ? "visual.usdc composed" : "not generated"}</span></div>
          <div className="kv-row"><span className="kv-k">World layer</span><span className="kv-v mono">{files.has("world.usda") ? "world.usda composed" : "not generated"}</span></div>
        </div>
      </InspSection>
      <InspSection title="Source and validation" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Collection</span><span className="kv-v">Bright Data image search</span></div>
          <div className="kv-row"><span className="kv-k">Foreground</span><span className="kv-v">Local U²-NetP</span></div>
          <div className="kv-row"><span className="kv-k">Conditioning</span><span className="kv-v">Local DINOv3</span></div>
          <div className="kv-row"><span className="kv-k">Evaluation</span><span className="kv-v mono">{asset.lastEvalResult} · readiness {asset.readiness}/100</span></div>
        </div>
      </InspSection>
      <p className="micro t3" style={{ margin: 0, lineHeight: 1.5 }}>
        This is the generated visual asset. Physical affordances and task validity remain separate evaluation work; this application does not mark them passed from a single image.
      </p>
    </div>
  );
}

/* ---- Console Component ---------------------------------------------------- */

function AcceptanceConsole({
  scenario,
  catalog,
  job,
}: {
  scenario?: AcceptanceScenario;
  catalog?: AcceptanceCatalog;
  job: AcceptanceJob | null;
}) {
  if (!scenario) return <div style={{ padding: 12 }}><Skeleton rows={3} /></div>;
  return (
    <div className="acceptance-console" style={{ padding: "8px 12px" }}>
      <div className="row between acceptance-summary" style={{ gap: 10, paddingBottom: 6, borderBottom: "1px solid var(--border)" }}>
        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <strong style={{ fontSize: 12 }}>{scenario.name}</strong>
          <span className="micro t3">{scenario.description}</span>
        </div>
        <span className="micro t3 mono">{catalog?.readiness.policyConfigured ? "configured policy available" : "policy not configured"}</span>
      </div>
      <div className="acceptance-log mono" style={{ fontSize: 11, maxHeight: 130, overflowY: "auto", marginTop: 6 }}>
        {!job ? (
          <div className="console-line"><span className="console-time">INFO</span><span>Engine ready. Select Kitchen World or Logistics World to build randomized physical rollout.</span></div>
        ) : (
          <>
            <div className="console-line"><span className="console-time">JOB</span><span>{job.id} · Status: {job.status.toUpperCase()}</span></div>
            {job.detail.stages.map((stage, index) => (
              <div className={`console-line ${stage.status}`} key={`${stage.name}-${index}`}>
                <span className="console-time">{new Date(stage.at).toLocaleTimeString([], { hour12: false })}</span>
                <span><b>[{stage.status.toUpperCase()}]</b> {stage.name} - {stage.detail}</span>
              </div>
            ))}
            {job.detail.error && <div className="console-line failed"><span className="console-time">ERR</span><span>{job.detail.error}</span></div>}
            {job.detail.result && <div className="console-line blocked"><span className="console-time">RESULT</span><span>taskSuccess={String(job.detail.result.taskSuccess)} · {job.detail.result.message}</span></div>}
          </>
        )}
      </div>
    </div>
  );
}
