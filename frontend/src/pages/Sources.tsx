import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Progress } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { Badge, InspSection, Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { Modal } from "../components/ui/Modal";
import { useToast } from "../components/ui/Toast";
import { pctTone } from "../components/ui/helpers";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";
import { fmtInt } from "../lib/format";
import type { Source, SourceDetail, Stat } from "../data/types";

const PAGE_SIZE = 20;

interface SourcesData {
  stats: Stat[];
  sources: Source[];
}

export default function Sources() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<SourcesData>("/sources");
  const [q, setQ] = useState("");
  const [health, setHealth] = useState("All");
  const [page, setPage] = useState(1);
  const [selectedSource, setSelectedSource] = useState<string>("");
  const [addOpen, setAddOpen] = useState(false);
  const [photo, setPhoto] = useState(1);
  const [creating, setCreating] = useState(false);
  const [running, setRunning] = useState(false);

  // Add-source form
  const domainRef = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState("Refrigerators");
  const [collector, setCollector] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");

  const sources = useMemo(() => data?.sources ?? [], [data]);
  const filtered = useMemo(
    () =>
      sources.filter(
        (s) =>
          (s.domain.includes(q.toLowerCase()) || s.category.toLowerCase().includes(q.toLowerCase())) &&
          (health === "All" || s.health === health.toLowerCase()),
      ),
    [sources, q, health],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const sel = sources.find((s) => s.id === selectedSource) ?? filtered[0] ?? null;
  const detailPath = sel ? `/sources/${sel.id}` : null;
  const { data: d, error: detailError, loading: detailLoading, refetch: refetchDetail } = useApi<SourceDetail>(detailPath);

  const addSource = async () => {
    const domain = domainRef.current?.value.trim().replace(/^https?:\/\/(www\.)?/, "").replace(/\/.*$/, "");
    if (!domain) {
      toast.push("err", "URL required", "Enter the listing or category URL to collect from");
      return;
    }
    setCreating(true);
    try {
      const created = await api.post<Source>("/sources", { domain, category, query: sourceQuery || domain, collector });
      setAddOpen(false);
      toast.push("ok", "Source registered", collector ? `${created.domain} · custom collector ${collector}` : `${created.domain} · add a c_* collector ID before running`);
      setSelectedSource(created.id);
      refetch();
    } catch (e) {
      toast.push("err", "Could not add source", e instanceof ApiError ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const runCollector = async () => {
    if (!sel) return;
    setRunning(true);
    try {
      const { jobId } = await api.post<{ jobId: string }>(`/sources/${sel.id}/run`);
      toast.push("ok", "Re-scrape queued", `${sel.domain} · job ${jobId}`);
    } catch (e) {
      // backend returns 503 with detail when Bright Data is not configured
      toast.push("err", "Collector could not run", e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Sources</h1>
          <p className="page-sub">Custom Scraper Studio collectors, validated structured output, and human-approved self-healing.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-ghost btn-sm" title="Refresh" onClick={refetch}><Icon name="refresh" size={13} /></button>
          <button className="btn btn-primary" onClick={() => setAddOpen(true)}><Icon name="plus" size={13} /> Add source</button>
        </div>
      </div>

      <div className="so-stats">
        {loading && !data
          ? Array.from({ length: 5 }, (_, i) => <div key={i} className="stat-card"><Skeleton rows={2} height={12} style={{ padding: 4 }} /></div>)
          : data?.stats.map((s) => <StatCard key={s.label} stat={s} />)}
      </div>

      <div className="so-main" style={{ gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)" }}>
        {/* Sources table */}
        <Card title="Sources &amp; Scrapers" flush>
          <div className="row" style={{ gap: 7, padding: "10px 14px 8px" }}>
            <SearchBox placeholder="Search sources…" value={q} onChange={(v) => { setQ(v); setPage(1); }} style={{ width: 220 }} />
            <select className="select" style={{ width: 110 }} value={health} onChange={(e) => { setHealth(e.target.value); setPage(1); }}>
              {["All", "Healthy", "Degraded", "Repairing"].map((h) => <option key={h}>{h}</option>)}
            </select>
          </div>
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
                      <th>Source</th><th>Collector</th><th style={{ textAlign: "right" }}>Items</th>
                      <th style={{ width: 130 }}>Completeness</th><th>Last run</th><th>Health</th>
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((s) => (
                      <tr key={s.id + s.category} className={`rowlink ${sel?.id === s.id ? "selected" : ""}`} onClick={() => setSelectedSource(s.id)}>
                        <td>
                          <div className="cell-main">
                            <span className={`brand-ico brand-${s.brand}`}>{s.domain.slice(0, 1).toUpperCase()}</span>
                            <span className="col" style={{ gap: 0 }}>
                              <span style={{ fontWeight: 580 }}>{s.domain}</span>
                              <span className="micro t3">{s.category}</span>
                            </span>
                          </div>
                        </td>
                        <td className="mono t2" style={{ fontSize: "var(--fs-small)" }}>{s.collector}</td>
                        <td className="mono" style={{ textAlign: "right" }}>{fmtInt(s.items)}</td>
                        <td>
                          <div className="row" style={{ gap: 8 }}>
                            <span className="mono t2" style={{ width: 32, fontSize: "var(--fs-small)" }}>{s.completeness}%</span>
                            <Progress value={s.completeness} tone={pctTone(s.completeness)} style={{ flex: 1 }} />
                          </div>
                        </td>
                        <td className="t-muted" style={{ fontSize: "var(--fs-small)" }}>{s.lastRun}</td>
                        <td><StatusBadge status={s.health} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="row between" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
                <span className="micro t3">Showing {paged.length} of {sources.length} sources</span>
                <Pagination page={page} pages={pages} onPage={setPage} />
              </div>
            </>
          ) : (
            <EmptyState icon="sources">No sources registered yet — add one with <b>Add source</b>.</EmptyState>
          )}
        </Card>

        {/* Extraction detail */}
        <Card
          title={sel ? <span className="row" style={{ gap: 6 }}>{sel.domain} <span className="t3" style={{ fontWeight: 450 }}>/ {sel.category}</span></span> : "Extraction detail"}
          right={sel && <StatusBadge status={sel.health} />}
          flush
          style={{ minHeight: 0 }}
        >
          {!sel ? (
            <EmptyState icon="sources">Select a source to inspect extraction quality.</EmptyState>
          ) : detailLoading && !d ? (
            <Skeleton rows={8} />
          ) : detailError ? (
            detailError.status === 404 ? (
              <EmptyState icon="sources">No extraction detail for this source yet — run its collector.</EmptyState>
            ) : (
              <ErrorState message={detailError.message} onRetry={refetchDetail} />
            )
          ) : d ? (
            <>
              <div style={{ overflowY: "auto", minHeight: 0, maxHeight: "calc(100vh - 320px)" }}>
                <div className="row" style={{ gap: 12, padding: "12px 14px", alignItems: "flex-start" }}>
                  <SourceImage url={d.photos.find((item) => item.state === "selected")?.url ?? d.photos[0]?.url} alt={d.product} width={88} height={108} />
                  <div className="col" style={{ gap: 2 }}>
                    <b style={{ fontSize: "var(--fs-title)", lineHeight: 1.35 }}>{d.product}</b>
                    <span className="small t2" style={{ marginTop: 3 }}>Model {d.model}</span>
                    <span className="row" style={{ gap: 6, marginTop: 7 }}>
                      <Badge tone="blue">Bright Data</Badge>
                      <span className="micro t3 mono">collector {sel.collector}</span>
                    </span>
                  </div>
                </div>

                <InspSection title="Specifications">
                  <div className="kv">
                    {d.specs.map(([k, v]) => (
                      <div key={k} className="kv-row">
                        <span className="kv-k">{k}</span>
                        <span className="kv-v" style={v.startsWith("http") ? { color: "var(--link)", fontSize: "var(--fs-small)" } : {}}>
                          {v.startsWith("http") ? <a>{v.slice(0, 42)}…</a> : v}
                        </span>
                      </div>
                    ))}
                  </div>
                </InspSection>

                <InspSection title="Photo Selection">
                  <div className="col" style={{ gap: 7 }}>
                    {d.photos.map((p) => (
                      <div key={p.id} className={`photo-row ${photo === p.id ? "sel" : ""}`} style={{ cursor: "pointer" }} onClick={() => setPhoto(p.id)}>
                        <div className="p-thumb"><SourceImage url={p.url} alt={`${d.product} candidate ${p.id}`} width={128} height={104} /></div>
                        <div className="row" style={{ gap: 8 }}>
                          <span className="cell-ico" style={{ width: 18, height: 18, fontSize: 9.5, fontFamily: "var(--font-mono)", fontWeight: 700 }}>{p.id}</span>
                          <span className="col grow" style={{ gap: 1 }}>
                            <span className="row" style={{ gap: 8 }}>
                              <PhotoMetric k="Front" v={p.front} />
                              <PhotoMetric k="Bkg" v={p.background} />
                              <PhotoMetric k="Obj" v={p.isolation} />
                              <PhotoMetric k="ID" v={p.identity} />
                            </span>
                          </span>
                          <b className="mono" style={{ fontSize: 14, color: p.score >= 90 ? "var(--green)" : p.score >= 75 ? "var(--text-1)" : "var(--amber)" }}>{p.score}</b>
                          {p.state === "selected" && <Badge tone="green">Selected</Badge>}
                          {p.state === "rejected" && <Badge tone="red">Rejected</Badge>}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="micro t3" style={{ marginTop: 8 }}>The selected hero image is used across listings and generated assets.</div>
                </InspSection>

                <InspSection title="Repair Timeline" right={<Badge tone="blue">Bright Data</Badge>}>
                  {d.repairs.length > 0 ? (
                    <div className="repair-tl">
                      {d.repairs.map((r) => (
                        <div key={r.title} className="repair-evt">
                          <span
                            className="r-ico"
                            style={{
                              color: r.kind === "detect" ? "var(--amber)" : r.kind === "fail" ? "var(--red)" : r.kind === "heal" ? "var(--accent)" : "var(--green)",
                              borderColor: r.kind === "fail" ? "rgba(214,95,91,0.5)" : undefined,
                            }}
                          >
                            <Icon name={(r.kind === "detect" ? "warning" : r.kind === "fail" ? "x" : r.kind === "heal" ? "chip" : "check") as IconName} size={10} />
                          </span>
                          <span className="r-time">{r.time}</span>
                          <div className="r-title">{r.title}</div>
                          <div className="r-desc">{r.desc}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState icon="check">No repair events — extractor is healthy.</EmptyState>
                  )}
                </InspSection>

                <InspSection title="Provenance" defaultOpen={false}>
                  <div className="kv">
                    {d.provenance.map(([k, v]) => (
                      <div key={k} className="kv-row">
                        <span className="kv-k">{k}</span>
                        <span className="kv-v" style={v.startsWith("http") ? { color: "var(--link)", fontSize: "var(--fs-small)" } : {}}>
                          {v.startsWith("http") ? <a>{v.slice(0, 42)}…</a> : v}
                        </span>
                      </div>
                    ))}
                  </div>
                </InspSection>
              </div>
              <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", flex: "none" }} className="row">
                <button className="btn btn-secondary btn-sm" onClick={() => toast.push("info", "Open in source", "External navigation requires the backend proxy")}>Open in source <Icon name="external" size={11} /></button>
                <span className="grow" />
                {sel.collector !== "—" && (
                  <Link className="btn btn-secondary btn-sm" to="/scraper-repair"><Icon name="shield" size={12} /> Governed repair</Link>
                )}
                <button className="btn btn-ghost btn-sm" onClick={runCollector} disabled={running}>
                  <Icon name="refresh" size={12} className={running ? "spin" : undefined} /> {running ? "Queued…" : "Re-run collector"}
                </button>
              </div>
            </>
          ) : null}
        </Card>
      </div>

      {addOpen && (
        <Modal
          title="Add source"
          onClose={() => setAddOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setAddOpen(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={addSource} disabled={creating}>
                {creating ? "Registering…" : "Register source"}
              </button>
            </>
          }
        >
          <div className="col" style={{ gap: 12 }}>
            <div className="field"><label>URL</label><input ref={domainRef} className="input mono" placeholder="https://www.retailer.com/appliances/refrigerators" autoFocus /></div>
            <div className="field"><label>Custom Scraper Studio collector ID</label><input className="input mono" value={collector} onChange={(e) => setCollector(e.target.value.trim())} placeholder="c_... (created in Bright Data Scraper Studio)" /></div>
            <div className="field"><label>Product query / model</label><input className="input" value={sourceQuery} onChange={(e) => setSourceQuery(e.target.value)} placeholder="Samsung RF28T5001SR refrigerator" /></div>
            <div className="row" style={{ gap: 10 }}>
              <div className="field grow">
                <label>Category</label>
                <select className="select" value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option>Refrigerators</option><option>Cabinets</option><option>Tableware</option><option>Room photos</option>
                </select>
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function SourceImage({ url, alt, width, height }: { url?: string; alt: string; width: number; height: number }) {
  if (!url) {
    return <div className="empty-note center" style={{ width, height, display: "flex", padding: 8 }}>No source image</div>;
  }
  return <img src={url} alt={alt} width={width} height={height} loading="lazy" referrerPolicy="no-referrer" style={{ objectFit: "contain", background: "var(--bg-inset)", borderRadius: "var(--r-sm)" }} />;
}

function PhotoMetric({ k, v }: { k: string; v: number }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      <span className="micro t3">{k}</span>
      <span className="mono" style={{ fontSize: "var(--fs-small)", color: v >= 90 ? "var(--green)" : v >= 70 ? "var(--text-1)" : "var(--amber)" }}>{v}%</span>
    </span>
  );
}
