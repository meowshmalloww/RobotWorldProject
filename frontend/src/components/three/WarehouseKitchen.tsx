import * as THREE from "three";
import { useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import { M, variantMat, type RenderVariant } from "./materials";
import { BaseCabinetRun, CabinetAsset } from "./Cabinet";
import { Cart, CounterProps, Microwave, Refrigerator, Stool, TrashCan } from "./Appliances";
import { RobotArm, samplePose, type ArmPose } from "./RobotArm";
import { Selectable, type SelectInfo } from "./Selectable";

/**
 * Warehouse Kitchen v2 — fully procedural simulation world.
 * Units are meters. Every mesh casts/receives shadows; variant="seg"|"depth"
 * re-renders the same scene as a semantic / depth pass.
 */

export interface SceneSelection {
  selectedId?: string | null;
  onSelect?: (s: SelectInfo | null) => void;
  interactive?: boolean;
}

function RoomShell({ variant }: { variant: RenderVariant }) {
  const W = 17, Dd = 11, H = 4.6;
  const floor = variantMat(variant, M.floor(), 0);
  const wall = variantMat(variant, M.wall(), 1);
  const wallD = variantMat(variant, M.wallDark(), 2);
  const beam = variantMat(variant, M.ceilingBeam(), 3);
  const safety = variantMat(variant, M.safetyYellow(), 4);
  const strip = variantMat(variant, M.lightStrip(), 5);
  const exit = variantMat(variant, M.exitSign(), 6);
  // keep emissive look on light strips in rgb mode
  if (variant === "rgb") {
    (strip as THREE.MeshStandardMaterial).userData.keepEmissive = true;
  }

  return (
    <group>
      {/* concrete floor */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow material={floor}>
        <planeGeometry args={[W, Dd]} />
      </mesh>
      {/* back + side walls */}
      <mesh position={[0, H / 2, -5.5]} receiveShadow material={wall}>
        <boxGeometry args={[W, H, 0.15]} />
      </mesh>
      <mesh position={[-W / 2, H / 2, 0]} receiveShadow material={wallD}>
        <boxGeometry args={[0.15, H, Dd]} />
      </mesh>
      <mesh position={[W / 2, H / 2, 0]} receiveShadow material={wallD}>
        <boxGeometry args={[0.15, H, Dd]} />
      </mesh>
      {/* wall panel seams */}
      {Array.from({ length: 7 }, (_, i) => (
        <mesh key={i} position={[-W / 2 + 2.1 + i * 2.1, H / 2, -5.42]} material={wallD}>
          <boxGeometry args={[0.06, H, 0.02]} />
        </mesh>
      ))}
      {/* ceiling beams + light strips */}
      {[-3.4, 0, 3.4].map((x) => (
        <mesh key={x} position={[x, H - 0.08, 0]} material={beam}>
          <boxGeometry args={[0.22, 0.16, Dd]} />
        </mesh>
      ))}
      {[-1.8, 1.8].map((x) => (
        <mesh key={x} position={[x, H - 0.16, -1]} material={strip}>
          <boxGeometry args={[0.3, 0.04, 7.5]} />
        </mesh>
      ))}
      {/* exit sign */}
      <mesh position={[6.2, 3.1, -5.4]} material={exit}>
        <boxGeometry args={[0.5, 0.22, 0.08]} />
      </mesh>
      {/* floor safety lines */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[-1.9, 0.004, -0.6]} material={safety}>
        <planeGeometry args={[0.08, 6.4]} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0.6, 0.004, 2.6]} material={safety}>
        <planeGeometry args={[5.1, 0.08]} />
      </mesh>
      {/* dashed hazard strip in front of cabinet run */}
      {Array.from({ length: 12 }, (_, i) => (
        <mesh key={i} rotation={[-Math.PI / 2, 0, Math.PI / 5]} position={[-2.6 + i * 0.45, 0.004, -1.35]} material={safety}>
          <planeGeometry args={[0.1, 0.28]} />
        </mesh>
      ))}
    </group>
  );
}

