import * as THREE from "three";
import { useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import { M, variantMat, type RenderVariant } from "./materials";

const FRIDGE_MAX_ANGLE = THREE.MathUtils.degToRad(95);

/** French-door refrigerator — articulated left door for the live eval. */
export function Refrigerator({
  variant = "rgb",
  doorOpen = 0, // 0..1 static
  doorRef,      // live drive: sampled per frame
  position = [0, 0, 0] as [number, number, number],
  rotationY = 0,
}: {
  variant?: RenderVariant;
  doorOpen?: number;
  doorRef?: MutableRefObject<number>;
  position?: [number, number, number];
  rotationY?: number;
}) {
  const W = 0.91, H = 1.78, D = 0.73;
  const body = variantMat(variant, M.fridgeBody(), 30);
  const dark = variantMat(variant, M.fridgeDark(), 31);
  const handle = variantMat(variant, M.handle(), 32);
  const steel = variantMat(variant, M.steel(), 33);
  const doorG = useRef<THREE.Group>(null);
  const glowM = useRef<THREE.MeshStandardMaterial>(null);

  useFrame(() => {
    const open = doorRef ? doorRef.current : doorOpen;
    if (doorG.current) doorG.current.rotation.y = -FRIDGE_MAX_ANGLE * open;
    if (glowM.current) glowM.current.emissiveIntensity = open > 0.02 ? 0.9 * Math.min(1, open * 2) : 0;
  });

  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      {/* main carcass */}
      <mesh position={[0, H / 2, 0]} castShadow receiveShadow material={body}>
        <boxGeometry args={[W, H, D]} />
      </mesh>
      {/* kick plate */}
      <mesh position={[0, 0.045, D / 2 + 0.005]} material={dark}>
        <boxGeometry args={[W - 0.06, 0.09, 0.012]} />
      </mesh>
      {/* freezer drawer front (lower third) */}
      <mesh position={[0, 0.42, D / 2 + 0.012]} castShadow material={steel}>
        <boxGeometry args={[W - 0.04, 0.6, 0.03]} />
      </mesh>
      <mesh position={[0, 0.62, D / 2 + 0.045]} material={handle}>
        <boxGeometry args={[0.5, 0.028, 0.03]} />
      </mesh>
      {/* left french door — hinged at left edge */}
      <group position={[-W / 2 + 0.02, 1.25, D / 2 + 0.015]} ref={doorG}>
        <mesh position={[(W / 2 - 0.03), 0, 0]} castShadow material={steel}>
          <boxGeometry args={[W / 2 - 0.03, 1.02, 0.035]} />
        </mesh>
        {/* vertical handle */}
        <mesh position={[W / 2 - 0.1, 0.05, 0.05]} castShadow material={handle}>
          <boxGeometry args={[0.032, 0.62, 0.035]} />
        </mesh>
      </group>
      {/* right french door — static */}
      <group position={[W / 2 - 0.02, 1.25, D / 2 + 0.015]}>
        <mesh position={[-(W / 2 - 0.03), 0, 0]} castShadow material={steel}>
          <boxGeometry args={[W / 2 - 0.03, 1.02, 0.035]} />
        </mesh>
        <mesh position={[-(W / 2 - 0.1), 0.05, 0.05]} castShadow material={handle}>
          <boxGeometry args={[0.032, 0.62, 0.035]} />
        </mesh>
        {/* dispenser recess */}
        <mesh position={[-0.06, 0.12, 0.045]} material={dark}>
          <boxGeometry args={[0.17, 0.3, 0.02]} />
        </mesh>
      </group>
      {/* interior glow revealed when open */}
      <mesh position={[-W / 4, 1.25, D / 2 - 0.1]}>
        <boxGeometry args={[W / 2 - 0.1, 0.95, 0.02]} />
        <meshStandardMaterial ref={glowM} color="#DFE9EF" emissive="#CFE0EA" emissiveIntensity={0} transparent opacity={0.9} />
      </mesh>
    </group>
  );
}

/** Simple pedal trash can. */
export function TrashCan({ variant = "rgb", position = [0, 0, 0] as [number, number, number] }: { variant?: RenderVariant; position?: [number, number, number] }) {
  const m = variantMat(variant, M.trashCan(), 40);
  const dark = variantMat(variant, M.rubber(), 41);
  return (
    <group position={position}>
      <mesh position={[0, 0.33, 0]} castShadow receiveShadow material={m}>
        <cylinderGeometry args={[0.19, 0.16, 0.62, 20]} />
      </mesh>
      <mesh position={[0, 0.66, 0]} castShadow material={m}>
        <cylinderGeometry args={[0.2, 0.2, 0.06, 20]} />
      </mesh>
      <mesh position={[0, 0.1, 0.17]} material={dark}>
        <boxGeometry args={[0.1, 0.05, 0.04]} />
      </mesh>
    </group>
  );
}

