import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { TransformControls } from "@react-three/drei";
import * as THREE from "three";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, Segmented, StatusBadge } from "../components/ui/controls";
import { Tree } from "../components/ui/Tree";
import { PanelRail, ResizeHandle, usePanelSize } from "../components/ui/Resizable";
import { Viewport } from "../components/three/Viewport";
import { WarehouseKitchen } from "../components/three/WarehouseKitchen";
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

type GizmoMode = "translate" | "rotate" | "scale";

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
  const [selected, setSelected] = useState<string | null>("cabinet-02");
  const [selectedName, setSelectedName] = useState("Kitchen Cabinet 02");
  const [gizmo, setGizmo] = useState<GizmoMode>("translate");
  const [playing, setPlaying] = useState(false);
  const [seed, setSeed] = useState("1048576");
  const [variant, setVariant] = useState("");
  const [inspTab, setInspTab] = useState<"Properties" | "References">("Properties");
  const [shelfTab, setShelfTab] = useState<"Variants" | "Checks">("Variants");
  const [saved, setSaved] = useState("never");
  const [saving, setSaving] = useState(false);
  const [checks, setChecks] = useState<PhysicsCheck[] | null>(null);
  const [checksRunning, setChecksRunning] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [newVariantOpen, setNewVariantOpen] = useState(false);
  const [creatingVariant, setCreatingVariant] = useState(false);
  const variantNameRef = useRef<HTMLInputElement>(null);
  const variantDescRef = useRef<HTMLInputElement>(null);

  // panel state — resizable + collapsible
  const [leftW, setLeftW] = usePanelSize(248, 190, 420);
  const [rightW, setRightW] = usePanelSize(318, 250, 460);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [shelfH, setShelfH] = usePanelSize(196, 120, 340);
  const [shelfOpen, setShelfOpen] = useState(true);
  const [camMode, setCamMode] = useState<"orbit" | "fly">("orbit");

  // live clock for Play mode inside the composer viewport
  const sim = useRef({ t: 0 });

  const [proxyObj, setProxyObj] = useState<THREE.Group | null>(null);
  const showGizmo = selected === "cabinet-02" || selected === "fridge" || selected === "cart" || selected === "worktable";

  const sceneTree = useMemo(() => scene?.sceneTree ?? [], [scene]);
  const variantCards = useMemo(() => scene?.variants ?? [], [scene]);
  const physicsChecks = checks ?? scene?.physicsChecks ?? [];

  // select the backend-flagged active variant once loaded
  useEffect(() => {
    if (scene && !variant) {
      const active = scene.variants.find((v) => v.active) ?? scene.variants[0];
      if (active) setVariant(active.id);
    }
  }, [scene, variant]);

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
    <div className="col" style={{ flex: 1, minHeight: 0, gap: 8 }}>
      {/* transport toolbar */}
      <div className="dockbar">
        <button className={`btn btn-sm ${playing ? "btn-secondary" : "btn-primary"}`} onClick={() => setPlaying(!playing)}>
          <Icon name={playing ? "pause" : "play"} size={12} /> {playing ? "Pause" : "Play"}
        </button>
        <button className="btn btn-ghost btn-sm btn-icon" title="Stop" onClick={() => { setPlaying(false); sim.current.t = 0; }}>
          <Icon name="stop" size={12} />
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
        <Segmented
          options={[
            { value: "translate", icon: "move", label: "Move" },
            { value: "rotate", icon: "rotate", label: "Rotate" },
            { value: "scale", icon: "gizmoScale", label: "Scale" },
          ]}
          value={gizmo}
          onChange={(v) => setGizmo(v)}
        />
        <span className="v-divider" style={{ margin: "0 2px" }} />
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
                <span className="head-right">
                  <button className="icon-btn btn-sm" title="Add node" onClick={() => toast.push("info", "Add node", "Pick an asset from the library (backend pending)")}><Icon name="plus" size={12} /></button>
                </span>
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
                style={{ flex: 1, minHeight: 0, borderRadius: 0 }}
                onPointerMissed={() => setSelected(null)}
                grid
                fly={camMode === "fly"}
                controls={camMode === "orbit"}
              >
                <WarehouseKitchen
                  simRef={playing ? sim : undefined}
                  simTime={playing ? undefined : 0}
                  selection={{
                    interactive: true,
                    selectedId: selected,
                    onSelect: (s) => {
                      setSelected(s?.id ?? null);
                      setSelectedName(s?.name ?? "");
                    },
                  }}
                  cabinetDoorOpen={{ left: variant.includes("left") ? 0.8 : 0 }}
                />
                <group ref={setProxyObj} position={[-0.5, 1.62, -3.48]}>
                  {showGizmo && (
                    <mesh>
                      <boxGeometry args={[0.94, 0.79, 0.38]} />
                      <meshBasicMaterial color="#4C86E8" transparent opacity={0.07} depthWrite={false} />
                    </mesh>
                  )}
                </group>
                {showGizmo && proxyObj && (
                  <TransformControls object={proxyObj} mode={gizmo} size={0.75} />
                )}
              </Viewport>

              {/* engine HUD overlays */}
              <div className="vp-overlay" style={{ top: 10, left: 10 }}>
                <span className="vp-chip"><span className="dot" style={{ background: "var(--text-2)" }} /> Scene preview</span>
              </div>
              <div className="vp-overlay" style={{ top: 10, right: 10 }}>
                <div className="vp-toolbar">
                  <button title="Perspective" className="on" style={{ width: "auto", padding: "0 8px", fontSize: "var(--fs-small)" }}>Persp</button>
                  <span className="sep" />
                  <button title="Lit" style={{ width: "auto", padding: "0 8px", gap: 5 }}><Icon name="sun" size={12} /> Lit</button>
                  <button title="Collision view is available in the MuJoCo diagnostics" disabled><Icon name="collider" size={13} /></button>
                  <span className="sep" />
                  <button title="Frame all is not available in preview mode" disabled><Icon name="maximize" size={12} /></button>
                </div>
              </div>
              {/* camera mode toggle — orbit vs fly */}
              <div className="vp-overlay" style={{ top: 44, right: 10 }}>
                <Segmented
                  options={[
                    { value: "orbit", icon: "rotate", label: "Orbit" },
                    { value: "fly", icon: "hand", label: "Fly" },
                  ]}
                  value={camMode}
                  onChange={(v) => setCamMode(v)}
                />
              </div>
              <div className="vp-overlay" style={{ bottom: 10, left: "50%", transform: "translateX(-50%)" }}>
                <div className="vp-toolbar">
                  <button title="Select" className="on"><Icon name="hand" size={13} /></button>
                  <button title="Orbit"><Icon name="rotate" size={13} /></button>
                  <button title="Pan"><Icon name="move" size={13} /></button>
                  <span className="sep" />
                  <button title="Focus selection"><Icon name="target" size={13} /></button>
                  <button title="Measure is not available in preview mode" disabled><Icon name="ruler" size={13} /></button>
                  <button title="Screenshot is not available in preview mode" disabled><Icon name="camera" size={13} /></button>
                </div>
              </div>
              {/* engine status strip — bottom-right, away from the gizmo */}
              <div className="vp-overlay" style={{ bottom: 10, right: 10 }}>
                <div className="vp-stat">
                  <span className="mono">Three.js preview</span>
                  <span className="vp-stat-sep" />
                  <span className="mono g-blue">{selected ? selectedName : "—"}</span>
                </div>
              </div>
              {camMode === "fly" && (
                <div className="vp-overlay" style={{ top: 38, left: 10 }}>
                  <span className="vp-chip" style={{ fontSize: 10.5 }}>WASD move · drag to look · shift = boost</span>
                </div>
              )}
            </div>
          </div>

          {/* bottom shelf */}
          {shelfOpen ? (
            <>
              <ResizeHandle dir="row" onDrag={(d) => setShelfH(shelfH - d)} />
              <div className="card" style={{ height: shelfH, flex: "none", display: "flex", flexDirection: "column", minHeight: 0 }}>
                <header className="card-head" style={{ minHeight: 34, padding: "0 8px 0 12px" }}>
                  <span className="tabs" style={{ border: 0 }}>
                    {(["Variants", "Checks"] as const).map((t) => (
                      <button key={t} className={shelfTab === t ? "on" : ""} style={{ height: 26 }} onClick={() => setShelfTab(t)}>
                        {t === "Checks" ? "Placement & Physics" : "Scenario Variants"}
                      </button>
                    ))}
                  </span>
                  <span className="head-right">
                    {shelfTab === "Variants" ? (
                      <button className="btn btn-ghost btn-sm" onClick={() => setNewVariantOpen(true)}><Icon name="plus" size={12} /> New Variant</button>
                    ) : (
                      <button className="btn btn-ghost btn-sm" onClick={rerunChecks} disabled={checksRunning}>
                        <Icon name="refresh" size={12} className={checksRunning ? "spin" : undefined} /> {checksRunning ? "Running…" : "Re-run Checks"}
                      </button>
                    )}
                  </span>
                </header>
                <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
                  {shelfTab === "Variants" ? (
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
                                  style={{ height: "100%", borderRadius: 0 }}
                                  gizmo={false}
                                  controls={false}
                                  shadows={false}
                                  dpr={[0.5, 0.8]}
                                >
                                  <WarehouseKitchen
                                    cabinetDoorOpen={{
                                      left: v.id.includes("left") ? 0.85 : v.id.includes("default") ? 0.3 : 0,
                                      right: v.id.includes("cluttered") ? 0.5 : 0,
                                    }}
                                  />
                                </Viewport>
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
                  {selected && <Badge tone="grey">Static Mesh</Badge>}
                </span>
                <span className="head-right">
                  <button className="icon-btn btn-sm" title="Lock node" onClick={() => toast.push("info", "Node locked", selectedName)}><Icon name="lock" size={12} /></button>
                </span>
              </header>
              <div className="tabs" style={{ padding: "0 12px" }}>
                {(["Properties", "References"] as const).map((t) => (
                  <button key={t} className={inspTab === t ? "on" : ""} onClick={() => setInspTab(t)}>{t}</button>
                ))}
              </div>
              <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
                {selected ? (
                  <InspectorBody
                    selected={selected}
                    onDelete={() => setDeleteConfirm(true)}
                  />
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
      {deleteConfirm && (
        <Modal
          title="Delete node"
          onClose={() => setDeleteConfirm(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setDeleteConfirm(false)}>Cancel</button>
              <button
                className="btn btn-primary"
                style={{ background: "var(--red)" }}
                onClick={() => {
                  setDeleteConfirm(false);
                  setSelected(null);
                  toast.push("ok", "Node deleted", selectedName);
                }}
              >
                Delete
              </button>
            </>
          }
        >
          <p style={{ fontSize: "var(--fs-body)", color: "var(--text-2)" }}>
            Delete <b style={{ color: "var(--text-1)" }}>{selectedName}</b> from this world? References in saved scenario variants will be removed.
          </p>
        </Modal>
      )}
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
function InspectorBody({ selected, onDelete }: { selected: string; onDelete: () => void }) {
  return (
    <>
      <InspSection title="Identity">
        <div className="kv">
          <div className="kv-row"><span className="kv-k">ID</span><span className="kv-v mono">{selected}</span></div>
          <div className="kv-row"><span className="kv-k">Asset</span><span className="kv-v row" style={{ gap: 5 }}>Kitchen_Cabinet_02.usd <Icon name="external" size={10} /></span></div>
        </div>
        <div className="row wrap" style={{ gap: 5, marginTop: 8 }}>
          {["furniture", "storage", "kitchen"].map((t) => (
            <span key={t} className="tag">{t}<button><Icon name="x" size={9} /></button></span>
          ))}
          <button className="tag-add"><Icon name="plus" size={10} /></button>
        </div>
      </InspSection>
      <InspSection title="Mesh">
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Materials</span><span className="kv-v mono">4</span></div>
          <div className="kv-row"><span className="kv-k">Collision</span><span className="kv-v">Convex Decomposition</span></div>
          <div className="kv-row"><span className="kv-k">Physics</span><span className="kv-v">Static</span></div>
          <div className="kv-row"><span className="kv-k">Mass (kg)</span><span className="kv-v mono">—</span></div>
        </div>
      </InspSection>
      <InspSection title="Interaction" defaultOpen={false}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Graspable</span><span className="kv-v">Handle only</span></div>
          <div className="kv-row"><span className="kv-k">Articulation</span><span className="kv-v">2 revolute doors</span></div>
        </div>
      </InspSection>
      <InspSection title="Metadata" defaultOpen={false}>
        <div className="kv">
          <div className="kv-row"><span className="kv-k">Source</span><span className="kv-v">bestbuy.com</span></div>
          <div className="kv-row"><span className="kv-k">Compiled</span><span className="kv-v">May 24, 10:02 AM</span></div>
          <div className="kv-row"><span className="kv-k">Compiler</span><span className="kv-v mono">usd.compiler v1.4.0</span></div>
        </div>
      </InspSection>
      <div style={{ padding: 12 }}>
        <button className="btn btn-danger-ghost" style={{ width: "100%" }} onClick={onDelete}>Delete Node</button>
      </div>
    </>
  );
}