/** Storage rack unit with cardboard boxes. */
function Rack({ variant, position, rotationY = 0, seed = 1 }: { variant: RenderVariant; position: [number, number, number]; rotationY?: number; seed?: number }) {
  const frame = variantMat(variant, M.rackFrame(), 7);
  const beamM = variantMat(variant, M.rackBeam(), 8);
  const box1 = variantMat(variant, M.cardboard(), 9);
  const box2 = variantMat(variant, M.cardboard2(), 10);
  const boxes = useMemo(() => {
    const rnd = (i: number) => {
      const x = Math.sin(seed * 91.7 + i * 47.3) * 10000;
      return x - Math.floor(x);
    };
    const arr: { pos: [number, number, number]; size: [number, number, number]; m: number }[] = [];
    const shelfY = [0.35, 1.05, 1.75];
    shelfY.forEach((y, s) => {
      for (let i = 0; i < 3; i++) {
        if (rnd(s * 3 + i) < 0.18) continue;
        const w = 0.34 + rnd(i + s * 7) * 0.2;
        const h = 0.26 + rnd(i * 3 + s) * 0.22;
        arr.push({
          pos: [-0.72 + i * 0.72 + (rnd(i + 9) - 0.5) * 0.14, y + h / 2 + 0.03, (rnd(i + 4) - 0.5) * 0.16],
          size: [w, h, 0.5],
          m: rnd(i + s) > 0.5 ? 1 : 0,
        });
      }
    });
    return arr;
  }, [seed]);

  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      {/* uprights */}
      {[[-1.1, -0.35], [1.1, -0.35], [-1.1, 0.35], [1.1, 0.35]].map(([x, z], i) => (
        <mesh key={i} position={[x, 1.1, z]} castShadow material={frame}>
          <boxGeometry args={[0.08, 2.2, 0.08]} />
        </mesh>
      ))}
      {/* shelf beams */}
      {[0.32, 1.02, 1.72].map((y) => (
        <group key={y}>
          <mesh position={[0, y, -0.35]} material={beamM}><boxGeometry args={[2.3, 0.09, 0.07]} /></mesh>
          <mesh position={[0, y, 0.35]} material={beamM}><boxGeometry args={[2.3, 0.09, 0.07]} /></mesh>
          <mesh position={[0, y - 0.02, 0]} material={frame}><boxGeometry args={[2.2, 0.03, 0.72]} /></mesh>
        </group>
      ))}
      {boxes.map((b, i) => (
        <mesh key={i} position={b.pos} castShadow receiveShadow material={b.m ? box2 : box1}>
          <boxGeometry args={b.size} />
        </mesh>
      ))}
    </group>
  );
}

