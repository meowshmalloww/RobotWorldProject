import { useEffect, useRef, useState } from "react";
import { Card } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, apiUrl, ApiError, uploadBinary } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

interface CameraValidation {
  rgbVariance: number;
  robotPixels: number;
  gripperPixels: number;
  workspacePixels: number;
  sha256: string;
}

interface RobotManifest {
  id: string;
  name: string;
  format: string;
  sourcePath?: string;
  sourceRevision?: string;
  sourceUrl?: string;
  sha256?: string;
  runtimeSha256?: string;
  armDof?: number;
  gripperJoints?: number;
  links: number;
  joints: number;
  cameras: number;
  cameraNames: string[];
  physicsReady?: boolean;
  wristCameraMount?: { translationM: number[]; quaternionWxyz: number[] };
  wristCameraCalibrated?: boolean;
  validation?: {
    passed: boolean;
    errors: string[];
    mujocoVersion: string;
    timestepSeconds: number;
    severeInitialContacts: number;
    maxHomeDrift: number;
    closedWidthM: number;
    openWidthM: number;
    cameraCalibration: Record<string, CameraValidation>;
  };
  readiness: { executable: boolean; physicsExecutable?: boolean; policyExecutable?: boolean; blockers: string[] };
}

interface RobotRegistration {
  id: string;
  lifecycleState: string;
  active: boolean;
  definition: { sensors?: { id: string; parentLink: string; calibrated: boolean; calibrationSource: string }[] };
}

interface RobotListResponse {
  robots: RobotManifest[];
  registrations: RobotRegistration[];
  accepted: string[];
  maxBytes: number;
  defaultBackend: string;
  deferredBackends: string[];
}

interface CommandResponse {
  commandId: string;
  status: string;
  result: { robot?: RobotManifest; registration?: RobotRegistration; loadProbe?: { resident: boolean; homeFinite: boolean; nq: number; actuators: number; workerContract: string } };
}

interface IsaacStatus {
  installed: boolean;
  ready: boolean;
  version: string;
  eulaAcceptedForApiProcess: boolean;
  blockers: string[];
}

function failureText(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}

