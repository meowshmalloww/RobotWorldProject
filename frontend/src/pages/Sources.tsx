import { useMemo, useState } from "react";
import { Card, CardLink, Progress } from "../components/ui/Card";
import { StatCard } from "../components/ui/StatCard";
import { Icon, type IconName } from "../components/ui/Icon";
import { Badge, Pagination, SearchBox, StatusBadge } from "../components/ui/controls";
import { FridgePhoto } from "../components/three/ProductRender";
import { pctTone } from "../components/ui/helpers";
import { bestBuyDetail, sources, sourceStats } from "../data/sources";
import { fmtInt } from "../data/util";

const PHOTO_VIEWS = ["front", "angle", "open", "kitchen"] as const;

export default function Sources() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [selectedSource, setSelectedSource] = useState("s1");
  const d = bestBuyDetail;
  const [photo, setPhoto] = useState(1);

  const filtered = useMemo(() => sources.filter((s) => s.domain.includes(q.toLowerCase())), [q]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Sources</h1>
          <p className="page-sub">Monitor web data collection, extraction quality, and self-healing scrapers.</p>
        </div>
        <div className="head-actions">
          <span className="small t3">Last updated: 1m ago</span>
          <button className="btn btn-ghost btn-icon" title="Refresh"><Icon name="refresh" size={14} /></button>
          <button className="btn btn-primary"><Icon name="plus" size={13} /> Add source</button>
        </div>
      </div>

      <div className="so-stats">
        {sourceStats.map((s) => <StatCard key={s.label} stat={s} />)}
      </div>

      <div className="so-main">
        {/* Sources table */}
        <Card title="Sources &amp; Scrapers" right={<Badge tone="blue">Bright Data</Badge>} flush>
          <div style={{ padding: "10px 14px 6px" }}>
            <SearchBox placeholder="Search sources…" value={q} onChange={(v) => { setQ(v); setPage(1); }} style={{ maxWidth: 260 }} />
          </div>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Source</th><th>Collector</th><th style={{ textAlign: "right" }}>Items</th>
                  <th style={{ width: 130 }}>Completeness</th><th>Last run</th><th>Health</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((s) => (
                  <tr key={s.id + s.category} className={`rowlink ${selectedSource === s.id ? "selected" : ""}`} onClick={() => setSelectedSource(s.id)}>
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
            <span className="micro t3">Showing {filtered.length} of 1,842 sources</span>
            <Pagination page={page} pages={185} onPage={setPage} />
          </div>
          <div className="row" style={{ gap: 7, padding: "9px 14px", borderTop: "1px solid var(--border)" }}>
            <span className="micro t3">Integration</span>
            <span className="badge b-grey" style={{ height: 19 }}>Bright Data</span>
            <span className="badge b-green" style={{ height: 19 }}><span className="dot" /> Active</span>
          </div>
        </Card>

        {/* Product / extraction detail */}
        <Card
          title={
            <span className="row" style={{ gap: 6 }}>
              bestbuy.com / Refrigerators
            </span>
          }
          right={<Badge tone="green" dot>Healthy</Badge>}
          flush
        >
          <div className="row" style={{ gap: 12, padding: "12px 14px", alignItems: "flex-start" }}>
            <div className="thumb" style={{ width: 96, height: 118, flex: "none" }}>
              <FridgePhoto seed={d.imageSeed} view="front" width={96} height={118} />
            </div>
            <div className="col" style={{ gap: 2 }}>
              <b style={{ fontSize: "var(--fs-title)", lineHeight: 1.35 }}>{d.product}</b>
              <span className="small t2" style={{ marginTop: 3 }}>Model {d.model}</span>
            </div>
          </div>
          <div className="card-body-flush" style={{ padding: "0 14px 6px" }}>
            <div className="section-label" style={{ marginBottom: 4 }}>Specifications</div>
            <div className="kv">
              {d.specs.map(([k, v]) => (
                <div key={k} className="kv-row">
                  <span className="kv-k">{k}</span>
                  <span className="kv-v" style={v.startsWith("http") ? { color: "var(--link)", fontSize: "var(--fs-small)", maxWidth: 250 } : {}}>
                    {v.startsWith("http") ? <a>{v.slice(0, 44)}… <Icon name="external" size={9} /></a> : v}
                  </span>
                </div>
              ))}
            </div>
            <div className="section-label" style={{ margin: "10px 0 4px" }}>Provenance</div>
            <div className="kv">
              {d.provenance.map(([k, v]) => (
                <div key={k} className="kv-row">
                  <span className="kv-k">{k}</span>
                  <span className="kv-v" style={v.startsWith("http") ? { color: "var(--link)", fontSize: "var(--fs-small)", maxWidth: 250 } : {}}>
                    {v.startsWith("http") ? <a>{v.slice(0, 44)}… <Icon name="external" size={9} /></a> : v}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}>
            <button className="btn btn-secondary btn-sm">Open in source <Icon name="external" size={11} /></button>
          </div>
        </Card>

        {/* Right column: photo selection + repair timeline */}
        <div className="so-right">
          <Card title="Best Photo Selection" right={<Badge tone="blue">Bright Data</Badge>}>
            <div className="row micro t3" style={{ paddingBottom: 6, gap: 6 }}>
              <span style={{ width: 64 }} />
              <span className="grow" />
              {["Front", "Bkg", "Obj", "ID"].map((x) => <span key={x} style={{ width: 30, textAlign: "center" }}>{x}</span>)}
              <span style={{ width: 34, textAlign: "center" }}>Score</span>
              <span style={{ width: 62 }} />
            </div>
            <div className="col" style={{ gap: 7 }}>
              {d.photos.map((p, i) => (
                <div key={p.id} className={`photo-row ${p.state === "selected" ? "sel" : ""}`} style={{ cursor: "pointer" }} onClick={() => setPhoto(p.id)}>
                  <div className="p-thumb">
                    <FridgePhoto seed={p.seed} view={PHOTO_VIEWS[i % PHOTO_VIEWS.length]} width={128} height={104} />
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="cell-ico" style={{ width: 18, height: 18, fontSize: 9.5, fontFamily: "var(--font-mono)", fontWeight: 700 }}>{p.id}</span>
                    <span className="grow" />
                    {[p.front, p.background, p.isolation, p.identity].map((v, j) => (
                      <div key={j} className="photo-metric" style={{ width: 30 }}>
                        <span className="v" style={{ color: v >= 90 ? "var(--green)" : v >= 70 ? "var(--text-1)" : "var(--amber)" }}>{v}%</span>
                      </div>
                    ))}
                    <div className="photo-metric" style={{ width: 34 }}>
                      <span className="v" style={{ fontWeight: 700, color: p.score >= 90 ? "var(--green)" : p.score >= 75 ? "var(--text-1)" : "var(--amber)", fontSize: "var(--fs-body)" }}>{p.score}</span>
                    </div>
                    <span style={{ width: 62, textAlign: "right" }}>
                      {p.state === "selected" && <Badge tone="green">Selected</Badge>}
                      {p.state === "secondary" && <Badge tone="blue">Secondary</Badge>}
                      {p.state === "rejected" && <Badge tone="red">Rejected</Badge>}
                      {p.state === "candidate" && <Badge tone={photo === p.id ? "blue" : "grey"}>{photo === p.id ? "Preview" : "Candidate"}</Badge>}
                    </span>
                  </div>
                </div>
              ))}
            </div>
            <div className="micro t3" style={{ marginTop: 8 }}>Selected hero image will be used across listings and assets.</div>
          </Card>

          <Card title="Repair Timeline" right={<Badge tone="blue">Bright Data</Badge>}>
            <div className="repair-tl">
              {d.repairs.map((r) => (
                <div key={r.title} className="repair-evt">
                  <span
                    className="r-ico"
                    style={{
                      color: r.kind === "detect" ? "var(--amber)" : r.kind === "fail" ? "var(--red)" : r.kind === "heal" ? "var(--accent)" : "var(--green)",
                      borderColor: r.kind === "fail" ? "rgba(240,86,79,0.5)" : undefined,
                    }}
                  >
                    <Icon
                      name={(r.kind === "detect" ? "warning" : r.kind === "fail" ? "x" : r.kind === "heal" ? "chip" : "check") as IconName}
                      size={10}
                    />
                  </span>
                  <span className="r-time">{r.time}</span>
                  <div className="r-title">{r.title}</div>
                  <div className="r-desc">{r.desc}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 8 }}>
              <CardLink>View all history</CardLink>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
