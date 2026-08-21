import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Card, Progress } from "../components/ui/Card";
import { Badge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

interface ObjectRequestView {
  id: string;
  revision: number;
  requestedName: string;
  manufacturer: string | null;
  modelNumber: string | null;
  sku: string | null;
  gtin: string | null;
  category: string;
  exactIdentity: boolean;
  authoritativeDomains: string[];
  requiredProperties: string[];
  lifecycleState: string;
  validationErrors: string[];
  requestSha256: string;
  createdAt: string;
  updatedAt: string;
}

interface ObjectIdentity {
  manufacturer: string;
  modelNumber: string | null;
  sku: string | null;
  gtin: string | null;
  exact: boolean;
  confidence: number;
  method: string;
  evidenceRecordIds: string[];
  conflicts: string[];
}

interface PropertyEstimate {
  name: string;
  value: number | string | number[];
  unit: string | null;
  method: string;
  confidence: number;
  uncertaintyLow: number | null;
  uncertaintyHigh: number | null;
  evidenceRecordIds: string[];
}

interface EvidenceBundle {
  id: string;
  revision: number;
  lifecycleState: string;
  identity: ObjectIdentity;
  properties: PropertyEstimate[];
  completeness: number;
  identityConfidence: number;
  validationErrors: string[];
  bundleSha256: string;
  artifactRef: string;
  source: string;
  createdAt: string;
}

interface EvidenceRecord {
  id: string;
  sourceUrl: string;
  sourceType: string;
  sourceDomain: string;
  retrievedAt: string;
  collectorId: string | null;
  collectorVersion: string | null;
  contentSha256: string;
  artifactRef: string;
  identityClaims: Record<string, unknown>;
  qualityErrors: string[];
}

interface RequestDetail {
  objectRequest: ObjectRequestView;
  bundles: EvidenceBundle[];
  records: EvidenceRecord[];
}

interface EvidenceCollectionRun {
  id: string;
  requestId: string;
  collectorId: string;
  collectorVersion: string | null;
  inputUrls: string[];
  lifecycleState: string;
  snapshotId: string | null;
  bundleId: string | null;
  commandId: string;
  providerAttempt: number;
  normalizationAttempt: number;
  timeoutSeconds: number;
  cancellationRequested: boolean;
  error: string | null;
  heartbeatAt: string | null;
  createdAt: string;
}

interface CommandResponse {
  commandId: string;
  status: string;
  reused: boolean;
  result: {
    objectRequest: ObjectRequestView;
    bundle?: EvidenceBundle;
    records?: EvidenceRecord[];
    collectionRun?: EvidenceCollectionRun;
  };
}

const REQUIRED_PROPERTIES = ["manufacturer", "exact_identifier", "dimensions", "mass", "material", "image", "source_url"];

function errorText(reason: unknown): string {
  return reason instanceof ApiError ? reason.message : String(reason);
}

function statusTone(state: string): "green" | "amber" | "red" | "blue" | "grey" {
  if (["QUALITY_PASSED", "IDENTITY_VALIDATED", "SUCCEEDED"].includes(state)) return "green";
  if (["QUALITY_FAILED", "REJECTED", "FAILED", "CANCELLED"].includes(state)) return "red";
  if (["DISCOVERING", "REQUESTED", "QUEUED", "STARTING", "RUNNING"].includes(state)) return "blue";
  return "grey";
}

function compactHash(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-8)}` : value;
}

function formatValue(property: PropertyEstimate): string {
  const raw = Array.isArray(property.value) ? property.value.join(" × ") : String(property.value);
  return property.unit ? `${raw} ${property.unit}` : raw;
}

export default function Evidence() {
  const toast = useToast();
  const { data, error, loading, refetch } = useApi<{ objectRequests: ObjectRequestView[] }>("/evidence/requests");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const detailPath = selectedId ? `/evidence/requests/${selectedId}` : null;
  const { data: detail, error: detailError, loading: detailLoading, refetch: refetchDetail } = useApi<RequestDetail>(detailPath);
  const collectionPath = selectedId ? `/evidence/collections?requestId=${encodeURIComponent(selectedId)}` : null;
  const { data: collectionData, refetch: refetchCollections } = useApi<{ collectionRuns: EvidenceCollectionRun[] }>(collectionPath);

  const [requestedName, setRequestedName] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [modelNumber, setModelNumber] = useState("");
  const [sku, setSku] = useState("");
  const [category, setCategory] = useState("");
  const [domains, setDomains] = useState("");
  const [required, setRequired] = useState(["manufacturer", "exact_identifier", "dimensions", "mass", "material", "source_url"]);
  const [creating, setCreating] = useState(false);
  const [source, setSource] = useState<"recorded_brightdata" | "controlled_fixture">("recorded_brightdata");
  const [collectorId, setCollectorId] = useState("");
  const [collectorVersion, setCollectorVersion] = useState("");
  const [recordedRows, setRecordedRows] = useState("");
  const [normalizing, setNormalizing] = useState(false);
  const [liveCollectorId, setLiveCollectorId] = useState("");
  const [liveCollectorVersion, setLiveCollectorVersion] = useState("");
  const [liveUrl, setLiveUrl] = useState("");
  const [startingCollection, setStartingCollection] = useState(false);
  const [cancellingCollection, setCancellingCollection] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId && data?.objectRequests[0]) setSelectedId(data.objectRequests[0].id);
  }, [data, selectedId]);

  useEffect(() => {
    const active = collectionData?.collectionRuns.some((run) => ["QUEUED", "STARTING", "RUNNING"].includes(run.lifecycleState));
    if (!active) return;
    const timer = window.setInterval(() => {
      refetchCollections();
      refetchDetail();
      refetch();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [collectionData, refetch, refetchCollections, refetchDetail]);

  const latestBundle = detail?.bundles[0] ?? null;
  const requests = useMemo(() => data?.objectRequests ?? [], [data]);

  const createRequest = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    try {
      const response = await api.post<CommandResponse>("/evidence/requests", {
        requestedName,
        manufacturer,
        modelNumber: modelNumber || null,
        sku: sku || null,
        category,
        exactIdentity: true,
        authoritativeDomains: domains.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean),
        requiredProperties: required,
      });
      setSelectedId(response.result.objectRequest.id);
      setRequestedName("");
      setManufacturer("");
      setModelNumber("");
      setSku("");
      toast.push("ok", "Evidence request created", `${response.result.objectRequest.id} · exact identity gates enabled`);
      refetch();
    } catch (reason) {
      toast.push("err", "Request rejected", errorText(reason));
    } finally {
      setCreating(false);
    }
  };

  const toggleRequired = (name: string) => {
    setRequired((current) => current.includes(name) ? current.filter((item) => item !== name) : [...current, name]);
  };

  const normalizeRows = async () => {
    if (!selectedId) return;
    let rows: unknown;
    try {
      rows = JSON.parse(recordedRows);
    } catch {
      toast.push("err", "Invalid JSON", "Paste a JSON array of captured provider rows.");
      return;
    }
    if (!Array.isArray(rows) || rows.length === 0) {
      toast.push("err", "Rows required", "The recorded import must be a non-empty JSON array.");
      return;
    }
    setNormalizing(true);
    try {
      const response = await api.post<CommandResponse>(`/evidence/requests/${selectedId}/normalize-recorded`, {
        rows,
        source,
        collectorId: collectorId || null,
        collectorVersion: collectorVersion || null,
      });
      const passed = response.result.bundle?.lifecycleState === "QUALITY_PASSED";
      toast.push(passed ? "ok" : "err", passed ? "Evidence quality passed" : "Evidence quality failed", response.result.bundle?.validationErrors[0] ?? response.commandId);
      refetch();
      refetchDetail();
    } catch (reason) {
      toast.push("err", "Normalization failed", errorText(reason));
    } finally {
      setNormalizing(false);
    }
  };

  const startCollection = async () => {
    if (!selectedId) return;
    if (!window.confirm("Start this external Scraper Studio collection? Provider usage may be billable.")) return;
    setStartingCollection(true);
    try {
      const response = await api.post<CommandResponse>(`/evidence/requests/${selectedId}/collections`, {
        collectorId: liveCollectorId,
        collectorVersion: liveCollectorVersion || null,
        inputUrls: [liveUrl],
        timeoutSeconds: 180,
      });
      toast.push("info", "Provider collection queued", `${response.result.collectionRun?.id ?? response.commandId} · durable backend run`);
      refetchCollections();
      refetch();
    } catch (reason) {
      toast.push("err", "Collection not started", errorText(reason));
    } finally {
      setStartingCollection(false);
    }
  };

  const cancelCollection = async (run: EvidenceCollectionRun) => {
    setCancellingCollection(run.id);
    try {
      await api.post(`/evidence/collections/${run.id}/cancel`, {});
      toast.push("info", "Cancellation requested", run.id);
      refetchCollections();
    } catch (reason) {
      toast.push("err", "Cancellation failed", errorText(reason));
    } finally {
      setCancellingCollection(null);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Provenance · Identity</div>
          <h1 className="page-title">Evidence</h1>
          <p className="page-sub">Exact-object identity, provenance, normalized physical properties, and immutable quality-gated bundles.</p>
        </div>
        <div className="head-actions"><button className="btn btn-secondary" onClick={() => { refetch(); refetchDetail(); }}><Icon name="refresh" size={13} /> Refresh</button></div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(340px, .82fr) minmax(540px, 1.55fr)", gap: 10, alignItems: "start" }}>
        <div className="col" style={{ gap: 10 }}>
          <Card title="Request exact object" right={<Badge tone="blue" icon="shield">Fail closed</Badge>}>
            <form className="col" style={{ gap: 10 }} onSubmit={createRequest}>
              <div className="field"><label>Requested product</label><input className="input" required maxLength={240} value={requestedName} onChange={(event) => setRequestedName(event.target.value)} placeholder="Acme Blender 500 blue" /></div>
              <div className="st-grid">
                <div className="field"><label>Manufacturer</label><input className="input" required value={manufacturer} onChange={(event) => setManufacturer(event.target.value)} /></div>
                <div className="field"><label>Category</label><input className="input" required value={category} onChange={(event) => setCategory(event.target.value)} placeholder="countertop_blender" /></div>
              </div>
              <div className="st-grid">
                <div className="field"><label>Model number</label><input className="input mono" value={modelNumber} onChange={(event) => setModelNumber(event.target.value)} /></div>
                <div className="field"><label>SKU</label><input className="input mono" value={sku} onChange={(event) => setSku(event.target.value)} /></div>
              </div>
              <div className="field"><label>Authoritative domains (comma separated)</label><input className="input mono" value={domains} onChange={(event) => setDomains(event.target.value)} placeholder="manufacturer.example" /></div>
              <div className="field">
                <label>Required quality fields</label>
                <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                  {REQUIRED_PROPERTIES.map((name) => <button key={name} type="button" className={`btn btn-sm ${required.includes(name) ? "btn-secondary" : "btn-ghost"}`} onClick={() => toggleRequired(name)}>{name}</button>)}
                </div>
              </div>
              <button className="btn btn-primary" disabled={creating || required.length === 0} type="submit"><Icon name="plus" size={13} /> {creating ? "Creating…" : "Create request"}</button>
              <div className="micro t3">Exact requests require a manufacturer and at least one model number or SKU. No network collection begins from this form.</div>
            </form>
          </Card>

          <Card title="Object requests" flush right={<span className="micro t3">{requests.length} persisted</span>}>
            {error ? <ErrorState message={error.message} onRetry={refetch} /> : loading && !data ? <Skeleton rows={4} /> : requests.length === 0 ? <EmptyState icon="book">No evidence requests yet.</EmptyState> : (
              <div className="table-scroll"><table className="table"><thead><tr><th>Object</th><th>State</th></tr></thead><tbody>{requests.map((request) => (
                <tr key={request.id} className={`rowlink ${request.id === selectedId ? "selected" : ""}`} onClick={() => setSelectedId(request.id)}>
                  <td><div style={{ fontWeight: 580 }}>{request.requestedName}</div><div className="micro t3 mono">{request.modelNumber ?? request.sku ?? request.id}</div></td>
                  <td><Badge tone={statusTone(request.lifecycleState)}>{request.lifecycleState}</Badge></td>
                </tr>
              ))}</tbody></table></div>
            )}
          </Card>
        </div>

        <div className="col" style={{ gap: 10 }}>
          <Card title={detail?.objectRequest.requestedName ?? "Evidence bundle"} flush right={detail && <Badge tone={statusTone(detail.objectRequest.lifecycleState)}>{detail.objectRequest.lifecycleState}</Badge>}>
            {!selectedId ? <EmptyState icon="book">Select or create an object request.</EmptyState> : detailError ? <ErrorState message={detailError.message} onRetry={refetchDetail} /> : detailLoading && !detail ? <Skeleton rows={8} /> : detail ? (
              <div className="col" style={{ gap: 0 }}>
                <div style={{ padding: 14, borderBottom: "1px solid var(--border)" }} className="col">
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}><Badge tone="blue">Exact identity</Badge><span className="mono small">{detail.objectRequest.manufacturer}</span><span className="mono small">{detail.objectRequest.modelNumber ?? "no model"}</span><span className="mono small">{detail.objectRequest.sku ?? "no SKU"}</span></div>
                  <div className="micro t3">request {detail.objectRequest.id} · sha256 {compactHash(detail.objectRequest.requestSha256)}</div>
                </div>
                {!latestBundle ? <EmptyState icon="sources">No normalized bundle yet. Import a controlled capture below.</EmptyState> : (
                  <>
                    <div style={{ padding: 14, borderBottom: "1px solid var(--border)" }} className="col">
                      <div className="row between"><b>Bundle revision {latestBundle.revision}</b><Badge tone={statusTone(latestBundle.lifecycleState)}>{latestBundle.lifecycleState}</Badge></div>
                      <div className="st-grid">
                        <div><div className="micro t3">Identity confidence</div><div className="row" style={{ gap: 8 }}><span className="mono">{(latestBundle.identityConfidence * 100).toFixed(0)}%</span><Progress value={latestBundle.identityConfidence * 100} tone={latestBundle.identityConfidence >= .9 ? "green" : "red"} style={{ flex: 1 }} /></div></div>
                        <div><div className="micro t3">Field completeness</div><div className="row" style={{ gap: 8 }}><span className="mono">{(latestBundle.completeness * 100).toFixed(0)}%</span><Progress value={latestBundle.completeness * 100} tone={latestBundle.completeness >= .8 ? "green" : "amber"} style={{ flex: 1 }} /></div></div>
                      </div>
                      <div className="micro t3 mono">bundle {latestBundle.id} · sha256 {compactHash(latestBundle.bundleSha256)}</div>
                      {latestBundle.validationErrors.length > 0 && <div className="col" style={{ gap: 4 }}>{latestBundle.validationErrors.map((value) => <div key={value} className="small" style={{ color: "var(--red)" }}><Icon name="warning" size={11} /> {value}</div>)}</div>}
                    </div>
                    <div className="table-scroll"><table className="table"><thead><tr><th>Property</th><th>Value</th><th>Method</th><th>Confidence</th></tr></thead><tbody>{latestBundle.properties.map((property) => (
                      <tr key={`${property.name}:${property.method}`}><td style={{ fontWeight: 580 }}>{property.name}</td><td className="mono">{formatValue(property)}</td><td>{property.method}</td><td className="mono">{(property.confidence * 100).toFixed(0)}%</td></tr>
                    ))}</tbody></table></div>
                  </>
                )}
              </div>
            ) : null}
          </Card>

          {detail && <Card title="Run Scraper Studio collector" right={<Badge tone="red" icon="external">Live provider · billable</Badge>}>
            <div className="col" style={{ gap: 10 }}>
              <div className="st-grid">
                <div className="field"><label>Published collector ID</label><input className="input mono" value={liveCollectorId} onChange={(event) => setLiveCollectorId(event.target.value)} placeholder="c_exact_products" /></div>
                <div className="field"><label>Collector version metadata</label><input className="input mono" value={liveCollectorVersion} onChange={(event) => setLiveCollectorVersion(event.target.value)} placeholder="v17" /></div>
              </div>
              <div className="field"><label>Exact public product URL</label><input className="input mono" value={liveUrl} onChange={(event) => setLiveUrl(event.target.value)} placeholder="https://manufacturer.example/products/exact-model" /></div>
              <button className="btn btn-primary" onClick={startCollection} disabled={startingCollection || !liveCollectorId.startsWith("c_") || !liveUrl.startsWith("https://")}><Icon name="external" size={13} /> {startingCollection ? "Queueing…" : "Start durable collection"}</button>
              <div className="micro t3">Requires the server-side BRIGHTDATA_API_TOKEN. Snapshot IDs and heartbeats persist across browser refreshes and API restarts; no credential reaches this page.</div>
              {(collectionData?.collectionRuns.length ?? 0) > 0 && <div className="table-scroll"><table className="table"><thead><tr><th>Run</th><th>Provider state</th><th>Snapshot / result</th><th /></tr></thead><tbody>{collectionData?.collectionRuns.map((run) => (
                <tr key={run.id}>
                  <td><div className="mono">{run.id}</div><div className="micro t3">{run.collectorId}</div></td>
                  <td><Badge tone={statusTone(run.lifecycleState)}>{run.lifecycleState}</Badge>{run.error && <div className="micro" style={{ color: "var(--red)", maxWidth: 300 }}>{run.error}</div>}</td>
                  <td><div className="mono small">{run.snapshotId ?? "no snapshot"}</div><div className="micro t3">{run.bundleId ? `bundle ${run.bundleId}` : `attempt ${run.providerAttempt}`}</div></td>
                  <td>{["QUEUED", "STARTING", "RUNNING"].includes(run.lifecycleState) && <button className="btn btn-ghost btn-sm" onClick={() => cancelCollection(run)} disabled={cancellingCollection === run.id}><Icon name="stop" size={11} /> Cancel</button>}</td>
                </tr>
              ))}</tbody></table></div>}
            </div>
          </Card>}

          {detail && <Card title="Normalize captured provider rows" right={<Badge tone="amber" icon="warning">Recorded data — not live</Badge>}>
            <div className="col" style={{ gap: 10 }}>
              <div className="st-grid">
                <div className="field"><label>Capture type</label><select className="select" value={source} onChange={(event) => setSource(event.target.value as typeof source)}><option value="recorded_brightdata">Recorded Bright Data output</option><option value="controlled_fixture">Controlled local fixture</option></select></div>
                <div className="field"><label>Collector ID (optional)</label><input className="input mono" value={collectorId} onChange={(event) => setCollectorId(event.target.value)} placeholder="c_products" /></div>
              </div>
              <div className="field"><label>Collector version (optional)</label><input className="input mono" value={collectorVersion} onChange={(event) => setCollectorVersion(event.target.value)} placeholder="v17" /></div>
              <div className="field"><label>Captured rows (JSON array)</label><textarea className="input mono" rows={8} value={recordedRows} onChange={(event) => setRecordedRows(event.target.value)} placeholder={'[{"source_url":"https://manufacturer.example/product/model","manufacturer":"…","model_number":"…"}]'} /></div>
              <button className="btn btn-primary" onClick={normalizeRows} disabled={normalizing || !recordedRows.trim()}><Icon name="shield" size={13} /> {normalizing ? "Validating…" : "Normalize and quality-gate"}</button>
              <div className="micro t3">Rows are treated as untrusted data. URL policy, identity agreement, semantic error-page detection, unit conversion, provenance, and content hashing run server-side.</div>
            </div>
          </Card>}

          {detail && <Card title="Evidence records" flush right={<span className="micro t3">{detail.records.length} immutable records</span>}>
            {detail.records.length === 0 ? <EmptyState icon="sources">No records captured.</EmptyState> : <div className="table-scroll"><table className="table"><thead><tr><th>Source</th><th>Type</th><th>Collector</th><th>Quality</th></tr></thead><tbody>{detail.records.map((record) => (
              <tr key={record.id}><td><div>{record.sourceDomain}</div><div className="micro t3 mono" title={record.sourceUrl}>{record.sourceUrl}</div></td><td>{record.sourceType}</td><td className="mono">{record.collectorId ?? "—"}</td><td>{record.qualityErrors.length === 0 ? <Badge tone="green" icon="check">Passed</Badge> : <Badge tone="red" icon="x">{record.qualityErrors.length} errors</Badge>}</td></tr>
            ))}</tbody></table></div>}
          </Card>}
        </div>
      </div>
    </div>
  );
}
