import type { EvalComparisonRow, Stat, TrainingRun } from "./types";
import { decayCurve, learningCurve, series } from "./util";

export const trainingStats: Stat[] = [
  { label: "Active runs", value: "24", icon: "play", tint: "blue", delta: { value: "4", dir: "up", label: "vs yesterday" }, spark: series(51, 16, 14, 26, 0.6) },
  { label: "Best policy (success)", value: "Refrigerator v2.1.3", icon: "trophy", tint: "amber", foot: "98.7% on Refrigerator Open" },
  { label: "Average improvement", value: "+12.4%", icon: "training", tint: "green", foot: "vs previous best", spark: series(52, 16, 4, 13, 0.3) },
  { label: "Evaluation success", value: "92.6%", icon: "gauge", tint: "green", delta: { value: "3.1pp", dir: "up", label: "vs yesterday" }, donut: 0.926 },
  { label: "Current skill target", value: "Refrigerator Open", icon: "target", tint: "purple", foot: "Left-hinge heavy doors" },
];

export const trainingRuns: TrainingRun[] = [
  { id: "r1", runId: "9f2a7c1", name: "Open Cabinet Curriculum 12", policy: "Cabinet Open v1.4.2", worlds: 820, duration: "2h 14m", delta: 8.7, status: "completed", when: "Today, 10:12 AM" },
  { id: "r2", runId: "3c91b5e", name: "Refrigerator Handle Adaptation 04", policy: "Refrigerator v2.1.3", worlds: 1240, duration: "3h 02m", delta: 14.2, status: "completed", when: "Today, 8:56 AM" },
  { id: "r3", runId: "7d8e6fa", name: "Trash Sorting Eval 07", policy: "Trash Sort v1.0.9", worlds: 640, duration: "1h 33m", delta: 6.1, status: "completed", when: "Today, 7:21 AM" },
  { id: "r4", runId: "2b6d4a1", name: "Drawer Close Robustness 05", policy: "Drawer Close v1.2.1", worlds: 600, duration: "1h 41m", delta: 5.3, status: "completed", when: "Yesterday, 9:42 PM" },
  { id: "r5", runId: "8a1f2d9", name: "Microwave Door Open Curriculum 03", policy: "Microwave v1.1.5", worlds: 480, duration: "1h 28m", delta: -0.4, status: "failed", when: "Yesterday, 6:12 PM" },
  { id: "r6", runId: "1c7e9de", name: "Refrigerator Light On Eval 02", policy: "Refrigerator Light v1.0.4", worlds: 320, duration: "58m", delta: 2.1, status: "completed", when: "Yesterday, 3:05 PM" },
];

export const evalComparison: EvalComparisonRow[] = [
  { task: "Open Door (Left-Hinge)", icon: "cabinet", baseline: 68.2, candidate: 94.1 },
  { task: "Open Door (Right-Hinge)", icon: "cabinet", baseline: 96.3, candidate: 98.7 },
  { task: "Close Door", icon: "cabinet", baseline: 92.1, candidate: 97.4 },
  { task: "Light On", icon: "lighting", baseline: 88.7, candidate: 94.6 },
  { task: "Shelf Reach", icon: "layers", baseline: 91.0, candidate: 95.8 },
];

export const successCurve = {
  best: learningCurve(61, 36, 26, 98.7, 2.0),
  baseline: learningCurve(62, 36, 24, 86.3, 2.6),
};
export const collisionCurve = {
  best: decayCurve(63, 36, 20, 1.8, 0.8),
  baseline: decayCurve(64, 36, 21, 6.7, 0.9),
};

export const agentDecision = {
  title: "Why we selected the next curriculum",
  decision: "Low success on left-hinge heavy doors; request 8 new worlds.",
  evidence: [
    "Left-hinge success 68.2% (target ≥ 90%)",
    "Failure spikes with door mass > 5kg",
    "Higher collisions at grasp approach (18%)",
  ],
  nextStep: { name: "Open Cabinet - Left-Hinge Heavy v1", meta: "8 new worlds · difficulty 0.72" },
  confidence: 0.86,
};
