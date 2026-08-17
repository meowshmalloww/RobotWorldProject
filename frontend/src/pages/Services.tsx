import { Card } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, StatusBadge } from "../components/ui/controls";
import { services } from "../data/services";

const KIND_TONE = { core: "blue", agent: "purple", integration: "teal", worker: "grey" } as const;

export default function Services() {
  const running = services.filter((s) => s.status === "running").length;
  const degraded = services.filter((s) => s.status === "degraded").length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Services</h1>
          <p className="page-sub">Runtime topology of the world-building pipeline — processes, agents, and integrations.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary"><Icon name="refresh" size={13} /> Restart all workers</button>
        </div>
      </div>

      <div className="sv-grid">
        {[
          { label: "Services", value: String(services.length), foot: "registered in catalog" },
          { label: "Running", value: String(running), foot: "healthy processes", tone: "var(--green)" },
          { label: "Degraded", value: String(degraded), foot: "elevated error rate", tone: "var(--amber)" },
          { label: "Total restarts (7d)", value: String(services.reduce((a, s) => a + s.restarts, 0)), foot: "across fleet" },
        ].map((s) => (
          <div key={s.label} className="stat-card">
            <div className="stat-meta">
              <div className="stat-label">{s.label}</div>
              <div className="stat-value" style={s.tone ? { color: s.tone } : undefined}>{s.value}</div>
              <div className="stat-foot">{s.foot}</div>
            </div>
          </div>
        ))}
      </div>

      <Card title="Service Catalog" flush>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Service</th><th>Kind</th><th>Status</th><th>Version</th>
                <th style={{ textAlign: "right" }}>Latency</th><th style={{ textAlign: "right" }}>Uptime</th>
                <th style={{ textAlign: "right" }}>Restarts</th><th>GPU</th><th style={{ width: 30 }} />
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={s.name} className="rowlink">
                  <td>
                    <div className="cell-main">
                      <span className="cell-ico"><Icon name={s.kind === "integration" ? "sources" : s.kind === "agent" ? "agent" : s.kind === "worker" ? "chip" : "services"} size={13} /></span>
                      <span className="mono" style={{ fontWeight: 580, fontSize: "var(--fs-small)" }}>{s.name}</span>
                    </div>
                  </td>
                  <td><Badge tone={KIND_TONE[s.kind]}>{s.kind}</Badge></td>
                  <td><StatusBadge status={s.status} /></td>
                  <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{s.version}</td>
                  <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{s.latency}</td>
                  <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{s.uptime}</td>
                  <td className="mono" style={{ textAlign: "right", fontSize: "var(--fs-small)", color: s.restarts > 2 ? "var(--amber)" : "var(--text-2)" }}>{s.restarts}</td>
                  <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{s.gpu ?? "—"}</td>
                  <td><button className="icon-btn btn-sm"><Icon name="dots" size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
