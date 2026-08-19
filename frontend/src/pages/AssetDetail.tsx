import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, Menu, MenuItem, VecInput } from "../components/ui/controls";
import { Tree, type TreeNodeData } from "../components/ui/Tree";
import { Modal } from "../components/ui/Modal";
import { useToast } from "../components/ui/Toast";
import { Viewport } from "../components/three/Viewport";
import { api, ApiError, downloadApiFile } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { Asset, AssetPart } from "../data/types";

/** Asset → tree model for the part hierarchy panel. */
function partsToTree(parts: AssetPart[]): TreeNodeData[] {
  const conv = (p: AssetPart): TreeNodeData => ({
    id: p.id,
    name: p.name,
    icon: p.joint ? "joint" : "cube",
    tag: p.joint,
    children: p.children?.map(conv),
  });
  return parts.map(conv);
}

export default function AssetDetail() {
  const { assetId = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { data: asset, error, loading, refetch } = useApi<Asset>(assetId ? `/assets/${assetId}` : null);
  const [reevaluating, setReevaluating] = useState(false);
  const [retireConfirm, setRetireConfirm] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const [part, setPart] = useState<string | null>("body");
  const [turntable, setTurntable] = useState(true);
  const [doorOpen, setDoorOpen] = useState(0.65);
  const [pos, setPos] = useState<[string, string, string]>(["1.842", "0.000", "0.000"]);
  const [rot, setRot] = useState<[string, string, string]>(["0.000", "0.000", "90.000"]);
  const [scl, setScl] = useState<[string, string, string]>(["1.000", "1.000", "1.000"]);

  if (loading && !asset) {
    return (
      <div className="page">
        <Skeleton rows={2} height={22} style={{ width: 340, padding: 0, marginBottom: 12 }} />
        <div className="ad-stats">{Array.from({ length: 5 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)}</div>
        <div className="card" style={{ marginTop: 10 }}><Skeleton rows={6} /></div>
      </div>
    );
  }

  if (error || !asset) {
    return (
      <div className="page">
        <div className="card">
          {error?.status === 404 ? (
            <EmptyState icon="cube">Asset not found — it may have been retired.</EmptyState>
          ) : (
            <ErrorState message={error?.message ?? "Failed to load asset"} onRetry={refetch} />
          )}
          <div style={{ textAlign: "center", paddingBottom: 16 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => nav("/assets")}><Icon name="chevronLeft" size={12} /> Back to assets</button>
          </div>
        </div>
      </div>
    );
  }

  const downloadUsd = async () => {
    try {
      await downloadApiFile(`/assets/${asset.id}/usd`, `${asset.id}.usda`);
      toast.push("ok", "USD downloaded", `${asset.id}.usda`);
    } catch (e) {
      toast.push("err", "USD download failed", e instanceof ApiError ? e.message : String(e));
    }
  };

  const downloadArtifact = async (file: string) => {
    try {
      await downloadApiFile(`/assets/${asset.id}/artifacts/${encodeURIComponent(file)}`, file);
      toast.push("ok", "Artifact downloaded", file);
    } catch (e) {
      toast.push("err", "Artifact download failed", e instanceof ApiError ? e.message : String(e));
    }
  };

  const reevaluate = async () => {
    setReevaluating(true);
    try {
      const { jobId } = await api.post<{ jobId: string }>(`/assets/${asset.id}/reevaluate`);
      toast.push("info", "Re-evaluation queued", `Job ${jobId} · physics + readiness`);
      const prevResult = asset.lastEvalResult;
      const poll = async (attempt = 0) => {
        if (attempt >= 120) {
          setReevaluating(false);
          toast.push("info", "Evaluation still running", "Polling stopped after four minutes; refresh to check the persisted result.");
          return;
        }
        try {
          const a = await api.get<Asset>(`/assets/${asset.id}`);
          if (a.lastEvalResult === prevResult && a.lastEval === asset.lastEval) {
            setTimeout(() => poll(attempt + 1), Math.min(2000 + attempt * 100, 5000));
          } else {
            setReevaluating(false);
            refetch();
            toast.push("ok", "Re-evaluation complete", `${a.name} · readiness ${a.readiness}/100`);
          }
        } catch {
          setTimeout(() => poll(attempt + 1), Math.min(2000 + attempt * 100, 5000));
        }
      };
      setTimeout(poll, 2000);
    } catch (e) {
      setReevaluating(false);
      toast.push("err", "Re-evaluation failed", e instanceof ApiError ? e.message : String(e));
    }
  };

  const retire = async () => {
    setRetiring(true);
    try {
      await api.del(`/assets/${asset.id}`);
      setRetireConfirm(false);
      toast.push("ok", "Asset retired", asset.name);
      nav("/assets");
    } catch (e) {
      setRetiring(false);
      toast.push("err", "Could not retire asset", e instanceof ApiError ? e.message : String(e));
    }
  };

  const selectedPartName = findPartName(asset.parts, part) ?? "Body";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title row" style={{ gap: 9 }}>
            <button className="icon-btn" onClick={() => nav("/assets")} title="Back to assets"><Icon name="chevronLeft" size={14} /></button>
            Asset Detail — {asset.name}
          </h1>
          <p className="page-sub row" style={{ gap: 6 }}>
            <span className={`health-dot ${asset.status === "ready" ? "ok" : ""}`} /> {asset.kind === "articulated" ? "Articulated asset" : asset.kind === "rigid" ? "Rigid asset" : "Environment"}
            <span className="t3">·</span> <span className="t2">{asset.lastEvalResult === "passed" ? "Compiled and validated" : asset.lastEvalResult === "failed" ? "Validation issues" : "Evaluation pending"}</span>
            <span className="t3">·</span> <span className={asset.status === "ready" ? "g-green" : asset.status === "blocked" ? "g-red" : "g-amber"}>{asset.status === "ready" ? "Ready for simulation" : asset.status}</span>
          </p>
        </div>
        <div className="head-actions">
          <Menu trigger={() => <button className="btn btn-secondary">Actions <Icon name="chevronDown" size={12} /></button>} align="right" width={200}>
            <MenuItem icon="refresh" onClick={reevaluate}>Rebuild asset</MenuItem>
            <MenuItem icon="download" onClick={downloadUsd}>Download USD</MenuItem>
            <MenuItem icon="edit" onClick={() => toast.push("info", "Edit metadata", "Metadata editing enables with the asset API")}>Edit metadata</MenuItem>
            <div className="menu-sep" />
            <MenuItem icon="x" onClick={() => setRetireConfirm(true)}>Retire asset</MenuItem>
          </Menu>
          <button className="btn btn-secondary" onClick={reevaluate} disabled={reevaluating}>
            <Icon name="refresh" size={13} className={reevaluating ? "spin" : undefined} /> {reevaluating ? "Evaluating…" : "Re-evaluate"}
          </button>
          <button className="btn btn-primary" onClick={() => nav("/worlds")}><Icon name="play" size={13} /> Open in Sim</button>
        </div>
      </div>

      {retireConfirm && (
        <Modal
          title="Retire asset"
          onClose={() => setRetireConfirm(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setRetireConfirm(false)}>Cancel</button>
              <button className="btn btn-primary" style={{ background: "var(--red)" }} onClick={retire} disabled={retiring}>
                {retiring ? "Retiring…" : "Retire"}
              </button>
            </>
          }
        >
          <p style={{ fontSize: "var(--fs-body)", color: "var(--text-2)" }}>
            Retire <b style={{ color: "var(--text-1)" }}>{asset.name}</b>? It will be removed from promoted libraries but remain in version history.
          </p>
        </Modal>
      )}

      {/* metric band */}
      <div className="ad-stats">
        {[
          { label: "Readiness score", value: `${asset.readiness}`, sub: asset.readiness >= 85 ? "Excellent" : "Needs work", ring: asset.readiness / 100, ringColor: asset.readiness >= 85 ? "var(--green)" : "var(--amber)", suffix: " /100" },
          { label: "Physics validity", value: asset.physicsValidity.toFixed(1), suffix: "%", sub: asset.lastEvalResult === "passed" ? "Valid" : "Issues found", ring: asset.physicsValidity / 100, ringColor: "var(--green)" },
          { label: "Scale confidence", value: asset.scaleConfidence.toFixed(2), sub: asset.scaleConfidence > 0.9 ? "High" : "Medium", ring: asset.scaleConfidence, ringColor: "var(--accent)" },
          { label: "Articulation completeness", value: `${asset.articulation}%`, sub: asset.articulation === 100 ? "Complete" : "Partial", ring: asset.articulation / 100, ringColor: asset.articulation === 100 ? "var(--green)" : "var(--amber)" },
        ].map((m) => (
          <div key={m.label} className="stat-card">
            <span className="stat-ico" style={{ background: "transparent", width: 44, height: 44 }}>
              <Ring value={m.ring} color={m.ringColor} label={m.value} />
            </span>
            <div className="stat-meta">
              <div className="stat-label">{m.label}</div>
              <div className="stat-value sm">{m.value}<span className="t3" style={{ fontSize: 13 }}>{m.suffix}</span></div>
              <div className="stat-foot" style={{ color: m.ringColor }}>{m.sub}</div>
            </div>
          </div>
        ))}
        <div className="stat-card">
          <span className={`stat-ico ${asset.lastEvalResult === "passed" ? "c-green" : asset.lastEvalResult === "failed" ? "c-red" : "c-amber"}`} style={{ borderRadius: "50%" }}>
            <Icon name={asset.lastEvalResult === "passed" ? "check" : asset.lastEvalResult === "failed" ? "warning" : "clock"} size={17} />
          </span>
          <div className="stat-meta">
            <div className="stat-label">Last evaluation result</div>
            <div className={`stat-value sm ${asset.lastEvalResult === "passed" ? "g-green" : asset.lastEvalResult === "failed" ? "g-red" : "g-amber"}`}>
              {asset.lastEvalResult === "passed" ? "Passed" : asset.lastEvalResult === "failed" ? "Failed" : "Pending"}
            </div>
            <div className="stat-foot">{asset.lastEval}</div>
          </div>
        </div>
      </div>

      <div className="ad-main">
        {/* 3D preview */}
        <Card
          title="3D Preview"
          flush
          right={
            <span className="row" style={{ gap: 7 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setTurntable(!turntable)}
                style={turntable ? { color: "var(--accent)", borderColor: "var(--accent-line)" } : undefined}
              >
                <Icon name="rotate" size={12} /> Turntable
              </button>
            </span>
          }
        >
          <div style={{ padding: 10, position: "relative" }}>
            <Viewport
              camera={{ position: [1.5, 1.35, 1.9], fov: 38 }}
              target={[0, 0.5, 0]}
              doorAngle={doorOpen * 75}
              style={{ height: 392 }}
              gizmo={false}
              autoRotate={turntable}
              grid
            />
            <div className="vp-overlay" style={{ left: "50%", bottom: 18, transform: "translateX(-50%)" }}>
              <span className="vp-chip">Drag to orbit · wheel to zoom</span>
            </div>
          </div>
          {/* door articulation slider — real kinematic control */}
          <div className="row" style={{ gap: 10, padding: "0 14px 12px" }}>
            <span className="small t2" style={{ width: 96 }}>Door articulation</span>
            <input
              type="range" min={0} max={100} value={Math.round(doorOpen * 100)}
              onChange={(e) => setDoorOpen(Number(e.target.value) / 100)}
              style={{ flex: 1, accentColor: "var(--accent)" }}
            />
            <span className="mono small" style={{ width: 44, textAlign: "right" }}>{Math.round(doorOpen * 110)}°</span>
          </div>
        </Card>

        {/* Part tree */}
        <Card title="Part Tree" right={<Badge tone="grey">{asset.parts[0]?.children?.length ?? 0} parts</Badge>} flush>
          <div style={{ padding: "6px 4px", maxHeight: 430, overflowY: "auto" }}>
            {asset.parts.length > 0 ? (
              <Tree nodes={partsToTree(asset.parts)} selected={part} onSelect={(id) => setPart(id)} />
            ) : (
              <EmptyState icon="cube">No part hierarchy compiled yet.</EmptyState>
            )}
          </div>
        </Card>

        {/* Properties inspector */}
        <Card title="Properties" right={<Badge tone="blue">{selectedPartName}</Badge>} flush pad={false}>
          <div style={{ maxHeight: 470, overflowY: "auto" }}>
            <InspSection title="Transform">
              <div className="col" style={{ gap: 8 }}>
                <div className="field"><label>Position (m)</label><VecInput values={pos} onChange={(i, v) => setPos((p) => { const n = [...p] as typeof p; n[i] = v; return n; })} /></div>
                <div className="field"><label>Rotation (°)</label><VecInput values={rot} onChange={(i, v) => setRot((p) => { const n = [...p] as typeof p; n[i] = v; return n; })} /></div>
                <div className="field"><label>Scale</label><VecInput values={scl} onChange={(i, v) => setScl((p) => { const n = [...p] as typeof p; n[i] = v; return n; })} /></div>
              </div>
            </InspSection>
            <InspSection title="Joint">
              <div className="kv">
                <KV k="Joint type" v={asset.properties.jointType} />
                <KV k="Axis" v={asset.properties.axis} />
                <KV k="Limits" v={asset.properties.limits} />
              </div>
            </InspSection>
            <InspSection title="Physics">
              <div className="kv">
                <KV k="Mass" v={asset.properties.mass} />
                <KV k="Material" v={asset.properties.material} />
                <KV k="Collider type" v={asset.properties.collider} />
              </div>
            </InspSection>
            <InspSection title="Semantics">
              <div className="kv">
                <KV k="Semantic label" v={<span className="mono">{asset.properties.semantic}</span>} />
                <KV k="Affordance" v={asset.properties.affordance} />
              </div>
            </InspSection>
            <InspSection title="Tags">
              <div className="row wrap" style={{ gap: 5 }}>
                {asset.tags.map((t) => (
                  <span key={t} className="tag">{t}</span>
                ))}
              </div>
            </InspSection>
          </div>
        </Card>
      </div>

      <div className="ad-bottom">
        {/* Compiler output */}
        <Card title="Compiler Output" flush>
          {asset.compile.length > 0 ? (
            <>
              <div className="row" style={{ padding: "16px 14px 6px", alignItems: "flex-start", overflowX: "auto" }}>
                {asset.compile.map((s, i) => (
                  <div key={s.name} className="row" style={{ flex: 1, minWidth: 108 }}>
                    <div className="col" style={{ gap: 5, flex: 1 }}>
                      <span
                        className="center"
                        style={{
                          width: 30, height: 30, borderRadius: "50%",
                          background: s.status === "passed" ? "var(--green-soft)" : s.status === "failed" ? "var(--red-soft)" : "var(--bg-panel-3)",
                          color: s.status === "passed" ? "var(--green)" : s.status === "failed" ? "var(--red)" : "var(--text-3)",
                          border: `1px solid ${s.status === "passed" ? "rgba(76,195,138,0.4)" : s.status === "failed" ? "rgba(240,86,79,0.4)" : "var(--border-strong)"}`,
                        }}
                      >
                        {s.status === "passed" ? <Icon name="check" size={13} /> : s.status === "failed" ? <Icon name="x" size={13} /> : <Icon name="clock" size={13} />}
                      </span>
                      <span className="small" style={{ fontWeight: 600 }}>{i + 1}. {s.name}</span>
                      <span className="micro t3 mono">{s.duration}</span>
                    </div>
                    {i < asset.compile.length - 1 && (
                      <Icon name="arrowRight" size={13} style={{ color: "var(--text-3)", margin: "0 6px", marginTop: -28 }} />
                    )}
                  </div>
                ))}
              </div>
              <div className="row" style={{ gap: 8, padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
                {asset.lastEvalResult === "passed" ? (
                  <>
                    <Icon name="check" size={13} style={{ color: "var(--green)" }} />
                    <span className="small g-green" style={{ fontWeight: 580 }}>Compilation completed successfully</span>
                  </>
                ) : asset.lastEvalResult === "failed" ? (
                  <>
                    <Icon name="warning" size={13} style={{ color: "var(--red)" }} />
                    <span className="small g-red" style={{ fontWeight: 580 }}>Compilation failed — see stage results above</span>
                  </>
                ) : (
                  <>
                    <Icon name="clock" size={13} style={{ color: "var(--amber)" }} />
                    <span className="small g-amber" style={{ fontWeight: 580 }}>Compilation pending</span>
                  </>
                )}
                <span className="micro t3">Last evaluation: {asset.lastEval}</span>
              </div>
            </>
          ) : (
            <EmptyState icon="usd">No compiler output recorded yet.</EmptyState>
          )}
        </Card>

        {/* Artifacts */}
        <Card title="Artifacts" flush>
          {asset.artifacts.length > 0 ? (
            <table className="table">
              <thead>
                <tr><th>Artifact</th><th>File</th><th style={{ textAlign: "right" }}>Size</th><th>Generated</th><th style={{ width: 52 }} /></tr>
              </thead>
              <tbody>
                {asset.artifacts.map((a) => (
                  <tr key={a.file}>
                    <td>
                      <div className="cell-main">
                        <span className="cell-ico">
                          <Icon name={a.type === "USD" ? "usd" : a.type.includes("image") || a.type.includes("render") ? "image" : a.type === "Mesh" || a.type.includes("Collider") ? "mesh" : "box"} size={13} />
                        </span>
                        {a.type}
                      </div>
                    </td>
                    <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{a.file}</td>
                    <td className="mono t2" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{a.size}</td>
                    <td className="t-muted" style={{ fontSize: "var(--fs-small)" }}>{a.generated}</td>
                    <td>
                      <span className="row" style={{ gap: 2 }}>
                        <button className="icon-btn btn-sm" title="Download" onClick={() => downloadArtifact(a.file)}>
                          <Icon name="download" size={12} />
                        </button>
                        <button className="icon-btn btn-sm" title="More" onClick={() => toast.push("info", a.file, `${a.type} · ${a.size} · generated ${a.generated}`)}><Icon name="dots" size={12} /></button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState icon="box">No artifacts generated yet — run a build or re-evaluation.</EmptyState>
          )}
        </Card>
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

function Ring({ value, color, label }: { value: number; color: string; label: string }) {
  const r = 17, c = 2 * Math.PI * r;
  return (
    <svg width={44} height={44} style={{ transform: "rotate(-90deg)" }}>
      <circle cx={22} cy={22} r={r} fill="none" stroke="rgba(148,170,220,0.13)" strokeWidth={4} />
      <circle cx={22} cy={22} r={r} fill="none" stroke={color} strokeWidth={4} strokeLinecap="round" strokeDasharray={`${value * c} ${c}`} />
      <text x={22} y={26} textAnchor="middle" fontSize={10.5} fontWeight={700} fill="var(--text-1)" style={{ transform: "rotate(90deg)", transformOrigin: "center" }} fontFamily="var(--font-mono)">
        {label}
      </text>
    </svg>
  );
}

function findPartName(parts: AssetPart[], id: string | null): string | null {
  for (const p of parts) {
    if (p.id === id) return p.name;
    if (p.children) {
      const found = findPartName(p.children, id);
      if (found) return found;
    }
  }
  return null;
}
