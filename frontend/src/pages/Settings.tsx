import { useState } from "react";
import { Card } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Toggle } from "../components/ui/controls";

const NAV: { id: string; label: string; icon: IconName }[] = [
  { id: "general", label: "General", icon: "settings" },
  { id: "integrations", label: "Integrations", icon: "sources" },
  { id: "simulation", label: "Simulation", icon: "worlds" },
  { id: "telemetry", label: "Telemetry", icon: "observability" },
  { id: "models", label: "Models", icon: "agent" },
  { id: "apikeys", label: "API Keys", icon: "lock" },
];

export default function Settings() {
  const [tab, setTab] = useState("general");
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Project configuration, integrations, and pipeline defaults.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-primary"><Icon name="check" size={13} /> Save changes</button>
        </div>
      </div>

      <div className="st-layout">
        <div className="st-nav card" style={{ padding: 6 }}>
          {NAV.map((n) => (
            <button key={n.id} className={tab === n.id ? "on" : ""} onClick={() => setTab(n.id)}>
              <Icon name={n.icon} size={14} /> {n.label}
            </button>
          ))}
        </div>

        <div className="col" style={{ gap: 12 }}>
          {tab === "general" && <GeneralPane />}
          {tab === "integrations" && <IntegrationsPane />}
          {tab === "simulation" && <SimulationPane />}
          {tab === "telemetry" && <TelemetryPane />}
          {tab === "models" && <ModelsPane />}
          {tab === "apikeys" && <ApiKeysPane />}
        </div>
      </div>
    </div>
  );
}

function GeneralPane() {
  return (
    <>
      <Card title="Project">
        <div className="col" style={{ gap: 12 }}>
          <div className="field"><label>Project name</label><input className="input" defaultValue="Zero Downtime Project" /></div>
          <div className="row" style={{ gap: 10 }}>
            <div className="field grow"><label>Project ID</label><input className="input mono" defaultValue="wops_zdp_01" readOnly /></div>
            <div className="field grow"><label>Region</label>
              <select className="select" defaultValue="us-west-2"><option>us-west-2</option><option>us-east-1</option><option>eu-central-1</option></select>
            </div>
          </div>
          <div className="field"><label>Description</label><input className="input" defaultValue="Autonomous world-building and curriculum engine for physical AI" /></div>
        </div>
      </Card>
      <Card title="Workspace">
        <FieldRow label="Autosave scene edits" desc="Write composer changes back to the stage every 30s" defaultOn />
        <FieldRow label="Weekly coverage digest" desc="Email a skill coverage summary every Monday" defaultOn />
        <FieldRow label="Beta features" desc="Enable experimental pipeline stages" />
      </Card>
    </>
  );
}

function IntegrationsPane() {
  return (
    <>
      <Card title="Port" flush>
        <div style={{ padding: "12px 14px" }}>
          <div className="row" style={{ gap: 10, marginBottom: 10 }}>
            <span className="brand-ico brand-port" style={{ width: 32, height: 32, fontSize: 12 }}>P</span>
            <span className="col grow" style={{ gap: 0 }}>
              <b>Port catalog sync</b>
              <span className="micro t3">Entities: Skill · Asset · Environment · Scenario · Scraper · TrainingRun · Service</span>
            </span>
            <span className="badge b-green"><span className="dot" /> Connected</span>
          </div>
          <div className="field"><label>Org endpoint</label><input className="input mono" defaultValue="https://api.port.io/v1/orgs/worldops" /></div>
          <div style={{ height: 10 }} />
          <FieldRow label="Publish run results" desc="Push evaluation + promotion events to Port scorecards" defaultOn />
          <FieldRow label="Agent MCP access" desc="Allow the curriculum agent to query coverage via Port MCP" defaultOn />
        </div>
      </Card>
      <Card title="Bright Data">
        <div className="row" style={{ gap: 10, marginBottom: 10 }}>
          <span className="brand-ico brand-brightdata" style={{ width: 32, height: 32, fontSize: 12 }}>b</span>
          <span className="col grow" style={{ gap: 0 }}>
            <b>Scraper Studio</b>
            <span className="micro t3">Collector lifecycle: run → heal → approve → rerun</span>
          </span>
          <span className="badge b-green"><span className="dot" /> Active</span>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <div className="field grow"><label>Collector prefix</label><input className="input mono" defaultValue="bd_" /></div>
          <div className="field grow"><label>Default zone</label><select className="select" defaultValue="us"><option value="us">US residential</option><option value="dc">Datacenter</option></select></div>
        </div>
        <div style={{ height: 10 }} />
        <FieldRow label="Auto-approve healed scrapers" desc="Rerun repaired collectors without manual approval when preview validates" defaultOn />
      </Card>
      <Card title="SigNoz">
        <div className="row" style={{ gap: 10, marginBottom: 10 }}>
          <span className="brand-ico brand-signoz" style={{ width: 32, height: 32, fontSize: 12 }}>S</span>
          <span className="col grow" style={{ gap: 0 }}>
            <b>SigNoz Cloud</b>
            <span className="micro t3">OpenTelemetry traces + metrics for every pipeline stage</span>
          </span>
          <span className="badge b-green"><span className="dot" /> Live</span>
        </div>
        <div className="field"><label>Ingestion endpoint</label><input className="input mono" defaultValue="https://ingest.us.signoz.cloud:443" /></div>
        <div style={{ height: 10 }} />
        <FieldRow label="Agent trace queries" desc="Let the failure-analysis agent query the Traces API" defaultOn />
      </Card>
    </>
  );
}

