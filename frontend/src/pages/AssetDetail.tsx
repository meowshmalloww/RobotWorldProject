import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { Badge, InspSection, Menu, MenuItem, Segmented, VecInput } from "../components/ui/controls";
import { Tree, type TreeNodeData } from "../components/ui/Tree";
import { Viewport } from "../components/three/Viewport";
import { CabinetAsset } from "../components/three/Cabinet";
import { Refrigerator } from "../components/three/Appliances";
import { getAsset } from "../data/assets";

/** Asset → tree model for the part hierarchy panel. */
function partsToTree(assetId: string): TreeNodeData[] {
  const a = getAsset(assetId);
  const conv = (p: (typeof a.parts)[number]): TreeNodeData => ({
    id: p.id,
    name: p.name,
    icon: p.joint ? "joint" : "cube",
    tag: p.joint,
    children: p.children?.map(conv),
  });
  return a.parts.map(conv);
}

export default function AssetDetail() {
  const { assetId = "kitchen-cabinet-02" } = useParams();
  const nav = useNavigate();
  const asset = useMemo(() => getAsset(assetId), [assetId]);
  const [part, setPart] = useState<string | null>("body");
  const [turntable, setTurntable] = useState(true);
  const [wireframe, setWireframe] = useState(false);
  const [doorOpen, setDoorOpen] = useState(0.65);
  const [pos, setPos] = useState<[string, string, string]>(["1.842", "0.000", "0.000"]);
  const [rot, setRot] = useState<[string, string, string]>(["0.000", "0.000", "90.000"]);
  const [scl, setScl] = useState<[string, string, string]>(["1.000", "1.000", "1.000"]);

  const isFridge = asset.id === "refrigerator-rf56";
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
            <span className="health-dot ok" /> Articulated asset
            <span className="t3">·</span> <span className="t2">Compiled and validated</span>
            <span className="t3">·</span> <span className="g-green">Ready for simulation</span>
          </p>
        </div>
        <div className="head-actions">
          <Menu trigger={() => <button className="btn btn-secondary">Actions <Icon name="chevronDown" size={12} /></button>} align="right" width={200}>
            <MenuItem icon="refresh">Rebuild asset</MenuItem>
            <MenuItem icon="download">Download USD</MenuItem>
            <MenuItem icon="edit">Edit metadata</MenuItem>
            <div className="menu-sep" />
            <MenuItem icon="x">Retire asset</MenuItem>
          </Menu>
          <button className="btn btn-secondary"><Icon name="refresh" size={13} /> Re-evaluate</button>
          <button className="btn btn-primary" onClick={() => nav("/worlds")}><Icon name="play" size={13} /> Open in Sim</button>
        </div>
      </div>

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
          <span className={`stat-ico ${asset.lastEvalResult === "passed" ? "c-green" : "c-red"}`} style={{ borderRadius: "50%" }}>
            <Icon name={asset.lastEvalResult === "passed" ? "check" : "warning"} size={17} />
          </span>
          <div className="stat-meta">
            <div className="stat-label">Last evaluation result</div>
            <div className={`stat-value sm ${asset.lastEvalResult === "passed" ? "g-green" : "g-red"}`}>
              {asset.lastEvalResult === "passed" ? "Passed" : "Failed"}
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
              <Segmented
                options={[{ value: "pbr", label: "PBR" }, { value: "wire", label: "Wireframe" }]}
                value={wireframe ? "wire" : "pbr"}
                onChange={(v) => setWireframe(v === "wire")}
              />
              <button
                className={`btn btn-ghost btn-sm ${turntable ? "" : ""}`}
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
              style={{ height: 392 }}
              gizmo={false}
              autoRotate={turntable}
              grid
            >
              {/* studio turntable disc */}
              <mesh position={[0, -0.045, 0]} receiveShadow>
                <cylinderGeometry args={[1.05, 1.12, 0.09, 48]} />
              </mesh>
              <mesh position={[0, 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
                <ringGeometry args={[1.02, 1.07, 48]} />
                <meshStandardMaterial color="#4C8DFF" emissive="#4C8DFF" emissiveIntensity={1.1} transparent opacity={0.75} />
              </mesh>
              <group position={[0, isFridge ? 0 : 0.375, 0]}>
                {isFridge ? (
                  <Refrigerator doorOpen={doorOpen} />
                ) : (
                  <CabinetAsset leftOpen={doorOpen} rightOpen={0} wireframe={wireframe} />
                )}
              </group>
            </Viewport>
            {/* viewport bottom toolbar */}
            <div className="vp-overlay" style={{ left: "50%", bottom: 18, transform: "translateX(-50%)" }}>
              <div className="vp-toolbar">
                <button title="Reset view"><Icon name="reset" size={13} /></button>
                <button title="Pan"><Icon name="hand" size={13} /></button>
                <button title="Move"><Icon name="move" size={13} /></button>
                <button title="Rotate"><Icon name="rotate" size={13} /></button>
                <span className="sep" />
                <button title="Frame selection"><Icon name="maximize" size={13} /></button>
              </div>
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
        <Card title="Part Tree" right={<Badge tone="grey">{asset.parts[0].children?.length ?? 0} parts</Badge>} flush>
          <div style={{ padding: "6px 4px", maxHeight: 430, overflowY: "auto" }}>
            <Tree nodes={partsToTree(asset.id)} selected={part} onSelect={(id) => setPart(id)} />
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
                  <span key={t} className="tag">{t}<button aria-label={`Remove ${t}`}><Icon name="x" size={9} /></button></span>
                ))}
                <button className="tag-add" title="Add tag"><Icon name="plus" size={10} /></button>
              </div>
            </InspSection>
          </div>
        </Card>
      </div>

      <div className="ad-bottom">
        {/* Compiler output */}
        <Card title="Compiler Output" flush>
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
                <span className="micro t3">Today at 10:02 AM · Total time: 13.9s</span>
                <span className="grow" />
                <CardLinkSmall>View logs</CardLinkSmall>
              </>
            ) : (
              <>
                <Icon name="warning" size={13} style={{ color: "var(--red)" }} />
                <span className="small g-red" style={{ fontWeight: 580 }}>Collider generation failed — non-manifold region in lid mesh</span>
                <span className="micro t3">Today at 9:55 AM</span>
                <span className="grow" />
                <CardLinkSmall>View logs</CardLinkSmall>
              </>
            )}
          </div>
        </Card>

        {/* Artifacts */}
        <Card title="Artifacts" flush>
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
                      <button className="icon-btn btn-sm" title="Download"><Icon name="download" size={12} /></button>
                      <button className="icon-btn btn-sm" title="More"><Icon name="dots" size={12} /></button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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

function CardLinkSmall({ children }: { children: React.ReactNode }) {
  return (
    <a className="card-link" style={{ cursor: "pointer", fontSize: "var(--fs-small)" }}>{children}</a>
  );
}

function findPartName(parts: { id: string; name: string; children?: { id: string; name: string; children?: { id: string; name: string }[] }[] }[], id: string | null): string | null {
  for (const p of parts) {
    if (p.id === id) return p.name;
    if (p.children) {
      for (const c of p.children) {
        if (c.id === id) return c.name;
        if (c.children) for (const g of c.children) if (g.id === id) return g.name;
      }
    }
  }
  return null;
}
