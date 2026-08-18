import * as THREE from "three";

/**
 * Shared material palette for the articulated-door validation lab.
 * Segmentation mode swaps every mesh to a flat, per-object ID color —
 * a real semantic-segmentation render pass, not a texture trick.
 */

export type RenderVariant = "rgb" | "seg" | "depth";

const cache = new Map<string, THREE.MeshStandardMaterial>();

export function mat(key: string, params: THREE.MeshStandardMaterialParameters): THREE.MeshStandardMaterial {
  const hit = cache.get(key);
  if (hit) return hit;
  const m = new THREE.MeshStandardMaterial(params);
  cache.set(key, m);
  return m;
}

export const M = {
  floor: () => mat("floor", { color: "#3B3F46", roughness: 0.93, metalness: 0.04 }),
  wall: () => mat("wall", { color: "#2E3239", roughness: 0.95 }),
  wallDark: () => mat("wallDark", { color: "#23262C", roughness: 0.97 }),
  ceilingBeam: () => mat("beam", { color: "#1E2126", roughness: 0.9 }),
  safetyYellow: () => mat("safety", { color: "#C9A227", roughness: 0.8 }),
  steel: () => mat("steel", { color: "#8A8F98", roughness: 0.42, metalness: 0.85 }),
  steelDark: () => mat("steelDark", { color: "#4A4E55", roughness: 0.5, metalness: 0.7 }),
  rackFrame: () => mat("rackFrame", { color: "#50545A", roughness: 0.55, metalness: 0.6 }),
  rackBeam: () => mat("rackBeam", { color: "#756A5F", roughness: 0.6, metalness: 0.4 }),
  cardboard: () => mat("cardboard", { color: "#8A6F4D", roughness: 0.95 }),
  cardboard2: () => mat("cardboard2", { color: "#77603F", roughness: 0.95 }),
  cabinetBody: () => mat("cabBody", { color: "#B9A88E", roughness: 0.72 }),
  cabinetDoor: () => mat("cabDoor", { color: "#C4B498", roughness: 0.68 }),
  counterTop: () => mat("counter", { color: "#D8D3C8", roughness: 0.35 }),
  fridgeBody: () => mat("fridge", { color: "#9BA1A8", roughness: 0.34, metalness: 0.8 }),
  fridgeDark: () => mat("fridgeDark", { color: "#22252A", roughness: 0.5, metalness: 0.3 }),
  handle: () => mat("handle", { color: "#6E737B", roughness: 0.3, metalness: 0.9 }),
  robotWhite: () => mat("robotWhite", { color: "#D9DCE1", roughness: 0.4, metalness: 0.25 }),
  robotJoint: () => mat("robotJoint", { color: "#33373D", roughness: 0.45, metalness: 0.6 }),
  robotBase: () => mat("robotBase", { color: "#585D66", roughness: 0.5, metalness: 0.5 }),
  rubber: () => mat("rubber", { color: "#1B1D20", roughness: 0.9 }),
  binBlue: () => mat("binBlue", { color: "#44494E", roughness: 0.7 }),
  mugRed: () => mat("mugRed", { color: "#A63D33", roughness: 0.55 }),
  bottleGreen: () => mat("bottleGreen", { color: "#4B6B45", roughness: 0.5 }),
  towel: () => mat("towel", { color: "#C8C2B4", roughness: 0.95 }),
  trashCan: () => mat("trashCan", { color: "#565B63", roughness: 0.45, metalness: 0.6 }),
  lightStrip: () => mat("lightStrip", { color: "#E0E0E0", emissive: "#D8D8D8", emissiveIntensity: 0.8, roughness: 0.5 }),
  exitSign: () => mat("exitSign", { color: "#123B1D", emissive: "#2E9E4F", emissiveIntensity: 0.9 }),
  glassDark: () => mat("glassDark", { color: "#14161A", roughness: 0.15, metalness: 0.4 }),
};

/** Segmentation ID colors — stable, visually separable. */
export const SEG_COLORS = [
  "#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4", "#42D4F4",
  "#F032E6", "#BFEF45", "#469990", "#9A6324", "#800000", "#000075",
  "#808000", "#FFD8B1", "#E6BEFF", "#AAFFC3", "#0064C8", "#C88200",
];
export const segColor = (i: number) => SEG_COLORS[i % SEG_COLORS.length];

let depthMatSingleton: THREE.MeshDepthMaterial | null = null;

/** Resolve material for the current render variant. */
export function variantMat(variant: RenderVariant, std: THREE.Material, segIndex: number): THREE.Material {
  if (variant === "seg") {
    return mat(`seg${segIndex}`, { color: segColor(segIndex), roughness: 1 });
  }
  if (variant === "depth") {
    if (!depthMatSingleton) depthMatSingleton = new THREE.MeshDepthMaterial();
    return depthMatSingleton;
  }
  return std;
}
