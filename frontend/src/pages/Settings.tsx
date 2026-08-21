import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Badge, Toggle } from "../components/ui/controls";
import { MangoAvatar } from "../components/ui/MangoAvatar";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { ErrorState, Skeleton } from "../lib/states";

const NAV: { id: Section; label: string; icon: IconName }[] = [
  { id: "general", label: "General", icon: "settings" },
  { id: "models", label: "Models", icon: "robot" },
  { id: "integrations", label: "Integrations", icon: "link" },
  { id: "simulation", label: "Simulation", icon: "worlds" },
  { id: "appearance", label: "Appearance", icon: "sun" },
  { id: "about", label: "About", icon: "info" },
];

/* ---- API contract (backend/app/schemas.py) -------------------------------- */
export interface SettingsData {
  general: { workspaceName: string; region: string; autosave: boolean; telemetry: boolean };
  appearance: { theme: string; accent: string; density: string };
  integrations: {
    port: { enabled: boolean; endpoint: string; clientId: string; clientSecret: string; token: string };
    brightdata: { enabled: boolean; accountId: string; serpZone: string; unlockerZone: string; apiKey: string };
    signoz: { enabled: boolean; mode: string; endpoint: string; queryEndpoint: string; ingestionKey: string; apiKey: string; region: string };
  };
  simulation: { engine: string; gravity: number; timestepHz: number; renderer: string; isaacRoot: string; isaacAssetRoot: string; isaacVersion: string };
  models: {
    planner: string; vlm: string; assetAnalysisModel: string; reasoningEffort: string; verbosity: string;
    policy: string; openaiKey: string; openaiBaseUrl: string; provider: string; timeoutS: number;
    policyEndpoint: string; policyApiKey: string; policyId: string; policyEmbodiment: string; policyInstruction: string;
    policyPath: string;
    policyModelRevision: string; policyModelSha256: string; policyNormalizationSha256: string; policyEnvironmentSha256: string;
    policyTimeoutS: number; policyExecutionHorizon: number;
    trellisEndpoint: string; trellisApiKey: string; trellisModel: string; trellisTimeoutS: number;
    trellisRuntime: string; trellisResolution: number; trellisSeed: number; trellisBackgroundRemoval: boolean; trellisNativePath: string; trellisGgufPath: string; trellisCppPath: string;
  };
}

type Section = "general" | "appearance" | "integrations" | "models" | "simulation" | "about";
type EditableSection = "general" | "appearance" | "integrations" | "models" | "simulation";

const TAB_IDS: Section[] = ["general", "models", "integrations", "simulation", "appearance", "about"];

const ACCENTS: Record<string, string> = { graphite: "#E5E5E5", orange: "#B77B55", teal: "#629A9A", purple: "#8E82B5" };

/** Apply appearance settings to the document immediately. */
function applyAppearance(a: SettingsData["appearance"]) {
  const root = document.documentElement;
  root.dataset.theme = a.theme;
  root.dataset.accent = a.accent;
  root.dataset.density = a.density;
  const accent = ACCENTS[a.accent] ?? ACCENTS.graphite;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-line", `${accent}73`);
  root.style.setProperty("--accent-soft", `${accent}24`);
  root.style.setProperty("--blue-soft", `${accent}24`);
  root.style.setProperty("--series-1", accent);
  root.style.setProperty("--border-focus", `${accent}8C`);
  root.style.setProperty("--fs-body", a.density === "compact" ? "12px" : "12.5px");
}

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const rawTab = params.get("tab");
  const tab = (rawTab && TAB_IDS.includes(rawTab as Section) ? (rawTab as Section) : "general");
  const setTab = (t: Section) => setParams(t === "general" ? {} : { tab: t }, { replace: true });
  const { data, error, loading, refetch } = useApi<SettingsData>("/settings");
  const [draft, setDraft] = useState<SettingsData | null>(null);

  // hydrate the local draft from the fetched settings
  useEffect(() => {
    if (data && !draft) setDraft(data);
  }, [data, draft]);

  // apply persisted appearance on first load
  useEffect(() => {
    if (data) applyAppearance(data.appearance);
  }, [data]);

  const update = <S extends EditableSection>(section: S, patch: Partial<SettingsData[S]>) =>
    setDraft((d) => (d ? { ...d, [section]: { ...d[section], ...patch } } : d));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Configure the editor, appearance, runtime, and local workspace behavior.</p>
        </div>
      </div>

      {error && <div className="card" style={{ marginBottom: 10 }}><ErrorState message={error.message} onRetry={refetch} /></div>}
      {loading && !draft && <div className="card"><Skeleton rows={6} /></div>}

      {draft && (
        <div className="st-layout">
          <div className="st-nav card" style={{ padding: 5 }}>
            {NAV.map((n) => (
              <button key={n.id} className={tab === n.id ? "on" : ""} onClick={() => setTab(n.id)}>
                <Icon name={n.icon} size={14} /> {n.label}
              </button>
            ))}
          </div>

          <div className="st-content">
            {tab === "general" && <GeneralPane draft={draft.general} onChange={(p) => update("general", p)} />}
            {tab === "models" && <ModelsPane draft={draft.models} onChange={(p) => update("models", p)} />}
            {tab === "integrations" && <IntegrationsPane draft={draft.integrations} onChange={(p) => update("integrations", p)} />}
            {tab === "appearance" && <AppearancePane draft={draft.appearance} onChange={(p) => update("appearance", p)} />}
            {tab === "simulation" && <SimulationPane draft={draft.simulation} onChange={(p) => update("simulation", p)} />}
            {tab === "about" && <AboutPane />}
          </div>
        </div>
      )}
    </div>
  );
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="st-row">
      <span className="st-row-label">{label}</span>
      <span className="st-row-control">{children}</span>
    </div>
  );
}

