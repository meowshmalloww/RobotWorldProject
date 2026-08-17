import type { Skill, SkillDetail } from "./types";
import { decayCurve, learningCurve, series } from "./util";

export const skills: Skill[] = [
  { id: "open-cabinet", name: "Open Cabinet", category: "Manipulation", description: "Open cabinet doors of varying types and configurations.", success: 92.1, successDelta: 2.1, coverage: 70, lastTrained: "10:14 AM · May 24", status: "ready", icon: "cabinet" },
  { id: "open-refrigerator", name: "Open Refrigerator", category: "Manipulation", description: "Open refrigerator door and expose the interior.", success: 87.8, successDelta: -1.3, coverage: 62, lastTrained: "9:32 AM · May 24", status: "improving", icon: "fridge" },
  { id: "take-out-trash", name: "Take Out Trash", category: "Navigation", description: "Remove trash bag and place in collection bin.", success: 76.3, successDelta: -3.6, coverage: 48, lastTrained: "Yesterday · 8:41 PM", status: "weak", icon: "trash" },
  { id: "bin-sorting", name: "Bin Sorting", category: "Manipulation", description: "Sort items into the correct bin.", success: 90.7, successDelta: 1.8, coverage: 74, lastTrained: "Yesterday · 7:15 PM", status: "ready", icon: "bin" },
  { id: "load-dishwasher", name: "Load Dishwasher", category: "Manipulation", description: "Place dishes in dishwasher racks.", success: 84.2, successDelta: 0.7, coverage: 58, lastTrained: "May 23 · 10:02 AM", status: "improving", icon: "dishwasher" },
  { id: "place-ingredients", name: "Place Ingredients in Bowl", category: "Manipulation", description: "Place ingredients into a mixing bowl.", success: 82.6, successDelta: -0.9, coverage: 54, lastTrained: "May 23 · 9:11 AM", status: "improving", icon: "bowl" },
];

export const recommendedSkills = [
  { rank: 1, name: "Close Cabinet", impact: "High impact", gaps: 3 },
  { rank: 2, name: "Take Out Recycling", impact: "High impact", gaps: 4 },
  { rank: 3, name: "Wipe Counter", impact: "Medium impact", gaps: 2 },
];

export const skillRelations = {
  root: { name: "Open Cabinet", status: "Ready" },
  edges: [
    { to: "Close Cabinet", status: "Weak", kind: "prereq" },
    { to: "Put Away Item", status: "Improving", kind: "prereq" },
    { to: "Search in Cabinet", status: "Weak", kind: "stronger" },
  ],
};

export const scenarioCoverageDims: { dimension: string; coverage: number; gaps: number; bands: [number, number, number, number] }[] = [
  { dimension: "Handle orientation", coverage: 70, gaps: 3, bands: [82, 48, 65, 22] },
  { dimension: "Object weight", coverage: 60, gaps: 4, bands: [76, 58, 61, 18] },
  { dimension: "Hinge side", coverage: 80, gaps: 1, bands: [88, 84, 72, 12] },
  { dimension: "Height", coverage: 65, gaps: 3, bands: [71, 63, 44, 8] },
  { dimension: "Clutter level", coverage: 45, gaps: 5, bands: [58, 52, 30, 26] },
  { dimension: "Room type", coverage: 60, gaps: 4, bands: [70, 41, 55, 34] },
];

