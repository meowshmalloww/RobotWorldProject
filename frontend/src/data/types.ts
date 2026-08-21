/* API contract mirrored by backend/app/schemas.py */

export type Health = "healthy" | "degraded" | "failed" | "repairing";
export type RunStatus = "in_progress" | "success" | "failed" | "pending" | "building" | "completed";
export type SkillStatus = "ready" | "improving" | "in_training" | "weak" | "not_started";
export type Severity = "high" | "medium" | "low" | "info";
export type ConfidenceSource = "manufacturer_manual" | "retailer" | "material_prior" | "inferred";

export interface Delta { value: string; dir: "up" | "down" | "flat"; goodWhen?: "up" | "down"; label?: string }

export interface Stat {
  label: string;
  value: string;
  icon: string;
  tint: "blue" | "green" | "amber" | "red" | "orange" | "purple" | "teal";
  foot?: string;
  delta?: Delta;
  spark?: number[];
  donut?: number; // 0..1
}

/* ---- Skills ------------------------------------------------------------- */
export interface Skill {
  id: string;
  name: string;
  category: "Manipulation" | "Navigation" | "Perception";
  description: string;
  success: number;          // 0..100
  successDelta: number;     // pp
  coverage: number;         // 0..100
  lastTrained: string;
  status: SkillStatus;
  icon: string;
}

export interface Weakness {
  mode: string;
  detail: string;
  contribution: number; // %
  examples: number;
}

export interface ScenarioFamily {
  id: string;
  family: string;
  count: number;
  success: number;
  coverage: number;
  source: string;
  status: "promoted" | "needs_data" | "in_progress" | "at_risk" | "healthy" | "needs_attention";
  updated: string;
}

export interface CoverageDimension {
  dimension: string;
  coverage: number;
  gaps: number;
  /** coverage per difficulty band: easy, nominal, hard, extreme (0..100) */
  bands: [number, number, number, number];
}

export interface CurriculumItem {
  rank: number;
  name: string;
  desc: string;
  impact: "high" | "medium" | "low";
  scenarios: number;
}

export interface SkillDetail extends Skill {
  target: number;
  avgCollisions: number;
  collisionsDelta: number;
  lastGain: string;
  scenarioCount: string;
  weaknesses: Weakness[];
  families: ScenarioFamily[];
  curriculum: CurriculumItem[];
  beforeAfter: { before: number[]; after: number[]; labels: string[] };
  successTrend: number[];
  coverageTrend: number[];
  collisionTrend: number[];
  promoted: boolean;
}

/* ---- Assets ------------------------------------------------------------- */
export interface AssetPart {
  id: string;
  name: string;
  joint?: "Hinge Joint" | "Prismatic Joint" | "Fixed";
  children?: AssetPart[];
}

export interface Artifact {
  type: string;
  file: string;
  size: string;
  generated: string;
}

export interface CompileStage { name: string; duration: string; durationSeconds: number; status: "passed" | "failed" | "running" }

export interface Asset {
  id: string;
  name: string;
  kind: "articulated" | "rigid" | "environment";
  status: "ready" | "building" | "testing" | "blocked" | "draft";
  readiness: number;      // 0..100
  physicsValidity: number;
  scaleConfidence: number;
  articulation: number;   // % completeness
  lastEval: string;
  lastEvalResult: "passed" | "failed" | "pending";
  source: string;
  sourceImage?: string;
  sourcePhotos?: PhotoCandidate[];
  collectionTrace?: {
    provider: string;
    inputQuery: string;
    requests: { tool: string; query: string; purpose: string }[];
    results: { type: string; value: string; title?: string; domain?: string; state?: string }[];
    resultCount: number;
  };
  parts: AssetPart[];
  artifacts: Artifact[];
  compile: CompileStage[];
  properties: {
    jointType: string; axis: string; limits: string; mass: string;
    material: string; collider: string; semantic: string; affordance: string;
  };
  tags: string[];
}

/* ---- Worlds / scene composer ------------------------------------------- */
export interface SceneNode {
  id: string;
  assetId?: string;
  name: string;
  icon: string;
  visible?: boolean;
  translation?: number[];
  rotationZDeg?: number;
  anchor?: { mode: string; surface: string; gap_m: number };
  locked?: boolean;
  tag?: string;
  children?: SceneNode[];
}

export interface ScenarioVariant {
  id: string;
  name: string;
  desc: string;
  active?: boolean;
}

export interface PhysicsCheck {
  check: string;
  status: "pass" | "warn" | "fail";
  details: string;
  impacted: string;
  severity: "Info" | "Medium" | "High";
}

/* ---- Sources ------------------------------------------------------------- */
export interface Source {
  id: string;
  domain: string;
  category: string;
  collector: string;
  items: number;
  completeness: number;
  lastRun: string;
  health: Health;
  brand: string; // brand glyph colors key
}

export interface PhotoCandidate {
  id: number;
  score: number;
  state: "selected" | "secondary" | "rejected" | "candidate";
  front: number; background: number; isolation: number; identity: number;
  seed: number; // procedural product render seed
  url?: string;
}

export interface RepairEvent { time: string; title: string; desc: string; kind: "detect" | "fail" | "heal" | "approve" | "success" | "done" }

export interface SourceDetail {
  product: string;
  model: string;
  imageSeed: number;
  specs: [string, string][];
  provenance: [string, string][];
  photos: PhotoCandidate[];
  repairs: RepairEvent[];
}

/* ---- Training ------------------------------------------------------------ */
export interface TrainingRun {
  id: string;
  runId: string;
  name: string;
  policy: string;
  worlds: number;
  duration: string;
  delta: number; // pp
  status: RunStatus;
  when: string;
}

export interface EvalComparisonRow {
  task: string;
  icon: string;
  baseline: number;
  candidate: number;
}

/* ---- Observability -------------------------------------------------------- */
export interface TraceSpan {
  name: string;
  service: string;
  startMs: number;
  durationMs: number;
  status: "ok" | "error";
  icon: string;
  color: string;
}

export interface LogLine { time: string; level: "INFO" | "WARN" | "ERROR" | "DEBUG"; service: string; message: string }

export interface Alert {
  title: string;
  severity: Severity;
  service: string;
  firingFor: string;
  pending?: boolean;
  meta: [string, string][];
  tags: string[];
}

export interface AgentInsight { icon: string; title: string; body: string }

export interface ServiceRow {
  name: string;
  kind: "core" | "agent" | "integration" | "worker";
  status: "running" | "degraded" | "stopped";
  version: string;
  latency: string;
  uptime: string;
  restarts: number;
  gpu?: string;
}

/* ---- Overview -------------------------------------------------------------- */
export interface PipelineActivity {
  pipeline: string;
  icon: string;
  stage: string;
  stageIcon: string;
  status: RunStatus;
  started: string;
  duration: string;
}

export interface SkillGap { icon: string; name: string; family: string; success: number; coverage: number }

export interface RecentCandidate { id: string; name: string; status: "promoted" | "blocked" }