/** Save button wired to PUT /api/settings/{section}. */
function SaveSection({ section, draft, extra }: { section: Section; draft: unknown; extra?: () => void }) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try {
      await api.put(`/settings/${section}`, draft);
      extra?.();
      toast.push("ok", "Settings saved", `${section} configuration updated`);
    } catch (e) {
      toast.push("err", "Could not save settings", e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
      <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
        <Icon name="check" size={12} /> {saving ? "Saving..." : "Save changes"}
      </button>
    </div>
  );
}

function GeneralPane({ draft, onChange }: { draft: SettingsData["general"]; onChange: (p: Partial<SettingsData["general"]>) => void }) {
  return (
    <Card title="General">
      <div className="st-grid">
        <FormRow label="Workspace name"><input className="input" value={draft.workspaceName} onChange={(e) => onChange({ workspaceName: e.target.value })} /></FormRow>
        <FormRow label="Region">
          <select className="select" value={draft.region} onChange={(e) => onChange({ region: e.target.value })}>
            <option value="local">Local workstation</option><option value="us-west-2">US West</option><option value="us-east-1">US East</option><option value="eu-central-1">EU Central</option>
          </select>
        </FormRow>
      </div>
      <hr className="divider" style={{ margin: "16px 0" }} />
      <ToggleRow label="Autosave scene edits" desc="Write composer changes back to the stage every 30s" checked={draft.autosave} onChange={(v) => onChange({ autosave: v })} />
      <ToggleRow label="Usage telemetry" desc="Share anonymous pipeline metrics with the team workspace" checked={draft.telemetry} onChange={(v) => onChange({ telemetry: v })} />
      <SaveSection section="general" draft={draft} />
    </Card>
  );
}

function AppearancePane({ draft, onChange }: { draft: SettingsData["appearance"]; onChange: (p: Partial<SettingsData["appearance"]>) => void }) {
  const set = (p: Partial<SettingsData["appearance"]>) => {
    const next = { ...draft, ...p };
    applyAppearance(next);
    onChange(p);
  };
  return (
    <Card title="Appearance">
      <div className="st-grid">
        <FormRow label="Theme">
          <select className="select" value={draft.theme} onChange={(e) => set({ theme: e.target.value })}>
            <option value="dark">Dark - Editor</option><option value="darker">Dark - Neutral</option>
          </select>
        </FormRow>
        <FormRow label="Accent color">
          <select className="select" value={draft.accent} onChange={(e) => set({ accent: e.target.value })}>
            <option value="graphite">Graphite</option><option value="orange">Muted orange</option><option value="teal">Muted teal</option><option value="purple">Muted purple</option>
          </select>
        </FormRow>
        <FormRow label="Density">
          <select className="select" value={draft.density} onChange={(e) => set({ density: e.target.value })}>
            <option value="comfortable">Comfortable</option><option value="compact">Compact</option>
          </select>
        </FormRow>
      </div>
      <SaveSection section="appearance" draft={draft} />
    </Card>
  );
}

