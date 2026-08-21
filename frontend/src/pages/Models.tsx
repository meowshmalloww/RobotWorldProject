import { useState, type FormEvent } from "react";
import { Card } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

type ProviderType = "local_path" | "hugging_face" | "openai_compatible" | "native_provider" | "local_server";

interface RegisteredModel {
  id: string;
  revision: number;
  displayName: string;
  roles: string[];
  providerType: ProviderType;
  localPath: string | null;
  baseUrl: string | null;
  modelId: string | null;
  modelRevision: string | null;
  apiKeyEnv: string | null;
  apiKeyConfigured: boolean;
  expectedDevice: string;
  precision: string;
  capabilities: Record<string, unknown>;
  lifecycleState: string;
  healthStatus: string;
  manifestSha256: string | null;
  contentSha256: string | null;
  lastError: string | null;
  lastValidatedAt: string | null;
}

interface ModelListResponse {
  models: RegisteredModel[];
  allowedLocalRoots: string[];
}

interface CommandResponse {
  commandId: string;
  status: string;
  reused: boolean;
  result: { model?: RegisteredModel; validation?: { valid: boolean; error: string | null } };
  error?: string | null;
}

interface WorkerProbe {
  readyForLoad: boolean;
  blockers: string[];
  offlineMode: boolean;
  python: { executable: string; version: string };
  cuda: { available: boolean; deviceName?: string; totalMemoryBytes?: number; torchVersion?: string };
  worker: { running: boolean; pid: number | null };
}

const PROVIDERS: { value: ProviderType; label: string }[] = [
  { value: "local_path", label: "Local path" },
  { value: "local_server", label: "Local inference server" },
  { value: "openai_compatible", label: "OpenAI-compatible API" },
  { value: "hugging_face", label: "Hugging Face repository" },
  { value: "native_provider", label: "Native provider adapter" },
];

const ROLES = [
  ["platform_agent", "Platform agent LLM/VLM"],
  ["vla_policy", "VLA policy"],
  ["vision_encoder", "Vision encoder"],
  ["world_model", "World model"],
  ["image_to_3d", "Image-to-3D generator"],
  ["part_understanding", "Part understanding"],
  ["embedding", "Embedding / retrieval"],
] as const;

type DeployTarget = "local" | "huggingface" | "jetson";

const DEPLOY_TARGETS: { value: DeployTarget; icon: "hardDrive" | "download" | "chip"; title: string; sub: string; body: string }[] = [
  { value: "local", icon: "hardDrive", title: "This PC", sub: "local_path", body: "Checkpoint or repository on this machine. Validated by bounded metadata inspection and hashing." },
  { value: "huggingface", icon: "download", title: "Hugging Face", sub: "hugging_face", body: "Hub repository id. Resolved by the server adapter; weights are never uploaded through the browser." },
  { value: "jetson", icon: "chip", title: "Jetson Nano endpoint", sub: "local_server", body: "Remote inference server (e.g. Jetson Nano). Point the base URL at the device's serving port." },
];

function lifecycleStatus(value: string): string {
  return ({ AVAILABLE: "ready", LOADED: "running", INVALID: "failed", VALIDATING: "in_progress", UNLOADING: "in_progress", REGISTERED: "pending" } as Record<string, string>)[value] ?? value.toLowerCase();
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}

