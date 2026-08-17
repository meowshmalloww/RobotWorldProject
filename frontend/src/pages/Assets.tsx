import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { DonutGauge } from "../components/charts/DonutGauge";
import { assets } from "../data/assets";

const KIND_LABEL = { articulated: "Articulated", rigid: "Rigid", environment: "Environment" } as const;
const KIND_ICON = { articulated: "joint", rigid: "cube", environment: "worlds" } as const;

export default function Assets() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("All types");
  const [status, setStatus] = useState("All status");
  const [page, setPage] = useState(1);

  const filtered = useMemo(
    () =>
      assets.filter(
        (a) =>
          a.name.toLowerCase().includes(q.toLowerCase()) &&
          (kind === "All types" || KIND_LABEL[a.kind] === kind) &&
          (status === "All status" || a.status === status),
      ),
    [q, kind, status],
  );

  const ready = assets.filter((a) => a.status === "ready").length;
  const blocked = assets.filter((a) => a.status === "blocked").length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Assets</h1>
          <p className="page-sub">SimReady objects compiled from real-world source data — geometry, physics, joints, semantics.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary"><Icon name="download" size={13} /> Export catalog</button>
          <button className="btn btn-primary"><Icon name="plus" size={13} /> New asset build</button>
        </div>
      </div>

      <div className="ov-stats" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 10 }}>
        {[
          { label: "Library assets", value: "3,812", foot: "92% of discovered objects" },
          { label: "Ready for simulation", value: String(ready), foot: "all checks passed" },
          { label: "In pipeline", value: "7", foot: "2 high priority" },
          { label: "Blocked", value: String(blocked), foot: "missing collider / scale" },
        ].map((s) => (
          <div key={s.label} className="stat-card">
            <div className="stat-meta">
              <div className="stat-label">{s.label}</div>
              <div className="stat-value">{s.value}</div>
              <div className="stat-foot">{s.foot}</div>
            </div>
          </div>
        ))}
      </div>

      <Card
        title="Asset Library"
        flush
        right={
          <span className="row" style={{ gap: 7 }}>
            <SearchBox placeholder="Search assets" value={q} onChange={(v) => { setQ(v); setPage(1); }} style={{ width: 200 }} />
            <select className="select" style={{ width: 126 }} value={kind} onChange={(e) => setKind(e.target.value)}>
              {["All types", "Articulated", "Rigid", "Environment"].map((k) => <option key={k}>{k}</option>)}
            </select>
            <select className="select" style={{ width: 120 }} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option>All status</option>
              <option value="ready">Ready</option>
              <option value="testing">Testing</option>
              <option value="building">Building</option>
              <option value="blocked">Blocked</option>
            </select>
          </span>
        }
      >
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Asset</th><th>Type</th><th>Readiness</th><th>Physics validity</th><th>Scale conf.</th>
                <th>Source</th><th>Status</th><th style={{ textAlign: "right" }}>Last evaluation</th><th style={{ width: 30 }} />
              </tr>
            </thead>
            <tbody>
              {filtered.map((a) => (
                <tr key={a.id} className="rowlink" onClick={() => nav(`/assets/${a.id}`)}>
                  <td>
                    <div className="cell-main">
                      <span className="cell-ico"><Icon name={KIND_ICON[a.kind] as IconName} size={13} /></span>
                      <span className="col" style={{ gap: 0 }}>
                        <span style={{ fontWeight: 580 }}>{a.name}</span>
                        <span className="micro t3 mono">{a.id}</span>
                      </span>
                    </div>
                  </td>
                  <td className="t-muted">{KIND_LABEL[a.kind]}</td>
                  <td>
                    <div className="row" style={{ gap: 8 }}>
                      <DonutGauge
                        value={a.readiness / 100}
                        size={26}
                        stroke={3}
                        color={a.readiness >= 85 ? "var(--green)" : a.readiness >= 70 ? "var(--amber)" : "var(--red)"}
                      />
                      <span className="mono" style={{ fontWeight: 620 }}>{a.readiness}</span>
                    </div>
                  </td>
                  <td className="mono t2">{a.physicsValidity.toFixed(1)}%</td>
                  <td className="mono t2">{a.scaleConfidence.toFixed(2)}</td>
                  <td className="t-muted">{a.source}</td>
                  <td><StatusBadge status={a.status} /></td>
                  <td className="t-muted mono" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{a.lastEval}</td>
                  <td><button className="icon-btn btn-sm" onClick={(e) => e.stopPropagation()}><Icon name="dots" size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="row between" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
          <span className="micro t3">Showing {filtered.length} of 3,812 assets</span>
          <Pagination page={page} pages={48} onPage={setPage} />
        </div>
      </Card>
    </div>
  );
}