export default function Robots() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<RobotListResponse>("/robots");
  const { data: isaac } = useApi<IsaacStatus>("/simulation/isaac");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [mountTranslation, setMountTranslation] = useState(["0.04", "0", "0.055"]);
  const [mountQuaternion, setMountQuaternion] = useState(["0", "0.70710678", "0.70710678", "0"]);

  useEffect(() => {
    if (!selectedId && data?.robots.length) setSelectedId(data.robots[0].id);
  }, [data, selectedId]);

  const selected = data?.robots.find((robot) => robot.id === selectedId) ?? data?.robots[0] ?? null;
  const registrations = new Map((data?.registrations ?? []).map((registration) => [registration.id, registration]));
  const selectedRegistration = selected ? registrations.get(selected.id) : undefined;

  const registerFranka = async () => {
    setBusy("franka");
    try {
      const result = await api.post<CommandResponse>("/robots/franka/mujoco", {
        allowDownload: false,
        wristCameraTranslationM: mountTranslation.map(Number),
        wristCameraQuaternionWxyz: mountQuaternion.map(Number),
      });
      const robot = result.result.robot;
      if (robot) setSelectedId(robot.id);
      toast.push("ok", "Franka physics validation passed", `${robot?.id ?? "registered"} · ${result.commandId}`);
      refetch();
    } catch (reason) {
      toast.push("err", "Franka registration failed", failureText(reason));
    } finally {
      setBusy(null);
    }
  };

  const activate = async (robot: RobotManifest) => {
    setBusy(`activate:${robot.id}`);
    try {
      const result = await api.post<CommandResponse>(`/robots/${robot.id}/activate`, {});
      const probe = result.result.loadProbe;
      toast.push("ok", "Robot runtime selected", `${robot.name} · nq ${probe?.nq} · ${probe?.resident ? "resident" : "reload-per-job"}`);
      refetch();
    } catch (reason) {
      toast.push("err", "Robot activation failed", failureText(reason));
    } finally {
      setBusy(null);
    }
  };

  const importFile = async (file: File) => {
    setBusy("import");
    try {
      const robot = await uploadBinary<RobotManifest>("/robots/import", file);
      setSelectedId(robot.id);
      toast.push("ok", "Robot source imported", `${robot.name} · ${robot.format}`);
      refetch();
    } catch (reason) {
      toast.push("err", "Robot import failed", failureText(reason));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Embodiment registry</div>
          <h1 className="page-title">Robots &amp; Embodiments</h1>
          <p className="page-sub">Inspect canonical kinematics, cameras, controllers, and authoritative physics readiness. A visual mesh alone is never a controllable robot.</p>
        </div>
        <div className="head-actions">
          <input ref={fileRef} type="file" hidden accept=".urdf,.xml,.mjcf,.usd,.usda,.usdc,.glb" onChange={(event) => event.target.files?.[0] && importFile(event.target.files[0])} />
          <button className="btn btn-secondary" disabled={busy !== null} onClick={() => fileRef.current?.click()}><Icon name="upload" size={13} /> Import source</button>
          <button className="btn btn-secondary" onClick={refetch}><Icon name="refresh" size={13} /> Refresh</button>
        </div>
      </div>

      <div className="callout" style={{ margin: "0 0 10px", borderColor: "rgba(94, 234, 212, .25)" }}>
        <Icon name="info" size={13} style={{ color: "var(--teal)" }} />
        <span><b>Live backend: MuJoCo.</b> Isaac Sim {isaac?.installed ? `${isaac.version} is installed${isaac.ready ? " and worker-ready" : " but still gated before live PhysX"}` : "is not installed"}. Calibration previews below are authoritative MJCF renders, not frontend animation.</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "330px minmax(0, 1fr)", gap: 10, alignItems: "start" }}>
        <div className="col" style={{ gap: 10 }}>
          <Card title="Default Franka Panda" right={<Badge tone="green" icon="check">Apache-2.0</Badge>}>
            <div className="col" style={{ gap: 10 }}>
              <div className="small t2">Pinned MuJoCo Menagerie source, 7 arm joints, two-finger gripper, fixed base, deterministic home keyframe, front camera, and an explicit wrist mount.</div>
              <div className="field"><label>Wrist camera translation (m)</label><div className="vec-row">{["X", "Y", "Z"].map((axis, index) => <span className="vec-cell" key={axis}><span className={`axis-tag axis-${axis.toLowerCase()}`}>{axis}</span><input className="input" value={mountTranslation[index]} onChange={(event) => setMountTranslation((values) => values.map((value, i) => i === index ? event.target.value : value))} /></span>)}</div></div>
              <div className="field"><label>Wrist quaternion W / X / Y / Z</label><div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 5 }}>{["W", "X", "Y", "Z"].map((axis, index) => <input className="input mono" aria-label={`Quaternion ${axis}`} key={axis} value={mountQuaternion[index]} onChange={(event) => setMountQuaternion((values) => values.map((value, i) => i === index ? event.target.value : value))} />)}</div></div>
              <button className="btn btn-primary" disabled={busy !== null || [...mountTranslation, ...mountQuaternion].some((value) => !Number.isFinite(Number(value)))} onClick={registerFranka}><Icon name={busy === "franka" ? "refresh" : "robot"} size={13} /> {busy === "franka" ? "Validating physics…" : "Register & validate Franka"}</button>
              <div className="micro t3">No model or simulator download occurs unless the source cache is absent and an explicit allow-download command is sent.</div>
            </div>
          </Card>

          <Card title="Robot catalog" flush right={<span className="micro t3">{data?.robots.length ?? 0}</span>}>
            {loading && !data ? <Skeleton rows={5} /> : error ? <ErrorState message={error.message} onRetry={refetch} /> : !data?.robots.length ? <EmptyState icon="robot">No robots imported.</EmptyState> : <div>{data.robots.map((robot) => {
              const registration = registrations.get(robot.id);
              return <button key={robot.id} onClick={() => setSelectedId(robot.id)} style={{ width: "100%", textAlign: "left", padding: "10px 12px", borderBottom: "1px solid var(--border)", background: selected?.id === robot.id ? "var(--accent-soft)" : "transparent" }}>
                <div className="row" style={{ gap: 8 }}><Icon name="robot" size={14} style={{ color: robot.physicsReady ? "var(--green)" : "var(--text-3)" }} /><span style={{ fontWeight: 620 }}>{robot.name}</span>{registration?.active && <Badge tone="live" dot>Active</Badge>}</div>
                <div className="micro t3 mono" style={{ marginTop: 3 }}>{robot.id}</div>
                <div className="micro t3">{robot.format} · {robot.joints} joints · {robot.cameras} cameras</div>
              </button>;
            })}</div>}
          </Card>
        </div>

        {selected ? <div className="col" style={{ gap: 10 }}>
          <Card title={selected.name} right={<div className="row" style={{ gap: 6 }}><StatusBadge status={selected.physicsReady ? "ready" : "blocked"} />{selectedRegistration?.active && <Badge tone="live" dot>Selected</Badge>}</div>}>
            <div className="st-grid">
              <div className="kv-row"><span className="kv-k">Source</span><span className="kv-v mono">{selected.format}</span></div>
              <div className="kv-row"><span className="kv-k">Revision</span><span className="kv-v mono">{selected.sourceRevision?.slice(0, 16) ?? "unrecorded"}</span></div>
              <div className="kv-row"><span className="kv-k">Embodiment</span><span className="kv-v">{selected.armDof ?? "?"} arm DoF · {selected.gripperJoints ?? "?"} gripper joints</span></div>
              <div className="kv-row"><span className="kv-k">Sensors</span><span className="kv-v">{selected.cameraNames.length ? selected.cameraNames.join(", ") : "no cameras"}</span></div>
              <div className="kv-row"><span className="kv-k">Runtime hash</span><span className="kv-v mono">{selected.runtimeSha256 ? `${selected.runtimeSha256.slice(0, 16)}…` : "not compiled"}</span></div>
              <div className="kv-row"><span className="kv-k">Lifecycle</span><span className="kv-v">{selectedRegistration?.lifecycleState ?? "imported only"}</span></div>
            </div>
            <div className="row" style={{ marginTop: 10, gap: 8 }}><button className="btn btn-primary" disabled={busy !== null || !selectedRegistration || !selected.physicsReady} onClick={() => activate(selected)}><Icon name="play" size={12} /> {busy === `activate:${selected.id}` ? "Loading…" : "Select runtime"}</button><a className="btn btn-secondary" href="#/worlds"><Icon name="worlds" size={12} /> Use in Worlds</a>{selected.sourceUrl && <a className="btn btn-secondary" href={selected.sourceUrl} target="_blank" rel="noreferrer"><Icon name="external" size={12} /> Source attribution</a>}</div>
          </Card>

          {selected.validation ? <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {(["front", "wrist"] as const).map((camera) => <Card key={camera} title={`${camera === "front" ? "Front" : "Wrist"} calibration view`} right={<Badge tone="blue">MuJoCo offscreen</Badge>} flush>
                <img src={apiUrl(`/robots/${selected.id}/previews/${camera}.png`)} alt={`${camera} camera calibration render`} style={{ display: "block", width: "100%", aspectRatio: "1 / 1", maxHeight: 330, objectFit: "contain", background: "#000" }} />
                <div style={{ padding: "8px 10px" }} className="micro t3">robot {selected.validation?.cameraCalibration[camera]?.robotPixels.toLocaleString()} px · gripper {selected.validation?.cameraCalibration[camera]?.gripperPixels.toLocaleString()} px · workspace {selected.validation?.cameraCalibration[camera]?.workspacePixels.toLocaleString()} px</div>
              </Card>)}
            </div>
            <Card title="Deterministic physics validation" right={<StatusBadge status={selected.validation.passed ? "passed" : "failed"} />}>
              <div className="st-grid">
                <div className="kv-row"><span className="kv-k">Runtime</span><span className="kv-v mono">MuJoCo {selected.validation.mujocoVersion} · {(1 / selected.validation.timestepSeconds).toFixed(0)} Hz</span></div>
                <div className="kv-row"><span className="kv-k">Initial penetration</span><span className="kv-v mono">{selected.validation.severeInitialContacts} contacts &gt; 5 mm</span></div>
                <div className="kv-row"><span className="kv-k">Home drift</span><span className="kv-v mono">{selected.validation.maxHomeDrift.toFixed(6)} rad/m</span></div>
                <div className="kv-row"><span className="kv-k">Gripper travel</span><span className="kv-v mono">{selected.validation.closedWidthM.toFixed(4)} → {selected.validation.openWidthM.toFixed(4)} m</span></div>
              </div>
            </Card>
          </> : null}

          {selected.readiness.blockers.length > 0 && <Card title="Remaining blockers" right={<StatusBadge status="blocked" />}><div className="col" style={{ gap: 6 }}>{selected.readiness.blockers.map((blocker) => <div className="row small" style={{ gap: 7 }} key={blocker}><Icon name="warning" size={12} style={{ color: "var(--amber)" }} /><span>{blocker}</span></div>)}</div></Card>}
        </div> : <Card><EmptyState icon="robot">Select or import a robot.</EmptyState></Card>}
      </div>
    </div>
  );
}
