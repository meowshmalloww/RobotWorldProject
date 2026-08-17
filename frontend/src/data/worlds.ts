import type { PhysicsCheck, ScenarioVariant, SceneNode } from "./types";

export const sceneTree: SceneNode[] = [
  {
    id: "world", name: "Warehouse Kitchen v2", icon: "worlds", children: [
      {
        id: "room", name: "Room Shell", icon: "cube", children: [
          { id: "floor", name: "Floor", icon: "floor" },
          { id: "walls", name: "Walls", icon: "wall" },
          { id: "ceiling", name: "Ceiling", icon: "ceiling" },
          { id: "lighting", name: "Lighting", icon: "lighting" },
        ],
      },
      {
        id: "robot", name: "Robot", icon: "robot", children: [
          { id: "robot-base", name: "Base", icon: "robot", locked: true },
          { id: "robot-arm", name: "Arm", icon: "joint" },
          { id: "robot-gripper", name: "Gripper", icon: "gripper" },
          { id: "robot-cam", name: "Camera (wrist)", icon: "camera" },
        ],
      },
      {
        id: "furniture", name: "Furniture", icon: "cabinet", children: [
          { id: "cabinet-02", name: "Kitchen Cabinet 02", icon: "cabinet", tag: "Static Mesh" },
          { id: "bins", name: "Storage Bins", icon: "bin" },
          { id: "worktable", name: "Worktable", icon: "floor" },
          { id: "stool", name: "Stool", icon: "cylinder" },
        ],
      },
      {
        id: "appliances", name: "Appliances", icon: "fridge", children: [
          { id: "fridge", name: "Refrigerator Samsung RF56", icon: "fridge" },
          { id: "trashcan", name: "Trash Can Simplehuman 40L", icon: "trash" },
          { id: "microwave", name: "Microwave", icon: "microwave" },
        ],
      },
      {
        id: "props", name: "Props", icon: "box", children: [
          { id: "mug", name: "Mug Ceramic White 02", icon: "cylinder" },
          { id: "bottle", name: "Bottle Detergent", icon: "cylinder" },
          { id: "towel", name: "Towel Roll", icon: "cylinder" },
        ],
      },
      {
        id: "sensors", name: "Sensors", icon: "sensor", children: [
          { id: "rgb-ceiling", name: "RGB Camera (ceiling)", icon: "camera" },
          { id: "depth-corner", name: "Depth Camera (corner)", icon: "camera" },
          { id: "lidar", name: "Lidar (rear wall)", icon: "lidar" },
        ],
      },
    ],
  },
];

export const scenarioVariants: (ScenarioVariant & { desc: string })[] = [
  { id: "var_default", name: "Default", desc: "baseline layout", active: true },
  { id: "var_low_handle", name: "Low Handle", desc: "cabinet handle lowered" },
  { id: "var_left_hinge", name: "Left Hinge", desc: "door opens left" },
  { id: "var_narrow_aisle", name: "Narrow Aisle", desc: "aisle width 1.2m" },
  { id: "var_cluttered", name: "Cluttered Counter", desc: "more items on counter" },
];

export const physicsChecks: PhysicsCheck[] = [
  { check: "Collisions", status: "pass", details: "No interpenetrations detected", impacted: "0", severity: "Info" },
  { check: "Floor Contact", status: "pass", details: "All static objects on floor", impacted: "14", severity: "Info" },
  { check: "Reachability (Robot)", status: "warn", details: "3 handles partially occluded", impacted: "Kitchen Cabinet 02", severity: "Medium" },
  { check: "Lighting", status: "pass", details: "Avg lux: 312 (min: 48, max: 812)", impacted: "—", severity: "Info" },
  { check: "Semantic Zones", status: "pass", details: "All objects in valid zones", impacted: "—", severity: "Info" },
  { check: "Spawn Validity", status: "pass", details: "All spawn points valid", impacted: "Robot Base", severity: "Info" },
];

export interface LiveStep { name: string; state: "done" | "active" | "pending" | "failed" }

export const taskSteps: LiveStep[] = [
  { name: "Navigate to refrigerator", state: "done" },
  { name: "Reach for handle", state: "done" },
  { name: "Open refrigerator door", state: "active" },
  { name: "Move to target position", state: "pending" },
  { name: "Complete subtask", state: "pending" },
  { name: "Return to idle", state: "pending" },
];

export const successConditions = [
  { name: "Door open angle ≥ 60°", state: "active" as const, value: "21.3°" },
  { name: "Handle released", state: "done" as const, value: "Yes" },
  { name: "Door stable", state: "done" as const, value: "Yes" },
  { name: "No collisions", state: "done" as const, value: "Passed" },
  { name: "Within time limit (120s)", state: "done" as const, value: "Passed" },
];

export const eventTimeline = [
  { t: 0, time: "00:00", name: "Run started", sub: "Environment initialized", state: "done" as const },
  { t: 0.13, time: "00:01", name: "Approach handle", sub: "Completed", state: "done" as const },
  { t: 0.3, time: "00:04", name: "Grasp handle", sub: "Completed", state: "done" as const },
  { t: 0.55, time: "00:08", name: "Pull door", sub: "Completed", state: "done" as const },
  { t: 0.82, time: "00:12", name: "Release handle", sub: "Completed", state: "done" as const },
  { t: 0.97, time: "00:14", name: "Success", sub: "Completed", state: "active" as const },
];
