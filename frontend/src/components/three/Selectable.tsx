import { useEffect, useRef, type ReactNode } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

export interface SelectInfo {
  id: string;
  name: string;
}

/**
 * Click-to-select wrapper. Renders an editor-style bounding-box selection
 * indicator (THREE.BoxHelper added at scene root so it tracks the group's
 * world transform) — no material mutation, so shared materials stay clean.
 */
export function Selectable({
  id,
  name,
  selected,
  onSelect,
  children,
  enabled = true,
}: {
  id: string;
  name: string;
  selected?: boolean;
  onSelect?: (info: SelectInfo) => void;
  children: ReactNode;
  enabled?: boolean;
}) {
  const g = useRef<THREE.Group>(null);
  const scene = useThree((s) => s.scene);
  const helperRef = useRef<THREE.BoxHelper | null>(null);

  useEffect(() => {
    if (!selected || !g.current) return;
    const h = new THREE.BoxHelper(g.current, new THREE.Color("#5B9DFF"));
    (h.material as THREE.LineBasicMaterial).transparent = true;
    (h.material as THREE.LineBasicMaterial).opacity = 0.9;
    (h.material as THREE.LineBasicMaterial).depthTest = false;
    h.renderOrder = 999;
    scene.add(h);
    helperRef.current = h;
    return () => {
      scene.remove(h);
      h.dispose();
      helperRef.current = null;
    };
  }, [selected, scene]);

  useFrame(() => {
    helperRef.current?.update();
  });

  return (
    <group
      ref={g}
      onClick={
        enabled
          ? (e) => {
              e.stopPropagation();
              onSelect?.({ id, name });
            }
          : undefined
      }
      onPointerOver={enabled ? (e) => { e.stopPropagation(); document.body.style.cursor = "pointer"; } : undefined}
      onPointerOut={enabled ? () => { document.body.style.cursor = "default"; } : undefined}
    >
      {children}
    </group>
  );
}
