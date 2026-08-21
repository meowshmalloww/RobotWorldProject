import { useEffect, useMemo, useState } from "react";
import { Card } from "../components/ui/Card";
import { Badge, StatusBadge } from "../components/ui/controls";
import { Icon } from "../components/ui/Icon";
import { useToast } from "../components/ui/Toast";
import { api, apiUrl, ApiError } from "../lib/api";
import { useApi } from "../lib/useApi";
import { EmptyState, ErrorState, Skeleton } from "../lib/states";

interface CollectorVersion {
  id: string;
  collectorId: string;
  revision: number;
  versionLabel: string;
  lifecycleState: string;
  active: boolean;
  previousVersionId: string | null;
  schemaSha256: string;
  extractorRevision: string;
  source: string;
  createdAt: string;
}

interface CaseReport {
  name: string;
  url: string;
  passed: boolean;
  completeness: number;
  identityConfidence: number;
  recordCount: number;
  errors: string[];
  baselineRowsSha256: string;
  candidateRowsSha256: string;
}

interface SuiteReport {
  allPassed?: boolean;
  passedCount?: number;
  caseCount?: number;
  cases?: CaseReport[];
  testedAt?: string;
}

interface RepairRun {
  id: string;
  revision: number;
  lifecycleState: string;
  collectorId: string;
  activeVersionId: string;
  lastKnownGoodVersionId: string;
  candidateVersionId: string | null;
  objectRequestId: string;
  failureBundleId: string;
  providerMode: string;
  repairPrompt: string;
  failingFields: string[];
  failureExamples: { sourceUrl: string; contentSha256: string; errors: string[] }[];
  testCases: { golden?: unknown[]; canary?: unknown[]; artifactSha256?: string };
  candidateArtifactSha256: string | null;
  schemaDiff: { compatible?: boolean; addedFields?: string[]; removedFields?: string[]; changedFields?: string[] };
  recordDiff: Record<string, unknown>;
  goldenReport: SuiteReport;
  canaryReport: SuiteReport;
  policy: { automaticPromotion?: boolean; allowSchemaChange?: boolean; lastKnownGoodContinuityRequired?: boolean };
  providerDetail: Record<string, unknown>;
  attempt: number;
  maxAttempts: number;
  commandId: string;
  error: string | null;
  createdAt: string;
  finishedAt: string | null;
}

interface AuditEvent {
  id: number;
  action: string;
  fromState: string | null;
  toState: string | null;
  detail: Record<string, unknown>;
  actor: string;
  createdAt: string;
}

function errorText(reason: unknown): string {
  return reason instanceof ApiError ? reason.message : String(reason);
}

