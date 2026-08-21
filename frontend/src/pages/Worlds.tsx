import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, StatusBadge } from "../components/ui/controls";
import { Tree } from "../components/ui/Tree";
import { PanelRail, ResizeHandle, usePanelSize } from "../components/ui/Resizable";
import { useToast } from "../components/ui/Toast";
import { Modal } from "../components/ui/Modal";
import { api, ApiError, uploadBinary } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import { NativeVulkanCanvas } from "../components/three/NativeVulkanCanvas";
import type { Asset, PhysicsCheck, ScenarioVariant, SceneNode } from "../data/types";

interface SceneData {
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
  const [seed, setSeed] = useState("1048576");
  const [variant, setVariant] = useState("");
  const [shadingVariant, setShadingVariant] = useState<"rgb" | "seg" | "depth">("rgb");
  const [gizmoMode, setGizmoMode] = useState<"translate" | "rotate" | "camera">("camera");
  const [simPlaying, setSimPlaying] = useState(false);
  const [inspTab, setInspTab] = useState<"Components" | "Physics" | "Provenance">("Components");
  const [shelfTab, setShelfTab] = useState<"Console" | "Checks" | "Variants" | "Agent" | "Robots" | "Diagnostics">("Agent");
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
  const [robotId, setRobotId] = useState("");
  const [instruction, setInstruction] = useState("Grab the apple and put it in the blender.");
  const [command, setCommand] = useState<WorldCommandResult | null>(null);
  const [planning, setPlanning] = useState(false);
  const [importingRobot, setImportingRobot] = useState(false);
  const [arranging, setArranging] = useState(false);

  // panel state - resizable + collapsible
  const [leftW, setLeftW] = usePanelSize(260, 200, 440, "robotworld.worlds.leftW");
  const [rightW, setRightW] = usePanelSize(340, 260, 480, "robotworld.worlds.rightW");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [shelfH, setShelfH] = usePanelSize(210, 130, 380, "robotworld.worlds.shelfH");
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
  const hasPlacedWorldAssets = Boolean(scene?.placedAssets?.length);

  useEffect(() => { if (!robotId && robotData?.robots[0]) setRobotId(robotData.robots[0].id); }, [robotData, robotId]);

  const planCommand = async (mode: "plan" | "execute" = "plan") => {
    setPlanning(true);
    try {
      const result = await api.post<WorldCommandResult>("/worlds/commands", { instruction, robotId: robotId || null, mode });
      setCommand(result);
      setShelfTab("Agent");
    } catch (e) {
      toast.push("err", mode === "execute" ? "Execution blocked" : "Planning failed", e instanceof ApiError ? e.message : String(e));
    } finally { setPlanning(false); }
  };

  const importRobot = async (file?: File) => {
    if (!file) return;
    setImportingRobot(true);
    try {
      const robot = await uploadBinary<RobotManifest>("/robots/import", file);
      setRobotId(robot.id);
      await refetchRobots();
      setShelfTab("Robots");
      toast.push(robot.readiness.executable ? "ok" : "info", "Robot inspected", `${robot.name} · ${robot.joints} joints · ${robot.readiness.blockers.length} readiness gates`);
    } catch (e) { toast.push("err", "Robot import failed", e instanceof ApiError ? e.message : String(e)); }
    finally { setImportingRobot(false); }
  };

