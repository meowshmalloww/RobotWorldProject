import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TransformControls } from "@react-three/drei";
import * as THREE from "three";
import { Card } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, Segmented, StatusBadge, VecInput } from "../components/ui/controls";
import { Tree } from "../components/ui/Tree";
import { Viewport } from "../components/three/Viewport";
import { WarehouseKitchen } from "../components/three/WarehouseKitchen";
import { physicsChecks, scenarioVariants, sceneTree } from "../data/worlds";

type GizmoMode = "translate" | "rotate" | "scale";

export default function Worlds() {
  const nav = useNavigate();
  const [selected, setSelected] = useState<string | null>("cabinet-02");
  const [selectedName, setSelectedName] = useState("Kitchen Cabinet 02");
  const [gizmo, setGizmo] = useState<GizmoMode>("translate");
  const [playing, setPlaying] = useState(false);
  const [seed, setSeed] = useState("1048576");
  const [variant, setVariant] = useState("var_default");
  const [stageTab, setStageTab] = useState<"Stage Tree" | "Layers">("Stage Tree");
  const [inspTab, setInspTab] = useState<"Properties" | "References">("Properties");
  const [pos, setPos] = useState<[string, string, string]>(["1.842", "-0.615", "0.000"]);
  const [rot, setRot] = useState<[string, string, string]>(["0.000", "0.000", "90.000"]);
  const [scl, setScl] = useState<[string, string, string]>(["1.000", "1.000", "1.000"]);

  // gizmo target: a proxy object the TransformControls attach to
  const [proxyObj, setProxyObj] = useState<THREE.Group | null>(null);
  const showGizmo = selected === "cabinet-02" || selected === "fridge" || selected === "cart" || selected === "worktable";

  const variantCards = useMemo(() => scenarioVariants, []);

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", height: "calc(100vh - var(--topbar-h) - var(--statusbar-h))", overflow: "hidden" }}>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div>
          <h1 className="page-title">Scene Composer</h1>
          <p className="page-sub row" style={{ gap: 6 }}>
            Warehouse Kitchen v2
            <button className="icon-btn btn-sm" title="Rename"><Icon name="edit" size={11} /></button>
          </p>
        </div>
        <div className="head-actions">
          <span className="small t3">Last saved: 2m ago</span>
          <span className="btn-split">
            <button className="btn btn-primary"><Icon name="check" size={13} /> Save</button>
            <button className="btn btn-primary btn-split-caret"><Icon name="chevronDown" size={12} /></button>
          </span>
          <button className="btn btn-secondary" onClick={() => nav("/worlds/live")}>
            <Icon name="play" size={13} /> Live Evaluation
          </button>
        </div>
      </div>

      <div className="wo-layout" style={{ flex: 1 }}>
        {/* ---- Left: stage tree ---- */}
        <div className="wo-col">
          <Card
            flush
            style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
            title={
              <span className="tabs" style={{ border: 0 }}>
                {(["Stage Tree", "Layers"] as const).map((t) => (
                  <button key={t} className={stageTab === t ? "on" : ""} style={{ height: 26 }} onClick={() => setStageTab(t)}>
                    {t}
                  </button>
                ))}
              </span>
            }
            right={
              <span className="row" style={{ gap: 2 }}>
                <button className="icon-btn btn-sm" title="Add node"><Icon name="plus" size={12} /></button>
              </span>
            }
          >
            <div style={{ padding: "8px 8px 4px", borderBottom: "1px solid var(--border)" }}>
              <div className="search-box">
                <span className="search-ico"><Icon name="search" size={12} /></span>
                <input className="input" placeholder="Search nodes…" style={{ height: 26 }} />
              </div>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "6px 4px" }}>
              <Tree
                nodes={sceneTree as never}
                selected={selected}
                onSelect={(id, name) => { setSelected(id); setSelectedName(name); }}
              />
            </div>
          </Card>
        </div>

        {/* ---- Center: viewport + bottom panels ---- */}
        <div className="wo-center">
          <Card flush style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            {/* transport + toolbars */}
            <div className="row" style={{ gap: 6, padding: "7px 10px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
              <button className={`btn btn-sm ${playing ? "btn-secondary" : "btn-primary"}`} onClick={() => setPlaying(!playing)}>
                <Icon name={playing ? "pause" : "play"} size={12} /> {playing ? "Pause" : "Play"}
              </button>
              <button className="btn btn-ghost btn-sm btn-icon" title="Stop"><Icon name="stop" size={12} /></button>
              <span className="v-divider" style={{ margin: "0 4px" }} />
              <span className="small t2">Variant</span>
              <select className="select" style={{ width: 118, height: 26 }} value={variant} onChange={(e) => setVariant(e.target.value)}>
                {variantCards.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
              <span className="small t2">Seed</span>
              <input className="input mono" style={{ width: 84, height: 26 }} value={seed} onChange={(e) => setSeed(e.target.value)} />
              <button className="btn btn-ghost btn-sm btn-icon" title="Randomize seed" onClick={() => setSeed(String(Math.floor(Math.random() * 9_000_000 + 1_000_000)))}>
                <Icon name="refresh" size={12} />
              </button>
              <button className="btn btn-ghost btn-sm btn-icon" title="Step"><Icon name="gauge" size={12} /></button>
              <span className="grow" />
              <Segmented
                options={[
                  { value: "translate", icon: "move", label: "" },
                  { value: "rotate", icon: "rotate", label: "" },
                  { value: "scale", icon: "gizmoScale", label: "" },
                ]}
                value={gizmo}
                onChange={(v) => setGizmo(v)}
              />
              <button className="btn btn-ghost btn-sm btn-icon" title="Grid"><Icon name="grid" size={13} /></button>
              <button className="btn btn-ghost btn-sm btn-icon" title="Snapping"><Icon name="ruler" size={13} /></button>
            </div>

            {/* viewport */}
            <div style={{ flex: 1, padding: 10, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <div style={{ position: "relative", flex: 1, minHeight: 280, display: "flex", flexDirection: "column" }}>
                <Viewport
                  camera={{ position: [3.2, 2.4, 1.2], fov: 42 }}
                  target={[-0.1, 0.9, -2.8]}
                  style={{ flex: 1, minHeight: 0 }}
                  onPointerMissed={() => setSelected(null)}
                >
                  <WarehouseKitchen
                    selection={{
                      interactive: true,
                      selectedId: selected,
                      onSelect: (s) => {
                        setSelected(s?.id ?? null);
                        setSelectedName(s?.name ?? "");
                      },
                    }}
                    cabinetDoorOpen={{ left: variant === "var_left_hinge" ? 0.8 : 0 }}
                  />
                  {/* selection proxy on the wall cabinet + transform gizmo */}
                  <group ref={setProxyObj} position={[-0.5, 1.62, -3.48]}>
                    {showGizmo && (
                      <mesh>
                        <boxGeometry args={[0.94, 0.79, 0.38]} />
                        <meshBasicMaterial color="#4C8DFF" transparent opacity={0.08} depthWrite={false} />
                      </mesh>
                    )}
                  </group>
                  {showGizmo && proxyObj && (
                    <TransformControls object={proxyObj} mode={gizmo} size={0.75} />
                  )}
                </Viewport>
                <div className="vp-overlay" style={{ top: 10, left: 10 }}>
                  <span className="vp-chip"><span className="dot" style={{ background: "var(--green)" }} /> Simulation ready</span>
                </div>
                <div className="vp-overlay" style={{ top: 10, right: 10 }}>
                  <div className="vp-toolbar">
                    <button title="Perspective" style={{ width: "auto", padding: "0 8px", fontSize: "var(--fs-small)" }}>Persp</button>
                    <span className="sep" />
                    <button title="Lit" className="on" style={{ width: "auto", padding: "0 8px", gap: 5 }}><Icon name="sun" size={12} /> Lit</button>
                    <button title="Collision view"><Icon name="collider" size={13} /></button>
                    <span className="sep" />
                    <button title="Top view"><Icon name="grid" size={13} /></button>
                    <button title="Fullscreen"><Icon name="maximize" size={12} /></button>
                  </div>
                </div>
                {/* bottom transform toolbar */}
                <div className="vp-overlay" style={{ bottom: 10, left: "50%", transform: "translateX(-50%)" }}>
                  <div className="vp-toolbar">
                    <button title="Select"><Icon name="hand" size={13} /></button>
                    <button title="Orbit"><Icon name="rotate" size={13} /></button>
                    <button title="Pan"><Icon name="move" size={13} /></button>
                    <span className="sep" />
                    <button title="Focus selection"><Icon name="target" size={13} /></button>
                    <button title="Measure"><Icon name="ruler" size={13} /></button>
                    <button title="Screenshot"><Icon name="camera" size={13} /></button>
                  </div>
                </div>
              </div>
            </div>
          </Card>

          <div className="wo-center-bottom">
            {/* Scenario variants */}
            <Card title="Scenario Variants" right={<button className="btn btn-ghost btn-sm"><Icon name="plus" size={12} /> New Variant</button>} flush>
              <div style={{ padding: 10, overflowX: "auto" }}>
                <div className="variant-row" style={{ minWidth: 620 }}>
                  {variantCards.map((v) => (
                    <button key={v.id} className={`variant-card ${variant === v.id ? "active" : ""}`} onClick={() => setVariant(v.id)}>
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
                              left: v.id === "var_left_hinge" ? 0.85 : v.id === "var_default" ? 0.3 : 0,
                              right: v.id === "var_cluttered" ? 0.5 : 0,
                            }}
                          />
                        </Viewport>
                        {v.active && <span className="badge b-blue" style={{ position: "absolute", top: 5, left: 5, height: 16, fontSize: 9 }}>Active</span>}
                      </div>
                      <div className="v-body">
                        <div className="v-name">{v.name}<Icon name="dots" size={11} style={{ color: "var(--text-3)" }} /></div>
                        <div className="v-desc">{v.desc}</div>
                        {v.id !== "var_default" && <div className="v-id">ID: {v.id}</div>}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            </Card>

            {/* Physics checks */}
            <Card title="Placement &amp; Physics Checks" right={<button className="btn btn-ghost btn-sm"><Icon name="refresh" size={12} /> Re-run Checks</button>} flush>
              <div className="table-scroll" style={{ maxHeight: 178, overflowY: "auto" }}>
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
                        <td><span className={`sev ${c.severity.toLowerCase()}`}>● {c.severity}</span></td>
                        <td>{c.severity !== "Info" && <button className="btn btn-ghost btn-sm">View</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </div>

        {/* ---- Right: properties ---- */}
        <div className="wo-col">
          <Card
            flush
            pad={false}
            style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
            title={
              <span className="row" style={{ gap: 8, minWidth: 0 }}>
                <span className="cell-ico" style={{ width: 24, height: 24 }}><Icon name="cabinet" size={12} /></span>
                <span className="ellipsis" style={{ fontWeight: 620 }}>{selected ? selectedName : "Nothing selected"}</span>
                {selected && <Badge tone="grey">Static Mesh</Badge>}
              </span>
            }
            right={
              <span className="row" style={{ gap: 2 }}>
                <button className="icon-btn btn-sm" title="Lock"><Icon name="lock" size={12} /></button>
                <button className="icon-btn btn-sm" title="Visibility"><Icon name="eye" size={12} /></button>
              </span>
            }
          >
            <div className="tabs" style={{ padding: "0 12px" }}>
              {(["Properties", "References"] as const).map((t) => (
                <button key={t} className={inspTab === t ? "on" : ""} onClick={() => setInspTab(t)}>{t}</button>
              ))}
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}>
              {selected ? (
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
                  <InspSection title="Transform">
                    <div className="col" style={{ gap: 8 }}>
                      <div className="field"><label>Position (m)</label><VecInput values={pos} onChange={(i, v) => setPos((p) => { const n = [...p] as typeof p; n[i] = v; return n; })} /></div>
                      <div className="field"><label>Rotation (°)</label><VecInput values={rot} onChange={(i, v) => setRot((p) => { const n = [...p] as typeof p; n[i] = v; return n; })} /></div>
                      <div className="field"><label>Scale</label><VecInput values={scl} onChange={(i, v) => setScl((p) => { const n = [...p] as typeof p; n[i] = v; return n; })} /></div>
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
                    <button className="btn btn-danger-ghost" style={{ width: "100%" }}>Delete Node</button>
                  </div>
                </>
              ) : (
                <div className="empty-note">Select a node in the viewport or stage tree to inspect its properties.</div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