/** Countertop microwave. */
export function Microwave({ variant = "rgb", position = [0, 0, 0] as [number, number, number], rotationY = 0 }: { variant?: RenderVariant; position?: [number, number, number]; rotationY?: number }) {
  const body = variantMat(variant, M.steelDark(), 42);
  const glass = variantMat(variant, M.glassDark(), 43);
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <mesh position={[0, 0.16, 0]} castShadow receiveShadow material={body}>
        <boxGeometry args={[0.5, 0.31, 0.36]} />
      </mesh>
      <mesh position={[-0.03, 0.16, 0.185]} material={glass}>
        <boxGeometry args={[0.34, 0.22, 0.012]} />
      </mesh>
      <mesh position={[0.19, 0.16, 0.185]} material={glass}>
        <boxGeometry args={[0.08, 0.24, 0.012]} />
      </mesh>
    </group>
  );
}

/** Wooden stool. */
export function Stool({ variant = "rgb", position = [0, 0, 0] as [number, number, number] }: { variant?: RenderVariant; position?: [number, number, number] }) {
  const wood = variantMat(variant, M.cardboard2(), 44);
  const leg = variantMat(variant, M.steelDark(), 45);
  return (
    <group position={position}>
      <mesh position={[0, 0.62, 0]} castShadow material={wood}>
        <cylinderGeometry args={[0.17, 0.17, 0.035, 20]} />
      </mesh>
      {[0, 1, 2, 3].map((i) => {
        const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
        return (
          <mesh key={i} position={[Math.cos(a) * 0.13, 0.31, Math.sin(a) * 0.13]} rotation={[Math.cos(a) * 0.18, 0, Math.sin(a) * -0.18]} castShadow material={leg}>
            <cylinderGeometry args={[0.012, 0.012, 0.62, 8]} />
          </mesh>
        );
      })}
    </group>
  );
}

/** Rolling utility cart. */
export function Cart({ variant = "rgb", position = [0, 0, 0] as [number, number, number], rotationY = 0 }: { variant?: RenderVariant; position?: [number, number, number]; rotationY?: number }) {
  const steel = variantMat(variant, M.steel(), 46);
  const binM = variantMat(variant, M.steelDark(), 47);
  const rubber = variantMat(variant, M.rubber(), 48);
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      {/* tub */}
      <mesh position={[0, 0.62, 0]} castShadow material={binM}>
        <boxGeometry args={[0.92, 0.3, 0.56]} />
      </mesh>
      <mesh position={[0, 0.78, 0]} material={steel}>
        <boxGeometry args={[0.98, 0.03, 0.62]} />
      </mesh>
      {/* legs */}
      {[[-0.42, -0.24], [0.42, -0.24], [-0.42, 0.24], [0.42, 0.24]].map(([x, z], i) => (
        <group key={i}>
          <mesh position={[x, 0.32, z]} material={steel}>
            <cylinderGeometry args={[0.014, 0.014, 0.62, 8]} />
          </mesh>
          <mesh position={[x, 0.05, z]} rotation={[Math.PI / 2, 0, 0]} castShadow material={rubber}>
            <cylinderGeometry args={[0.05, 0.05, 0.03, 12]} />
          </mesh>
        </group>
      ))}
      {/* push handle */}
      <mesh position={[0.5, 0.85, 0]} material={steel}>
        <boxGeometry args={[0.03, 0.03, 0.56]} />
      </mesh>
    </group>
  );
}

/** Small props used on counters. */
export function CounterProps({ variant = "rgb" }: { variant?: RenderVariant }) {
  return (
    <group>
      {/* red mug */}
      <mesh position={[0.55, 0.945, -0.08]} castShadow material={variantMat(variant, M.mugRed(), 50)}>
        <cylinderGeometry args={[0.04, 0.033, 0.09, 16]} />
      </mesh>
      {/* detergent bottle */}
      <group position={[0.72, 0.98, 0.08]}>
        <mesh castShadow material={variantMat(variant, M.bottleGreen(), 51)}>
          <cylinderGeometry args={[0.032, 0.04, 0.14, 12]} />
        </mesh>
        <mesh position={[0, 0.085, 0]} material={variantMat(variant, M.rubber(), 52)}>
          <cylinderGeometry args={[0.014, 0.014, 0.03, 10]} />
        </mesh>
      </group>
      {/* towel roll */}
      <group position={[-0.5, 1.0, 0.05]}>
        <mesh castShadow material={variantMat(variant, M.towel(), 53)}>
          <cylinderGeometry args={[0.055, 0.055, 0.22, 16]} />
        </mesh>
      </group>
      {/* blue parts bin */}
      <mesh position={[-0.72, 0.955, -0.1]} castShadow material={variantMat(variant, M.binBlue(), 54)}>
        <boxGeometry args={[0.22, 0.11, 0.16]} />
      </mesh>
    </group>
  );
}
