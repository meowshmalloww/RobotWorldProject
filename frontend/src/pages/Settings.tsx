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

const NAV: { id: string; label: string; icon: IconName }[] = [
  { id: "general", label: "General", icon: "settings" },
  { id: "appearance", label: "Appearance", icon: "sun" },
  { id: "integrations", label: "Integrations", icon: "link" },
  { id: "simulation", label: "Simulation", icon: "worlds" },
  { id: "models", label: "Models", icon: "agent" },
  { id: "apikeys", label: "API Keys", icon: "lock" },
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
  simulation: { engine: string; gravity: number; timestepHz: number; renderer: string };
  models: {
    planner: string; vlm: string; policy: string; openaiKey: string; openaiBaseUrl: string; provider: string; timeoutS: number;
    policyEndpoint: string; policyApiKey: string; policyId: string; policyEmbodiment: string; policyInstruction: string;
    policyModelRevision: string; policyModelSha256: string; policyNormalizationSha256: string; policyEnvironmentSha256: string;
    policyTimeoutS: number; policyExecutionHorizon: number;
    trellisEndpoint: string; trellisApiKey: string; trellisModel: string; trellisTimeoutS: number;
  };
}

type Section = "general" | "appearance" | "integrations" | "simulation" | "models";

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
  const tab = params.get("tab") ?? "general";
  const setTab = (t: string) => setParams(t === "general" ? {} : { tab: t }, { replace: true });
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

  const update = <S extends Section>(section: S, patch: Partial<SettingsData[S]>) =>
    setDraft((d) => (d ? { ...d, [section]: { ...d[section], ...patch } } : d));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Configure RobotWorld, integrations, and the simulation pipeline.</p>
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
            {tab === "appearance" && <AppearancePane draft={draft.appearance} onChange={(p) => update("appearance", p)} />}
            {tab === "integrations" && <IntegrationsPane draft={draft.integrations} onChange={(p) => update("integrations", p)} />}
            {tab === "simulation" && <SimulationPane draft={draft.simulation} onChange={(p) => update("simulation", p)} />}
            {tab === "models" && <ModelsPane draft={draft.models} onChange={(p) => update("models", p)} />}
            {tab === "apikeys" && <ApiKeysPane settings={draft} />}
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
        <Icon name="check" size={12} /> {saving ? "Saving…" : "Save changes"}
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
            <option value="dark">Dark — Editor</option><option value="darker">Dark — Neutral</option>
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