export function WarehouseKitchen({
  variant = "rgb",
  simTime,
  selection = {},
  cabinetDoorOpen,
  simRef,
}: {
  variant?: RenderVariant;
  /** static pose time on the scripted timeline (non-animated renders) */
  simTime?: number;
  selection?: SceneSelection;
  /** manual override for the wall-cabinet doors (scene composer) */
  cabinetDoorOpen?: { left?: number; right?: number };
  /** live simulation clock — when set, robot + fridge door are driven per-frame */
  simRef?: MutableRefObject<{ t: number }>;
}) {
  // Live-drive refs (sampled by useFrame, applied imperatively — no re-renders)
  const poseRef = useRef<ArmPose>(samplePose(0));
  const doorOpenRef = useRef(0);

  useFrame(() => {
    if (!simRef) return;
    poseRef.current = samplePose(simRef.current.t);
    doorOpenRef.current = poseRef.current.door;
  });

  const staticPose = samplePose(simTime ?? 0);
  const sel = selection.interactive ? selection : {};

  return (
    <group
      onPointerMissed={selection.interactive ? () => selection.onSelect?.(null) : undefined}
    >
      <RoomShell variant={variant} />

      {/* storage racks in the back */}
      <Rack variant={variant} position={[-4.6, 0, -4.4]} seed={3} />
      <Rack variant={variant} position={[-1.9, 0, -4.4]} seed={8} />
      <Rack variant={variant} position={[4.3, 0, -4.4]} seed={5} />
      <Rack variant={variant} position={[6.9, 0, -4.4]} seed={11} />

      {/* cabinet run along back wall */}
      <group position={[-0.5, 0, -3.6]}>
        <Selectable id="base-cabinet" name="Base Cabinet Run" selected={selection.selectedId === "base-cabinet"} onSelect={sel.onSelect} enabled={selection.interactive}>
          <BaseCabinetRun variant={variant} doorOpen={0} width={2.0} />
        </Selectable>
        {/* wall cabinet — the asset under test */}
        <group position={[0, 1.62, 0.12]}>
          <Selectable id="cabinet-02" name="Kitchen Cabinet 02" selected={selection.selectedId === "cabinet-02"} onSelect={sel.onSelect} enabled={selection.interactive}>
            <CabinetAsset
              variant={variant}
              leftOpen={cabinetDoorOpen?.left ?? 0}
              rightOpen={cabinetDoorOpen?.right ?? 0}
            />
          </Selectable>
        </group>
        <CounterProps variant={variant} />
      </group>

      {/* refrigerator */}
      <group position={[1.75, 0, -3.55]}>
        <Selectable id="fridge" name="Refrigerator Samsung RF56" selected={selection.selectedId === "fridge"} onSelect={sel.onSelect} enabled={selection.interactive}>
          <Refrigerator variant={variant} doorOpen={staticPose.door} doorRef={simRef ? doorOpenRef : undefined} />
        </Selectable>
      </group>

      {/* worktable + microwave */}
      <group position={[-3.2, 0, -3.5]}>
        <Selectable id="worktable" name="Worktable" selected={selection.selectedId === "worktable"} onSelect={sel.onSelect} enabled={selection.interactive}>
          <group>
            <mesh position={[0, 0.44, 0]} castShadow receiveShadow material={variantMat(variant, M.steelDark(), 70)}>
              <boxGeometry args={[1.3, 0.05, 0.6]} />
            </mesh>
            {[[-0.58, -0.24], [0.58, -0.24], [-0.58, 0.24], [0.58, 0.24]].map(([x, z], i) => (
              <mesh key={i} position={[x, 0.21, z]} material={variantMat(variant, M.steel(), 71)}>
                <boxGeometry args={[0.05, 0.42, 0.05]} />
              </mesh>
            ))}
          </group>
        </Selectable>
        <group position={[0, 0.47, 0]}>
          <Microwave variant={variant} />
        </group>
      </group>

      {/* mobile robot */}
      <group position={[0.55, 0, -1.7]}>
        <Selectable id="robot-base" name="Robot Base" selected={selection.selectedId === "robot-base"} onSelect={sel.onSelect} enabled={selection.interactive}>
          <RobotArm pose={staticPose} poseRef={simRef ? poseRef : undefined} variant={variant} />
        </Selectable>
      </group>

      {/* props & furniture */}
      <group position={[-2.5, 0, -1.1]} rotation={[0, 0.35, 0]}>
        <Selectable id="cart" name="Utility Cart" selected={selection.selectedId === "cart"} onSelect={sel.onSelect} enabled={selection.interactive}>
          <Cart variant={variant} />
        </Selectable>
      </group>
      <Stool variant={variant} position={[2.9, 0, -1.9]} />
      <group position={[3.6, 0, -3.3]}>
        <TrashCan variant={variant} />
      </group>

      {/* storage bins on floor */}
      {[[-3.9, -2.2], [-3.5, -2.2], [-3.7, -1.82]].map(([x, z], i) => (
        <mesh key={i} position={[x, 0.14, z]} castShadow receiveShadow material={variantMat(variant, M.binBlue(), 72)}>
          <boxGeometry args={[0.34, 0.28, 0.26]} />
        </mesh>
      ))}
    </group>
  );
}
