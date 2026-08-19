import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, Segmented, StatusBadge } from "../components/ui/controls";
import { Tree } from "../components/ui/Tree";
import { PanelRail, ResizeHandle, usePanelSize } from "../components/ui/Resizable";
import { Viewport } from "../components/three/Viewport";
import { useToast } from "../components/ui/Toast";
import { Modal } from "../components/ui/Modal";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { PhysicsCheck, ScenarioVariant, SceneNode } from "../data/types";
import LiveEvaluation from "./LiveEvaluation";

interface SceneData {
  sceneTree: SceneNode[];
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
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") === "live" ? "live" : "edit";
  const setMode = (m: "edit" | "live") => setParams(m === "live" ? { mode: "live" } : {}, { replace: true });

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="page-head" style={{ marginBottom: 10 }}>
        <div>
          <h1 className="page-title">Worlds</h1>
          <p className="page-sub">Articulated Door Validation Lab — inspect, validate, and run persisted MuJoCo worlds.</p>
        </div>
        <div className="head-actions">
          <Segmented
            options={[
              { value: "edit", label: "Scene Editor", icon: "edit" },
              { value: "live", label: "Live Evaluation", icon: "play" },
            ]}
            value={mode}
            onChange={setMode}
          />
        </div>
      </div>
      {mode === "edit" ? <SceneComposer /> : <LiveEvaluation embedded />}
    </div>
  );
}

/* ========================================================================== */

