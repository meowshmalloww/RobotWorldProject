import type { PipelineActivity, RecentCandidate, SkillGap, Stat } from "./types";
import { series } from "./util";

export const overviewStats: Stat[] = [
  { label: "Active skills", value: "128", icon: "robot", tint: "blue", delta: { value: "12", dir: "up", label: "vs yesterday" }, spark: series(11, 18, 96, 128, 1.2) },
  { label: "Asset builds today", value: "312", icon: "cube", tint: "purple", delta: { value: "18%", dir: "up", label: "vs yesterday" }, spark: series(12, 18, 180, 320, 4) },
  { label: "Simulation pass rate", value: "87.6%", icon: "shield", tint: "green", delta: { value: "4.3pp", dir: "up", label: "vs yesterday" }, donut: 0.876 },
  { label: "Scraper health", value: "98.2%", icon: "worlds", tint: "green", delta: { value: "1.6pp", dir: "up", label: "vs yesterday" }, spark: series(13, 18, 93, 99, 0.2) },
  { label: "Training runs", value: "24", icon: "training", tint: "blue", delta: { value: "3", dir: "up", label: "vs yesterday" }, spark: series(14, 18, 12, 26, 0.5) },
  { label: "Avg pipeline latency", value: "18m 42s", icon: "clock", tint: "amber", delta: { value: "2m 31s", dir: "down", label: "vs yesterday" }, spark: series(15, 18, 14, 24, -0.2) },
];

export const loopStages = [
  { icon: "gauge", title: "1. Evaluate robot", desc: "Run eval suite on real & sim tasks" },
  { icon: "search", title: "2. Diagnose weakness", desc: "Identify failure modes and skill gaps" },
  { icon: "worlds", title: "3. Query sources", desc: "Find relevant objects and environments" },
  { icon: "cube", title: "4. Build asset/world", desc: "Reconstruct 3D assets and scenes" },
  { icon: "scale", title: "5. Validate physics", desc: "Check collisions, articulation, materials" },
  { icon: "skills", title: "6. Train or test", desc: "Run training or evaluation loops" },
  { icon: "refresh", title: "7. Re-evaluate", desc: "Measure improvement and iterate" },
] as const;

export const skillGaps: SkillGap[] = [
  { icon: "cabinet", name: "Open Cabinet", family: "Generalization", success: 42.1, coverage: 28 },
  { icon: "fridge", name: "Open Refrigerator", family: "Generalization", success: 37.8, coverage: 31 },
  { icon: "trash", name: "Take Out Trash", family: "Manipulation", success: 51.3, coverage: 44 },
  { icon: "bin", name: "Bin Sorting", family: "Perception", success: 63.7, coverage: 62 },
];

export const pipelineActivity: PipelineActivity[] = [
  { pipeline: "Open Cabinet v12", icon: "cabinet", stage: "Validate Physics", stageIcon: "scale", status: "success", started: "10:12 AM", duration: "8m 24s" },
  { pipeline: "Refrigerator Scene v5", icon: "fridge", stage: "Build World", stageIcon: "cube", status: "building", started: "10:08 AM", duration: "12m 37s" },
  { pipeline: "Mug 04 Asset", icon: "cylinder", stage: "Build Asset", stageIcon: "cube", status: "success", started: "10:02 AM", duration: "6m 11s" },
  { pipeline: "Trash Can v3", icon: "trash", stage: "Validate Physics", stageIcon: "scale", status: "failed", started: "9:58 AM", duration: "4m 49s" },
  { pipeline: "Bin Sorting Eval", icon: "bin", stage: "Evaluate", stageIcon: "gauge", status: "completed", started: "9:45 AM", duration: "21m 33s" },
];

export const sourceSummary = {
  objectsFound: "18,742",
  objectsDelta: "2,341",
  completeness: "82.4%",
  completenessDelta: "3.7pp",
  top: [
    { name: "Thingiverse", objects: "7,235", completeness: 86.1 },
    { name: "Sketchfab", objects: "6,103", completeness: 81.3 },
    { name: "Google Images", objects: "3,842", completeness: 78.7 },
    { name: "Poly Haven", objects: "1,562", completeness: 88.9 },
  ],
};

export const readiness = {
  promoted: 46,
  promotedDelta: "8",
  blocked: 12,
  blockedDelta: "3",
  recent: [
    { name: "Kitchen Cabinet 02", status: "promoted" },
    { name: "Refrigerator Samsung RF56", status: "promoted" },
    { name: "Trash Can Simplehuman 40L", status: "blocked" },
    { name: "Plastic Bin Blue 30L", status: "promoted" },
    { name: "Mug Ceramic White 02", status: "blocked" },
  ] as RecentCandidate[],
};

export const integrations = [
  { key: "port", name: "Port", desc: "Dataset & model registry", status: "Synced", meta: "10:08 AM" },
  { key: "brightdata", name: "Bright Data", desc: "Web data collection", status: "Active", meta: "100% success" },
  { key: "signoz", name: "SigNoz", desc: "Traces & metrics", status: "Live", meta: "12.4k spans/min" },
];