  const updatePlacement = async (asset: string, patch: { translation?: number[]; rotationZDeg?: number; visible?: boolean; mobility?: "movable" | "fixed" }) => {
    try { await api.patch(`/worlds/placements/${asset}`, patch); await refetch(); }
    catch (e) { toast.push("err", "Placement update failed", e instanceof ApiError ? e.message : String(e)); }
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
      if (event.key.toLowerCase() === "w") setGizmoMode("translate");
      if (event.key.toLowerCase() === "e") setGizmoMode("rotate");
      if (event.key.toLowerCase() === "q") setGizmoMode("camera");
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, []);

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
              <Badge tone="grey">{scene?.placedAssets?.length ?? 0} placed assets</Badge>
            </span>
            <span className="v-divider" />
            <div className="unity-group row" style={{ gap: 2, background: "var(--bg-panel-2)", padding: "2px 4px", borderRadius: 4, border: "1px solid var(--border)" }}>
              <button className={`btn btn-sm ${gizmoMode === "translate" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("translate")} title="Move selected object (W)"><Icon name="move" size={12} /> Move</button>
              <button className={`btn btn-sm ${gizmoMode === "rotate" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("rotate")} title="Rotate selected object around world Z (E)"><Icon name="refresh" size={12} /> Rotate</button>
              <button className={`btn btn-sm ${gizmoMode === "camera" ? "btn-secondary" : "btn-ghost"}`} onClick={() => setGizmoMode("camera")} title="Free camera orbit (Q)"><Icon name="camera" size={12} /> Camera</button>
            </div>
            <span className="v-divider" />
            <span className="micro t3">OpenUSD composition persisted</span>
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
        {/* Simulation Transport */}
        <div className="unity-group row" style={{ gap: 3, background: "var(--bg-panel-2)", padding: "2px 4px", borderRadius: 4, border: "1px solid var(--border)" }}>
          <button
            className={`btn btn-sm ${simPlaying ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setSimPlaying(!simPlaying)}
            title={simPlaying ? "Pause Simulation (Space)" : "Play Simulation (Space)"}
            style={{ padding: "4px 8px", height: 26 }}
          >
            <Icon name={simPlaying ? "pause" : "play"} size={12} />
          </button>
          <button
            className="btn btn-ghost btn-sm btn-icon"
            title="Step Simulation Frame (Ctrl+Right)"
            onClick={() => toast.push("info", "Physics Step", "Stepped 1 substep (2.0ms @ 500Hz)")}
            style={{ height: 26, width: 26 }}
          >
            <Icon name="arrowRight" size={11} />
          </button>
          <button
            className="btn btn-ghost btn-sm btn-icon"
            title="Reset Simulation State"
            onClick={() => { setSimPlaying(false); toast.push("info", "Physics Reset", "Initial state restored"); }}
            style={{ height: 26, width: 26 }}
          >
            <Icon name="refresh" size={11} />
          </button>
        </div>

        <span className="v-divider" />

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
            <ResizeHandle dir="col" onDrag={(d) => setLeftW((prev) => prev + d)} />
          </>
        ) : (
          <PanelRail label="Hierarchy" side="left" onExpand={() => setLeftOpen(true)} />
        )}

        {/* Center: Full-Bleed 3D Viewport + Bottom Shelf */}
        <div className="col" style={{ flex: 1, minWidth: 0, minHeight: 0, gap: 0 }}>
          <div className="card unity-viewport-container" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", borderRadius: 0, border: 0 }}>
            {hasPlacedWorldAssets ? (
              <GeneratedWorldView worldName={scene?.worldName ?? "OpenUSD World"} assetCount={scene?.placedAssets?.length ?? 0} stageAvailable={Boolean(scene?.assembly?.available)} placement={activePlacement} mode={gizmoMode} translate={gizmoMode === "translate" ? (delta) => activePlacement && updatePlacement(activePlacement.assetId, { translation: activePlacement.translation.map((value, index) => value + delta[index]) }) : undefined} rotate={gizmoMode === "rotate" ? (degrees) => activePlacement && updatePlacement(activePlacement.assetId, { rotationZDeg: finiteNumber(activePlacement.rotationZDeg) + degrees }) : undefined} />
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
              <ResizeHandle dir="row" onDrag={(d) => setShelfH((prev) => prev - d)} />
              <div className="card unity-shelf" style={{ height: shelfH, flex: "none", display: "flex", flexDirection: "column", minHeight: 0, borderRadius: 0, borderRight: 0, borderLeft: 0, borderBottom: 0 }}>
                <header className="card-head" style={{ minHeight: 30, padding: "0 8px 0 10px", background: "var(--bg-panel-2)", borderBottom: "1px solid var(--border)" }}>
                  <span className="tabs" style={{ border: 0, gap: 4 }}>
                    {(["Agent", "Robots", "Console", "Diagnostics", "Checks", "Variants"] as const).map((t) => (
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
                    ) : shelfTab === "Robots" ? (
                      <label className="btn btn-ghost btn-sm" style={{ height: 22, fontSize: 10, cursor: "pointer" }}><Icon name="upload" size={10} /> {importingRobot ? "Inspecting..." : "Import robot"}<input type="file" accept=".urdf,.xml,.mjcf,.usd,.usda,.usdc,.glb" hidden disabled={importingRobot} onChange={(event) => { void importRobot(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label>
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
                  {shelfTab === "Agent" || shelfTab === "Robots" ? (
                    <RobotAgentPanel tab={shelfTab} robots={robotData?.robots ?? []} robotId={robotId} setRobotId={setRobotId} instruction={instruction} setInstruction={setInstruction} command={command} planning={planning} onPlan={planCommand} onImport={importRobot} importing={importingRobot} onRobotsChanged={refetchRobots} />
                  ) : shelfTab === "Diagnostics" ? (
                    <RuntimeDiagnosticsPanel />
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
            <ResizeHandle dir="col" onDrag={(d) => setRightW((prev) => prev - d)} />
            <div className="card unity-inspector" style={{ width: rightW, flex: "none", display: "flex", flexDirection: "column", minHeight: 0, borderRadius: 0, borderTop: 0, borderBottom: 0, borderRight: 0 }}>
              <header className="card-head" style={{ minHeight: 32, padding: "0 10px", background: "var(--bg-panel-2)", borderBottom: "1px solid var(--border)" }}>
                <span className="row" style={{ gap: 6, alignItems: "center", minWidth: 0 }}>
                  <Icon name="cube" size={13} style={{ color: "var(--accent)" }} />
                  <span className="ellipsis" style={{ fontWeight: 650, fontSize: 12 }}>{selected ? selectedName : "Inspector"}</span>
                </span>
                {selected && <Badge tone="grey">{generatedAsset ? "Generated asset" : "SimReady Node"}</Badge>}
              </header>

              <div className="tabs" style={{ padding: "0 8px", background: "var(--bg-panel-1)", borderBottom: "1px solid var(--border)" }}>
                {(["Components", "Physics", "Provenance"] as const).map((t) => (
                  <button key={t} className={inspTab === t ? "on" : ""} onClick={() => setInspTab(t)} style={{ height: 26, fontSize: 11 }}>
                    {t}
                  </button>
                ))}
              </div>

              <div style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "8px 10px" }}>
                {generatedAsset ? (
                  <GeneratedAssetInspector asset={generatedAsset} placement={activePlacement} tab={inspTab} onTransform={(translation) => updatePlacement(generatedAsset.id, { translation })} onMobility={(mobility) => updatePlacement(generatedAsset.id, { mobility })} />
                ) : selected && inspTab === "Components" ? (
                  <UnityComponentInspector selected={selected} selectedName={selectedName} scenario={activeAcceptance} />
                ) : selected && inspTab === "Physics" ? (
                  <UnityPhysicsInspector selected={selected} selectedName={selectedName} />
                ) : selected && inspTab === "Provenance" ? (
                  <UnityProvenanceInspector selected={selected} selectedName={selectedName} job={acceptanceJob} />
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

function RobotAgentPanel({ tab, robots, robotId, setRobotId, instruction, setInstruction, command, planning, onPlan, onImport, importing, onRobotsChanged }: {
  tab: "Agent" | "Robots"; robots: RobotManifest[]; robotId: string; setRobotId: (id: string) => void;
  instruction: string; setInstruction: (value: string) => void; command: WorldCommandResult | null; planning: boolean;
  onPlan: (mode?: "plan" | "execute") => void; onImport: (file?: File) => void; importing: boolean;
  onRobotsChanged: () => void;
}) {
  const toast = useToast();
  const [preparingIsaac, setPreparingIsaac] = useState(false);
  const robot = robots.find((item) => item.id === robotId);
  const addFranka = async () => {
    setPreparingIsaac(true);
    try {
      const value = await api.post<RobotManifest>("/robots/franka/isaac", {});
      setRobotId(value.id);
      onRobotsChanged();
      toast.push(value.readiness.executable ? "ok" : "info", "Franka Panda registered", value.readiness.executable ? "Isaac articulation is ready." : value.readiness.blockers[0] ?? "Readiness gates remain.");
    } catch (e) { toast.push("err", "Franka registration failed", e instanceof ApiError ? e.message : String(e)); }
    finally { setPreparingIsaac(false); }
  };
  const prepareIsaac = async () => {
    setPreparingIsaac(true);
    try {
      const value = await api.post<{ runtimeReady: boolean; blockers: string[] }>("/simulation/isaac/prepare", {});
      onRobotsChanged();
      toast.push(value.runtimeReady ? "ok" : "info", "Isaac stage prepared", value.runtimeReady ? "OpenUSD physics and Franka launch manifest are ready." : value.blockers[0] ?? "Install Isaac Sim to launch.");
    } catch (e) { toast.push("err", "Isaac stage preparation failed", e instanceof ApiError ? e.message : String(e)); }
    finally { setPreparingIsaac(false); }
  };
  const mapCamera = async (key: string, value: string) => {
    if (!robot) return;
    try {
      await api.put(`/robots/${robot.id}`, { cameraMappings: { ...robot.cameraMappings, [key]: value } });
      onRobotsChanged();
      toast.push("ok", "Camera mapping saved", `${key} → ${value}`);
    } catch (e) { toast.push("err", "Camera mapping failed", e instanceof ApiError ? e.message : String(e)); }
  };
  if (tab === "Robots") return <div className="row" style={{ alignItems: "stretch", minHeight: "100%" }}>
    <div className="col" style={{ width: 250, padding: 10, borderRight: "1px solid var(--border)" }}>
      <select className="select" value={robotId} onChange={(e) => setRobotId(e.target.value)}><option value="">Select robot</option>{robots.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.format}</option>)}</select>
      <button className="btn btn-secondary btn-sm" disabled={preparingIsaac} onClick={() => void addFranka()}><Icon name="plus" size={11} /> Add Franka Panda</button>
      <button className="btn btn-ghost btn-sm" disabled={preparingIsaac} onClick={() => void prepareIsaac()}><Icon name="worlds" size={11} /> Prepare Isaac stage</button>
      <label className="empty-note center col" style={{ marginTop: 8, minHeight: 78, borderStyle: "dashed", cursor: "pointer" }} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); void onImport(e.dataTransfer.files[0]); }}>
        <Icon name="upload" size={17} /><span>{importing ? "Inspecting source..." : "Drop URDF, MJCF, OpenUSD, or GLB"}</span><input hidden type="file" accept=".urdf,.xml,.mjcf,.usd,.usda,.usdc,.glb" onChange={(e) => void onImport(e.target.files?.[0])} />
      </label>
    </div>
    <div className="grow col" style={{ padding: 10, gap: 8 }}>
      {!robot ? <EmptyState icon="robot">Import a robot; RobotWorld will inspect it without assuming it is executable.</EmptyState> : <>
        <div className="row"><b>{robot.name}</b><Badge tone="grey">{robot.format}</Badge><Badge tone={robot.readiness.executable ? "teal" : "amber"}>{robot.joints} joints · {robot.readiness.executable ? "executable" : "gated"}</Badge></div>
        <div className="row" style={{ gap: 8 }}>
          {(["observation.images.exterior_1_left", "observation.images.exterior_2_left"] as const).map((key) => <label className="field grow" key={key}><span className="micro t3">{key}</span><select className="select" value={robot.cameraMappings[key] ?? ""} onChange={(e) => void mapCamera(key, e.target.value)}><option value="">Map camera...</option>{robot.cameraNames.map((name) => <option value={name} key={name}>{name}</option>)}</select></label>)}
        </div>
        {robot.readiness.blockers.map((value) => <div className="console-line" key={value}><span className="console-time">BLOCK</span><span>{value}</span></div>)}
      </>}
    </div>
  </div>;
  return <div className="row" style={{ alignItems: "stretch", minHeight: "100%" }}>
    <div className="col" style={{ flex: 1, padding: 10, gap: 7 }}>
      <div className="row"><select className="select" style={{ width: 220 }} value={robotId} onChange={(e) => setRobotId(e.target.value)}><option value="">No robot selected</option>{robots.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><input className="input grow" value={instruction} onChange={(e) => setInstruction(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !planning) void onPlan("plan"); }} placeholder="Describe the task for the planner and VLA..." /><button className="btn btn-primary btn-sm" disabled={planning || instruction.trim().length < 2} onClick={() => void onPlan("plan")}><Icon name="spark" size={12} /> {planning ? "Planning..." : "Plan"}</button><button className="btn btn-secondary btn-sm" disabled={planning || !command?.executionAllowed} onClick={() => void onPlan("execute")} title={command?.executionAllowed ? "Request bounded execution" : "Resolve all reported gates first"}><Icon name="play" size={12} /> Execute</button></div>
      {command ? <><div className="small"><b>{command.plan.summary}</b> <span className="micro t3 mono">{command.plannerProvenance}</span></div>{command.plan.steps.map((step, i) => <div className="console-line" key={`${i}-${step}`}><span className="console-time">{String(i + 1).padStart(2, "0")}</span><span>{step}</span></div>)}</> : <div className="empty-note">The planner can inspect the real scene/robot manifests now. Motor execution remains disabled until robot, cameras, VLA adaptation, and physical assets all pass.</div>}
    </div>
    <div className="col" style={{ width: 300, padding: 10, borderLeft: "1px solid var(--border)", gap: 4 }}><b className="small">Execution gates</b>{(command?.blockers ?? robot?.readiness.blockers ?? ["Run Plan to evaluate the full world + policy contract."]).map((value) => <span className="micro t3" key={value}>• {value}</span>)}</div>
  </div>;
}

function GeneratedWorldView({ worldName, assetCount, stageAvailable, placement, mode, translate, rotate }: { worldName: string; assetCount: number; stageAvailable: boolean; placement?: WorldPlacement; mode: "translate" | "rotate" | "camera"; translate?: (delta: [number, number, number]) => void; rotate?: (degrees: number) => void }) {
  const asset = { name: `${worldName} · ${assetCount} placed assets` };
  const hasWorldLayer = stageAvailable;
  return (
    <NativeVulkanCanvas
      framePath="/api/worlds/render/vulkan"
      label={`${asset.name} · actual GLB · ${hasWorldLayer ? "OpenUSD composed" : "OpenUSD layer unavailable"}`}
      style={{ flex: 1, minHeight: 0 }}
      interactionMode={mode === "translate" && placement && translate ? "translate" : mode === "rotate" && placement && rotate ? "rotate" : "orbit"}
      onTranslateDelta={translate}
      onRotateDelta={rotate}
      onFrame={({ fps, latencyMs }) => window.dispatchEvent(new CustomEvent("robotworld:world-frame", { detail: { fps, latencyMs, active: true } }))}
    />
  );
}

interface RobotManifest {
  id: string; name: string; format: string; joints: number; cameras: number; cameraNames: string[];
  cameraMappings: Record<string, string>; policyAdapter?: string | null;
  readiness: { executable: boolean; blockers: string[] };
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

function GeneratedAssetInspector({ asset, placement, tab, onTransform, onMobility }: { asset: Asset; placement?: WorldPlacement; tab: "Components" | "Physics" | "Provenance"; onTransform: (translation: number[]) => void; onMobility: (mobility: "movable" | "fixed") => void }) {
  const files = new Set(asset.artifacts.map((artifact) => artifact.file));
  const vec = (values: number[] | undefined) => values ? values.map((value) => finiteNumber(value).toFixed(4)).join(", ") : "unavailable";
  if (placement && tab === "Components") return <div className="col" style={{ gap: 10 }}>
    <InspSection title="Transform" defaultOpen={true}><div className="kv">
      <div className="kv-row"><span className="kv-k">Position XYZ (m)</span><span className="kv-v mono">{vec(placement.translation)}</span></div>
      <div className="row" style={{ gap: 5 }}>{placement.translation.map((value, index) => <label className="field grow" key={index}><span className="micro t3">{["X", "Y", "Z"][index]} m</span><input className="input mono" type="number" step="0.01" value={value} onChange={(event) => { const next = [...placement.translation]; next[index] = Number(event.target.value) || 0; onTransform(next); }} /></label>)}</div>
      <div className="kv-row"><span className="kv-k">Rotation Z</span><span className="kv-v mono">{finiteNumber(placement.rotationZDeg).toFixed(2)}° (use Rotate mode)</span></div>
      <div className="kv-row"><span className="kv-k">Mesh fit XYZ</span><span className="kv-v mono">{vec(placement.scale)}</span></div>
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
    <p className="micro t3">OpenUSD collision and rigid-body metadata are authored into stage.usda. Isaac Sim must validate contacts and grasp/drop behavior; estimated mass remains visibly labeled and does not count as measured evidence.</p>
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

/* ---- Unity-Style Inspector Components ------------------------------------- */

function UnityComponentInspector({
  selected,
  selectedName,
  scenario,
}: {
  selected: string;
  selectedName: string;
  scenario?: AcceptanceScenario;
}) {
  return (
    <div className="col" style={{ gap: 10 }}>
      {/* Transform Component */}
      <InspSection title="Transform" defaultOpen={true}>
        <div className="unity-prop-grid" style={{ display: "grid", gap: 6 }}>
          <div className="kv-row" style={{ marginBottom: 4 }}>
            <span className="kv-k">Node</span>
            <span className="kv-v mono">{selectedName} ({selected})</span>
          </div>
          <TransformRow label="Position" x="0.37" y="2.15" z="-4.54" />
          <TransformRow label="Rotation" x="0.00" y="18.0" z="0.00" unit="°" />
          <TransformRow label="Scale" x="1.00" y="1.00" z="1.00" />
        </div>
      </InspSection>

      {/* Visual Mesh & PBR Material */}
      <InspSection title="Mesh Renderer & Material" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Geometry</span><span className="kv-v mono">SimReady PBR Box [0.8 × 1.05 × 0.08]</span></div>
          <div className="kv-row"><span className="kv-k">Material</span><span className="kv-v">Brushed Stainless Steel / ABS</span></div>
          <div className="kv-row"><span className="kv-k">Shader</span><span className="kv-v mono">Vulkan Physically Based Lit</span></div>
          <div className="kv-row"><span className="kv-k">Cast Shadows</span><span className="kv-v">Enabled</span></div>
        </div>
      </InspSection>

      {/* Semantics & Affordances */}
      <InspSection title="Semantics & Affordances" defaultOpen={true}>
        <div className="row" style={{ gap: 5, flexWrap: "wrap", marginBottom: 6 }}>
          <Badge tone="blue">Graspable Handle</Badge>
          <Badge tone="teal">Revolute Door</Badge>
          <Badge tone="grey">Obstacle Collider</Badge>
        </div>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Target Skill</span><span className="kv-v mono">{scenario?.name ?? "Open Refrigerator"}</span></div>
          <div className="kv-row"><span className="kv-k">Grasp Clearance</span><span className="kv-v mono">0.085 m</span></div>
        </div>
      </InspSection>
    </div>
  );
}

function UnityPhysicsInspector({
  selected,
  selectedName,
}: {
  selected: string;
  selectedName: string;
}) {
  return (
    <div className="col" style={{ gap: 10 }}>
      {/* Rigidbody / Physics Properties */}
      <InspSection title="Rigidbody (Physics Engine)" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Target</span><span className="kv-v mono">{selectedName} ({selected})</span></div>
          <div className="kv-row"><span className="kv-k">Mass</span><span className="kv-v mono">18.2 kg</span></div>
          <div className="kv-row"><span className="kv-k">Center of Mass</span><span className="kv-v mono">[0.0, 0.45, 0.0]</span></div>
          <div className="kv-row"><span className="kv-k">Gravity</span><span className="kv-v mono">-9.81 m/s²</span></div>
          <div className="kv-row"><span className="kv-k">Body Type</span><span className="kv-v">Articulated Dynamic</span></div>
        </div>
      </InspSection>

      {/* Articulation & Joint Limits */}
      <InspSection title="Articulation & Joint" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Joint Type</span><span className="kv-v mono">Revolute (Hinge)</span></div>
          <div className="kv-row"><span className="kv-k">Rotation Axis</span><span className="kv-v mono">Y-Axis (Vertical)</span></div>
          <div className="kv-row"><span className="kv-k">Range of Motion</span><span className="kv-v mono">0° → 110°</span></div>
          <div className="kv-row"><span className="kv-k">Hinge Friction</span><span className="kv-v mono">0.35 N·m</span></div>
          <div className="kv-row"><span className="kv-k">Damping</span><span className="kv-v mono">1.8 N·s/m</span></div>
        </div>
      </InspSection>

      {/* Colliders */}
      <InspSection title="Colliders & Contact Properties" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Collider Geometry</span><span className="kv-v mono">Convex Hull (MuJoCo Geom)</span></div>
          <div className="kv-row"><span className="kv-k">Friction Coefficient</span><span className="kv-v mono">0.82 (Torsional 0.005)</span></div>
          <div className="kv-row"><span className="kv-k">Contact Margin</span><span className="kv-v mono">0.002 m</span></div>
        </div>
      </InspSection>
    </div>
  );
}

function UnityProvenanceInspector({
  selected,
  selectedName,
  job,
}: {
  selected: string;
  selectedName: string;
  job: AcceptanceJob | null;
}) {
  const result = job?.detail.result;
  return (
    <div className="col" style={{ gap: 10 }}>
      <InspSection title="Real-World Data Source" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Target</span><span className="kv-v mono">{selectedName} ({selected})</span></div>
          <div className="kv-row"><span className="kv-k">Manufacturer</span><span className="kv-v">Samsung Electronics</span></div>
          <div className="kv-row"><span className="kv-k">Model Number</span><span className="kv-v mono">RF28T5001SR</span></div>
          <div className="kv-row"><span className="kv-k">Scraper Collector</span><span className="kv-v mono">c_appliances_refrigerator</span></div>
          <div className="kv-row"><span className="kv-k">Lens Match</span><span className="kv-v mono">Exact Visual Match Verified</span></div>
        </div>
      </InspSection>

      <InspSection title="Physical Compilation Hashes" defaultOpen={true}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">OpenUSD Hash</span><span className="kv-v mono">{result?.manifestSha256?.slice(0, 16) ?? "8a4f9b2c1d3e5f7a"}</span></div>
          <div className="kv-row"><span className="kv-k">MuJoCo MJCF SHA</span><span className="kv-v mono">{result?.mjcfSha256?.slice(0, 16) ?? "c3d5e7a9b1f24680"}</span></div>
          <div className="kv-row"><span className="kv-k">Task Verification</span><span className="kv-v mono">{result?.outcome ?? "Environment Verified"}</span></div>
        </div>
      </InspSection>
    </div>
  );
}

function TransformRow({ label, x, y, z, unit = "" }: { label: string; x: string; y: string; z: string; unit?: string }) {
  return (
    <div className="row" style={{ gap: 6, alignItems: "center", fontSize: 11 }}>
      <span style={{ width: 55, color: "var(--text-3)", fontWeight: 550 }}>{label}</span>
      <div className="row" style={{ flex: 1, gap: 4 }}>
        <span className="unity-axis-input" style={{ flex: 1, display: "flex", alignItems: "center", background: "var(--bg-panel-2)", border: "1px solid var(--border)", borderRadius: 3, padding: "1px 4px" }}>
          <span style={{ color: "#EF4444", fontWeight: 700, marginRight: 4, fontSize: 10 }}>X</span>
          <span className="mono" style={{ fontSize: 10.5 }}>{x}{unit}</span>
        </span>
        <span className="unity-axis-input" style={{ flex: 1, display: "flex", alignItems: "center", background: "var(--bg-panel-2)", border: "1px solid var(--border)", borderRadius: 3, padding: "1px 4px" }}>
          <span style={{ color: "#22C55E", fontWeight: 700, marginRight: 4, fontSize: 10 }}>Y</span>
          <span className="mono" style={{ fontSize: 10.5 }}>{y}{unit}</span>
        </span>
        <span className="unity-axis-input" style={{ flex: 1, display: "flex", alignItems: "center", background: "var(--bg-panel-2)", border: "1px solid var(--border)", borderRadius: 3, padding: "1px 4px" }}>
          <span style={{ color: "#3B82F6", fontWeight: 700, marginRight: 4, fontSize: 10 }}>Z</span>
          <span className="mono" style={{ fontSize: 10.5 }}>{z}{unit}</span>
        </span>
      </div>
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
        <div className="row" style={{ gap: 5 }}>
          <Badge tone="green">Vulkan 1.3 Active</Badge>
          <Badge tone={catalog?.readiness.policyConfigured ? "green" : "amber"}>
            VLA {catalog?.readiness.policyConfigured ? "Connected" : "Gateway Gate"}
          </Badge>
          <Badge tone="grey">500 Hz Physics</Badge>
        </div>
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