function SceneComposer() {
  const toast = useToast();
  const { data: scene, error, loading, refetch } = useApi<SceneData>("/worlds/scene");
  const { data: acceptance } = useApi<AcceptanceCatalog>("/demo-scenarios");
  const [acceptanceId, setAcceptanceId] = useState<AcceptanceScenario["id"]>("kitchen-juice");
  const [acceptanceJob, setAcceptanceJob] = useState<AcceptanceJob | null>(null);
  const [startingAcceptance, setStartingAcceptance] = useState(false);
  const [selected, setSelected] = useState<string | null>("cabinet-02");
  const [selectedName, setSelectedName] = useState("Kitchen Cabinet 02");
  const [seed, setSeed] = useState("1048576");
  const [variant, setVariant] = useState("");
  const [inspTab, setInspTab] = useState<"Properties" | "References">("Properties");
  const [shelfTab, setShelfTab] = useState<"Console" | "Variants" | "Checks">("Console");
  const [saved, setSaved] = useState("never");
  const [saving, setSaving] = useState(false);
  const [checks, setChecks] = useState<PhysicsCheck[] | null>(null);
  const [checksRunning, setChecksRunning] = useState(false);
  const [newVariantOpen, setNewVariantOpen] = useState(false);
  const [creatingVariant, setCreatingVariant] = useState(false);
  const variantNameRef = useRef<HTMLInputElement>(null);
  const variantDescRef = useRef<HTMLInputElement>(null);

  // panel state — resizable + collapsible
  const [leftW, setLeftW] = usePanelSize(248, 190, 420, "robotworld.worlds.leftW");
  const [rightW, setRightW] = usePanelSize(318, 250, 460, "robotworld.worlds.rightW");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [shelfH, setShelfH] = usePanelSize(196, 120, 340, "robotworld.worlds.shelfH");
  const [shelfOpen, setShelfOpen] = useState(true);

  const activeAcceptance = acceptance?.scenarios.find((item) => item.id === acceptanceId);
  const sceneTree = useMemo(() => activeAcceptance?.hierarchy ?? scene?.sceneTree ?? [], [activeAcceptance, scene]);
  const variantCards = useMemo(() => scene?.variants ?? [], [scene]);
  const physicsChecks = checks ?? scene?.physicsChecks ?? [];

  // select the backend-flagged active variant once loaded
  useEffect(() => {
    if (scene && !variant) {
      const active = scene.variants.find((v) => v.active) ?? scene.variants[0];
      if (active) setVariant(active.id);
    }
  }, [scene, variant]);

  useEffect(() => {
    const reset = () => {
      setLeftW(248);
      setRightW(318);
      setShelfH(196);
      setLeftOpen(true);
      setRightOpen(true);
      setShelfOpen(true);
    };
    window.addEventListener("robotworld:reset-layout", reset);
    return () => window.removeEventListener("robotworld:reset-layout", reset);
  }, [setLeftW, setRightW, setShelfH]);

  useEffect(() => {
    if (!acceptanceJob || ["success", "failed", "blocked"].includes(acceptanceJob.status)) return;
    const timer = window.setInterval(() => {
      api.get<AcceptanceJob>(`/jobs/${acceptanceJob.id}`)
        .then((job) => {
          setAcceptanceJob(job);
          if (job.status === "blocked") toast.push("info", "Environment ready; VLA required", job.detail.result?.message ?? "No robot-task success was claimed.");
          if (job.status === "failed") toast.push("err", "Acceptance run failed", job.detail.error ?? "See the console for evidence.");
        })
        .catch(() => undefined);
    }, 800);
    return () => window.clearInterval(timer);
  }, [acceptanceJob, toast]);

  const runAcceptance = async (id: AcceptanceScenario["id"]) => {
    setAcceptanceId(id);
    setSelected(id === "kitchen-juice" ? "blender" : "parcel-set");
    setSelectedName(id === "kitchen-juice" ? "Blender" : "Randomized parcel set");
    setShelfTab("Console");
    setShelfOpen(true);
    setStartingAcceptance(true);
    try {
      const response = await api.post<{ jobId: string }>(`/demo-scenarios/${id}/runs`, { seed: Number(seed) });
      setAcceptanceJob({ id: response.jobId, status: "pending", detail: { scenarioId: id, stages: [] } });
      toast.push("ok", "Acceptance run queued", "A fresh seed will compile and validate; the run will fail closed if the VLA is absent.");
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
      toast.push("ok", "Scene saved", `Validation Lab · ${variantCards.length} variants`);
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
      toast.push(fails ? "err" : warns ? "info" : "ok", "Placement & physics checks complete", `${r.physicsChecks.length} checks · ${fails} failed · ${warns} warnings`);
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
    <div className="world-editor col" style={{ flex: 1, minHeight: 0, gap: 2 }}>
      {/* transport toolbar */}
      <div className="dockbar">
        <button className={`btn btn-sm ${acceptanceId === "kitchen-juice" ? "btn-secondary" : "btn-ghost"}`} disabled={startingAcceptance || acceptanceJob?.status === "running"} onClick={() => runAcceptance("kitchen-juice")} title="Compile a fresh randomized kitchen world, run physical validation, then execute only if a compatible VLA is configured">
          <Icon name="play" size={12} /> Kitchen acceptance
        </button>
        <button className={`btn btn-sm ${acceptanceId === "factory-sort" ? "btn-secondary" : "btn-ghost"}`} disabled={startingAcceptance || acceptanceJob?.status === "running"} onClick={() => runAcceptance("factory-sort")} title="Compile a fresh randomized logistics world, run physical validation, then execute only if a compatible VLA is configured">
          <Icon name="play" size={12} /> Logistics acceptance
        </button>
        <span className="v-divider" style={{ margin: "0 4px" }} />
        <span className="small t2">Variant</span>
        <select className="select" style={{ width: 112, height: 26 }} value={variant} onChange={(e) => setVariant(e.target.value)}>
          {variantCards.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
        <span className="small t2">Seed</span>
        <input className="input mono" style={{ width: 80, height: 26 }} value={seed} onChange={(e) => setSeed(e.target.value.replace(/\D/g, ""))} />
        <button className="btn btn-ghost btn-sm btn-icon" title="Randomize seed" onClick={() => setSeed(String(Math.floor(Math.random() * 9_000_000 + 1_000_000)))}>
          <Icon name="refresh" size={12} />
        </button>
        <span className="grow" />
        <button className={`btn btn-ghost btn-sm btn-icon ${leftOpen ? "" : ""}`} title="Toggle stage tree" onClick={() => setLeftOpen(!leftOpen)} style={!leftOpen ? { color: "var(--accent)" } : undefined}>
          <Icon name="panelLeft" size={13} />
        </button>
        <button className="btn btn-ghost btn-sm btn-icon" title="Toggle bottom shelf" onClick={() => setShelfOpen(!shelfOpen)} style={!shelfOpen ? { color: "var(--accent)" } : undefined}>
          <Icon name="panelBottom" size={13} />
        </button>
        <button className="btn btn-ghost btn-sm btn-icon" title="Toggle inspector" onClick={() => setRightOpen(!rightOpen)} style={!rightOpen ? { color: "var(--accent)" } : undefined}>
          <Icon name="panelRight" size={13} />
        </button>
        <span className="v-divider" style={{ margin: "0 2px" }} />
        <span className="small t3" style={{ padding: "0 4px" }}>Saved {saved}</span>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}><Icon name="save" size={12} /> {saving ? "Saving…" : "Save"}</button>
      </div>

      {/* main dock */}
      <div className="row" style={{ flex: 1, minHeight: 0, gap: 0, alignItems: "stretch" }}>
        {/* left: stage tree */}
        {leftOpen ? (
          <>
            <div className="card" style={{ width: leftW, flex: "none", display: "flex", flexDirection: "column", minHeight: 0 }}>
              <header className="card-head" style={{ minHeight: 36 }}>
                <span className="card-title" style={{ fontSize: "var(--fs-small)" }}>Stage Tree</span>
              </header>
              <div style={{ padding: "6px 8px 6px", borderBottom: "1px solid var(--border)" }}>
                <div className="search-box">
                  <span className="search-ico"><Icon name="search" size={12} /></span>
                  <input className="input" placeholder="Search nodes…" style={{ height: 25 }} />
                </div>
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: "4px 4px 8px" }}>
                {error ? (
                  <ErrorState message={error.message} onRetry={refetch} />
                ) : loading && !scene ? (
                  <Skeleton rows={8} height={11} />
                ) : sceneTree.length > 0 ? (
                  <Tree
                    nodes={sceneTree as never}
                    selected={selected}
                    onSelect={(id, name) => { setSelected(id); setSelectedName(name); }}
                  />
                ) : (
                  <EmptyState icon="worlds">No scene loaded — the backend has not composed a world yet.</EmptyState>
                )}
              </div>
            </div>
            <ResizeHandle dir="col" onDrag={(d) => setLeftW(leftW + d)} />
          </>
        ) : (
          <>
            <PanelRail label="Stage Tree" side="left" onExpand={() => setLeftOpen(true)} />
            <div style={{ width: 8, flex: "none" }} />
          </>
        )}

        {/* center: viewport + shelf */}
        <div className="col" style={{ flex: 1, minWidth: 0, minHeight: 0, gap: 0 }}>
          <div className="card" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ position: "relative", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <Viewport
                camera={{ position: [3.6, 2.5, 1.4], fov: 42 }}
                target={[-0.1, 0.9, -2.8]}
                scene={activeAcceptance?.world ?? "kitchen"}
                doorAngle={variant.includes("left") ? 55 : variant.includes("default") ? 18 : 0}
                style={{ flex: 1, minHeight: 0, borderRadius: 0 }}
                onPointerMissed={() => setSelected(null)}
                grid
                controls
              />

              {/* engine HUD overlays */}
              <div className="vp-overlay" style={{ top: 10, left: 10 }}>
                <span className="vp-chip"><span className="dot" style={{ background: "var(--text-2)" }} /> Scene preview</span>
              </div>
              <div className="vp-overlay" style={{ top: 10, right: 10 }}>
                <span className="vp-chip">Drag to orbit · wheel to zoom</span>
              </div>
              {/* engine status strip — bottom-right, away from the gizmo */}
              <div className="vp-overlay" style={{ bottom: 10, right: 10 }}>
                <div className="vp-stat">
                  <span className="mono g-blue">{selected ? selectedName : "—"}</span>
                </div>
              </div>
            </div>
          </div>

          {/* bottom shelf */}
          {shelfOpen ? (
            <>
              <ResizeHandle dir="row" onDrag={(d) => setShelfH(shelfH - d)} />
              <div className="card" style={{ height: shelfH, flex: "none", display: "flex", flexDirection: "column", minHeight: 0 }}>
                <header className="card-head" style={{ minHeight: 34, padding: "0 8px 0 12px" }}>
                  <span className="tabs" style={{ border: 0 }}>
                    {(["Console", "Variants", "Checks"] as const).map((t) => (
                      <button key={t} className={shelfTab === t ? "on" : ""} style={{ height: 26 }} onClick={() => setShelfTab(t)}>
                        {t === "Checks" ? "Placement & Physics" : t === "Variants" ? "Scenario Variants" : "Acceptance Console"}
                      </button>
                    ))}
                  </span>
                  <span className="head-right">
                    {shelfTab === "Variants" ? (
                      <button className="btn btn-ghost btn-sm" onClick={() => setNewVariantOpen(true)}><Icon name="plus" size={12} /> New Variant</button>
                    ) : shelfTab === "Checks" ? (
                      <button className="btn btn-ghost btn-sm" onClick={rerunChecks} disabled={checksRunning}>
                        <Icon name="refresh" size={12} className={checksRunning ? "spin" : undefined} /> {checksRunning ? "Running…" : "Re-run Checks"}
                      </button>
                    ) : (
                      <StatusBadge status={acceptanceJob?.status ?? "idle"} />
                    )}
                  </span>
                </header>
                <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
                  {shelfTab === "Console" ? (
                    <AcceptanceConsole scenario={activeAcceptance} catalog={acceptance ?? undefined} job={acceptanceJob} />
                  ) : shelfTab === "Variants" ? (
                    loading && !scene ? (
                      <Skeleton rows={2} height={40} />
                    ) : variantCards.length > 0 ? (
                      <div style={{ padding: 10, overflowX: "auto" }}>
                        <div className="variant-row" style={{ minWidth: 560 }}>
                          {variantCards.map((v) => (
                            <button key={v.id} className={`variant-card ${variant === v.id ? "active" : ""}`} onClick={() => activateVariant(v.id)}>
                              <div className="v-thumb">
                                <Viewport
                                  camera={{ position: [2.7, 2.0, 0.4], fov: 44 }}
                                  target={[0.1, 1.0, -3.1]}
                                  scene={activeAcceptance?.world ?? "kitchen"}
                                  doorAngle={v.id.includes("left") ? 55 : v.id.includes("default") ? 18 : 0}
                                  style={{ height: "100%", borderRadius: 0 }}
                                  gizmo={false}
                                  controls={false}
                                  shadows={false}
                                  dpr={[0.5, 0.8]}
                                />
                                {v.active && <span className="badge b-blue" style={{ position: "absolute", top: 5, left: 5, height: 16, fontSize: 9 }}>Active</span>}
                              </div>
                              <div className="v-body">
                                <div className="v-name">{v.name}<Icon name="dots" size={11} style={{ color: "var(--text-3)" }} /></div>
                                <div className="v-desc">{v.desc}</div>
                              </div>
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <EmptyState icon="worlds">No scenario variants yet — create one with <b>New Variant</b>.</EmptyState>
                    )
                  ) : (
                    <div className="table-scroll">
                      {checksRunning && <div className="busy-bar" style={{ margin: "8px 12px 0" }}><i /></div>}
                      {physicsChecks.length > 0 ? (
                        <table className="table">
                          <thead>
                            <tr><th>Check</th><th>Status</th><th>Details</th><th>Impacted Nodes</th><th>Severity</th><th /></tr>
                          </thead>
                          <tbody>
                            {physicsChecks.map((c) => (
                              <tr key={c.check}>
                                <td style={{ fontWeight: 550 }}>{c.check}</td>
                                <td><StatusBadge status={c.status} /></td>
                                <td className="t-muted">{c.details}</td>
                                <td className="t-muted">{c.impacted}</td>
                                <td><span className={`sev ${c.severity.toLowerCase()}`}>{c.severity}</span></td>
                                <td>{c.severity !== "Info" && <button className="btn btn-ghost btn-sm" onClick={() => { setSelected("cabinet-02"); setSelectedName("Kitchen Cabinet 02"); }}>View</button>}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ) : (
                        !checksRunning && <EmptyState icon="scale">No checks have run yet — press <b>Re-run Checks</b>.</EmptyState>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </div>

        {/* right: inspector */}
        {rightOpen ? (
          <>
            <ResizeHandle dir="col" onDrag={(d) => setRightW(rightW - d)} />
            <div className="card" style={{ width: rightW, flex: "none", display: "flex", flexDirection: "column", minHeight: 0 }}>
              <header className="card-head" style={{ minHeight: 36 }}>
                <span className="row" style={{ gap: 8, minWidth: 0 }}>
                  <span className="cell-ico" style={{ width: 22, height: 22 }}><Icon name="cabinet" size={12} /></span>
                  <span className="ellipsis" style={{ fontWeight: 620, fontSize: "var(--fs-small)" }}>{selected ? selectedName : "Nothing selected"}</span>
                  {selected && <Badge tone="grey">Scene node</Badge>}
                </span>
              </header>
              <div className="tabs" style={{ padding: "0 12px" }}>
                {(["Properties", "References"] as const).map((t) => (
                  <button key={t} className={inspTab === t ? "on" : ""} onClick={() => setInspTab(t)}>{t}</button>
                ))}
              </div>
              <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
                {selected && inspTab === "Properties" ? (
                  <EvidenceInspectorBody
                    selected={selected}
                    selectedName={selectedName}
                    scenario={activeAcceptance}
                    job={acceptanceJob}
                  />
                ) : selected && inspTab === "References" ? (
                  <div className="col" style={{ padding: 12, gap: 8 }}>
                    <span className="section-label">Measured success predicates</span>
                    {(activeAcceptance?.successPredicates ?? []).map((predicate, index) => (
                      <div className="row small t2" style={{ alignItems: "flex-start", gap: 7 }} key={predicate}>
                        <span className="mono t3">{String(index + 1).padStart(2, "0")}</span><span>{predicate}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty-note">Select a node in the viewport or stage tree to inspect its properties.</div>
                )}
              </div>
            </div>
          </>
        ) : (
          <>
            <div style={{ width: 8, flex: "none" }} />
            <PanelRail label="Inspector" side="right" onExpand={() => setRightOpen(true)} />
          </>
        )}
      </div>

      {/* modals */}
      {newVariantOpen && (
        <Modal
          title="New scenario variant"
          onClose={() => setNewVariantOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setNewVariantOpen(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={createVariant} disabled={creatingVariant}>
                {creatingVariant ? "Creating…" : "Create variant"}
              </button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="field"><label>Name</label><input ref={variantNameRef} className="input" placeholder="e.g. High Clutter" autoFocus /></div>
            <div className="field"><label>Description</label><input ref={variantDescRef} className="input" placeholder="What changes in this variant?" /></div>
            <div className="field"><label>Base</label><select className="select">{variantCards.map((v) => <option key={v.id}>{v.name}</option>)}</select></div>
          </div>
        </Modal>
      )}
    </div>
  );
}

/* ---- inspector body (clean, no XYZ inputs) --------------------------------- */
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
    <div className="acceptance-console">
      <div className="acceptance-brief">
        <div className="row between acceptance-summary" style={{ gap: 10 }}>
          <strong>{scenario.name}</strong>
          <span className="row" style={{ gap: 5 }}>
            <Badge tone={catalog?.readiness.vulkan.available ? "green" : "red"}>Vulkan</Badge>
            <Badge tone={catalog?.readiness.policyConfigured ? "green" : "amber"}>VLA {catalog?.readiness.policyConfigured ? "configured" : "required"}</Badge>
            <Badge tone="grey">No training</Badge>
          </span>
        </div>
        <p className="small t2">{scenario.description}</p>
        <p className="micro t3">{scenario.disclosure}</p>
      </div>
      <div className="acceptance-log mono">
        {!job ? (
          <div className="console-line"><span className="console-time">ready</span><span>Select this scenario's acceptance button to compile a fresh randomized world.</span></div>
        ) : (
          <>
            <div className="console-line"><span className="console-time">job</span><span>{job.id} · {job.status}</span></div>
            {job.detail.stages.map((stage, index) => (
              <div className={`console-line ${stage.status}`} key={`${stage.name}-${index}`}>
                <span className="console-time">{new Date(stage.at).toLocaleTimeString([], { hour12: false })}</span>
                <span><b>{stage.status.toUpperCase()}</b> {stage.name} · {stage.detail}</span>
              </div>
            ))}
            {job.detail.error && <div className="console-line failed"><span className="console-time">error</span><span>{job.detail.error}</span></div>}
            {job.detail.result && <div className="console-line blocked"><span className="console-time">result</span><span>taskSuccess={String(job.detail.result.taskSuccess)} · {job.detail.result.message}</span></div>}
          </>
        )}
      </div>
    </div>
  );
}

function EvidenceInspectorBody({
  selected,
  selectedName,
  scenario,
  job,
}: {
  selected: string;
  selectedName: string;
  scenario?: AcceptanceScenario;
  job: AcceptanceJob | null;
}) {
  const result = job?.detail.result;
  return (
    <>
      <InspSection title="Identity">
        <div className="kv">
          <div className="kv-row"><span className="kv-k">ID</span><span className="kv-v mono">{selected}</span></div>
          <div className="kv-row"><span className="kv-k">Name</span><span className="kv-v">{selectedName}</span></div>
          <div className="kv-row"><span className="kv-k">Scenario</span><span className="kv-v mono">{scenario?.id ?? "not loaded"}</span></div>
        </div>
      </InspSection>
      <InspSection title="Runtime evidence">
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Job</span><span className="kv-v mono">{job?.id ?? "not run"}</span></div>
          <div className="kv-row"><span className="kv-k">State</span><span className="kv-v"><StatusBadge status={job?.status ?? "not run"} /></span></div>
          <div className="kv-row"><span className="kv-k">Seed</span><span className="kv-v mono">{result?.seed ?? "generated at run"}</span></div>
          <div className="kv-row"><span className="kv-k">Task success</span><span className="kv-v mono">{result ? String(result.taskSuccess) : "not evaluated"}</span></div>
        </div>
      </InspSection>
      <InspSection title="Provenance" defaultOpen={false}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Definition</span><span className="kv-v">Acceptance manifest</span></div>
          <div className="kv-row"><span className="kv-k">Physics</span><span className="kv-v">MuJoCo compile + stability gate</span></div>
          <div className="kv-row"><span className="kv-k">Manifest SHA</span><span className="kv-v mono">{result?.manifestSha256?.slice(0, 16) ?? "pending"}</span></div>
          <div className="kv-row"><span className="kv-k">MJCF SHA</span><span className="kv-v mono">{result?.mjcfSha256?.slice(0, 16) ?? "pending"}</span></div>
        </div>
      </InspSection>
    </>
  );
}
