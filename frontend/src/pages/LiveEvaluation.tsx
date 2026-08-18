import { useEffect, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { Card, Progress } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, Segmented } from "../components/ui/controls";
import { Viewport } from "../components/three/Viewport";
import { WarehouseKitchen } from "../components/three/WarehouseKitchen";
import { useToast } from "../components/ui/Toast";
import { downloadFile } from "../components/ui/Modal";
import { api, ApiError } from "../lib/api";
import { useWs } from "../lib/useWs";
import type { RenderVariant } from "../components/three/materials";
import type { ArmPose } from "../components/three/RobotArm";

/* ---- API contracts (backend/app/schemas.py) ------------------------------- */

interface SessionInfo {
  sessionId: string;
  runId: string;
  scenario: { name: string; desc: string; world: string; policy: string; variations: number; randomization: boolean };
  durationS: number;
}

interface LiveMeta {
  type: "meta";
  durationS: number;
  steps: { name: string }[];
  conditions: { name: string; target: string }[];
  events: { t: number; time: string; name: string; sub: string }[];
}

interface LiveFrame {
  type: "frame";
  t: number;
  pose: { yaw: number; shoulder: number; elbow: number; wrist: number; grip: number };
  door: number;
  gripper: "open" | "closed";
  forceN: number;
  inContact: boolean;
  contactName?: string;
  doorAngleDeg: number;
  success: number;
  stepsDone: number;
  conditions: boolean[];
  eventsFired: number[];
  done: boolean;
}

interface LiveEnd {
  type: "end";
  success: boolean;
  summary: string;
}

type LiveMsg = LiveMeta | LiveFrame | LiveEnd;

/**
 * Live Evaluation — the robot executes the scenario in real time on the
 * backend. Frames arrive over WS /ws/live/{sessionId}; `live` ref carries
 * the latest pose+door into every render of the shared procedural world
 * (no per-frame React renders); DOM panels sample state at 5 Hz.
 */
