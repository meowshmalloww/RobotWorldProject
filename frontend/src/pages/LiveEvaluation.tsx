import { useEffect, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { Card, Progress } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, Segmented } from "../components/ui/controls";
import { Viewport } from "../components/three/Viewport";
import { WarehouseKitchen } from "../components/three/WarehouseKitchen";
import { eventTimeline, successConditions, taskSteps } from "../data/worlds";
import type { RenderVariant } from "../components/three/materials";

const RUN_LENGTH = 14; // seconds on the scripted timeline

interface SimClock { t: number }

/**
 * Live Evaluation — the robot executes "Open Refrigerator" in real time.
 * Animation runs on a shared simulation clock advanced inside the master
 * canvas via useFrame (no per-frame React renders); DOM panels sample the
 * clock at 5 Hz. All five sensor views render the SAME procedural world:
 * wrist = gripper-frame camera, segmentation = per-object ID materials,
 * depth = MeshDepthMaterial pass.
 */
export default function LiveEvaluation() {
  const sim = useRef<SimClock>({ t: 0 });
  const [running, setRunning] = useState(true);
  const [speed, setSpeed] = useState("1×");
  const speedN = speed === "2×" ? 2 : speed === "0.5×" ? 0.5 : 1;
  const [, setTick] = useState(0);

  // sample the sim clock for DOM panels at 5 Hz
  useEffect(() => {
    const id = setInterval(() => setTick((x) => x + 1), 200);
    return () => clearInterval(id);
  }, []);

  const t = sim.current.t;
  const mm = String(Math.floor(t / 60)).padStart(2, "0");
  const ss = String(Math.floor(t % 60)).padStart(2, "0");
  const elapsed = `${mm}:${ss}`;
  const episodeProgress = Math.min(1, t / RUN_LENGTH);

  const gates = [0.05, 0.18, 0.55, 0.8, 0.93, 1.01];
  const p = t / RUN_LENGTH;
  const steps = taskSteps.map((s, i) => ({
    ...s,
    state: p >= gates[i] ? ("done" as const) : i === gates.findIndex((g) => p < g) ? ("active" as const) : ("pending" as const),
  }));

  const doorAngle = p < 0.2 ? 0 : p > 0.5 ? 95 : Math.round(((p - 0.2) / 0.3) * 95);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title row" style={{ gap: 9 }}>
            Live Evaluation <Badge tone="live" dot>Live</Badge>
          </h1>
          <p className="page-sub">Watching run: Open Cabinet — Generalization v2</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary" onClick={() => setRunning(true)}><Icon name="play" size={12} /> Start</button>
          <button className="btn btn-secondary" onClick={() => setRunning(false)}><Icon name="pause" size={12} /> Pause</button>
          <button className="btn btn-secondary" onClick={() => { sim.current.t = 0; setTick((x) => x + 1); }}><Icon name="reset" size={12} /> Reset</button>
          <button className="btn btn-secondary">Export replay <Icon name="chevronDown" size={12} /></button>
          <span className="col" style={{ textAlign: "right", gap: 0, marginLeft: 8 }}>
            <span className="micro t3">Run time &nbsp;·&nbsp; Sim time</span>
            <span className="mono" style={{ fontWeight: 620 }}>{elapsed} &nbsp; {elapsed}</span>
          </span>
          <button className="btn btn-danger-ghost">End Run</button>
        </div>
      </div>

      <div className="le-layout">
        {/* Left — run status + scenario */}
        <div className="col" style={{ gap: 10 }}>
          <Card title="Run status" right={<Badge tone="live" dot>Live</Badge>}>
            <div className="kv">
              <KV k="Run ID" v={<span className="mono">run_7f9c2e81</span>} />
              <KV k="World" v="Warehouse Kitchen v2" />
              <KV k="Scenario" v="Open Refrigerator" />
              <KV k="Policy" v="Open Cabinet Policy v3" />
              <KV k="Start time" v="10:13:42 AM" />
              <KV k="Elapsed" v={<span className="mono">{elapsed}</span>} />
              <KV k="Episode" v={<span className="mono">4 / ∞</span>} />
            </div>
            <div style={{ marginTop: 10 }}>
              <div className="row between small" style={{ marginBottom: 4 }}>
                <span className="t2">Overall success</span>
                <span className="mono" style={{ fontWeight: 620 }}>{(75 * episodeProgress).toFixed(1)}%</span>
              </div>
              <Progress value={75 * episodeProgress} tone="green" tall />
            </div>
          </Card>

          <Card title="Scenario">
            <div style={{ fontWeight: 620, marginBottom: 4 }}>Open Refrigerator</div>
            <p className="small t2" style={{ marginBottom: 10 }}>Open the refrigerator door and expose the interior.</p>
            <div className="kv">
              <KV k="Initial state" v={<span className="badge b-grey" style={{ height: 18 }}>warehouse_v2.usd</span>} />
              <KV k="Variations" v={<span className="mono">24</span>} />
              <KV k="Domain randomization" v={<Badge tone="green" dot>On</Badge>} />
            </div>
          </Card>
        </div>

        {/* Center — viewport + sensor views */}
        <div className="le-center">
          <Card flush style={{ padding: 10 }}>
            <div style={{ position: "relative" }}>
              <Viewport
                camera={{ position: [3.0, 2.1, 0.9], fov: 40 }}
                target={[0.8, 1.1, -3.0]}
                style={{ height: 400 }}
                gizmo={false}
              >
                <SimClock sim={sim} running={running} speed={speedN} master />
                <WarehouseKitchen simRef={sim} />
              </Viewport>
              <div className="vp-overlay" style={{ top: 12, left: 12 }}>
                <div className="row" style={{ gap: 6 }}>
                  <span className="vp-chip"><span className="dot" style={{ background: "var(--red)" }} /> LIVE</span>
                  <span className="vp-chip mono">60 FPS</span>
                </div>
              </div>
              <div className="vp-overlay" style={{ bottom: 12, left: 12 }}>
                <span className="vp-chip">Camera: Third Person <Icon name="chevronDown" size={11} /></span>
              </div>
              <div className="vp-overlay" style={{ bottom: 12, right: 12 }}>
                <div className="vp-toolbar">
                  <button title="Screenshot"><Icon name="camera" size={13} /></button>
                  <button title="Overlays" className="on"><Icon name="grid" size={13} /></button>
                  <button title="Markers"><Icon name="target" size={13} /></button>
                  <button title="Settings"><Icon name="settings" size={13} /></button>
                  <button title="Fullscreen"><Icon name="maximize" size={12} /></button>
                </div>
              </div>
            </div>

            {/* sensor views */}
            <div style={{ padding: "10px 2px 2px" }}>
              <div className="section-label" style={{ marginBottom: 7 }}>Views</div>
              <div className="le-views">
                <SensorView label="Third Person" dotColor="var(--accent)" variant="rgb" cam={[2.6, 1.9, 0.4]} tgt={[0.7, 1.1, -3.0]} sim={sim} />
                <SensorView label="Wrist Camera" dotColor="var(--green)" variant="rgb" cam={[0.9, 1.35, -2.2]} tgt={[1.2, 1.25, -3.0]} sim={sim} fov={58} />
                <SensorView label="Segmentation" dotColor="var(--purple)" variant="seg" cam={[2.6, 1.9, 0.4]} tgt={[0.7, 1.1, -3.0]} sim={sim} />
                <SensorView label="Depth" dotColor="#8A94A6" variant="depth" cam={[0.4, 1.7, 0.9]} tgt={[0.8, 1.1, -3.0]} sim={sim} />
                <button className="add-view" title="Add view"><Icon name="plus" size={14} /></button>
              </div>
            </div>
          </Card>

          {/* event timeline */}
          <Card
            title="Event timeline"
            right={
              <Segmented
                options={[{ value: "0.5×", label: "0.5×" }, { value: "1×", label: "1×" }, { value: "2×", label: "2×" }]}
                value={speed}
                onChange={setSpeed}
              />
            }
          >
            <div className="evt-timeline" style={{ paddingTop: 12 }}>
              <div className="evt-track">
                <div className="fill" style={{ width: `${episodeProgress * 100}%` }} />
                {eventTimeline.map((e) => {
                  const done = episodeProgress >= e.t;
                  const isNow = !done && eventTimeline.findIndex((x) => episodeProgress < x.t) === eventTimeline.indexOf(e);
                  return (
                    <div key={e.name} className="evt-node" style={{ left: `${e.t * 100}%` }}>
                      <div
                        className="n-dot center"
                        style={{
                          background: done ? "var(--green)" : isNow ? "var(--accent)" : "var(--bg-panel-3)",
                          borderColor: done ? "var(--green)" : isNow ? "var(--accent)" : "#3A455C",
                          color: "#fff",
                          boxShadow: isNow ? "0 0 10px rgba(76,141,255,0.7)" : undefined,
                        }}
                      >
                        {done && <Icon name="check" size={9} strokeWidth={2.4} />}
                      </div>
                      <div className="evt-label" style={{ left: 0 }}>
                        <div className="t">{e.time}</div>
                        <div className="n">{e.name}</div>
                        <div className={`s ${done ? "g-green" : isNow ? "g-blue" : "t3"}`}>{done ? "Completed" : isNow ? "In progress" : e.sub}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="row mono micro t3" style={{ justifyContent: "space-between", padding: "34px 8px 0" }}>
              {["00:00", "00:03", "00:06", "00:09", "00:12", "00:14"].map((x) => <span key={x}>{x}</span>)}
            </div>
          </Card>
        </div>

        {/* Right — robot state */}
        <div className="col" style={{ gap: 10 }}>
          <Card title="Robot state" right={<Badge tone="green" dot>Nominal</Badge>}>
            <div className="kv">
              <KV k="End-effector mode" v="Pinch" />
              <KV k="Gripper state" v={t > 2.9 && t < 8.1 ? "Closed" : "Open"} />
              <KV k="Applied force" v={<span className="mono">{t > 3 && t < 8 ? "4.8 N" : "0.6 N"}</span>} />
              <KV k="Task step" v={<span className="mono">{steps.findIndex((s) => s.state === "active") + 1 || 6} / 6</span>} />
            </div>
            <div style={{ marginTop: 10 }}>
              <div className="small t2" style={{ marginBottom: 5 }}>Pull door open</div>
              <div className="row" style={{ gap: 3 }}>
                {[0, 1, 2, 3, 4].map((i) => (
                  <i key={i} style={{ flex: 1, height: 5, borderRadius: 3, background: (doorAngle / 95) * 5 > i ? "var(--green)" : "rgba(148,170,220,0.15)" }} />
                ))}
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <div className="small t2" style={{ marginBottom: 6 }}>Object in contact</div>
              <div className="row card" style={{ gap: 9, padding: 8, background: "var(--bg-panel-2)" }}>
                <span className="cell-ico"><Icon name="gripper" size={13} /></span>
                <span className="col grow" style={{ gap: 0 }}>
                  <span style={{ fontWeight: 580, fontSize: "var(--fs-body)" }}>Refrigerator Door Handle</span>
                  <span className="micro t3 mono">M_Fridge_Handle_01</span>
                </span>
                {t > 2.9 && t < 8.1 ? <Badge tone="green" dot>In contact</Badge> : <Badge tone="grey">No contact</Badge>}
              </div>
            </div>
          </Card>

          <Card title="Success conditions" flush>
            <div style={{ padding: "4px 0" }}>
              {successConditions.map((c) => {
                const angleOk = c.name.startsWith("Door open") ? doorAngle >= 60 : true;
                return (
                  <div key={c.name} className="row" style={{ gap: 9, padding: "6px 14px", borderBottom: "1px solid rgba(148,170,220,0.05)" }}>
                    <span style={{ color: angleOk ? "var(--green)" : "var(--text-3)", display: "inline-flex" }}>
                      <Icon name={angleOk ? "check" : "clock"} size={13} />
                    </span>
                    <span className="grow" style={{ fontSize: "var(--fs-body)", color: angleOk ? "var(--text-1)" : "var(--text-2)" }}>{c.name}</span>
                    <span className={`mono small ${angleOk ? "g-green" : "g-blue"}`}>
                      {c.name.startsWith("Door open") ? `${doorAngle}°` : c.value}
                    </span>
                  </div>
                );
              })}
              <div className="row" style={{ gap: 9, padding: "8px 14px" }}>
                <span style={{ color: "var(--green)", display: "inline-flex" }}><Icon name="check" size={13} /></span>
                <span className="grow" style={{ fontWeight: 620 }}>Overall</span>
                <span className="g-green small" style={{ fontWeight: 620 }}>On track</span>
              </div>
            </div>
          </Card>

          <Card title="Task State">
            <div className="steps">
              {steps.map((s, i) => (
                <div key={s.name} className={`step ${s.state}`}>
                  <span className="s-rail">
                    <span className="s-dot">
                      {s.state === "done" ? <Icon name="check" size={9} strokeWidth={2.4} /> : i + 1}
                    </span>
                    <span className="s-line" />
                  </span>
                  <span className="s-body">
                    <span className="s-name">{i + 1} · {s.name}</span>
                    <span className={`micro ${s.state === "done" ? "g-green" : s.state === "active" ? "g-blue" : "t3"}`}>
                      {s.state === "done" ? "Success" : s.state === "active" ? "In Progress" : "Pending"}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="kv-row">
      <span className="kv-k">{k}</span>
      <span className="kv-v">{v}</span>
    </div>
  );
}

/** Advances the shared clock — only the master canvas mutates it. */
function SimClock({ sim, running, speed, master }: { sim: React.MutableRefObject<SimClock>; running: boolean; speed: number; master?: boolean }) {
  useFrame((_, dt) => {
    if (master && running) {
      sim.current.t = (sim.current.t + dt * speed) % RUN_LENGTH;
    }
  });
  return null;
}

/** World bound to the sim clock inside a sensor tile. */
function AnimatedWorld({ sim, variant }: { sim: React.MutableRefObject<SimClock>; variant: RenderVariant }) {
  return <WarehouseKitchen variant={variant} simRef={sim} />;
}

/** One live sensor tile — its own small render of the shared world. */
function SensorView({
  label, dotColor, variant, cam, tgt, sim, fov,
}: {
  label: string;
  dotColor: string;
  variant: RenderVariant;
  cam: [number, number, number];
  tgt: [number, number, number];
  sim: React.MutableRefObject<SimClock>;
  fov?: number;
}) {
  return (
    <div className="thumb clickable">
      <Canvas
        dpr={[0.5, 0.9]}
        camera={{ position: cam, fov: fov ?? 46 }}
        gl={{ antialias: false, toneMapping: variant === "rgb" ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping, preserveDrawingBuffer: true }}
        style={{ position: "absolute", inset: 0 }}
      >
        {variant === "rgb" ? (
          <>
            <hemisphereLight args={["#B8C4D6", "#26282D", 0.7]} />
            <directionalLight position={[5.5, 7.5, 3.5]} intensity={1.4} />
            <pointLight position={[0, 3.5, -1]} intensity={16} color="#DCE9F5" distance={12} decay={2} />
          </>
        ) : (
          <ambientLight intensity={1.5} />
        )}
        <AnimatedWorld sim={sim} variant={variant} />
        <OrbitControls target={tgt} enableDamping={false} enablePan={false} enableZoom={false} enableRotate={false} />
      </Canvas>
      <span className="thumb-label"><span className="dot" style={{ background: dotColor }} /> {label}</span>
    </div>
  );
}