function SimulationPane() {
  return (
    <>
      <Card title="Engine">
        <div className="row" style={{ gap: 10 }}>
          <div className="field grow"><label>Simulator</label><select className="select" defaultValue="isaac"><option value="isaac">NVIDIA Isaac Sim 4.5</option><option value="mujoco">MuJoCo 3</option></select></div>
          <div className="field grow"><label>Physics engine</label><select className="select" defaultValue="physx"><option value="physx">PhysX 5</option><option>Bullet</option></select></div>
        </div>
        <div style={{ height: 10 }} />
        <div className="row" style={{ gap: 10 }}>
          <div className="field grow"><label>Gravity (m/s²)</label><input className="input mono" defaultValue="-9.81" /></div>
          <div className="field grow"><label>Timestep (Hz)</label><input className="input mono" defaultValue="120" /></div>
        </div>
      </Card>
      <Card title="Viewport renderer">
        <div className="row" style={{ gap: 10 }}>
          <div className="field grow"><label>Backend</label><select className="select" defaultValue="vulkan"><option value="vulkan">Vulkan (wgpu)</option><option value="webgl">WebGL 2</option></select></div>
          <div className="field grow"><label>Shadow resolution</label><select className="select" defaultValue="2048"><option>1024</option><option value="2048">2048</option><option>4096</option></select></div>
        </div>
        <div style={{ height: 10 }} />
        <FieldRow label="Domain randomization" desc="Randomize friction, mass ±10%, and lighting per episode" defaultOn />
        <FieldRow label="Real-time factor lock" desc="Cap simulation at 1.0× wall clock during live evaluation" />
      </Card>
    </>
  );
}

function TelemetryPane() {
  return (
    <Card title="OpenTelemetry">
      <FieldRow label="Trace every curriculum iteration" desc="One distributed trace per autonomous loop" defaultOn />
      <FieldRow label="Capture robot joint telemetry" desc="Joint state, collisions, and forces at 60 Hz" defaultOn />
      <FieldRow label="Scraper lifecycle spans" desc="Emit spans for run / heal / approve / rerun" defaultOn />
      <FieldRow label="Verbose asset compiler logs" desc="Include USD validation output in log stream" />
    </Card>
  );
}

function ModelsPane() {
  return (
    <>
      <Card title="Language models">
        <div className="row" style={{ gap: 10 }}>
          <div className="field grow"><label>Planner model</label><select className="select" defaultValue="luna"><option value="luna">GPT Luna (planner)</option><option value="terra">GPT Terra (heavy reasoning)</option></select></div>
          <div className="field grow"><label>Part-graph VLM</label><select className="select" defaultValue="terra"><option value="terra">GPT Terra</option><option value="luna">GPT Luna</option></select></div>
        </div>
      </Card>
      <Card title="Policy">
        <div className="row" style={{ gap: 10 }}>
          <div className="field grow"><label>Architecture</label><select className="select" defaultValue="vla3b"><option value="vla3b">VLA 3B (Jetson Orin Nano)</option><option>VLM 3B</option></select></div>
          <div className="field grow"><label>Fine-tune method</label><select className="select" defaultValue="lora"><option value="lora">LoRA on scripted demos</option><option>Full FT</option></select></div>
        </div>
        <div style={{ height: 10 }} />
        <FieldRow label="Scripted motion planner demos" desc="Use planner trajectories as training examples before policy rollout" defaultOn />
      </Card>
    </>
  );
}

function ApiKeysPane() {
  const rows: [string, string, string][] = [
    ["OpenAI API", "sk-••••••••••••••••3fA9", "Added May 2, 2025"],
    ["Bright Data", "brd_••••••••••••c41d", "Added May 2, 2025"],
    ["SigNoz ingestion", "signoz_ingest_••••••8b21", "Added May 3, 2025"],
    ["Port", "port_••••••••••77fa", "Added May 3, 2025"],
  ];
  return (
    <Card title="API Keys" flush>
      <table className="table">
        <thead><tr><th>Service</th><th>Key</th><th>Added</th><th style={{ width: 60 }} /></tr></thead>
        <tbody>
          {rows.map(([s, k, a]) => (
            <tr key={s}>
              <td style={{ fontWeight: 580 }}>{s}</td>
              <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{k}</td>
              <td className="t-muted">{a}</td>
              <td>
                <span className="row" style={{ gap: 2 }}>
                  <button className="icon-btn btn-sm" title="Copy"><Icon name="copy" size={12} /></button>
                  <button className="icon-btn btn-sm" title="Rotate"><Icon name="refresh" size={12} /></button>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
        <button className="btn btn-secondary btn-sm"><Icon name="plus" size={12} /> Add key</button>
      </div>
    </Card>
  );
}

function FieldRow({ label, desc, defaultOn }: { label: string; desc?: string; defaultOn?: boolean }) {
  const [on, setOn] = useState(!!defaultOn);
  return (
    <div className="field-row">
      <span className="col" style={{ gap: 1 }}>
        <span style={{ fontSize: "var(--fs-body)", fontWeight: 550 }}>{label}</span>
        {desc && <span className="micro t3">{desc}</span>}
      </span>
      <Toggle checked={on} onChange={setOn} label={label} />
    </div>
  );
}