export function IntegrationsPane({ draft, onChange }: { draft: SettingsData["integrations"]; onChange: (p: Partial<SettingsData["integrations"]>) => void }) {
  const toast = useToast();
  const [probingBrightData, setProbingBrightData] = useState(false);
  const [probingSigNoz, setProbingSigNoz] = useState(false);
  const setBd = (p: Partial<SettingsData["integrations"]["brightdata"]>) => onChange({ brightdata: { ...draft.brightdata, ...p } });
  const setSz = (p: Partial<SettingsData["integrations"]["signoz"]>) => onChange({ signoz: { ...draft.signoz, ...p } });
  const probeBrightData = async () => {
    setProbingBrightData(true);
    try {
      const result = await api.post<{ organicCount: number; sampleDomains: string[] }>("/integrations/brightdata/probe", {});
      toast.push("ok", "Bright Data verified", `${result.organicCount} live organic results - ${result.sampleDomains.join(", ")}`);
    } catch (e) {
      toast.push("err", "Bright Data verification failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setProbingBrightData(false);
    }
  };
  const probeSigNoz = async () => {
    setProbingSigNoz(true);
    try {
      const result = await api.post<{ version: string | null; queryKeyConfigured: boolean }>("/integrations/signoz/probe", {});
      const queryState = result.queryKeyConfigured ? "query API ready" : "create a local service-account key for agent queries";
      toast.push("ok", "SigNoz Community verified", `${result.version ?? "local instance"} - OTLP receiver reachable - ${queryState}`);
    } catch (e) {
      toast.push("err", "SigNoz verification failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setProbingSigNoz(false);
    }
  };
  const openSigNoz = () => {
    const url = draft.signoz.queryEndpoint.replace(/\/$/, "");
    if (window.robotworld?.openExternal) void window.robotworld.openExternal(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  };
  return (
    <div className="st-stack">
      <Card
        title={<span className="row" style={{ gap: 10 }}><span className="brand-ico brand-brightdata" style={{ width: 26, height: 26, fontSize: 11 }}>B</span>Bright Data</span>}
        right={<Badge tone={draft.brightdata.enabled ? "green" : "grey"}>{draft.brightdata.enabled ? "Active" : "Disabled"}</Badge>}
      >
        <p className="small t2" style={{ marginBottom: 12 }}>Scraper Studio - collector lifecycle: run, heal, approve, rerun</p>
        <div className="st-grid">
          <FormRow label="Account ID"><input className="input mono" value={draft.brightdata.accountId} onChange={(e) => setBd({ accountId: e.target.value })} /></FormRow>
          <FormRow label="SERP zone"><input className="input mono" value={draft.brightdata.serpZone} onChange={(e) => setBd({ serpZone: e.target.value })} /></FormRow>
          <FormRow label="Unlocker zone"><input className="input mono" value={draft.brightdata.unlockerZone} onChange={(e) => setBd({ unlockerZone: e.target.value })} /></FormRow>
        </div>
        <ToggleRow label="Enabled" desc="Allow collectors to run through Bright Data zones" checked={draft.brightdata.enabled} onChange={(v) => setBd({ enabled: v })} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-secondary btn-sm" disabled={probingBrightData} onClick={probeBrightData} title="Sends one billable Google SERP request using the saved key and zone">
            <Icon name="shield" size={12} /> {probingBrightData ? "Checking live SERP..." : "Run paid SERP check"}
          </button>
          <span className="small t3">Save settings first. One live request.</span>
        </div>
      </Card>
      <Card
        title={<span className="row" style={{ gap: 10 }}><span className="brand-ico brand-signoz" style={{ width: 26, height: 26, fontSize: 11 }}>S</span>SigNoz</span>}
        right={<Badge tone={draft.signoz.enabled ? "green" : "grey"}>{draft.signoz.enabled ? "Community" : "Disabled"}</Badge>}
      >
        <p className="small t2" style={{ marginBottom: 12 }}>Self-hosted SigNoz Community. OTLP ingestion is keyless; agent queries use a service-account key created inside your local SigNoz.</p>
        <div className="st-grid">
          <FormRow label="Deployment"><input className="input mono" value="Community - self-hosted" readOnly /></FormRow>
          <FormRow label="OTLP HTTP endpoint"><input className="input mono" value={draft.signoz.endpoint} onChange={(e) => setSz({ endpoint: e.target.value })} placeholder="http://127.0.0.1:4318" /></FormRow>
          <FormRow label="SigNoz UI"><input className="input mono" value={draft.signoz.queryEndpoint} onChange={(e) => setSz({ queryEndpoint: e.target.value })} placeholder="http://127.0.0.1:8080" /></FormRow>
        </div>
        <ToggleRow label="Enabled" desc="Export OpenTelemetry pipelines to this SigNoz instance" checked={draft.signoz.enabled} onChange={(v) => setSz({ enabled: v })} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-secondary btn-sm" disabled={probingSigNoz} onClick={probeSigNoz}><Icon name="shield" size={12} /> {probingSigNoz ? "Checking local stack..." : "Verify local SigNoz"}</button>
          <button className="btn btn-ghost btn-sm" onClick={openSigNoz}><Icon name="external" size={12} /> Open SigNoz UI</button>
        </div>
        <p className="micro t3" style={{ marginTop: 8 }}>After enabling or changing OTLP settings, restart RobotWorld so traces, logs, and metrics attach together.</p>
      </Card>
      <Card>
        <SaveSection section="integrations" draft={draft} />
      </Card>
    </div>
  );
}

interface ModelStatusData {
  vlaJepa: { available: boolean; robotWorldContract?: { compatible: boolean; blockers: string[] } };
  trellis: Array<{ id: string; label: string; path: string; precision: string; weightsBytes: number; status: string; blockers?: string[]; conditioningPath?: string; conditioningReady?: boolean }>;
  generationHistory: Array<{ assetId: string; name: string; runtime: string; resolution: number; totalSeconds: number }>;
  benchmarkComparable: boolean;
  benchmarkRunnable: boolean;
  benchmarkBlocker: string | null;
}

export function ModelsPane({ draft, onChange }: { draft: SettingsData["models"]; onChange: (p: Partial<SettingsData["models"]>) => void }) {
  const toast = useToast();
  const [probingTrellis, setProbingTrellis] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const { data: status, error: statusError, refetch: refetchStatus } = useApi<ModelStatusData>("/models/status");
  const setModel = (patch: Partial<SettingsData["models"]>) => onChange(patch);

  const rotateOpenAIKey = async () => {
    if (!openaiKey.trim()) return;
    setSavingKey(true);
    try {
      await api.put("/settings/keys/openai", { key: openaiKey.trim() });
      setOpenaiKey("");
      toast.push("ok", "OpenAI credential updated", "Stored server-side; the editor cannot read it back.");
    } catch (e) {
      toast.push("err", "Could not update OpenAI key", e instanceof ApiError ? e.message : String(e));
    } finally {
      setSavingKey(false);
    }
  };

  const probeTrellis = async () => {
    setProbingTrellis(true);
    try {
      const result = await api.post<{ model: string; precision: string; supportedResolutions: number[] }>("/integrations/trellis/probe", {});
      toast.push("ok", "TRELLIS endpoint verified", `${result.model} - ${result.precision} - ${result.supportedResolutions.join(" / ")}`);
    } catch (e) {
      toast.push("err", "TRELLIS contract check failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setProbingTrellis(false);
    }
  };

  const incompatible = status?.vlaJepa.robotWorldContract && !status.vlaJepa.robotWorldContract.compatible;
  return <div className="st-stack">
    <Card title="OpenAI reasoning">
      <div className="st-grid">
        <FormRow label="Planner model"><input className="input mono" value={draft.planner} onChange={(e) => setModel({ planner: e.target.value })} /></FormRow>
        <FormRow label="VLM / orchestrator"><input className="input mono" value={draft.vlm} onChange={(e) => setModel({ vlm: e.target.value })} /></FormRow>
        <FormRow label="Evidence model"><input className="input mono" value={draft.assetAnalysisModel} onChange={(e) => setModel({ assetAnalysisModel: e.target.value })} /></FormRow>
        <FormRow label="Provider"><select className="select" value={draft.provider} onChange={(e) => setModel({ provider: e.target.value })}><option value="openai-compatible">OpenAI / compatible</option><option value="openai">OpenAI official</option><option value="local">Local compatible endpoint</option></select></FormRow>
        <FormRow label="Reasoning effort"><select className="select" value={draft.reasoningEffort} onChange={(e) => setModel({ reasoningEffort: e.target.value })}>{["none", "low", "medium", "high", "xhigh", "max"].map((value) => <option value={value} key={value}>{value}</option>)}</select></FormRow>
        <FormRow label="Response detail"><select className="select" value={draft.verbosity} onChange={(e) => setModel({ verbosity: e.target.value })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></FormRow>
        <FormRow label="OpenAI base URL"><input className="input mono" value={draft.openaiBaseUrl} onChange={(e) => setModel({ openaiBaseUrl: e.target.value })} /></FormRow>
        <FormRow label="Timeout (s)"><input className="input mono" type="number" value={draft.timeoutS} onChange={(e) => setModel({ timeoutS: Number(e.target.value) || 0 })} /></FormRow>
      </div>
      <div className="st-secret-row"><div><b>OpenAI API key</b><span className="micro t3">Stored: {draft.openaiKey ? "yes" : "no"}. Write-only.</span></div><input className="input mono" type="password" autoComplete="new-password" value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)} placeholder="Paste replacement key" /><button className="btn btn-secondary btn-sm" disabled={savingKey || !openaiKey.trim()} onClick={rotateOpenAIKey}>{savingKey ? "Saving..." : "Update key"}</button></div>
    </Card>

    <Card title="Robot policy / VLA-JEPA" right={<Badge tone={incompatible ? "amber" : "teal"}>{incompatible ? "Adaptation required" : "Detected"}</Badge>}>
      <div className="st-grid">
        <FormRow label="Default checkpoint"><input className="input mono" value={draft.policyId} onChange={(e) => setModel({ policyId: e.target.value })} /></FormRow>
        <FormRow label="Local checkpoint"><input className="input mono" value={draft.policyPath} onChange={(e) => setModel({ policyPath: e.target.value })} /></FormRow>
        <FormRow label="Policy endpoint"><input className="input mono" value={draft.policyEndpoint} onChange={(e) => setModel({ policyEndpoint: e.target.value })} /></FormRow>
        <FormRow label="Policy revision"><input className="input mono" value={draft.policyModelRevision} onChange={(e) => setModel({ policyModelRevision: e.target.value })} /></FormRow>
        <FormRow label="Execution timeout"><input className="input mono" type="number" value={draft.policyTimeoutS} onChange={(e) => setModel({ policyTimeoutS: Number(e.target.value) || 0 })} /></FormRow>
        <FormRow label="Action horizon"><input className="input mono" type="number" value={draft.policyExecutionHorizon} onChange={(e) => setModel({ policyExecutionHorizon: Number(e.target.value) || 0 })} /></FormRow>
      </div>
      {incompatible && <div className="st-blocker"><b>Execution remains safety-blocked</b>{status?.vlaJepa.robotWorldContract?.blockers.map((value) => <span key={value}>{value}</span>)}<span>Fine-tune or reinitialize the camera, state, and action adapters for this robot before control is enabled.</span></div>}
    </Card>

    <Card title="Microsoft TRELLIS.2" right={<Badge tone={draft.trellisRuntime === "native" ? "teal" : "amber"}>{draft.trellisRuntime === "native" ? "Native" : "GGUF"}</Badge>}>
      <div className="st-grid">
        <FormRow label="Runtime"><select className="select" value={draft.trellisRuntime} onChange={(e) => setModel({ trellisRuntime: e.target.value })}><option value="native">Microsoft native BF16/FP16</option><option value="gguf">GGUF / trellis.cpp</option></select></FormRow>
        <FormRow label="Resolution"><select className="select" value={draft.trellisResolution} onChange={(e) => setModel({ trellisResolution: Number(e.target.value) })}><option value={512}>512 - preview</option><option value={1024}>1024 - balanced</option><option value={1536}>1536 - maximum</option></select></FormRow>
        <FormRow label="Seed"><input className="input mono" type="number" min={0} max={2147483647} value={draft.trellisSeed} onChange={(e) => setModel({ trellisSeed: Math.max(0, Number(e.target.value) || 0) })} /></FormRow>
        <FormRow label="Foreground matte"><select className="select" value={draft.trellisBackgroundRemoval ? "on" : "off"} onChange={(e) => setModel({ trellisBackgroundRemoval: e.target.value === "on" })}><option value="on">BiRefNet/U2-Net enabled</option><option value="off">Use source alpha</option></select></FormRow>
        <FormRow label="Gateway endpoint"><input className="input mono" value={draft.trellisEndpoint} onChange={(e) => setModel({ trellisEndpoint: e.target.value })} /></FormRow>
        <FormRow label="Model"><input className="input mono" value={draft.trellisModel} onChange={(e) => setModel({ trellisModel: e.target.value })} /></FormRow>
        <FormRow label="Native weights"><input className="input mono" value={draft.trellisNativePath} onChange={(e) => setModel({ trellisNativePath: e.target.value })} /></FormRow>
        <FormRow label="Q4 GGUF bundle"><input className="input mono" value={draft.trellisGgufPath} onChange={(e) => setModel({ trellisGgufPath: e.target.value })} /></FormRow>
        <FormRow label="trellis.cpp v0.6 runtime"><input className="input mono" value={draft.trellisCppPath} onChange={(e) => setModel({ trellisCppPath: e.target.value })} /></FormRow>
        <FormRow label="Timeout (s)"><input className="input mono" type="number" value={draft.trellisTimeoutS} onChange={(e) => setModel({ trellisTimeoutS: Number(e.target.value) || 0 })} /></FormRow>
      </div>
      <p className="small t2" style={{ marginTop: 10 }}>Official modes are 512, 1024, and 1536. The requested 500/1584 choices map to supported 512/1536 modes.</p>
      <div className="row" style={{ marginTop: 10 }}><button className="btn btn-secondary btn-sm" disabled={probingTrellis} onClick={probeTrellis}><Icon name="shield" size={12} /> {probingTrellis ? "Checking..." : "Verify endpoint"}</button></div>
    </Card>

    <Card title="Installed runtimes" right={<button className="btn btn-ghost btn-sm" onClick={refetchStatus}><Icon name="refresh" size={12} /> Rescan</button>}>
      {statusError && <span className="small" style={{ color: "var(--red)" }}>{statusError.message}</span>}
      {status?.trellis.map((runtime) => <div className="st-runtime" key={runtime.id}><div><b>{runtime.label}</b><span className="mono micro t3">{runtime.path}</span>{runtime.conditioningPath && <span className="mono micro t3">DINOv3: {runtime.conditioningPath}</span>}</div><span className="mono small">{runtime.precision} - {(runtime.weightsBytes / 1024 ** 3).toFixed(1)} GB</span><Badge tone={runtime.status.startsWith("ready") ? "teal" : "amber"}>{runtime.status.replaceAll("_", " ")}</Badge>{runtime.blockers?.map((value) => <span className="micro t3 st-runtime-note" key={value}>{value}</span>)}</div>)}
      {status && draft.trellisRuntime === "gguf" && !status.benchmarkComparable && <div className="st-blocker"><b>{status.benchmarkRunnable ? "Matched benchmark not run yet" : "Quantized comparison not runnable"}</b><span>{status.benchmarkBlocker}</span><span>No quantized timing or quality score is fabricated.</span></div>}
      {status?.generationHistory.length ? <div className="table-scroll" style={{ marginTop: 10 }}><table className="table"><thead><tr><th>Asset</th><th>Runtime</th><th>Resolution</th><th style={{ textAlign: "right" }}>Total</th></tr></thead><tbody>{status.generationHistory.slice(0, 8).map((row) => <tr key={row.assetId}><td>{row.name}</td><td className="mono">{row.runtime}</td><td className="mono">{row.resolution}</td><td className="mono" style={{ textAlign: "right" }}>{row.totalSeconds.toFixed(1)} s</td></tr>)}</tbody></table></div> : null}
    </Card>
    <Card><SaveSection section="models" draft={draft} /></Card>
  </div>;
}

function LegacyModelsPane({ draft, onChange }: { draft: SettingsData["models"]; onChange: (p: Partial<SettingsData["models"]>) => void }) {
  const toast = useToast();
  const [probingTrellis, setProbingTrellis] = useState(false);

  const setModel = (p: Partial<SettingsData["models"]>) => onChange(p);

  const probeTrellis = async () => {
    setProbingTrellis(true);
    try {
      const result = await api.post<{ compatible: boolean; schemaVersion: string; model: string; output: string; articulation: boolean; pbr: boolean }>(
        "/integrations/trellis/probe",
        {},
      );
      toast.push("ok", "TRELLIS contract", `${result.model} • ${result.output} • schema ${result.schemaVersion}`);
    } catch (e) {
      toast.push("err", "TRELLIS contract check failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setProbingTrellis(false);
    }
  };

  return (
    <div className="st-stack">
      <Card title="Planning and policies">
        <div className="st-grid">
          <FormRow label="Planner model"><input className="input mono" value={draft.planner} onChange={(e) => setModel({ planner: e.target.value })} /></FormRow>
          <FormRow label="VLM / orchestration model"><input className="input mono" value={draft.vlm} onChange={(e) => setModel({ vlm: e.target.value })} /></FormRow>
          <FormRow label="OpenAI base URL"><input className="input mono" value={draft.openaiBaseUrl} onChange={(e) => setModel({ openaiBaseUrl: e.target.value })} /></FormRow>
          <FormRow label="OpenAI API key"><input className="input mono" value={draft.openaiKey} onChange={(e) => setModel({ openaiKey: e.target.value })} /></FormRow>
          <FormRow label="Model timeout (s)"><input className="input mono" type="number" value={draft.timeoutS} onChange={(e) => setModel({ timeoutS: Number(e.target.value) || 0 })} /></FormRow>
        </div>
      </Card>
      <Card title="Policy / VLA settings">
        <div className="st-grid">
          <FormRow label="Policy endpoint"><input className="input mono" value={draft.policyEndpoint} onChange={(e) => setModel({ policyEndpoint: e.target.value })} /></FormRow>
          <FormRow label="Policy API key"><input className="input mono" value={draft.policyApiKey} onChange={(e) => setModel({ policyApiKey: e.target.value })} /></FormRow>
          <FormRow label="Policy revision"><input className="input mono" value={draft.policyModelRevision} onChange={(e) => setModel({ policyModelRevision: e.target.value })} /></FormRow>
          <FormRow label="Policy execution timeout (s)"><input className="input mono" type="number" value={draft.policyTimeoutS} onChange={(e) => setModel({ policyTimeoutS: Number(e.target.value) || 0 })} /></FormRow>
          <FormRow label="Policy horizon"><input className="input mono" type="number" value={draft.policyExecutionHorizon} onChange={(e) => setModel({ policyExecutionHorizon: Number(e.target.value) || 0 })} /></FormRow>
        </div>
      </Card>
      <Card title="TRELLIS.2 visual generator">
        <div className="st-grid">
          <FormRow label="Gateway endpoint">
            <input className="input mono" value={draft.trellisEndpoint} onChange={(e) => setModel({ trellisEndpoint: e.target.value })} />
          </FormRow>
          <FormRow label="Gateway token"><input className="input mono" value={draft.trellisApiKey} onChange={(e) => setModel({ trellisApiKey: e.target.value })} /></FormRow>
          <FormRow label="Model"><input className="input mono" value={draft.trellisModel} onChange={(e) => setModel({ trellisModel: e.target.value })} /></FormRow>
          <FormRow label="Timeout (s)"><input className="input mono" type="number" value={draft.trellisTimeoutS} onChange={(e) => setModel({ trellisTimeoutS: Number(e.target.value) || 0 })} /></FormRow>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-secondary btn-sm" disabled={probingTrellis} onClick={probeTrellis}>
            <Icon name="shield" size={12} /> {probingTrellis ? "Checking TRELLIS ..." : "Verify TRELLIS endpoint"}
          </button>
          <span className="small t3">Checks the gateway schema + compatibility before a real TRELLIS build.</span>
        </div>
      </Card>
      <Card>
        <SaveSection section="models" draft={draft} />
      </Card>
    </div>
  );
}
void LegacyModelsPane;

function SimulationPane({ draft, onChange }: { draft: SettingsData["simulation"]; onChange: (p: Partial<SettingsData["simulation"]>) => void }) {
  const { data: isaac, error: isaacError, refetch } = useApi<{ ready: boolean; installed: boolean; version: string; root: string; frankaAsset: string; blockers: string[] }>("/simulation/isaac");
  return (
    <div className="st-stack">
      <Card title="Engine">
        <div className="st-grid">
          <FormRow label="Simulator">
            <input className="input mono" value="MuJoCo" readOnly aria-label="Simulator" />
          </FormRow>
            <FormRow label="Gravity (m/s2)"><input className="input mono" value={draft.gravity} onChange={(e) => onChange({ gravity: Number(e.target.value) || 0 })} /></FormRow>
          <FormRow label="Timestep (Hz)"><input className="input mono" value={draft.timestepHz} onChange={(e) => onChange({ timestepHz: Number(e.target.value) || 0 })} /></FormRow>
          <FormRow label="Renderer">
            <input className="input mono" value="Native Vulkan viewport / MuJoCo physics" readOnly aria-label="Renderer" />
          </FormRow>
        </div>
        <SaveSection section="simulation" draft={draft} />
      </Card>
      <Card title="NVIDIA Isaac Sim + Franka Panda" right={<Badge tone={isaac?.ready ? "teal" : "amber"}>{isaac?.ready ? "Ready" : "Setup required"}</Badge>}>
        <div className="st-grid">
          <FormRow label="Target version"><input className="input mono" value={draft.isaacVersion} readOnly /></FormRow>
          <FormRow label="Isaac Sim root"><input className="input mono" value={draft.isaacRoot} onChange={(e) => onChange({ isaacRoot: e.target.value })} placeholder="C:\\isaacsim" /></FormRow>
          <FormRow label="Asset root"><input className="input mono" value={draft.isaacAssetRoot} onChange={(e) => onChange({ isaacAssetRoot: e.target.value })} placeholder="Optional; Isaac 6.0 default asset server" /></FormRow>
          <FormRow label="Franka asset"><span className="mono small">{isaac?.frankaAsset ?? "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"}</span></FormRow>
        </div>
        <div className="col" style={{ gap: 4, marginTop: 10 }}>
          {isaacError && <span className="small g-red">{isaacError.message}</span>}
          {isaac?.blockers.map((value) => <span className="micro t3" key={value}>BLOCK · {value}</span>)}
          <div className="row"><button className="btn btn-secondary btn-sm" onClick={refetch}><Icon name="refresh" size={11} /> Recheck runtime</button><span className="micro t3">Physics runs in Isaac Sim; the web viewport remains the editor renderer.</span></div>
        </div>
        <SaveSection section="simulation" draft={draft} extra={refetch} />
      </Card>
    </div>
  );
}


function AboutPane() {
  const { data: health } = useApi<{ status: string; version: string; uptimeS: number }>("/health");
  return (
    <div className="st-stack">
      <Card title="About RobotWorld">
        <div className="row" style={{ gap: 14, alignItems: "flex-start" }}>
          <span style={{ flex: "none", borderRadius: "var(--r-lg)", overflow: "hidden", boxShadow: "var(--shadow-viewport)" }}>
            <MangoAvatar size={56} />
          </span>
          <div className="col" style={{ gap: 3 }}>
            <b style={{ fontSize: "var(--fs-title)" }}>RobotWorld {health ? `v${health.version}` : ""}</b>
            <span className="small t2">Autonomous world-building and curriculum engine for physical AI</span>
            <span className="micro t3 mono" style={{ marginTop: 2 }}>Backend {health ? `v${health.version} - ${health.status}` : "offline"}</span>
          </div>
        </div>
        <hr className="divider" style={{ margin: "16px 0" }} />
        <div className="st-grid">
          <FormRow label="Integrations"><span className="row" style={{ gap: 6 }}><Badge tone="teal">Bright Data</Badge><Badge tone="orange">SigNoz Community</Badge></span></FormRow>
          <FormRow label="API status"><span className="mono">{health ? health.status : "unreachable"}</span></FormRow>
        </div>
      </Card>
    </div>
  );
}


function ToggleRow({ label, desc, checked, onChange }: { label: string; desc?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="field-row">
      <span className="col" style={{ gap: 1 }}>
        <span style={{ fontSize: "var(--fs-body)", fontWeight: 550 }}>{label}</span>
        {desc && <span className="micro t3">{desc}</span>}
      </span>
      <Toggle checked={checked} onChange={onChange} label={label} />
    </div>
  );
}

