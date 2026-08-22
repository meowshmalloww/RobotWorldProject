import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { DonutGauge } from "../components/charts/DonutGauge";
import { Modal, downloadFile } from "../components/ui/Modal";
import { useToast } from "../components/ui/Toast";
import { api, apiUrl, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import { AssetVariantViewer } from "../components/three/AssetVariantViewer";
import type { Asset, Source, Stat } from "../data/types";

const KIND_LABEL = { articulated: "Articulated", rigid: "Rigid", environment: "Environment" } as const;
const KIND_ICON = { articulated: "joint", rigid: "cube", environment: "worlds" } as const;
const PAGE_SIZE = 20;

interface AssetsData {
  assets: Asset[];
  stats: Stat[];
}

interface BuildSourcesData { sources: Source[] }

interface CompiledAssetVersion {
  id: string;
  assetId: string;
  version: number;
  displayName: string;
  category: string;
  lifecycleState: string;
  sourceSha256: string;
  manifestSha256?: string | null;
  manifest: {
    dimensionsM?: number[];
    massKg?: number;
    sourceVisual?: { sizeBytes: number };
  };
  validationReport: {
    staticValidation?: { sourceGeometry?: { triangles?: number; maxAspectResidual?: number } };
    collision?: { triangles?: number };
    physicsValidation?: {
      passed?: boolean;
      maxPenetrationM?: number;
      settlePositionSpanM?: number;
      deterministicRepeatMaxQposError?: number;
      previewGenerated?: boolean;
    };
    oracleValidation?: {
      evaluationId: string;
      success: boolean;
      failureCode?: string | null;
      seed: number;
      predicate?: { targetErrorM?: number; settleRotationSpanRad?: number };
    };
  };
  validationErrors: string[];
  promotionEligible: boolean;
  promotionBlockers: string[];
  createdAt: string;
}

interface CompiledAssetsData { assetVersions: CompiledAssetVersion[] }

interface CompileEnvelope {
  commandId: string;
  status: "SUCCEEDED" | "FAILED";
  error?: string | null;
  result: { assetVersion?: CompiledAssetVersion };
}

interface RobotListResponse {
  robots: { id: string; name: string }[];
  registrations: { id: string; lifecycleState: string; active: boolean }[];
}

interface ModelListResponse {
  models: {
    id: string;
    displayName: string;
    roles: string[];
    lifecycleState: string;
    healthStatus: string;
    enabled: boolean;
  }[];
}

interface TrellisQ4Proof {
  model: string;
  runtime: string;
  device: string;
  seed: number;
  geometryResolution: number;
  textureResolution: number;
  durationSeconds: number;
  sizeBytes: number;
  sha256: string;
  vertices: number;
  faces: number;
  pbrMaterialCount: number;
  textureSemantics: string[];
  truth: string;
  sourceUrl: string;
  images: { conditioning: string; baseColor: string };
}

interface OracleEnvelope {
  commandId: string;
  status: "SUCCEEDED" | "FAILED";
  result: {
    evaluation: {
      id: string;
      status: string;
      success: boolean;
      failureCode?: string | null;
      failureDetail?: string | null;
    };
  };
}

export default function Assets() {
  const nav = useNavigate();
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<AssetsData>("/assets");
  const { data: sourceData } = useApi<BuildSourcesData>("/sources");
  const {
    data: compiledData,
    error: compiledError,
    loading: compiledLoading,
    refetch: refetchCompiled,
  } = useApi<CompiledAssetsData>("/asset-versions");
  const { data: robotData } = useApi<RobotListResponse>("/robots");
  const { data: modelData } = useApi<ModelListResponse>("/models");
  const { data: q4Proof } = useApi<TrellisQ4Proof>("/trellis/q4-proof");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("All types");
  const [status, setStatus] = useState("All status");
  const [page, setPage] = useState(1);
  const [newBuild, setNewBuild] = useState(false);
  const [building, setBuilding] = useState(false);
  const [smokeRunning, setSmokeRunning] = useState(false);
  const [newPhysicalCompile, setNewPhysicalCompile] = useState(false);
  const [compilingPhysical, setCompilingPhysical] = useState(false);
  const [sourceGlbPath, setSourceGlbPath] = useState("");
  const [physicalName, setPhysicalName] = useState("");
  const [physicalCategory, setPhysicalCategory] = useState("rigid_object");
  const [dimensions, setDimensions] = useState("0.20, 0.20, 0.20");
  const [massKg, setMassKg] = useState("1.0");
  const [identityScope, setIdentityScope] = useState<"exact" | "category_prior" | "unknown">("unknown");
  const [evidenceBundleId, setEvidenceBundleId] = useState("");
  const [dimensionConfidence, setDimensionConfidence] = useState("0.3");
  const [massConfidence, setMassConfidence] = useState("0.3");
  const [licenseSource, setLicenseSource] = useState("");
  const [redistribution, setRedistribution] = useState("unknown");
  const [oracleRobotId, setOracleRobotId] = useState("");
  const [oracleRunning, setOracleRunning] = useState<string | null>(null);
  const [vlaModelId, setVlaModelId] = useState("");
  const [vlaRunning, setVlaRunning] = useState<string | null>(null);
  const [vlaInstruction, setVlaInstruction] = useState("Pick up the object and place it in the target.");
  const [showPhysicalVersions, setShowPhysicalVersions] = useState(false);
  const [showQ4Proof, setShowQ4Proof] = useState(false);

  const assets = useMemo(() => data?.assets ?? [], [data]);
  const compiledVersions = useMemo(() => compiledData?.assetVersions ?? [], [compiledData]);
  const availableRobots = useMemo(
    () => (robotData?.registrations ?? []).filter((item) => item.active && item.lifecycleState === "AVAILABLE"),
    [robotData],
  );
  const vlaModels = useMemo(
    () => (modelData?.models ?? []).filter((item) => item.roles.includes("vla_policy")),
    [modelData],
  );
  const selectedVla = vlaModels.find((item) => item.id === vlaModelId) ?? null;
  useEffect(() => {
    if (!oracleRobotId && availableRobots.length) setOracleRobotId(availableRobots[0].id);
  }, [availableRobots, oracleRobotId]);
  useEffect(() => {
    if (!vlaModelId && vlaModels.length) {
      const loaded = vlaModels.find((item) => item.enabled && item.lifecycleState === "LOADED" && item.healthStatus === "healthy");
      setVlaModelId((loaded ?? vlaModels[0]).id);
    }
  }, [vlaModelId, vlaModels]);
  const filtered = useMemo(
    () =>
      assets.filter(
        (a) =>
          a.name.toLowerCase().includes(q.toLowerCase()) &&
          (kind === "All types" || KIND_LABEL[a.kind] === kind) &&
          (status === "All status" || a.status === status),
      ),
    [assets, q, kind, status],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // New build form state
  const queryRef = useRef<HTMLInputElement>(null);
  const [buildKind, setBuildKind] = useState<"articulated" | "rigid">("articulated");
  const [generator, setGenerator] = useState("parametric");
  const [sourceId, setSourceId] = useState("");
  const familiesRef = useRef<HTMLInputElement>(null);

  const startBuild = async () => {
    const query = queryRef.current?.value.trim();
    if (!query) {
      toast.push("err", "Object query required", "Describe the object to build, e.g. a model number");
      return;
    }
    setBuilding(true);
    try {
      const { assetId } = await api.post<{ assetId: string }>("/assets/build", {
        query,
        kind: buildKind,
        generator,
        sourceId: sourceId || null,
        families: familiesRef.current?.value.split(",").map((s) => s.trim()).filter(Boolean) ?? [],
      });
      setNewBuild(false);
      toast.push("ok", "Legacy asset build queued", "Source discovery and generation started; use the canonical physical compiler before simulation use.");
      // poll until the asset leaves the building state
      const poll = async (attempt = 0) => {
        if (attempt >= 120) {
          toast.push("info", "Build still running", "Polling stopped after four minutes; the job remains visible in Overview.");
          return;
        }
        try {
          const a = await api.get<Asset>(`/assets/${assetId}`);
          if (a.status === "building" || a.status === "draft") {
            setTimeout(() => poll(attempt + 1), Math.min(2000 + attempt * 100, 5000));
          } else {
            refetch();
            toast.push(a.status === "ready" ? "ok" : "info", "Asset build finished", `${a.name} · ${a.status}`);
          }
        } catch {
          setTimeout(() => poll(attempt + 1), Math.min(2000 + attempt * 100, 5000));
        }
      };
      setTimeout(poll, 2000);
      refetch();
    } catch (e) {
      toast.push("err", "Asset build failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setBuilding(false);
    }
  };

  const compilePhysicalAsset = async () => {
    const parsedDimensions = dimensions.split(",").map((value) => Number(value.trim()));
    const parsedMass = Number(massKg);
    const parsedDimensionConfidence = Number(dimensionConfidence);
    const parsedMassConfidence = Number(massConfidence);
    if (!sourceGlbPath.trim() || !physicalName.trim() || parsedDimensions.length !== 3 || parsedDimensions.some((value) => !Number.isFinite(value) || value <= 0)) {
      toast.push("err", "Invalid physical compile input", "Provide a local allowlisted GLB path, display name, and width, height, depth in metres.");
      return;
    }
    if (!Number.isFinite(parsedMass) || parsedMass <= 0 || ![parsedDimensionConfidence, parsedMassConfidence].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
      toast.push("err", "Invalid physical properties", "Mass must be positive and confidence values must be between 0 and 1.");
      return;
    }
    if (identityScope === "exact" && !evidenceBundleId.trim()) {
      toast.push("err", "Exact evidence required", "Link a QUALITY_PASSED evidence bundle before claiming exact identity.");
      return;
    }
    setCompilingPhysical(true);
    try {
      const envelope = await api.post<CompileEnvelope>("/asset-versions/rigid", {
        displayName: physicalName.trim(),
        category: physicalCategory.trim() || "rigid_object",
        sourceGlbPath: sourceGlbPath.trim(),
        evidenceBundleId: evidenceBundleId.trim() || null,
        sourceIdentityScope: identityScope,
        dimensionsM: parsedDimensions,
        dimensionMethod: identityScope === "exact" ? "linked_evidence" : "user_declared_prior",
        dimensionConfidence: parsedDimensionConfidence,
        massKg: parsedMass,
        massMethod: identityScope === "exact" ? "linked_evidence" : "user_declared_prior",
        massConfidence: parsedMassConfidence,
        frictionRange: [0.3, 0.8],
        restitutionRange: [0.0, 0.1],
        semantics: [physicalCategory.trim().toLowerCase().replace(/\s+/g, "_") || "rigid_object"],
        affordances: [],
        licenseMetadata: {
          source: licenseSource.trim() || "unknown",
          redistribution,
        },
      });
      await refetchCompiled();
      const version = envelope.result.assetVersion;
      if (envelope.status === "SUCCEEDED" && version) {
        setNewPhysicalCompile(false);
        toast.push("ok", "Physics candidate validated", `${version.id} · MuJoCo drop/settle passed; promotion gates remain explicit.`);
      } else {
        toast.push("err", "Physics candidate rejected", envelope.error || version?.validationErrors.join("; ") || "Validation failed.");
      }
    } catch (e) {
      toast.push("err", "Physical compile failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setCompilingPhysical(false);
    }
  };

  const runPhysicalOracle = async (version: CompiledAssetVersion) => {
    if (!oracleRobotId) {
      toast.push("err", "No active robot", "Activate an AVAILABLE Franka embodiment on the Robots page first.");
      return;
    }
    setOracleRunning(version.id);
    try {
      const envelope = await api.post<OracleEnvelope>("/evaluations/oracle/compiled-asset-pick-place", {
        robotId: oracleRobotId,
        assetVersionId: version.id,
        seed: 6203,
      });
      await refetchCompiled();
      const evaluation = envelope.result.evaluation;
      if (evaluation.success) {
        toast.push("ok", "Franka oracle passed", `${evaluation.id} · ${version.displayName} is ORACLE_VALIDATED`);
      } else {
        toast.push("err", `Oracle failed · ${evaluation.failureCode ?? "unknown"}`, evaluation.failureDetail ?? evaluation.id);
      }
    } catch (reason) {
      toast.push("err", "Oracle command failed", reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setOracleRunning(null);
    }
  };

  const runPhysicalVla = async (version: CompiledAssetVersion) => {
    if (!oracleRobotId || !selectedVla) {
      toast.push("err", "VLA prerequisites missing", "Select an active Franka and a registered VLA policy.");
      return;
    }
    setVlaRunning(version.id);
    try {
      const envelope = await api.post<OracleEnvelope>("/evaluations/vla/compiled-asset-pick-place", {
        robotId: oracleRobotId,
        modelId: selectedVla.id,
        assetVersionId: version.id,
        instruction: vlaInstruction.trim(),
        maxPolicySteps: 150,
        seed: 6203,
      });
      const evaluation = envelope.result.evaluation;
      toast.push(
        evaluation.success ? "ok" : "err",
        evaluation.success ? "VLA evaluation passed" : `VLA failed · ${evaluation.failureCode ?? "unknown"}`,
        evaluation.failureDetail ?? evaluation.id,
      );
    } catch (reason) {
      toast.push("err", "VLA evaluation blocked or failed", reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setVlaRunning(null);
    }
  };

  const waitForAsset = async (assetId: string, onDone?: (asset: Asset) => void) => {
    for (let attempt = 0; attempt < 160; attempt++) {
      try {
        const asset = await api.get<Asset>(`/assets/${assetId}`);
        if (asset.status !== "building" && asset.status !== "draft") {
          if (onDone) onDone(asset);
          return asset;
        }
      } catch {
        // keep polling on transient read failures
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
    throw new Error("Build did not finish within the smoke-test timeout window.");
  };

  const startTrellisSmokeTest = async () => {
    setSmokeRunning(true);
    try {
      const { assetId } = await api.post<{ assetId: string }>("/assets/build", {
        query: "kitchen blender",
        kind: "rigid",
        generator: "trellis2",
        sourceId: null,
        families: ["smoke-test", "trellis2"],
      });
      toast.push("ok", "TRELLIS smoke run started", `asset ${assetId} is running one-image TRELLIS generation`);
      const asset = await waitForAsset(assetId, (a) => {
        if (a.status === "ready" || a.status === "testing" || a.status === "blocked") {
          toast.push("ok", "TRELLIS smoke run complete", `${a.name} â€” ${a.status}`);
        }
      });
      refetch();
      nav(`/assets/${asset.id}`);
    } catch (e) {
      toast.push("err", "TRELLIS smoke run failed", e instanceof Error ? e.message : e instanceof ApiError ? e.message : String(e));
    } finally {
      setSmokeRunning(false);
    }
  };

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!advancedOpen) return;
    const close = (event: MouseEvent) => {
      if (advancedRef.current && !advancedRef.current.contains(event.target as Node)) setAdvancedOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [advancedOpen]);

  return (
    <div className="page">
      <div className="page-head" style={{ marginBottom: 10 }}>
        <div>
          <div className="page-eyebrow">Asset factory</div>
          <h1 className="page-title">Assets</h1>
          <p className="page-sub">OpenUSD/MuJoCo objects compiled from provenance-bearing source data — geometry, physics, joints, semantics.</p>
        </div>
      </div>

      <div className="auto-banner rise" style={{ marginBottom: 12 }}>
        <span className="auto-ico"><Icon name="workflow" size={16} /></span>
        <span className="col grow" style={{ gap: 2, minWidth: 0 }}>
          <b style={{ fontSize: 12.5 }}>Automated pipeline</b>
          <span className="micro t3" style={{ lineHeight: 1.45 }}>
            One build runs the full chain: Bright Data source → TRELLIS.2 PBR generation → physical compile → MuJoCo drop/settle → Franka oracle → VLA evaluation. Manual stages below are advanced overrides, not required steps.
          </span>
        </span>
        <span className="row" style={{ gap: 8, flex: "none" }}>
          <button className="btn btn-secondary" onClick={() => setNewPhysicalCompile(true)}><Icon name="shield" size={13} /> Compile physical asset</button>
          <button className="btn btn-primary" onClick={() => setNewBuild(true)}><Icon name="plus" size={13} /> New asset build</button>
          <div className="row" style={{ position: "relative" }} ref={advancedRef}>
            <button className="btn btn-ghost" onClick={() => setAdvancedOpen((open) => !open)}><Icon name="dots" size={13} /> Advanced</button>
            {advancedOpen && (
              <div className="menu-pop" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, minWidth: 230, zIndex: "var(--z-menu)" }}>
                <button
                  className="menu-item"
                  disabled={assets.length === 0}
                  onClick={() => {
                    setAdvancedOpen(false);
                    downloadFile("asset-catalog.json", JSON.stringify(assets.map(({ id, name, kind: k, status: st, readiness, source }) => ({ id, name, kind: k, status: st, readiness, source })), null, 2));
                    toast.push("ok", "Catalog exported", `asset-catalog.json · ${assets.length} assets`);
                  }}
                >
                  <Icon name="download" size={13} /> Export catalog (JSON)
                </button>
                <button
                  className="menu-item"
                  disabled={smokeRunning}
                  onClick={() => { setAdvancedOpen(false); void startTrellisSmokeTest(); }}
                >
                  <Icon name="spark" size={13} /> {smokeRunning ? "TRELLIS smoke in progress…" : "Run TRELLIS smoke build"}
                </button>
              </div>
            )}
          </div>
        </span>
      </div>

      {q4Proof && (
        <Card
          title="Local TRELLIS.2 Q4 result"
          right={<button className="btn btn-secondary btn-sm" onClick={() => setShowQ4Proof((value) => !value)}><Icon name="cube" size={11} /> {showQ4Proof ? "Hide preview" : "View real GLB"}</button>}
          style={{ marginBottom: 10 }}
        >
          <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
            <span className="small"><b>{q4Proof.durationSeconds.toFixed(1)} s</b> measured generation</span>
            <span className="small mono">{q4Proof.vertices.toLocaleString()} vertices · {q4Proof.faces.toLocaleString()} faces</span>
            <span className="small">{q4Proof.textureResolution}px PBR · {(q4Proof.sizeBytes / 1024 ** 2).toFixed(1)} MB</span>
            <span className="micro t3 mono">seed {q4Proof.seed} · {q4Proof.runtime}</span>
          </div>
          {showQ4Proof && <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 2fr) repeat(2, minmax(150px, 1fr))", gap: 10, marginTop: 10 }}>
            <AssetVariantViewer url={apiUrl(q4Proof.sourceUrl.replace(/^\/api/, ""))} label="Recorded local TRELLIS Q4 PBR GLB" />
            <figure style={{ margin: 0 }}><img src={apiUrl(q4Proof.images.conditioning.replace(/^\/api/, ""))} alt="TRELLIS conditioning image" style={{ width: "100%", height: 230, objectFit: "contain", background: "#050505", border: "1px solid var(--border)" }} /><figcaption className="micro t3" style={{ marginTop: 4 }}>Conditioning cutout</figcaption></figure>
            <figure style={{ margin: 0 }}><img src={apiUrl(q4Proof.images.baseColor.replace(/^\/api/, ""))} alt="TRELLIS baked base color" style={{ width: "100%", height: 230, objectFit: "contain", background: "#050505", border: "1px solid var(--border)" }} /><figcaption className="micro t3" style={{ marginTop: 4 }}>Baked base color</figcaption></figure>
          </div>}
          <div className="micro t3" style={{ marginTop: 8 }}>This is recorded local visual geometry with embedded PBR textures. It is not called physics-ready until the separate immutable physical compiler and oracle gates pass.</div>
        </Card>
      )}

      {newPhysicalCompile && (
        <Modal
          title="Compile immutable rigid asset"
          onClose={() => !compilingPhysical && setNewPhysicalCompile(false)}
          footer={
            <>
              <button className="btn btn-ghost" disabled={compilingPhysical} onClick={() => setNewPhysicalCompile(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={compilingPhysical} onClick={compilePhysicalAsset}>
                {compilingPhysical ? "Compiling and simulating…" : "Compile + validate"}
              </button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="empty-note">
              This sends a server-side path reference, not the GLB bytes. The path must be under <span className="mono">ROBOT_ASSET_ROOT</span> or the RobotWorld artifact store. The original GLB remains immutable and is never reused as the dynamic collider.
            </div>
            <div className="field"><label>Allowlisted local GLB path</label><input className="input mono" value={sourceGlbPath} onChange={(event) => setSourceGlbPath(event.target.value)} placeholder="D:\\assets\\generated\\model.glb" autoFocus /></div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow"><label>Display name</label><input className="input" value={physicalName} onChange={(event) => setPhysicalName(event.target.value)} placeholder="Exact product or honest category candidate" /></div>
              <div className="field grow"><label>Category</label><input className="input" value={physicalCategory} onChange={(event) => setPhysicalCategory(event.target.value)} /></div>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow"><label>Dimensions W, H, D (m)</label><input className="input mono" value={dimensions} onChange={(event) => setDimensions(event.target.value)} /></div>
              <div className="field grow"><label>Mass (kg)</label><input className="input mono" value={massKg} onChange={(event) => setMassKg(event.target.value)} /></div>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow">
                <label>Identity scope</label>
                <select className="select" value={identityScope} onChange={(event) => setIdentityScope(event.target.value as typeof identityScope)}>
                  <option value="unknown">Unknown</option>
                  <option value="category_prior">Category prior</option>
                  <option value="exact">Exact identity · evidence required</option>
                </select>
              </div>
              <div className="field grow"><label>Evidence bundle ID</label><input className="input mono" value={evidenceBundleId} onChange={(event) => setEvidenceBundleId(event.target.value)} placeholder={identityScope === "exact" ? "evb_… required" : "optional"} /></div>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow"><label>Dimension confidence (0–1)</label><input className="input mono" value={dimensionConfidence} onChange={(event) => setDimensionConfidence(event.target.value)} /></div>
              <div className="field grow"><label>Mass confidence (0–1)</label><input className="input mono" value={massConfidence} onChange={(event) => setMassConfidence(event.target.value)} /></div>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow"><label>Geometry/license source</label><input className="input" value={licenseSource} onChange={(event) => setLicenseSource(event.target.value)} placeholder="Repository, generator revision, or CAD source" /></div>
              <div className="field grow">
                <label>Redistribution state</label>
                <select className="select" value={redistribution} onChange={(event) => setRedistribution(event.target.value)}>
                  <option value="unknown">Unknown · blocks promotion</option>
                  <option value="review_required">Review required · blocks promotion</option>
                  <option value="allowed">Allowed</option>
                  <option value="cc-by">CC BY</option>
                  <option value="cc0">CC0</option>
                </select>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {newBuild && (
        <Modal
          title="New asset build"
          onClose={() => setNewBuild(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setNewBuild(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={startBuild} disabled={building}>{building ? "Starting…" : "Start build"}</button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="field"><label>Object query</label><input ref={queryRef} className="input" placeholder="e.g. Samsung RF28T5001SR refrigerator" autoFocus /></div>
            <div className="field">
              <label>Validated Scraper Studio source</label>
              <select className="select" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">None · use reference catalog or live SERP discovery</option>
                {(sourceData?.sources ?? []).map((s) => <option key={s.id} value={s.id}>{s.domain} · {s.collector} · {s.completeness}%</option>)}
              </select>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow">
                <label>Asset type</label>
                <select className="select" value={buildKind} onChange={(e) => setBuildKind(e.target.value as "articulated" | "rigid")}>
                  <option value="articulated">Articulated</option>
                  <option value="rigid">Rigid</option>
                </select>
              </div>
              <div className="field grow">
                <label>Generator</label>
                <select className="select" value={generator} onChange={(e) => setGenerator(e.target.value)}>
                  <option value="parametric">Legacy parametric build</option>
                  <option value="trellis2">TRELLIS.2 PBR visual generation · physical compile required</option>
                </select>
              </div>
            </div>
            <div className="field"><label>Scenario families</label><input ref={familiesRef} className="input" placeholder="e.g. left hinge, heavy door, low handle" /></div>
            {generator === "trellis2" && <div className="empty-note">TRELLIS.2 produces the visual PBR mesh only. RobotWorld still authors and validates articulation, collision proxies, mass, and joint physics from the selected structured source.</div>}
          </div>
        </Modal>
      )}

      <Card
        title={<span>Physical validation <span className="micro t3" style={{ marginLeft: 8 }}>advanced asset versions</span></span>}
        flush
        right={!showPhysicalVersions ? (
          <button className="btn btn-secondary btn-sm" onClick={() => setShowPhysicalVersions(true)}><Icon name="shield" size={11} /> Show {compiledVersions.length} versions</button>
        ) : (
          <span className="row" style={{ gap: 7 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowPhysicalVersions(false)}><Icon name="chevronLeft" size={11} /> Hide</button>
            <select className="select" value={oracleRobotId} onChange={(event) => setOracleRobotId(event.target.value)} aria-label="Oracle robot">
              {availableRobots.length === 0 ? <option value="">No active AVAILABLE robot</option> : availableRobots.map((registration) => {
                const robot = robotData?.robots.find((item) => item.id === registration.id);
                return <option key={registration.id} value={registration.id}>{robot?.name ?? registration.id}</option>;
              })}
            </select>
            <select className="select" value={vlaModelId} onChange={(event) => setVlaModelId(event.target.value)} aria-label="VLA policy">
              {vlaModels.length === 0 ? <option value="">No VLA policy registered</option> : vlaModels.map((model) => (
                <option key={model.id} value={model.id}>{model.displayName} · {model.lifecycleState}</option>
              ))}
            </select>
            <input
              className="input"
              value={vlaInstruction}
              onChange={(event) => setVlaInstruction(event.target.value)}
              aria-label="VLA task instruction"
              placeholder="Policy instruction"
              style={{ width: 250 }}
            />
            <button className="btn btn-ghost btn-sm" onClick={refetchCompiled}><Icon name="refresh" size={12} /> Refresh</button>
          </span>
        )}
        style={{ marginBottom: 10 }}
      >
        {!showPhysicalVersions ? (
          <div className="empty-note" style={{ margin: 10 }}>
            The main asset library remains below. Open this advanced section only when you need collision, mass, drop/settle, Franka-oracle, VLA, or promotion evidence for a specific immutable version.
          </div>
        ) : compiledError ? (
          <ErrorState message={compiledError.message} onRetry={refetchCompiled} />
        ) : compiledLoading && !compiledData ? (
          <Skeleton rows={3} />
        ) : compiledVersions.length === 0 ? (
          <EmptyState icon="shield">No immutable physical candidate has been compiled. Use <b>Compile physical asset</b> with an allowlisted local GLB.</EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Candidate</th><th>State</th><th>Physical contract</th><th>Geometry</th><th>Measured physics</th><th>Franka oracle</th><th>VLA-JEPA</th><th>Promotion</th><th>Recorded preview</th>
                </tr>
              </thead>
              <tbody>
                {compiledVersions.map((version) => {
                  const dimensionsM = version.manifest.dimensionsM ?? [];
                  const geometry = version.validationReport.staticValidation?.sourceGeometry;
                  const physics = version.validationReport.physicsValidation;
                  const oracle = version.validationReport.oracleValidation;
                  return (
                    <tr key={version.id}>
                      <td>
                        <div className="col" style={{ gap: 2 }}>
                          <span style={{ fontWeight: 580 }}>{version.displayName}</span>
                          <span className="micro t3 mono">{version.id} · {version.assetId}/v{String(version.version).padStart(4, "0")}</span>
                          <span className="micro t3 mono" title={version.sourceSha256}>source {version.sourceSha256.slice(0, 12)}…</span>
                        </div>
                      </td>
                      <td><StatusBadge status={version.lifecycleState} /></td>
                      <td>
                        <div className="col" style={{ gap: 2 }}>
                          <span className="mono t2">{dimensionsM.length === 3 ? dimensionsM.map((value) => value.toFixed(3)).join(" × ") : "—"} m</span>
                          <span className="micro t3">W × H × D · {version.manifest.massKg?.toFixed(3) ?? "—"} kg</span>
                        </div>
                      </td>
                      <td>
                        <div className="col" style={{ gap: 2 }}>
                          <span className="mono t2">{geometry?.triangles?.toLocaleString() ?? "—"} visual</span>
                          <span className="micro t3">{version.validationReport.collision?.triangles?.toLocaleString() ?? "—"} collision triangles</span>
                          <span className="micro t3">aspect residual {geometry?.maxAspectResidual !== undefined ? (geometry.maxAspectResidual * 100).toFixed(2) : "—"}%</span>
                        </div>
                      </td>
                      <td>
                        <div className="col" style={{ gap: 2 }}>
                          <span className={physics?.passed ? "t-green" : "t-red"}>{physics?.passed ? "Drop + settle passed" : version.lifecycleState === "REJECTED" ? "Rejected" : "Not run"}</span>
                          <span className="micro t3 mono">penetration {physics?.maxPenetrationM !== undefined ? `${(physics.maxPenetrationM * 1000).toFixed(3)} mm` : "—"}</span>
                          <span className="micro t3 mono">repeat Δ {physics?.deterministicRepeatMaxQposError?.toExponential(2) ?? "—"}</span>
                        </div>
                      </td>
                      <td>
                        <div className="col" style={{ gap: 4, minWidth: 150 }}>
                          {oracle ? (
                            <>
                              <StatusBadge status={oracle.success ? "passed" : "failed"} />
                              <span className="micro t3 mono">{oracle.evaluationId} · seed {oracle.seed}</span>
                              <span className="micro t3">{oracle.success ? `${((oracle.predicate?.targetErrorM ?? 0) * 1000).toFixed(2)} mm target error` : oracle.failureCode}</span>
                            </>
                          ) : <span className="micro t3">Not evaluated</span>}
                          <button
                            className="btn btn-secondary btn-sm"
                            disabled={!oracleRobotId || oracleRunning !== null || !["PHYSICS_VALIDATED", "ORACLE_VALIDATED"].includes(version.lifecycleState)}
                            onClick={() => runPhysicalOracle(version)}
                          >
                            <Icon name="play" size={11} /> {oracleRunning === version.id ? "Running physics…" : "Run oracle"}
                          </button>
                        </div>
                      </td>
                      <td>
                        <div className="col" style={{ gap: 4, minWidth: 160 }}>
                          <span className="micro t3">
                            {selectedVla
                              ? `${selectedVla.displayName} · ${selectedVla.lifecycleState}/${selectedVla.healthStatus}`
                              : "No policy selected"}
                          </span>
                          <button
                            className="btn btn-secondary btn-sm"
                            title={selectedVla?.lifecycleState !== "LOADED" ? "Load a compatible policy on the Models page first." : undefined}
                            disabled={
                              !oracleRobotId
                              || !selectedVla
                              || !selectedVla.enabled
                              || selectedVla.lifecycleState !== "LOADED"
                              || selectedVla.healthStatus !== "healthy"
                              || vlaRunning !== null
                              || version.lifecycleState !== "ORACLE_VALIDATED"
                              || !vlaInstruction.trim()
                            }
                            onClick={() => runPhysicalVla(version)}
                          >
                            <Icon name="play" size={11} /> {vlaRunning === version.id ? "Running policy…" : "Run VLA"}
                          </button>
                          {version.lifecycleState !== "ORACLE_VALIDATED" && <span className="micro t3">Oracle validation required first.</span>}
                        </div>
                      </td>
                      <td>
                        <div className="col" style={{ gap: 4, maxWidth: 260 }}>
                          <StatusBadge status={version.promotionEligible ? "ready" : "blocked"} />
                          <details>
                            <summary className="micro t3" style={{ cursor: "pointer" }}>{version.promotionBlockers.length} explicit blocker{version.promotionBlockers.length === 1 ? "" : "s"}</summary>
                            <div className="micro t3 mono" style={{ marginTop: 4, whiteSpace: "normal" }}>{version.promotionBlockers.join(" · ") || "none"}</div>
                          </details>
                        </div>
                      </td>
                      <td>
                        {physics?.previewGenerated ? (
                          <img
                            src={apiUrl(`/asset-versions/${version.id}/previews/drop-settled.png`)}
                            alt={`Recorded MuJoCo drop-settle result for ${version.displayName}`}
                            style={{ width: 76, height: 76, borderRadius: 5, objectFit: "cover", border: "1px solid var(--border)" }}
                          />
                        ) : <span className="micro t3">No frame</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="ov-stats" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 10 }}>
        {loading && !data
          ? Array.from({ length: 4 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : (data?.stats ?? []).map((s) => (
              <div key={s.label} className="stat-card">
                <div className="stat-meta">
                  <div className="stat-label">{s.label}</div>
                  <div className="stat-value">{s.value}</div>
                  <div className="stat-foot">{s.foot}</div>
                </div>
              </div>
            ))}
      </div>

      <Card
        title={<span>Asset library <span className="micro t3" style={{ marginLeft: 8 }}>original records and generated builds</span></span>}
        flush
        right={
          <span className="row" style={{ gap: 7 }}>
            <SearchBox placeholder="Search assets" value={q} onChange={(v) => { setQ(v); setPage(1); }} style={{ width: 200 }} />
            <select className="select" style={{ width: 126 }} value={kind} onChange={(e) => setKind(e.target.value)}>
              {["All types", "Articulated", "Rigid", "Environment"].map((k) => <option key={k}>{k}</option>)}
            </select>
            <select className="select" style={{ width: 120 }} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option>All status</option>
              <option value="ready">Ready</option>
              <option value="testing">Testing</option>
              <option value="building">Building</option>
              <option value="blocked">Blocked</option>
            </select>
          </span>
        }
      >
        {error ? (
          <ErrorState message={error.message} onRetry={refetch} />
        ) : loading && !data ? (
          <Skeleton rows={6} />
        ) : paged.length > 0 ? (
          <>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Asset</th><th>Type</th><th>Readiness</th><th>Physics validity</th><th>Scale conf.</th>
                    <th>Live source</th><th>Status</th><th style={{ textAlign: "right" }}>Last evaluation</th><th style={{ width: 30 }} />
                  </tr>
                </thead>
                <tbody>
                  {paged.map((a) => (
                    <tr key={a.id} className="rowlink" onClick={() => nav(`/assets/${a.id}`)}>
                      <td>
                        <div className="cell-main">
                          <span className="cell-ico"><Icon name={KIND_ICON[a.kind] as IconName} size={13} /></span>
                          <span className="col" style={{ gap: 0 }}>
                            <span style={{ fontWeight: 580 }}>{a.name}</span>
                            <span className="micro t3 mono">{a.id}</span>
                          </span>
                        </div>
                      </td>
                      <td className="t-muted">{KIND_LABEL[a.kind]}</td>
                      <td>
                        <div className="row" style={{ gap: 8 }}>
                          <DonutGauge
                            value={a.readiness / 100}
                            size={26}
                            stroke={3}
                            color={a.readiness >= 85 ? "var(--green)" : a.readiness >= 70 ? "var(--amber)" : "var(--red)"}
                          />
                          <span className="mono" style={{ fontWeight: 620 }}>{a.readiness}</span>
                        </div>
                      </td>
                      <td className="mono t2">{a.physicsValidity.toFixed(1)}%</td>
                      <td className="mono t2">{a.scaleConfidence.toFixed(2)}</td>
                      <td>
                        <div className="row" style={{ gap: 8, minWidth: 180 }}>
                          {a.sourceImage ? (
                            <img
                              src={a.sourceImage}
                              alt={`Bright Data source for ${a.name}`}
                              style={{ width: 38, height: 30, borderRadius: 4, objectFit: "cover", border: "1px solid var(--border)" }}
                            />
                          ) : (
                            <span className="cell-ico"><Icon name="sources" size={13} /></span>
                          )}
                          <span className="col" style={{ gap: 1, minWidth: 0 }}>
                            <span className="t-muted" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 180 }}>{a.source}</span>
                            <span className="micro t3">{a.sourceImage ? "Bright Data image acquired" : "No image evidence"}</span>
                          </span>
                        </div>
                      </td>
                      <td><StatusBadge status={a.status} /></td>
                      <td className="t-muted mono" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{a.lastEval}</td>
                      <td><button className="icon-btn btn-sm" onClick={(e) => e.stopPropagation()}><Icon name="dots" size={13} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row between" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
              <span className="micro t3">Showing {paged.length} of {assets.length} assets</span>
              <Pagination page={page} pages={pages} onPage={setPage} />
            </div>
          </>
        ) : (
          <EmptyState icon="cube">No assets in the library yet — queue a build with <b>New asset build</b>.</EmptyState>
        )}
      </Card>
    </div>
  );
}