export default function LiveEvaluation({ embedded = false }: { embedded?: boolean }) {
  const toast = useToast();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [startedAt, setStartedAt] = useState<string>("");
  const [starting, setStarting] = useState(false);
  const [wsState, setWsState] = useState<"idle" | "connecting" | "open" | "closed">("idle");
  const [paused, setPaused] = useState(false);
  const [ended, setEnded] = useState(false);
  const [endSummary, setEndSummary] = useState<{ success: boolean; summary: string } | null>(null);
  const [speed, setSpeed] = useState("1×");
  const [meta, setMeta] = useState<LiveMeta | null>(null);
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [, setTick] = useState(0);

  // per-frame drive into the 3D world (never triggers React renders)
  const live = useRef<{ pose: ArmPose; door: number }>({ pose: { yaw: 0.62, shoulder: 0.55, elbow: -1.5, wrist: 0.7, grip: 0, door: 0 }, door: 0 });

  const startSession = async () => {
    setStarting(true);
    try {
      const s = await api.post<SessionInfo>("/eval/sessions", {});
      setSession(s);
      setStartedAt(new Date().toLocaleTimeString());
      setMeta(null);
      setFrame(null);
      setPaused(false);
      setEnded(false);
      setEndSummary(null);
      live.current = { pose: { yaw: 0.62, shoulder: 0.55, elbow: -1.5, wrist: 0.7, grip: 0, door: 0 }, door: 0 };
    } catch (e) {
      toast.push("err", "Could not start evaluation", e instanceof ApiError ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const send = useWs<LiveMsg>(session ? `/live/${session.sessionId}` : null, {
    enabled: !!session,
    onStatus: (s) => setWsState(s),
    onMessage: (msg) => {
      if (msg.type === "meta") {
        setMeta(msg);
      } else if (msg.type === "frame") {
        live.current = { pose: { ...msg.pose, door: msg.door }, door: msg.door };
        setFrame(msg);
        if (msg.done) setEnded(true);
      } else if (msg.type === "end") {
        setEnded(true);
        setEndSummary({ success: msg.success, summary: msg.summary });
        toast.push(msg.success ? "ok" : "info", msg.success ? "Run succeeded" : "Run ended", msg.summary);
      }
    },
  });

  // 5 Hz sampler — re-renders DOM panels from the latest frame
  useEffect(() => {
    const id = setInterval(() => setTick((x) => x + 1), 200);
    return () => clearInterval(id);
  }, []);

  const control = (action: "pause" | "resume" | "reset" | "speed" | "end", value?: number) =>
    send({ type: "control", action, ...(value !== undefined ? { value } : {}) });

  const durationS = meta?.durationS ?? session?.durationS ?? 0;
  const t = frame?.t ?? 0;
  const mm = String(Math.floor(t / 60)).padStart(2, "0");
  const ss = String(Math.floor(t % 60)).padStart(2, "0");
  const elapsed = `${mm}:${ss}`;
  const episodeProgress = durationS > 0 ? Math.min(1, t / durationS) : 0;
  const success = frame?.success ?? 0;
  const doorAngle = Math.round(frame?.doorAngleDeg ?? 0);
  const stepsTotal = meta?.steps.length ?? 0;
  const stepsDone = frame?.stepsDone ?? 0;

  const exportReplay = async () => {
    if (!session) return;
    try {
      const res = await fetch(`/api/eval/sessions/${session.sessionId}/replay`);
      if (!res.ok) throw new ApiError(res.status, `Replay export failed (${res.status})`);
      downloadFile(`replay_${session.runId}.json`, JSON.stringify(await res.json(), null, 2));
      toast.push("ok", "Replay exported", `replay_${session.runId}.json`);
    } catch (e) {
      toast.push("err", "Replay export failed", e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className={embedded ? "col" : "page"} style={embedded ? { flex: 1, minHeight: 0, gap: 10 } : undefined}>
      {!embedded && (
        <div className="page-head">
          <div>
            <h1 className="page-title row" style={{ gap: 9 }}>
              Live Evaluation {session && wsState === "open" && <Badge tone="live" dot>Live</Badge>}
            </h1>
            <p className="page-sub">{session ? `Watching run: ${session.scenario.name} — ${session.runId}` : "Start a run to stream a live evaluation."}</p>
          </div>
        </div>
      )}
      {/* run controls */}
      <div className="dockbar">
        {!session ? (
          <button className="btn btn-primary btn-sm" onClick={startSession} disabled={starting}>
            <Icon name="play" size={12} /> {starting ? "Starting…" : "Start run"}
          </button>
        ) : (
          <>
            {paused ? (
              <button className="btn btn-primary btn-sm" onClick={() => { control("resume"); setPaused(false); }}>
                <Icon name="play" size={12} /> Resume
              </button>
            ) : (
              <button className="btn btn-secondary btn-sm" onClick={() => { control("pause"); setPaused(true); }} disabled={ended}>
                <Icon name="pause" size={12} /> Pause
              </button>
            )}
            <button className="btn btn-secondary btn-sm" onClick={() => { control("reset"); setPaused(false); setEnded(false); setEndSummary(null); }}>
              <Icon name="reset" size={12} /> Reset
            </button>
            <button className="btn btn-secondary btn-sm" onClick={exportReplay}>
              <Icon name="download" size={12} /> Export replay
            </button>
          </>
        )}
        <span className="grow" />
        {session && (
          <span className="row" style={{ gap: 12 }}>
            <span className="micro t3">Run time <b className="mono t1">{elapsed}</b></span>
            <span className="micro t3">Sim time <b className="mono t1">{elapsed}</b></span>
          </span>
        )}
        {session && (
          ended ? (
            <Badge tone="grey">Run ended</Badge>
          ) : (
            <button className="btn btn-danger-ghost btn-sm" onClick={() => control("end")}>End Run</button>
          )
        )}
      </div>

      {endSummary && (
        <div className="card" style={{ padding: "10px 14px", display: "flex", gap: 9, alignItems: "center" }}>
          <Icon name={endSummary.success ? "check" : "warning"} size={14} style={{ color: endSummary.success ? "var(--green)" : "var(--amber)" }} />
          <span className="small" style={{ fontWeight: 580 }}>{endSummary.success ? "Success" : "Run finished"}</span>
          <span className="small t2">{endSummary.summary}</span>
        </div>
      )}

      <div className="le-layout">
        {/* Left — run status + scenario */}
        <div className="col" style={{ gap: 10 }}>
          <Card title="Run status" right={session && wsState === "open" && !ended ? <Badge tone="live" dot>Live</Badge> : session ? <Badge tone="grey">{wsState === "connecting" ? "Connecting" : ended ? "Ended" : "Offline"}</Badge> : <Badge tone="grey">Idle</Badge>}>
            {session ? (
              <>
                <div className="kv">
                  <KV k="Run ID" v={<span className="mono">{session.runId}</span>} />
                  <KV k="World" v={session.scenario.world} />
                  <KV k="Scenario" v={session.scenario.name} />
                  <KV k="Policy" v={session.scenario.policy} />
                  <KV k="Start time" v={startedAt || "—"} />
                  <KV k="Elapsed" v={<span className="mono">{elapsed}</span>} />
                </div>
                <div style={{ marginTop: 10 }}>
                  <div className="row between small" style={{ marginBottom: 4 }}>
                    <span className="t2">Overall success</span>
                    <span className="mono" style={{ fontWeight: 620 }}>{success.toFixed(1)}%</span>
                  </div>
                  <Progress value={success} tone="green" tall />
                </div>
              </>
            ) : (
              <div className="empty-note">No active run — press <b>Start run</b> to create an evaluation session.</div>
            )}
          </Card>

          <Card title="Scenario">
            {session ? (
              <>
                <div style={{ fontWeight: 620, marginBottom: 4 }}>{session.scenario.name}</div>
                <p className="small t2" style={{ marginBottom: 10 }}>{session.scenario.desc}</p>
                <div className="kv">
                  <KV k="Initial state" v={<span className="badge b-grey" style={{ height: 18 }}>{session.scenario.world}</span>} />
                  <KV k="Variations" v={<span className="mono">{session.scenario.variations}</span>} />
                  <KV k="Domain randomization" v={session.scenario.randomization ? <Badge tone="green" dot>On</Badge> : <Badge tone="grey">Off</Badge>} />
                </div>
              </>
            ) : (
              <div className="empty-note">Scenario details appear once a session starts.</div>
            )}
          </Card>
        </div>

        {/* Center — viewport + sensor views */}
        <div className="le-center">
          <Card flush style={{ padding: 10, flex: 1, minHeight: 0 }}>
            <div style={{ position: "relative", flex: 1, minHeight: 420, display: "flex", flexDirection: "column" }}>
              <Viewport
                camera={{ position: [3.0, 2.1, 0.9], fov: 40 }}
                target={[0.8, 1.1, -3.0]}
                style={{ flex: 1, minHeight: 0 }}
                gizmo={false}
              >
                <WarehouseKitchen liveRef={live} />
              </Viewport>
              {!session && (
                <div className="vp-overlay" style={{ inset: 0, display: "grid", placeItems: "center", background: "rgba(20,22,27,0.55)", borderRadius: "var(--r-md)" }}>
                  <div className="col center" style={{ gap: 10 }}>
                    <Icon name="play" size={22} style={{ color: "var(--text-2)" }} />
                    <span className="t2 small">No evaluation session</span>
                    <button className="btn btn-primary" onClick={startSession} disabled={starting}>
                      <Icon name="play" size={13} /> {starting ? "Starting…" : "Start a run"}
                    </button>
                  </div>
                </div>
              )}
              {session && (
                <div className="vp-overlay" style={{ top: 12, left: 12 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="vp-chip"><span className="dot" style={{ background: wsState === "open" && !ended ? "var(--red)" : "var(--text-3)" }} /> {wsState === "open" ? (ended ? "ENDED" : "LIVE") : wsState === "connecting" ? "CONNECTING" : "OFFLINE"}</span>
                    <span className="vp-chip mono">20 Hz telemetry</span>
                  </div>
                </div>
              )}
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
                <SensorView label="Third Person" dotColor="var(--accent)" variant="rgb" cam={[2.6, 1.9, 0.4]} tgt={[0.7, 1.1, -3.0]} live={live} />
                <SensorView label="Wrist Camera" dotColor="var(--green)" variant="rgb" cam={[0.9, 1.35, -2.2]} tgt={[1.2, 1.25, -3.0]} live={live} fov={58} />
                <SensorView label="Segmentation" dotColor="var(--purple)" variant="seg" cam={[2.6, 1.9, 0.4]} tgt={[0.7, 1.1, -3.0]} live={live} />
                <SensorView label="Depth" dotColor="#8A94A6" variant="depth" cam={[0.4, 1.7, 0.9]} tgt={[0.8, 1.1, -3.0]} live={live} />
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
                onChange={(v) => {
                  setSpeed(v);
                  control("speed", v === "2×" ? 2 : v === "0.5×" ? 0.5 : 1);
                }}
              />
            }
          >
            {meta && meta.events.length > 0 ? (
              <>
                <div className="evt-timeline" style={{ paddingTop: 12 }}>
                  <div className="evt-track">
                    <div className="fill" style={{ width: `${episodeProgress * 100}%` }} />
                    {meta.events.map((e, i) => {
                      const done = frame?.eventsFired.includes(i) ?? false;
                      const isNow = !done && meta.events.findIndex((_, j) => !(frame?.eventsFired.includes(j) ?? false)) === i;
                      return (
                        <div key={e.name} className="evt-node" style={{ left: `${e.t * 100}%` }}>
                          <div
                            className="n-dot center"
                            style={{
                              background: done ? "var(--green)" : isNow ? "var(--accent)" : "var(--bg-panel-3)",
                              borderColor: done ? "var(--green)" : isNow ? "var(--accent)" : "#3A4150",
                              color: "#fff",
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
                  {meta.events.map((e) => <span key={e.time + e.name}>{e.time}</span>)}
                </div>
              </>
            ) : (
              <div className="empty-note">{session ? "Waiting for scenario metadata…" : "Start a run to see the event timeline."}</div>
            )}
          </Card>
        </div>

        {/* Right — robot state */}
        <div className="col" style={{ gap: 10 }}>
          <Card title="Robot state" right={<Badge tone={session && !ended ? "green" : "grey"} dot={!!session && !ended}>{session ? (ended ? "Ended" : "Nominal") : "Idle"}</Badge>}>
            <div className="kv">
              <KV k="End-effector mode" v="Pinch" />
              <KV k="Gripper state" v={frame ? (frame.gripper === "closed" ? "Closed" : "Open") : "—"} />
              <KV k="Applied force" v={<span className="mono">{frame ? `${frame.forceN.toFixed(1)} N` : "—"}</span>} />
              <KV k="Task step" v={<span className="mono">{stepsTotal ? `${Math.min(stepsDone + 1, stepsTotal)} / ${stepsTotal}` : "—"}</span>} />
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
                  <span style={{ fontWeight: 580, fontSize: "var(--fs-body)" }}>{frame?.contactName ?? "—"}</span>
                  <span className="micro t3 mono">{frame?.inContact ? "contact" : "no contact"}</span>
                </span>
                {frame?.inContact ? <Badge tone="green" dot>In contact</Badge> : <Badge tone="grey">No contact</Badge>}
              </div>
            </div>
          </Card>

          <Card title="Success conditions" flush>
            <div style={{ padding: "4px 0" }}>
              {meta && meta.conditions.length > 0 ? (
                <>
                  {meta.conditions.map((c, i) => {
                    const ok = frame?.conditions[i] ?? false;
                    return (
                      <div key={c.name} className="row" style={{ gap: 9, padding: "6px 14px", borderBottom: "1px solid rgba(148,170,220,0.05)" }}>
                        <span style={{ color: ok ? "var(--green)" : "var(--text-3)", display: "inline-flex" }}>
                          <Icon name={ok ? "check" : "clock"} size={13} />
                        </span>
                        <span className="grow" style={{ fontSize: "var(--fs-body)", color: ok ? "var(--text-1)" : "var(--text-2)" }}>{c.name}</span>
                        <span className={`mono small ${ok ? "g-green" : "g-blue"}`}>{c.target}</span>
                      </div>
                    );
                  })}
                  <div className="row" style={{ gap: 9, padding: "8px 14px" }}>
                    <span style={{ color: success >= 100 ? "var(--green)" : "var(--text-3)", display: "inline-flex" }}><Icon name={success >= 100 ? "check" : "clock"} size={13} /></span>
                    <span className="grow" style={{ fontWeight: 620 }}>Overall</span>
                    <span className={`${success >= 100 ? "g-green" : "g-blue"} small`} style={{ fontWeight: 620 }}>{success >= 100 ? "Passed" : "On track"}</span>
                  </div>
                </>
              ) : (
                <div className="empty-note">{session ? "Waiting for scenario metadata…" : "Start a run to evaluate success conditions."}</div>
              )}
            </div>
          </Card>

          <Card title="Task State">
            {meta && meta.steps.length > 0 ? (
              <div className="steps">
                {meta.steps.map((s, i) => {
                  const state = i < stepsDone ? "done" : i === stepsDone ? "active" : "pending";
                  return (
                    <div key={s.name} className={`step ${state}`}>
                      <span className="s-rail">
                        <span className="s-dot">
                          {state === "done" ? <Icon name="check" size={9} strokeWidth={2.4} /> : i + 1}
                        </span>
                        <span className="s-line" />
                      </span>
                      <span className="s-body">
                        <span className="s-name">{i + 1} · {s.name}</span>
                        <span className={`micro ${state === "done" ? "g-green" : state === "active" ? "g-blue" : "t3"}`}>
                          {state === "done" ? "Success" : state === "active" ? "In Progress" : "Pending"}
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="empty-note">{session ? "Waiting for scenario metadata…" : "Start a run to track task steps."}</div>
            )}
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

/** World bound to the live frame ref inside a sensor tile. */
function AnimatedWorld({ live, variant }: { live: React.MutableRefObject<{ pose: ArmPose; door: number }>; variant: RenderVariant }) {
  return <WarehouseKitchen variant={variant} liveRef={live} />;
}

/** One live sensor tile — its own small render of the shared world. */
function SensorView({
  label, dotColor, variant, cam, tgt, live, fov,
}: {
  label: string;
  dotColor: string;
  variant: RenderVariant;
  cam: [number, number, number];
  tgt: [number, number, number];
  live: React.MutableRefObject<{ pose: ArmPose; door: number }>;
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
        <AnimatedWorld live={live} variant={variant} />
        <OrbitControls target={tgt} enableDamping={false} enablePan={false} enableZoom={false} enableRotate={false} />
      </Canvas>
      <span className="thumb-label"><span className="dot" style={{ background: dotColor }} /> {label}</span>
    </div>
  );
}