export default function Models() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<ModelListResponse>("/models");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("platform_agent");
  const [provider, setProvider] = useState<ProviderType>("local_path");
  const [localPath, setLocalPath] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelId, setModelId] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [device, setDevice] = useState("auto");
  const [precision, setPrecision] = useState("unknown");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [workerProbes, setWorkerProbes] = useState<Record<string, WorkerProbe>>({});
  const [deployTarget, setDeployTarget] = useState<DeployTarget>("local");

  const applyDeployTarget = (target: DeployTarget) => {
    setDeployTarget(target);
    if (target === "local") {
      setProvider("local_path");
      setBaseUrl("");
    } else if (target === "huggingface") {
      setProvider("hugging_face");
      setBaseUrl("");
    } else {
      setProvider("local_server");
      setDevice("cuda");
      if (!baseUrl) setBaseUrl("http://jetson-nano.local:8001/v1");
    }
  };

  const create = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    try {
      const response = await api.post<CommandResponse>("/models", {
        displayName,
        roles: [role],
        providerType: provider,
        localPath: provider === "local_path" ? localPath : null,
        baseUrl: provider === "openai_compatible" || provider === "local_server" ? baseUrl : null,
        modelId: provider !== "local_path" ? modelId || null : null,
        apiKeyEnv: apiKeyEnv || null,
        expectedDevice: device,
        precision,
      });
      const model = response.result.model;
      toast.push("ok", "Model registered", `${model?.displayName ?? displayName} · ${response.commandId}`);
      setDisplayName("");
      refetch();
    } catch (reason) {
      toast.push("err", "Registration failed", errorText(reason));
    } finally {
      setCreating(false);
    }
  };

  const probeWorker = async (model: RegisteredModel) => {
    setBusy(`${model.id}:worker-probe`);
    try {
      const probe = await api.get<WorkerProbe>(`/models/${model.id}/worker-probe`);
      setWorkerProbes((current) => ({ ...current, [model.id]: probe }));
      toast.push(probe.readyForLoad ? "ok" : "info", "Policy worker probed", probe.readyForLoad ? `Ready in process ${probe.worker.pid ?? "starting"}` : probe.blockers[0] ?? "Worker readiness gates remain.");
    } catch (reason) {
      toast.push("err", "Worker probe failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const command = async (model: RegisteredModel, action: "validate" | "load" | "unload") => {
    setBusy(`${model.id}:${action}`);
    try {
      const response = await api.post<CommandResponse>(`/models/${model.id}/${action}`, action === "validate" ? { computeContentHash: false } : {});
      const validation = response.result.validation;
      if (validation && !validation.valid) {
        toast.push("err", "Validation failed", validation.error ?? "The configured adapter rejected this model.");
      } else {
        toast.push("ok", `Model ${action} command completed`, `${response.commandId}${response.reused ? " · replayed" : ""}`);
      }
      refetch();
    } catch (reason) {
      toast.push("err", `Model ${action} failed`, errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Model registry</div>
          <h1 className="page-title">Models</h1>
          <p className="page-sub">Register and validate explicit model connections. Paths are server-side references; checkpoints are never uploaded through the browser.</p>
        </div>
        <div className="head-actions"><button className="btn btn-secondary" onClick={refetch}><Icon name="refresh" size={13} /> Refresh</button></div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(340px, .72fr) minmax(520px, 1.6fr)", gap: 10, alignItems: "start" }}>
        <Card title="Register model" right={<Badge tone="blue" icon="hardDrive">Internal catalog</Badge>}>
          <div className="section-head"><span className="section-title">Deployment target</span><span className="section-line" /></div>
          <div className="deploy-grid" style={{ marginBottom: 12 }}>
            {DEPLOY_TARGETS.map((target) => (
              <button
                key={target.value}
                type="button"
                className={`deploy-card ${deployTarget === target.value ? "selected" : ""}`}
                onClick={() => applyDeployTarget(target.value)}
                style={{ textAlign: "left", cursor: "pointer" }}
              >
                <span className="deploy-head">
                  <span className="deploy-ico"><Icon name={target.icon} size={15} /></span>
                  <span className="col" style={{ gap: 1 }}>
                    <span className="deploy-title">{target.title}</span>
                    <span className="deploy-sub mono">{target.sub}</span>
                  </span>
                  {deployTarget === target.value && <Icon name="check" size={13} style={{ marginLeft: "auto", color: "var(--accent)" }} />}
                </span>
                <span className="deploy-body">{target.body}</span>
              </button>
            ))}
          </div>
          <form className="col" style={{ gap: 11 }} onSubmit={create}>
            <div className="field"><label>Display name</label><input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required maxLength={160} placeholder="Production platform agent" /></div>
            <div className="st-grid">
              <div className="field"><label>Role</label><select className="select" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
              <div className="field"><label>Connection</label><select className="select" value={provider} onChange={(e) => setProvider(e.target.value as ProviderType)}>{PROVIDERS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
            </div>
            {provider === "local_path" && <div className="field"><label>Allowlisted local repository or checkpoint path</label><input className="input mono" value={localPath} onChange={(e) => setLocalPath(e.target.value)} required placeholder="D:\\models\\checkpoint" /></div>}
            {(provider === "openai_compatible" || provider === "local_server") && <div className="field"><label>Base URL</label><input className="input mono" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required placeholder="http://127.0.0.1:8001/v1" /></div>}
            {provider !== "local_path" && <div className="field"><label>Model ID or alias</label><input className="input mono" value={modelId} onChange={(e) => setModelId(e.target.value)} required={provider === "hugging_face"} placeholder="provider/model-or-user-alias" /></div>}
            <div className="field"><label>API-key environment variable (optional; never the key)</label><input className="input mono" value={apiKeyEnv} onChange={(e) => setApiKeyEnv(e.target.value.toUpperCase())} placeholder="ROBOTWORLD_PROVIDER_API_KEY" /></div>
            <div className="st-grid">
              <div className="field"><label>Expected device</label><select className="select" value={device} onChange={(e) => setDevice(e.target.value)}><option>auto</option><option>cpu</option><option>cuda</option><option>mps</option></select></div>
              <div className="field"><label>Precision</label><select className="select" value={precision} onChange={(e) => setPrecision(e.target.value)}><option>unknown</option><option>float32</option><option>float16</option><option>bfloat16</option><option>int8</option><option>int4</option></select></div>
            </div>
            <button className="btn btn-primary" type="submit" disabled={creating}><Icon name={creating ? "refresh" : "plus"} size={13} /> {creating ? "Registering…" : "Register"}</button>
            <div className="micro t3">Validation is a separate command. Local validation inspects bounded metadata and hashes without loading weights into RAM or VRAM.</div>
          </form>
        </Card>

        <Card title="Registered connections" flush right={<span className="micro t3">{data?.models.length ?? 0} revisions</span>}>
          {loading && !data ? <Skeleton rows={6} /> : error ? <ErrorState message={error.message} onRetry={refetch} /> : !data?.models.length ? <EmptyState icon="hardDrive">No registered models. Add a path or endpoint to create the first durable revision.</EmptyState> : (
            <div className="table-scroll"><table className="table"><thead><tr><th>Model</th><th>Connection</th><th>Capabilities</th><th>Lifecycle</th><th>Actions</th></tr></thead><tbody>
              {data.models.map((model) => {
                const cameraKeys = Array.isArray(model.capabilities.cameraKeys) ? model.capabilities.cameraKeys as string[] : [];
                const workerProbe = workerProbes[model.id];
                const hasStateInput = model.capabilities.stateFeaturePresent !== false;
                return <tr key={model.id}>
                  <td><div className="col" style={{ gap: 1 }}><span style={{ fontWeight: 620 }}>{model.displayName}</span><span className="micro mono t3">{model.id} · r{model.revision}</span><span className="micro t3">{model.roles.join(", ")}</span>{model.lastError && <span className="micro" style={{ color: "var(--red)", maxWidth: 320 }}>{model.lastError}</span>}{workerProbe?.blockers.map((blocker) => <span className="micro t3" style={{ maxWidth: 340 }} key={blocker}>BLOCK · {blocker}</span>)}</div></td>
                  <td><div className="col" style={{ gap: 2 }}><span>{PROVIDERS.find((item) => item.value === model.providerType)?.label ?? model.providerType}</span><span className="micro mono t3" style={{ maxWidth: 300, overflowWrap: "anywhere" }}>{model.localPath ?? model.baseUrl ?? model.modelId ?? "adapter not configured"}</span>{model.apiKeyEnv && <span className="micro t3">{model.apiKeyEnv}: {model.apiKeyConfigured ? "configured" : "missing"}</span>}</div></td>
                  <td><div className="col" style={{ gap: 2 }}><span className="micro">{model.capabilities.actionDimension ? `action ${String(model.capabilities.actionDimension)} · ${hasStateInput ? `state input ${String(model.capabilities.stateFeatureDimension ?? model.capabilities.stateDimension ?? "?")}` : `no state input (declared ${String(model.capabilities.stateDimension ?? "?")})`}` : "Not probed"}</span>{cameraKeys.length > 0 && <span className="micro t3">{cameraKeys.length} camera inputs</span>}{workerProbe && <span className="micro t3">worker {workerProbe.readyForLoad ? "ready" : "blocked"} · {workerProbe.offlineMode ? "offline-safe" : "downloads enabled"}</span>}<span className="micro mono t3">{model.manifestSha256 ? `manifest ${model.manifestSha256.slice(0, 12)}…` : "no manifest hash"}</span></div></td>
                  <td><div className="col" style={{ gap: 4 }}><StatusBadge status={lifecycleStatus(model.lifecycleState)} />{model.healthStatus !== "unknown" && <Badge tone={model.healthStatus === "healthy" ? "green" : "red"}>{model.healthStatus}</Badge>}</div></td>
                  <td><div className="row" style={{ gap: 5, flexWrap: "wrap" }}><button className="btn btn-secondary btn-sm" disabled={busy !== null || model.lifecycleState === "LOADED"} onClick={() => command(model, "validate")}><Icon name="check" size={11} /> Validate</button>{model.providerType === "local_path" && model.roles.includes("vla_policy") && <button className="btn btn-ghost btn-sm" disabled={busy !== null} onClick={() => probeWorker(model)}><Icon name="chip" size={11} /> Probe worker</button>}{model.lifecycleState === "LOADED" ? <button className="btn btn-secondary btn-sm" disabled={busy !== null} onClick={() => command(model, "unload")}><Icon name="stop" size={11} /> Unload</button> : <button className="btn btn-secondary btn-sm" disabled={busy !== null || model.lifecycleState !== "AVAILABLE"} onClick={() => command(model, "load")}><Icon name="play" size={11} /> Load</button>}</div></td>
                </tr>;
              })}
            </tbody></table></div>
          )}
        </Card>
      </div>

      {data?.allowedLocalRoots.length ? <Card title="Server allowlisted roots" style={{ marginTop: 10 }}><div className="col" style={{ gap: 5 }}>{data.allowedLocalRoots.map((root) => <span className="mono small" key={root}>{root}</span>)}</div></Card> : null}
    </div>
  );
}
