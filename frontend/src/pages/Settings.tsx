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
    signoz: { enabled: boolean; endpoint: string; queryEndpoint: string; ingestionKey: string; apiKey: string; region: string };
  };
  simulation: { engine: string; gravity: number; timestepHz: number; renderer: string };
  models: { planner: string; vlm: string; policy: string; openaiKey: string; openaiBaseUrl: string; provider: string; timeoutS: number };
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
  const setPort = (p: Partial<SettingsData["integrations"]["port"]>) => onChange({ port: { ...draft.port, ...p } });
  const setBd = (p: Partial<SettingsData["integrations"]["brightdata"]>) => onChange({ brightdata: { ...draft.brightdata, ...p } });
  const setSz = (p: Partial<SettingsData["integrations"]["signoz"]>) => onChange({ signoz: { ...draft.signoz, ...p } });
  return (
    <div className="st-stack">
      <Card
        title={<span className="row" style={{ gap: 10 }}><span className="brand-ico brand-port" style={{ width: 26, height: 26, fontSize: 11 }}>P</span>Port</span>}
        right={<Badge tone={draft.port.enabled ? "green" : "grey"}>{draft.port.enabled ? "Connected" : "Disabled"}</Badge>}
      >
        <p className="small t2" style={{ marginBottom: 12 }}>World/skill/asset catalog — entities, scorecards, governed agent actions</p>
        <div className="st-grid">
          <FormRow label="Org endpoint"><input className="input mono" value={draft.port.endpoint} onChange={(e) => setPort({ endpoint: e.target.value })} /></FormRow>
          <FormRow label="Client ID"><input className="input mono" value={draft.port.clientId} onChange={(e) => setPort({ clientId: e.target.value })} /></FormRow>
          <FormRow label="Client secret"><input className="input mono" value={draft.port.clientSecret} readOnly title="Rotate under API Keys" /></FormRow>
          <FormRow label="Temporary token"><input className="input mono" value={draft.port.token} readOnly title="Optional short-lived token; rotate under API Keys" /></FormRow>
        </div>
        <ToggleRow label="Enabled" desc="Publish evaluation + promotion events to Port scorecards" checked={draft.port.enabled} onChange={(v) => setPort({ enabled: v })} />
      </Card>
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
      </Card>
      <Card
        title={<span className="row" style={{ gap: 10 }}><span className="brand-ico brand-signoz" style={{ width: 26, height: 26, fontSize: 11 }}>S</span>SigNoz</span>}
        right={<Badge tone={draft.signoz.enabled ? "green" : "grey"}>{draft.signoz.enabled ? "Live" : "Disabled"}</Badge>}
      >
        <p className="small t2" style={{ marginBottom: 12 }}>OpenTelemetry traces, metrics, and logs for every pipeline stage</p>
        <div className="st-grid">
          <FormRow label="Ingestion endpoint"><input className="input mono" value={draft.signoz.endpoint} onChange={(e) => setSz({ endpoint: e.target.value })} /></FormRow>
          <FormRow label="Query endpoint"><input className="input mono" value={draft.signoz.queryEndpoint} onChange={(e) => setSz({ queryEndpoint: e.target.value })} placeholder="https://your-workspace.us.signoz.cloud" /></FormRow>
          <FormRow label="Region">
            <select className="select" value={draft.signoz.region} onChange={(e) => setSz({ region: e.target.value })}>
              <option value="us">US</option><option value="eu">EU</option><option value="in">IN</option>
            </select>
          </FormRow>
          <FormRow label="Ingestion key"><input className="input mono" value={draft.signoz.ingestionKey} readOnly title="Rotate under API Keys" /></FormRow>
          <FormRow label="Query API key"><input className="input mono" value={draft.signoz.apiKey} readOnly title="Rotate under API Keys" /></FormRow>
        </div>
        <ToggleRow label="Enabled" desc="Export OpenTelemetry pipelines to this SigNoz instance" checked={draft.signoz.enabled} onChange={(v) => setSz({ enabled: v })} />
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
            <input className="input mono" value="MuJoCo offscreen / Three.js preview" readOnly aria-label="Renderer" />
          </FormRow>
        </div>
        <SaveSection section="simulation" draft={draft} />
      </Card>
    </div>
  );
}

function ModelsPane({ draft, onChange }: { draft: SettingsData["models"]; onChange: (p: Partial<SettingsData["models"]>) => void }) {
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
      <Card title="Policy">
        <div className="st-grid">
          <FormRow label="Architecture">
            <select className="select" value={draft.policy} onChange={(e) => onChange({ policy: e.target.value })}>
              <option value="vla3b">VLA 3B (Jetson Orin Nano)</option><option value="vlm3b">VLM 3B</option>
            </select>
          </FormRow>
        </div>
        <SaveSection section="models" draft={draft} />
      </Card>
    </div>
  );
}

const KEY_SERVICES: { service: string; label: string; read: (s: SettingsData) => string }[] = [
  { service: "openai", label: "OpenAI API", read: (s) => s.models.openaiKey },
  { service: "brightdata", label: "Bright Data", read: (s) => s.integrations.brightdata.apiKey },
  { service: "signoz", label: "SigNoz ingestion", read: (s) => s.integrations.signoz.ingestionKey },
  { service: "signoz_api", label: "SigNoz query API", read: (s) => s.integrations.signoz.apiKey },
  { service: "port_client_secret", label: "Port client secret", read: (s) => s.integrations.port.clientSecret },
  { service: "port", label: "Port temporary token", read: (s) => s.integrations.port.token },
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
          <FormRow label="Sponsors"><span className="row" style={{ gap: 6 }}><Badge tone="blue">Port</Badge><Badge tone="teal">Bright Data</Badge><Badge tone="orange">SigNoz</Badge></span></FormRow>
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
