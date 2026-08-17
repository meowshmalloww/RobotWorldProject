import * as THREE from "three";
import { M, variantMat, type RenderVariant } from "./materials";

/**
 * Articulated kitchen wall cabinet — the "Kitchen Cabinet 02" asset.
 * Real hinged doors: leftDoor / rightDoor rotate on Y around their hinge edge.
 *
 * Dimensions (m): W 0.9 × H 0.75 × D 0.34
 */
export function CabinetAsset({
  variant = "rgb",
  leftOpen = 0,      // 0..1 → 0..-105°
  rightOpen = 0,
  wireframe = false,
  lowHandle = false,
}: {
  variant?: RenderVariant;
  leftOpen?: number;
  rightOpen?: number;
  wireframe?: boolean;
  lowHandle?: boolean;
}) {
  const W = 0.9, H = 0.75, D = 0.34, T = 0.018; // panel thickness
  const doorW = W / 2 - T * 1.5;
  const hinge = (side: "l" | "r") => (side === "l" ? -1 : 1) * THREE.MathUtils.degToRad(105) * (side === "l" ? leftOpen : rightOpen);

  const mats: [THREE.Material, number][] = [
    [M.cabinetBody(), 10], [M.cabinetDoor(), 11], [M.handle(), 12], [M.steelDark(), 13],
  ];
  const vm = (i: number) => {
    const m = variantMat(variant, mats[i][0], mats[i][1]);
    if (wireframe && "wireframe" in m) (m as THREE.MeshStandardMaterial).wireframe = true;
    return m;
  };

  const panel = (w: number, h: number, d: number, x: number, y: number, z: number, mi = 0, key?: string) => (
    <mesh key={key} position={[x, y, z]} castShadow receiveShadow material={vm(mi)}>
      <boxGeometry args={[w, h, d]} />
    </mesh>
  );

  const door = (side: "l" | "r") => {
    const hx = side === "l" ? -W / 2 + T / 2 : W / 2 - T / 2; // hinge edge x
    const dir = side === "l" ? 1 : -1;
    const handleY = lowHandle ? -H * 0.28 : 0;
    return (
      <group position={[hx, 0, D / 2 - T / 2]} rotation={[0, hinge(side), 0]}>
        {/* door slab — offset from hinge edge */}
        {panel(doorW, H - T * 2, T, (doorW / 2 + T / 2) * dir, 0, 0, 1, `door-${side}`)}
        {/* handle */}
        <group position={[(doorW - 0.055) * dir, handleY, T / 2 + 0.014]}>
          <mesh castShadow material={vm(2)}>
            <boxGeometry args={[0.02, 0.11, 0.016]} />
          </mesh>
          <mesh position={[0.012 * dir, 0.045, -0.007]} castShadow material={vm(2)}>
            <boxGeometry args={[0.018, 0.016, 0.014]} />
          </mesh>
          <mesh position={[0.012 * dir, -0.045, -0.007]} castShadow material={vm(2)}>
            <boxGeometry args={[0.018, 0.016, 0.014]} />
          </mesh>
        </group>
        {/* hinges */}
        {[H / 2 - 0.1, -(H / 2 - 0.1)].map((y, i) => (
          <mesh key={i} position={[0, y, -T / 2 - 0.008]} rotation={[0, 0, 0]} material={vm(3)}>
            <cylinderGeometry args={[0.008, 0.008, 0.05, 10]} />
          </mesh>
        ))}
      </group>
    );
  };

  return (
    <group>
      {/* carcass: top, bottom, sides, back, mid shelf */}
      {panel(W, T, D, 0, H / 2 - T / 2, 0)}
      {panel(W, T, D, 0, -H / 2 + T / 2, 0)}
      {panel(T, H - 2 * T, D, -W / 2 + T / 2, 0, 0)}
      {panel(T, H - 2 * T, D, W / 2 - T / 2, 0, 0)}
      {panel(W, H, T, 0, 0, -D / 2 + T / 2)}
      {panel(W - 2 * T, T, D - 0.04, 0, 0, -0.01)}
      {door("l")}
      {door("r")}
    </group>
  );
}

/** Base cabinet with countertop — used in the scene composer run. */
export function BaseCabinetRun({
  variant = "rgb", doorOpen = 0, width = 1.6,
}: {
  variant?: RenderVariant; doorOpen?: number; width?: number;
}) {
  const W = width, H = 0.86, D = 0.62, T = 0.02;
  const body = variantMat(variant, M.cabinetBody(), 20);
  const doorM = variantMat(variant, M.cabinetDoor(), 21);
  const topM = variantMat(variant, M.counterTop(), 22);
  const handleM = variantMat(variant, M.handle(), 23);
  const angle = -THREE.MathUtils.degToRad(100) * doorOpen;
  const doorW = W / 2 - T * 1.5;

  return (
    <group>
      <mesh position={[0, H / 2, 0]} castShadow receiveShadow material={body}>
        <boxGeometry args={[W, H, D]} />
      </mesh>
      <mesh position={[0, H + 0.02, 0]} castShadow receiveShadow material={topM}>
        <boxGeometry args={[W + 0.04, 0.04, D + 0.03]} />
      </mesh>
      {/* recessed front: two doors */}
      {(["l", "r"] as const).map((side) => {
        const hx = side === "l" ? -W / 2 : W / 2;
        const dir = side === "l" ? 1 : -1;
        const open = side === "l" ? angle : 0;
        return (
          <group key={side} position={[hx, H / 2, D / 2 + 0.002]} rotation={[0, open, 0]}>
            <mesh position={[(doorW / 2) * dir, 0, 0]} castShadow material={doorM}>
              <boxGeometry args={[doorW - 0.01, H - 0.06, 0.018]} />
            </mesh>
            <mesh position={[(doorW - 0.06) * dir, 0.1, 0.017]} castShadow material={handleM}>
              <boxGeometry args={[0.02, 0.1, 0.02]} />
            </mesh>
          </group>
        );
      })}
      {/* interior shelf visible when open */}
      <mesh position={[0, H * 0.45, 0]} material={body}>
        <boxGeometry args={[W - 0.05, 0.015, D - 0.05]} />
      </mesh>
    </group>
  );
}