export const openCabinetDetail: SkillDetail = {
  id: "open-cabinet",
  name: "Open Cabinet",
  category: "Manipulation",
  description: "Open cabinet doors of varying types and configurations.",
  success: 42.1,
  successDelta: -2.7,
  coverage: 28,
  lastTrained: "10:14 AM · May 24",
  status: "weak",
  icon: "cabinet",
  target: 80.0,
  avgCollisions: 0.64,
  collisionsDelta: 0.08,
  lastGain: "+1.3pp",
  scenarioCount: "312 / 1,112 scenarios",
  promoted: true,
  successTrend: series(21, 24, 30, 46, 0.3),
  coverageTrend: series(22, 24, 18, 30, 0.15),
  collisionTrend: decayCurve(23, 24, 0.95, 0.64, 0.12),
  weaknesses: [
    { mode: "Heavy door resistance", detail: "Agent fails to overcome high resistance thresholds", contribution: 28.6, examples: 2143 },
    { mode: "Low handle placement", detail: "Handles near bottom edge or below midline", contribution: 21.3, examples: 1596 },
    { mode: "Left hinge cabinets", detail: "Left-hinged doors cause approach misalignment", contribution: 17.8, examples: 1332 },
    { mode: "Cluttered approach path", detail: "Obstacles or tight clearance near cabinet", contribution: 12.1, examples: 907 },
    { mode: "Double-door coordination", detail: "Fails to sequence or open second door", contribution: 7.9, examples: 592 },
    { mode: "Glass or reflective doors", detail: "Misperception of handle or door boundaries", contribution: 6.3, examples: 472 },
    { mode: "Latch / catch not released", detail: "Door requires lift/pull before swing", contribution: 5.0, examples: 376 },
    { mode: "Other", detail: "", contribution: 1.0, examples: 74 },
  ],
  curriculum: [
    { rank: 1, name: "Heavy resistance, left hinge", desc: "High resistance + left hinge combos", impact: "high", scenarios: 320 },
    { rank: 2, name: "Low handles, cluttered path", desc: "Low placement with obstacles", impact: "high", scenarios: 280 },
    { rank: 3, name: "Latch + heavy resistance", desc: "Latch or lift + high resistance", impact: "medium", scenarios: 200 },
    { rank: 4, name: "Glass doors with handles", desc: "Reflective / transparent panels", impact: "medium", scenarios: 180 },
    { rank: 5, name: "Double doors, mixed hinges", desc: "Asymmetric double-door setups", impact: "low", scenarios: 160 },
  ],
  families: [
    { id: "f1", family: "Standard cabinets, right hinge", count: 134, success: 68.7, coverage: 100, source: "WorldGen v2", status: "promoted", updated: "May 10, 10:02 AM" },
    { id: "f2", family: "Heavy resistance, left hinge", count: 96, success: 27.3, coverage: 62, source: "WorldGen v2", status: "needs_data", updated: "May 10, 9:41 AM" },
    { id: "f3", family: "Low handles, cluttered path", count: 112, success: 24.1, coverage: 41, source: "WorldGen v2", status: "needs_data", updated: "May 10, 9:40 AM" },
    { id: "f4", family: "Glass doors with handles", count: 88, success: 31.8, coverage: 53, source: "Imported (CAD)", status: "needs_data", updated: "May 9, 6:22 PM" },
    { id: "f5", family: "Double doors, mixed hinges", count: 76, success: 35.5, coverage: 47, source: "WorldGen v2", status: "in_progress", updated: "May 9, 2:11 PM" },
    { id: "f6", family: "Latch + heavy resistance", count: 104, success: 29.0, coverage: 38, source: "WorldGen v2", status: "needs_data", updated: "May 9, 12:05 PM" },
  ],
  beforeAfter: {
    labels: ["Cycle 32", "Cycle 33", "Cycle 34", "Cycle 35", "Cycle 36"],
    before: [25, 32, 41, 52, 58],
    after: [50, 58, 71, 78, 96],
  },
};

/* Skills-index summary band */
export const skillsBand = [
  { label: "Total skills", value: "142", foot: "7 vs yesterday", icon: "cube", tint: "blue" as const },
  { label: "Ready skills", value: "98", foot: "69% of total", icon: "check", tint: "green" as const },
  { label: "Weak skills", value: "22", foot: "15% of total", icon: "warning", tint: "orange" as const },
  { label: "Active curricula", value: "18", foot: "2 vs yesterday", icon: "book", tint: "purple" as const },
  { label: "Avg success rate", value: "87.6%", foot: "3.4pp vs yesterday", icon: "gauge", tint: "green" as const },
];

export const skillCurveBest = learningCurve(31, 36, 24, 98.7, 2.4);
export const skillCurveBaseline = learningCurve(32, 36, 22, 86.3, 2.8);
