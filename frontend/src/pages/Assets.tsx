import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { Icon, type IconName } from "../components/ui/Icon";
import { Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { DonutGauge } from "../components/charts/DonutGauge";
import { Modal, downloadFile } from "../components/ui/Modal";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import type { Asset, Source, Stat } from "../data/types";

const KIND_LABEL = { articulated: "Articulated", rigid: "Rigid", environment: "Environment" } as const;
const KIND_ICON = { articulated: "joint", rigid: "cube", environment: "worlds" } as const;
const PAGE_SIZE = 20;

interface AssetsData {
  assets: Asset[];
  stats: Stat[];
}

interface BuildSourcesData { sources: Source[] }

export default function Assets() {
  const nav = useNavigate();
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<AssetsData>("/assets");
  const { data: sourceData } = useApi<BuildSourcesData>("/sources");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("All types");
  const [status, setStatus] = useState("All status");
  const [page, setPage] = useState(1);
  const [newBuild, setNewBuild] = useState(false);
  const [building, setBuilding] = useState(false);
  const [smokeRunning, setSmokeRunning] = useState(false);

  const assets = useMemo(() => data?.assets ?? [], [data]);
  const filtered = useMemo(
    () =>
      assets.filter(
        (a) =>
          a.name.toLowerCase().includes(q.toLowerCase()) &&
          (kind === "All types" || KIND_LABEL[a.kind] === kind) &&
          (status === "All status" || a.status === status),
      ),
    [assets, q, kind, status],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // New build form state
  const queryRef = useRef<HTMLInputElement>(null);
  const [buildKind, setBuildKind] = useState<"articulated" | "rigid">("articulated");
  const [generator, setGenerator] = useState("parametric");
  const [sourceId, setSourceId] = useState("");
  const familiesRef = useRef<HTMLInputElement>(null);

  const startBuild = async () => {
    const query = queryRef.current?.value.trim();
    if (!query) {
      toast.push("err", "Object query required", "Describe the object to build, e.g. a model number");
      return;
    }
    setBuilding(true);
    try {
      const { assetId } = await api.post<{ assetId: string }>("/assets/build", {
        query,
        kind: buildKind,
        generator,
        sourceId: sourceId || null,
        families: familiesRef.current?.value.split(",").map((s) => s.trim()).filter(Boolean) ?? [],
      });
      setNewBuild(false);
      toast.push("ok", "Asset build queued", `Validated source → mesh generation → OpenUSD + MuJoCo compile`);
      // poll until the asset leaves the building state
      const poll = async (attempt = 0) => {
        if (attempt >= 120) {
          toast.push("info", "Build still running", "Polling stopped after four minutes; the job remains visible in Overview.");
          return;
        }
        try {
          const a = await api.get<Asset>(`/assets/${assetId}`);
          if (a.status === "building" || a.status === "draft") {
            setTimeout(() => poll(attempt + 1), Math.min(2000 + attempt * 100, 5000));
          } else {
            refetch();
            toast.push(a.status === "ready" ? "ok" : "info", "Asset build finished", `${a.name} · ${a.status}`);
          }
        } catch {
          setTimeout(() => poll(attempt + 1), Math.min(2000 + attempt * 100, 5000));
        }
      };
      setTimeout(poll, 2000);
      refetch();
    } catch (e) {
      toast.push("err", "Asset build failed", e instanceof ApiError ? e.message : String(e));
    } finally {
      setBuilding(false);
    }
  };

  const waitForAsset = async (assetId: string, onDone?: (asset: Asset) => void) => {
    for (let attempt = 0; attempt < 160; attempt++) {
      try {
        const asset = await api.get<Asset>(`/assets/${assetId}`);
        if (asset.status !== "building" && asset.status !== "draft") {
          if (onDone) onDone(asset);
          return asset;
        }
      } catch {
        // keep polling on transient read failures
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
    throw new Error("Build did not finish within the smoke-test timeout window.");
  };

  const startTrellisSmokeTest = async () => {
    setSmokeRunning(true);
    try {
      const { assetId } = await api.post<{ assetId: string }>("/assets/build", {
        query: "kitchen blender",
        kind: "rigid",
        generator: "trellis2",
        sourceId: null,
        families: ["smoke-test", "trellis2"],
      });
      toast.push("ok", "TRELLIS smoke run started", `asset ${assetId} is running one-image TRELLIS generation`);
      const asset = await waitForAsset(assetId, (a) => {
        if (a.status === "ready" || a.status === "testing" || a.status === "blocked") {
          toast.push("ok", "TRELLIS smoke run complete", `${a.name} â€” ${a.status}`);
        }
      });
      refetch();
      nav(`/assets/${asset.id}`);
    } catch (e) {
      toast.push("err", "TRELLIS smoke run failed", e instanceof Error ? e.message : e instanceof ApiError ? e.message : String(e));
    } finally {
      setSmokeRunning(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Assets</h1>
          <p className="page-sub">OpenUSD/MuJoCo objects compiled from provenance-bearing source data — geometry, physics, joints, semantics.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-secondary" disabled={assets.length === 0} onClick={() => {
            downloadFile("asset-catalog.json", JSON.stringify(assets.map(({ id, name, kind: k, status: st, readiness, source }) => ({ id, name, kind: k, status: st, readiness, source })), null, 2));
            toast.push("ok", "Catalog exported", `asset-catalog.json · ${assets.length} assets`);
          }}><Icon name="download" size={13} /> Export catalog</button>
          <button className="btn btn-secondary" disabled={smokeRunning} onClick={startTrellisSmokeTest}>
            <Icon name="spark" size={13} /> {smokeRunning ? "TRELLIS smoke in progress..." : "Run TRELLIS smoke build"}
          </button>
          <button className="btn btn-primary" onClick={() => setNewBuild(true)}><Icon name="plus" size={13} /> New asset build</button>
        </div>
      </div>

      {newBuild && (
        <Modal
          title="New asset build"
          onClose={() => setNewBuild(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setNewBuild(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={startBuild} disabled={building}>{building ? "Starting…" : "Start build"}</button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="field"><label>Object query</label><input ref={queryRef} className="input" placeholder="e.g. Samsung RF28T5001SR refrigerator" autoFocus /></div>
            <div className="field">
              <label>Validated Scraper Studio source</label>
              <select className="select" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">None · use reference catalog or live SERP discovery</option>
                {(sourceData?.sources ?? []).map((s) => <option key={s.id} value={s.id}>{s.domain} · {s.collector} · {s.completeness}%</option>)}
              </select>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow">
                <label>Asset type</label>
                <select className="select" value={buildKind} onChange={(e) => setBuildKind(e.target.value as "articulated" | "rigid")}>
                  <option value="articulated">Articulated</option>
                  <option value="rigid">Rigid</option>
                </select>
              </div>
              <div className="field grow">
                <label>Generator</label>
                <select className="select" value={generator} onChange={(e) => setGenerator(e.target.value)}>
                  <option value="parametric">Parametric physical compiler</option>
                  <option value="trellis2">TRELLIS.2 PBR visual + physical proxy compiler</option>
                </select>
              </div>
            </div>
            <div className="field"><label>Scenario families</label><input ref={familiesRef} className="input" placeholder="e.g. left hinge, heavy door, low handle" /></div>
            {generator === "trellis2" && <div className="empty-note">TRELLIS.2 produces the visual PBR mesh only. RobotWorld still authors and validates articulation, collision proxies, mass, and joint physics from the selected structured source.</div>}
          </div>
        </Modal>
      )}

      <div className="ov-stats" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 10 }}>
        {loading && !data
          ? Array.from({ length: 4 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : (data?.stats ?? []).map((s) => (
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
        {error ? (
          <ErrorState message={error.message} onRetry={refetch} />
        ) : loading && !data ? (
          <Skeleton rows={6} />
        ) : paged.length > 0 ? (
          <>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Asset</th><th>Type</th><th>Readiness</th><th>Physics validity</th><th>Scale conf.</th>
                    <th>Live source</th><th>Status</th><th style={{ textAlign: "right" }}>Last evaluation</th><th style={{ width: 30 }} />
                  </tr>
                </thead>
                <tbody>
                  {paged.map((a) => (
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
                      <td>
                        <div className="row" style={{ gap: 8, minWidth: 180 }}>
                          {a.sourceImage ? (
                            <img
                              src={a.sourceImage}
                              alt={`Bright Data source for ${a.name}`}
                              style={{ width: 38, height: 30, borderRadius: 4, objectFit: "cover", border: "1px solid var(--border)" }}
                            />
                          ) : (
                            <span className="cell-ico"><Icon name="sources" size={13} /></span>
                          )}
                          <span className="col" style={{ gap: 1, minWidth: 0 }}>
                            <span className="t-muted" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 180 }}>{a.source}</span>
                            <span className="micro t3">{a.sourceImage ? "Bright Data image acquired" : "No image evidence"}</span>
                          </span>
                        </div>
                      </td>
                      <td><StatusBadge status={a.status} /></td>
                      <td className="t-muted mono" style={{ textAlign: "right", fontSize: "var(--fs-small)" }}>{a.lastEval}</td>
                      <td><button className="icon-btn btn-sm" onClick={(e) => e.stopPropagation()}><Icon name="dots" size={13} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row between" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
              <span className="micro t3">Showing {paged.length} of {assets.length} assets</span>
              <Pagination page={page} pages={pages} onPage={setPage} />
            </div>
          </>
        ) : (
          <EmptyState icon="cube">No assets in the library yet — queue a build with <b>New asset build</b>.</EmptyState>
        )}
      </Card>
    </div>
  );
}