function runStatus(value: string): string {
  if (value === "PROMOTED" || value === "QUALITY_PASSED") return "passed";
  if (["REJECTED", "EXHAUSTED"].includes(value)) return "failed";
  if (value === "ROLLED_BACK") return "blocked";
  if (["GOLDEN_TESTING", "CANARY_TESTING", "REPAIR_REQUESTED"].includes(value)) return "running";
  return "ready";
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function ScraperRepair() {
  const toast = useToast();
  const runs = useApi<{ repairRuns: RepairRun[] }>("/scraper-repair-runs?limit=100");
  const versions = useApi<{ collectorVersions: CollectorVersion[] }>("/scraper-collector-versions?limit=200");
  const [selectedId, setSelectedId] = useState("");
  const [decisionReason, setDecisionReason] = useState("All fixed golden and canary cases passed review.");
  const [busy, setBusy] = useState<"demo" | "promote" | "reject" | "rollback" | "provider" | null>(null);
  const selected = (runs.data?.repairRuns ?? []).find((run) => run.id === selectedId)
    ?? runs.data?.repairRuns[0]
    ?? null;
  const auditPath = selected
    ? `/audit?entity_type=scraper_repair_run&entity_id=${encodeURIComponent(selected.id)}&limit=100`
    : null;
  const audit = useApi<{ events: AuditEvent[] }>(auditPath);
  const collectorVersions = useMemo(
    () => (versions.data?.collectorVersions ?? []).filter((version) => version.collectorId === selected?.collectorId),
    [selected?.collectorId, versions.data],
  );

  useEffect(() => {
    if (!selectedId && runs.data?.repairRuns.length) setSelectedId(runs.data.repairRuns[0].id);
  }, [runs.data, selectedId]);

  const refresh = async () => {
    runs.refetch();
    versions.refetch();
    audit.refetch();
  };

  const runDemo = async () => {
    setBusy("demo");
    try {
      const result = await api.post<{ repairRun: RepairRun }>("/scraper-repair/demo", { automaticPromotion: false });
      setSelectedId(result.repairRun.id);
      toast.push("ok", "Controlled semantic break reproduced", `${result.repairRun.id} passed golden/canary and awaits a decision.`);
      await refresh();
    } catch (reason) {
      toast.push("err", "Controlled repair demo failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const decide = async (decision: "PROMOTE" | "REJECT") => {
    if (!selected) return;
    setBusy(decision === "PROMOTE" ? "promote" : "reject");
    try {
      await api.post(`/scraper-repair-runs/${selected.id}/decision`, { decision, reason: decisionReason });
      toast.push(decision === "PROMOTE" ? "ok" : "info", `Candidate ${decision.toLowerCase()}d`, selected.id);
      await refresh();
    } catch (reason) {
      toast.push("err", "Decision failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const rollback = async () => {
    if (!selected) return;
    setBusy("rollback");
    try {
      await api.post(`/scraper-repair-runs/${selected.id}/rollback`, {
        reason: decisionReason,
        providerRollbackConfirmed: false,
      });
      toast.push("info", "Last-known-good restored", selected.lastKnownGoodVersionId);
      await refresh();
    } catch (reason) {
      toast.push("err", "Rollback failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const requestProvider = async () => {
    if (!selected || !window.confirm("Issue this stored repair prompt to Bright Data? Provider usage may be billable.")) return;
    setBusy("provider");
    try {
      await api.post(`/scraper-repair-runs/${selected.id}/provider-request`, {});
      toast.push("ok", "Provider draft requested", selected.id);
      await refresh();
    } catch (reason) {
      toast.push("err", "Provider repair request failed", errorText(reason));
    } finally {
      setBusy(null);
    }
  };

  const reports = [
    ["Golden", selected?.goldenReport] as const,
    ["Canary", selected?.canaryReport] as const,
  ];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Scraper Repair</h1>
          <p className="page-sub">Governed candidate drafts, semantic golden/canary gates, explicit promotion, and last-known-good rollback. Bright Data output is data, never executable instructions.</p>
        </div>
        <button className="btn btn-primary" disabled={busy !== null} onClick={runDemo}><Icon name="refresh" size={12} /> {busy === "demo" ? "Running real controlled break…" : "Run controlled layout-break demo"}</button>
      </div>

      <div className="callout" style={{ marginBottom: 10, borderColor: "rgba(94,234,212,.25)" }}>
        <Icon name="shield" size={13} style={{ color: "var(--teal)" }} />
        <span><b>Last-known-good continuity.</b> The active collector stays active throughout draft and testing. Promotion occurs only after every stored golden and canary case passes; rollback never deletes either revision.</span>
      </div>

      {(runs.error || versions.error) && <div className="card" style={{ marginBottom: 10 }}><ErrorState message={(runs.error ?? versions.error)?.message ?? "Catalog unavailable"} onRetry={refresh} /></div>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(300px, .7fr) minmax(0, 1.3fr)", gap: 10, alignItems: "start" }}>
        <Card title="Repair runs" right={<Badge tone="grey">{runs.data?.repairRuns.length ?? 0}</Badge>} flush>
          {runs.loading && !runs.data ? <Skeleton rows={6} /> : !runs.data?.repairRuns.length ? <EmptyState icon="refresh">No canonical repair run. Use the controlled break to create real evidence.</EmptyState> : <div>{runs.data.repairRuns.map((run) => <button key={run.id} onClick={() => setSelectedId(run.id)} style={{ width: "100%", textAlign: "left", padding: "10px 12px", borderBottom: "1px solid var(--border)", background: selected?.id === run.id ? "var(--accent-soft)" : "transparent" }}><div className="row between" style={{ gap: 7 }}><span className="mono small">{run.id}</span><StatusBadge status={runStatus(run.lifecycleState)} /></div><div className="micro t3" style={{ marginTop: 4 }}>{run.collectorId} · {run.providerMode.replaceAll("_", " ")}</div><div className="micro t3">{new Date(run.createdAt).toLocaleString()}</div></button>)}</div>}
        </Card>

        <div className="col" style={{ gap: 10 }}>
          <Card title="Governed state" right={selected ? <StatusBadge status={runStatus(selected.lifecycleState)} /> : undefined}>
            {!selected ? <EmptyState icon="refresh">Select or create a repair run.</EmptyState> : <div className="col" style={{ gap: 9 }}>
              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}><Badge tone={selected.providerMode === "brightdata_live" ? "red" : "blue"}>{selected.providerMode === "brightdata_live" ? "Live provider" : "Controlled fixture"}</Badge><Badge tone="grey">revision {selected.revision}</Badge><Badge tone={selected.schemaDiff.compatible ? "green" : "amber"}>{selected.schemaDiff.compatible ? "schema compatible" : "schema changed"}</Badge></div>
              <div className="st-grid">
                <div className="kv-row"><span className="kv-k">Run / command</span><span className="kv-v mono">{selected.id} · {selected.commandId}</span></div>
                <div className="kv-row"><span className="kv-k">Last known good</span><span className="kv-v mono">{selected.lastKnownGoodVersionId}</span></div>
                <div className="kv-row"><span className="kv-k">Candidate</span><span className="kv-v mono">{selected.candidateVersionId ?? "draft not captured"}</span></div>
                <div className="kv-row"><span className="kv-k">Failure bundle</span><span className="kv-v mono">{selected.failureBundleId}</span></div>
                <div className="kv-row"><span className="kv-k">Attempts</span><span className="kv-v mono">{selected.attempt} / {selected.maxAttempts}</span></div>
              </div>
              <div><div className="micro t3" style={{ marginBottom: 5 }}>Precise provider prompt · max 1,000 characters</div><pre className="mono micro" style={{ whiteSpace: "pre-wrap", margin: 0, padding: 10, border: "1px solid var(--border)", borderRadius: 5, background: "var(--bg-panel-2)" }}>{selected.repairPrompt}</pre></div>
              <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>{selected.failingFields.map((field) => <Badge key={field} tone="amber">{field}</Badge>)}</div>
              {selected.error && <div className="callout" style={{ borderColor: "rgba(248,113,113,.3)" }}><Icon name="warning" size={12} style={{ color: "var(--red)" }} /><span className="mono micro">{selected.error}</span></div>}
              {selected.providerMode === "brightdata_live" && selected.lifecycleState === "REPAIR_REQUESTED" && <button className="btn btn-secondary" onClick={requestProvider} disabled={busy !== null}><Icon name="external" size={12} /> {busy === "provider" ? "Requesting provider draft…" : "Request Bright Data self-heal draft"}</button>}
              {selected.lifecycleState === "AWAITING_POLICY_DECISION" && <div className="col" style={{ gap: 7 }}><label className="field"><span>Decision reason</span><input className="input" value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} /></label><div className="row" style={{ gap: 7 }}><button className="btn btn-primary" disabled={busy !== null || decisionReason.trim().length < 3} onClick={() => decide("PROMOTE")}><Icon name="check" size={12} /> {busy === "promote" ? "Promoting…" : "Promote candidate"}</button><button className="btn btn-secondary" disabled={busy !== null || decisionReason.trim().length < 3} onClick={() => decide("REJECT")}><Icon name="x" size={12} /> {busy === "reject" ? "Rejecting…" : "Reject candidate"}</button></div></div>}
              {selected.lifecycleState === "PROMOTED" && <button className="btn btn-secondary" disabled={busy !== null || selected.providerMode === "brightdata_live"} onClick={rollback}><Icon name="reset" size={12} /> {busy === "rollback" ? "Restoring…" : "Rollback to last-known-good"}</button>}
              {selected.lifecycleState === "PROMOTED" && selected.providerMode === "brightdata_live" && <span className="micro t-amber">Confirm the provider-side version rollback before restoring the internal active pointer.</span>}
            </div>}
          </Card>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
            {reports.map(([label, report]) => <Card key={label} title={`${label} gate`} right={report?.caseCount ? <StatusBadge status={report.allPassed ? "passed" : "failed"} /> : undefined}>{!report?.caseCount ? <EmptyState icon="target">Not run.</EmptyState> : <div className="col" style={{ gap: 8 }}><div className="row" style={{ gap: 6 }}><Badge tone={report.allPassed ? "green" : "red"}>{report.passedCount}/{report.caseCount} passed</Badge><span className="micro t3">{report.testedAt ? new Date(report.testedAt).toLocaleString() : ""}</span></div>{report.cases?.map((test) => <div key={test.name} style={{ borderTop: "1px solid var(--border)", paddingTop: 7 }}><div className="row between"><span className="mono small">{test.name}</span><StatusBadge status={test.passed ? "passed" : "failed"} /></div><div className="micro t3">identity {pct(test.identityConfidence)} · completeness {pct(test.completeness)} · {test.recordCount} row(s)</div>{test.errors.map((error) => <div className="micro t-red" key={error}>{error}</div>)}</div>)}</div>}</Card>)}
          </div>
        </div>
      </div>

      <Card title="Collector version continuity" flush style={{ marginTop: 10 }} right={<Badge tone="grey">{collectorVersions.length} versions</Badge>}>
        {!selected ? <EmptyState icon="sources">Select a repair run.</EmptyState> : !collectorVersions.length ? <EmptyState icon="sources">No version records.</EmptyState> : <div className="table-scroll"><table className="table"><thead><tr><th>Version</th><th>State</th><th>Extractor</th><th>Schema hash</th><th>Predecessor</th><th>Created</th></tr></thead><tbody>{collectorVersions.map((version) => <tr key={version.id}><td><div className="mono">{version.id}</div><div className="micro t3">r{version.revision} · {version.versionLabel}</div></td><td><div className="row" style={{ gap: 5 }}><StatusBadge status={version.active ? "running" : runStatus(version.lifecycleState)} />{version.active && <Badge tone="live" dot>Active</Badge>}</div></td><td className="mono small">{version.extractorRevision}</td><td className="mono micro" title={version.schemaSha256}>{version.schemaSha256.slice(0, 16)}…</td><td className="mono micro">{version.previousVersionId ?? "—"}</td><td className="micro t3">{new Date(version.createdAt).toLocaleString()}</td></tr>)}</tbody></table></div>}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(300px, .65fr)", gap: 10, marginTop: 10, alignItems: "start" }}>
        <Card title="Persisted transition audit" flush>
          {!selected ? <EmptyState icon="workflow">No selected run.</EmptyState> : audit.loading && !audit.data ? <Skeleton rows={5} /> : !audit.data?.events.length ? <EmptyState icon="workflow">No audit events.</EmptyState> : <div className="table-scroll"><table className="table"><thead><tr><th>Transition</th><th>Action</th><th>Actor</th><th>Recorded</th></tr></thead><tbody>{audit.data.events.slice().reverse().map((event) => <tr key={event.id}><td className="mono small">{event.fromState ?? "∅"} → {event.toState ?? "∅"}</td><td className="mono micro">{event.action}</td><td className="small">{event.actor}</td><td className="micro t3">{new Date(event.createdAt).toLocaleString()}</td></tr>)}</tbody></table></div>}
        </Card>
        <Card title="Controlled public-shaped pages" right={<Badge tone="blue">No provider cost</Badge>}>
          <div className="col" style={{ gap: 8 }}><p className="small t2" style={{ margin: 0 }}>The v1 page exposes legacy data attributes. The v2 page intentionally removes them and retains Product JSON-LD. The controlled candidate extracts JSON-LD; no downloaded script is executed.</p><a className="btn btn-secondary" href={apiUrl("/scraper-repair/demo/page/v1")} target="_blank" rel="noreferrer"><Icon name="external" size={12} /> Open layout v1</a><a className="btn btn-secondary" href={apiUrl("/scraper-repair/demo/page/v2")} target="_blank" rel="noreferrer"><Icon name="external" size={12} /> Open broken layout v2</a></div>
        </Card>
      </div>
    </div>
  );
}