function IntegrationsPane({ draft, onChange }: { draft: SettingsData["integrations"]; onChange: (p: Partial<SettingsData["integrations"]>) => void }) {
  const toast = useToast();
  const [probingBrightData, setProbingBrightData] = useState(false);
  const [probingSigNoz, setProbingSigNoz] = useState(false);
  const setBd = (p: Partial<SettingsData["integrations"]["brightdata"]>) => onChange({ brightdata: { ...draft.brightdata, ...p } });
  const setSz = (p: Partial<SettingsData["integrations"]["signoz"]>) => onChange({ signoz: { ...draft.signoz, ...p } });
  const probeBrightData = async () => {
    setProbingBrightData(true);
    try {
      const result = await api.post<{ organicCount: number; sampleDomains: string[] }>("/integrations/brightdata/probe", {});
      toast.push("ok", "Bright Data verified", `${result.organicCount} live organic results · ${result.sampleDomains.join(", ")}`);
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
      toast.push("ok", "SigNoz Community verified", `${result.version ?? "local instance"} · OTLP receiver reachable · ${queryState}`);
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
        <p className="small t2" style={{ marginBottom: 12 }}>Scraper Studio — collector lifecycle: run → heal → approve → rerun</p>
        <div className="st-grid">
          <FormRow label="Account ID"><input className="input mono" value={draft.brightdata.accountId} onChange={(e) => setBd({ accountId: e.target.value })} /></FormRow>
          <FormRow label="SERP zone"><input className="input mono" value={draft.brightdata.serpZone} onChange={(e) => setBd({ serpZone: e.target.value })} /></FormRow>
          <FormRow label="Unlocker zone"><input className="input mono" value={draft.brightdata.unlockerZone} onChange={(e) => setBd({ unlockerZone: e.target.value })} /></FormRow>
          <FormRow label="API key"><input className="input mono" value={draft.brightdata.apiKey} readOnly title="Rotate under API Keys" /></FormRow>
        </div>
        <ToggleRow label="Enabled" desc="Allow collectors to run through Bright Data zones" checked={draft.brightdata.enabled} onChange={(v) => setBd({ enabled: v })} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-secondary btn-sm" disabled={probingBrightData} onClick={probeBrightData} title="Sends one billable Google SERP request using the saved key and zone">
            <Icon name="shield" size={12} /> {probingBrightData ? "Checking live SERP…" : "Run paid SERP check"}
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
          <FormRow label="Deployment"><input className="input mono" value="Community · self-hosted" readOnly /></FormRow>
          <FormRow label="OTLP HTTP endpoint"><input className="input mono" value={draft.signoz.endpoint} onChange={(e) => setSz({ endpoint: e.target.value })} placeholder="http://127.0.0.1:4318" /></FormRow>
          <FormRow label="SigNoz UI"><input className="input mono" value={draft.signoz.queryEndpoint} onChange={(e) => setSz({ queryEndpoint: e.target.value })} placeholder="http://127.0.0.1:8080" /></FormRow>
          <FormRow label="Query API key"><input className="input mono" value={draft.signoz.apiKey} readOnly title="Rotate under API Keys" /></FormRow>
        </div>
        <ToggleRow label="Enabled" desc="Export OpenTelemetry pipelines to this SigNoz instance" checked={draft.signoz.enabled} onChange={(v) => setSz({ enabled: v })} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn btn-secondary btn-sm" disabled={probingSigNoz} onClick={probeSigNoz}><Icon name="shield" size={12} /> {probingSigNoz ? "Checking local stack…" : "Verify local SigNoz"}</button>
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

function SimulationPane({ draft, onChange }: { draft: SettingsData["simulation"]; onChange: (p: Partial<SettingsData["simulation"]>) => void }) {
  return (
    <div className="st-stack">
      <Card title="Engine">
        <div className="st-grid">
          <FormRow label="Simulator">
            <input className="input mono" value="MuJoCo" readOnly aria-label="Simulator" />
          </FormRow>
          <FormRow label="Gravity (m/s²)"><input className="input mono" value={draft.gravity} onChange={(e) => onChange({ gravity: Number(e.target.value) || 0 })} /></FormRow>
          <FormRow label="Timestep (Hz)"><input className="input mono" value={draft.timestepHz} onChange={(e) => onChange({ timestepHz: Number(e.target.value) || 0 })} /></FormRow>
          <FormRow label="Renderer">
            <input className="input mono" value="Native Vulkan viewport / MuJoCo physics" readOnly aria-label="Renderer" />
          </FormRow>
        </div>
        <SaveSection section="simulation" draft={draft} />
      </Card>
    </div>
  );
}

function ModelsPane({ draft, onChange }: { draft: SettingsData["models"]; onChange: (p: Partial<SettingsData["models"]>) => void }) {
  const toast = useToast();
  const [probing, setProbing] = useState<string | null>(null);
  const probe = async (kind: "policy" | "trellis") => {
    setProbing(kind);
    try {
      await api.post(`/integrations/${kind}/probe`, {});
      toast.push("ok", `${kind === "policy" ? "VLA" : "TRELLIS.2"} contract verified`, "Remote model and configured identity are compatible");
    } catch (e) {
      toast.push("err", "Model verification failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setProbing(null);
    }
  };
  return (
    <div className="st-stack">
      <Card title="Language models">
        <div className="st-grid">
          <FormRow label="Provider"><input className="input mono" value="OpenAI-compatible" readOnly /></FormRow>
          <FormRow label="Planner model">
            <input className="input mono" value={draft.planner} onChange={(e) => onChange({ planner: e.target.value })} placeholder="gpt-4.1-mini or local model ID" />
          </FormRow>
          <FormRow label="Part-graph VLM">
            <input className="input mono" value={draft.vlm} onChange={(e) => onChange({ vlm: e.target.value })} placeholder="Vision-capable model ID" />
          </FormRow>
          <FormRow label="API base URL"><input className="input mono" value={draft.openaiBaseUrl} onChange={(e) => onChange({ openaiBaseUrl: e.target.value })} placeholder="OpenAI, Ollama, or vLLM /v1 endpoint" /></FormRow>
          <FormRow label="Request timeout (s)"><input className="input mono" type="number" min={5} max={600} value={draft.timeoutS} onChange={(e) => onChange({ timeoutS: Number(e.target.value) || 60 })} /></FormRow>
          <FormRow label="API key"><input className="input mono" value={draft.openaiKey} readOnly title="Rotate under API Keys" /></FormRow>
        </div>
      </Card>
      <Card title="Learned robot policy">
        <p className="small t2" style={{ marginBottom: 12 }}>Separate closed-loop VLA gate: MuJoCo front/wrist RGB + 5-D proprioception + language. No scripted fallback.</p>
        <div className="st-grid">
          <FormRow label="Default evaluation">
            <select className="select" value={draft.policy} onChange={(e) => onChange({ policy: e.target.value })}>
              <option value="asset-validation">Asset validation (scripted oracle)</option>
              <option value="remote-vla">Remote VLA policy evaluation</option>
            </select>
          </FormRow>
          <FormRow label="Gateway URL"><input className="input mono" value={draft.policyEndpoint} onChange={(e) => onChange({ policyEndpoint: e.target.value })} placeholder="https://vla-gateway.internal" /></FormRow>
          <FormRow label="Checkpoint ID"><input className="input mono" value={draft.policyId} onChange={(e) => onChange({ policyId: e.target.value })} /></FormRow>
          <FormRow label="Embodiment"><input className="input mono" value={draft.policyEmbodiment} onChange={(e) => onChange({ policyEmbodiment: e.target.value })} /></FormRow>
          <FormRow label="Model revision"><input className="input mono" value={draft.policyModelRevision} onChange={(e) => onChange({ policyModelRevision: e.target.value })} placeholder="Pinned git/Hugging Face revision" /></FormRow>
          <FormRow label="Model SHA-256"><input className="input mono" value={draft.policyModelSha256} onChange={(e) => onChange({ policyModelSha256: e.target.value })} placeholder="64 hex characters" /></FormRow>
          <FormRow label="Normalization SHA-256"><input className="input mono" value={draft.policyNormalizationSha256} onChange={(e) => onChange({ policyNormalizationSha256: e.target.value })} placeholder="statistics/config hash" /></FormRow>
          <FormRow label="Environment SHA-256"><input className="input mono" value={draft.policyEnvironmentSha256} onChange={(e) => onChange({ policyEnvironmentSha256: e.target.value })} placeholder="frozen MJCF + evaluation manifest hash" /></FormRow>
          <FormRow label="Instruction"><input className="input" value={draft.policyInstruction} onChange={(e) => onChange({ policyInstruction: e.target.value })} /></FormRow>
          <FormRow label="Timeout (s)"><input className="input mono" type="number" min={1} max={120} value={draft.policyTimeoutS} onChange={(e) => onChange({ policyTimeoutS: Number(e.target.value) || 10 })} /></FormRow>
          <FormRow label="Execution horizon"><input className="input mono" type="number" min={1} max={40} value={draft.policyExecutionHorizon} onChange={(e) => onChange({ policyExecutionHorizon: Number(e.target.value) || 8 })} /></FormRow>
          <FormRow label="Gateway token"><input className="input mono" value={draft.policyApiKey} readOnly title="Rotate under API Keys" /></FormRow>
        </div>
        <div className="row" style={{ marginTop: 10 }}><button className="btn btn-secondary btn-sm" disabled={probing === "policy"} onClick={() => probe("policy")}><Icon name="shield" size={12} /> {probing === "policy" ? "Verifying…" : "Verify VLA contract"}</button></div>
      </Card>
      <Card title="TRELLIS.2 visual mesh generator">
        <p className="small t2" style={{ marginBottom: 12 }}>Real image-to-PBR-GLB gateway. RobotWorld separately authors articulation, colliders, mass, and USD physics.</p>
        <div className="st-grid">
          <FormRow label="Gateway URL"><input className="input mono" value={draft.trellisEndpoint} onChange={(e) => onChange({ trellisEndpoint: e.target.value })} placeholder="https://trellis-private.example" /></FormRow>
          <FormRow label="Model"><input className="input mono" value={draft.trellisModel} onChange={(e) => onChange({ trellisModel: e.target.value })} /></FormRow>
          <FormRow label="Timeout (s)"><input className="input mono" type="number" min={30} max={1800} value={draft.trellisTimeoutS} onChange={(e) => onChange({ trellisTimeoutS: Number(e.target.value) || 300 })} /></FormRow>
          <FormRow label="Gateway token"><input className="input mono" value={draft.trellisApiKey} readOnly title="Rotate under API Keys" /></FormRow>
        </div>
        <div className="row" style={{ marginTop: 10 }}><button className="btn btn-secondary btn-sm" disabled={probing === "trellis"} onClick={() => probe("trellis")}><Icon name="shield" size={12} /> {probing === "trellis" ? "Verifying…" : "Verify TRELLIS.2 contract"}</button></div>
        <SaveSection section="models" draft={draft} />
      </Card>
    </div>
  );
}

const KEY_SERVICES: { service: string; label: string; read: (s: SettingsData) => string }[] = [
  { service: "openai", label: "OpenAI API", read: (s) => s.models.openaiKey },
  { service: "policy", label: "VLA gateway token", read: (s) => s.models.policyApiKey },
  { service: "trellis", label: "TRELLIS.2 gateway token", read: (s) => s.models.trellisApiKey },
  { service: "brightdata", label: "Bright Data", read: (s) => s.integrations.brightdata.apiKey },
  { service: "signoz_api", label: "SigNoz query API", read: (s) => s.integrations.signoz.apiKey },
];

function ApiKeysPane({ settings }: { settings: SettingsData }) {
  const toast = useToast();
  const [rotating, setRotating] = useState<string | null>(null);

  const rotate = async (service: string, label: string) => {
    const key = window.prompt(`Paste the new ${label} key (stored write-only, never shown again):`);
    if (!key) return;
    setRotating(service);
    try {
      await api.put(`/settings/keys/${service}`, { key });
      toast.push("ok", "Key updated", `${label} · stored write-only`);
    } catch (e) {
      toast.push("err", "Could not store key", e instanceof ApiError ? e.message : String(e));
    } finally {
      setRotating(null);
    }
  };

  return (
    <Card title="API Keys" flush>
      <table className="table">
        <thead><tr><th>Service</th><th>Key</th><th style={{ width: 60 }} /></tr></thead>
        <tbody>
          {KEY_SERVICES.map(({ service, label, read }) => (
            <tr key={service}>
              <td style={{ fontWeight: 580 }}>{label}</td>
              <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{read(settings) || "—"}</td>
              <td>
                <span className="row" style={{ gap: 2 }}>
                  <button
                    className="icon-btn btn-sm"
                    title="Rotate key"
                    disabled={rotating === service}
                    onClick={() => rotate(service, label)}
                  >
                    <Icon name="refresh" size={12} className={rotating === service ? "spin" : undefined} />
                  </button>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
        <span className="micro t3">Keys are stored write-only on the backend and always shown masked.</span>
      </div>
    </Card>
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
            <span className="micro t3 mono" style={{ marginTop: 2 }}>Backend {health ? `v${health.version} · ${health.status}` : "offline"}</span>
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
