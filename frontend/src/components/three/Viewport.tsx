import { Canvas, useThree } from "@react-three/fiber";
import { GizmoHelper, GizmoViewport, Grid, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { useEffect, type ReactNode } from "react";
import type { RenderVariant } from "./materials";

/** PBR environment — PMREM of three's RoomEnvironment, fully offline. */
function EnvSetup() {
  const { gl, scene } = useThree();
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.06).texture;
    scene.environment = env;
    scene.environmentIntensity = 0.5;
    return () => {
      scene.environment = null;
      env.dispose();
      pmrem.dispose();
    };
  }, [gl, scene]);
  return null;
}

/** Aim the camera at the target when OrbitControls are disabled. */
function CameraLookAt({ target }: { target: [number, number, number] }) {
  const { camera } = useThree();
  useEffect(() => {
    camera.lookAt(new THREE.Vector3(...target));
    camera.updateProjectionMatrix();
  }, [camera, target]);
  return null;
}

/**
 * Shared 3D viewport. RGB mode uses studio + warehouse lighting with soft
 * shadows; seg/depth modes use flat lighting so IDs/depth read cleanly.
 */
export function Viewport({
  children,
  camera = { position: [2.6, 2.1, 1.8] as [number, number, number], fov: 42 },
  target = [-0.2, 0.9, -2.6] as [number, number, number],
  variant = "rgb",
  shadows = true,
  grid = false,
  gizmo = true,
  controls = true,
  dpr = [1, 1.75] as [number, number],
  onPointerMissed,
  className,
  style,
  fov,
  autoRotate = false,
}: {
  children: ReactNode;
  camera?: { position: [number, number, number]; fov?: number };
  target?: [number, number, number];
  variant?: RenderVariant;
  shadows?: boolean;
  grid?: boolean;
  gizmo?: boolean;
  controls?: boolean;
  dpr?: [number, number];
  onPointerMissed?: () => void;
  className?: string;
  style?: React.CSSProperties;
  fov?: number;
  autoRotate?: boolean;
}) {
  const flat = variant !== "rgb";
  return (
    <div className={`viewport ${className ?? ""}`} style={style}>
      <Canvas
        shadows={shadows && !flat}
        dpr={dpr}
        camera={{ position: camera.position, fov: camera.fov ?? fov ?? 42 }}
        gl={{
          antialias: true,
          toneMapping: flat ? THREE.NoToneMapping : THREE.ACESFilmicToneMapping,
          toneMappingExposure: 1.12,
          preserveDrawingBuffer: true,
        }}
        onPointerMissed={onPointerMissed}
        style={{ background: variant === "depth" ? "#000000" : "#0D1017" }}
      >
        {flat ? (
          <ambientLight intensity={1.5} />
        ) : (
          <>
            <EnvSetup />
            <hemisphereLight args={["#B8C4D6", "#26282D", 0.55]} />
            <directionalLight
              position={[5.5, 7.5, 3.5]}
              intensity={1.5}
              castShadow
              shadow-mapSize={[2048, 2048]}
              shadow-camera-left={-8}
              shadow-camera-right={8}
              shadow-camera-top={8}
              shadow-camera-bottom={-8}
              shadow-bias={-0.00035}
            />
            <directionalLight position={[-4, 5, -2]} intensity={0.35} color="#9FB6D8" />
            <pointLight position={[-1.8, 4.2, -1]} intensity={18} color="#DCE9F5" distance={12} decay={2} />
            <pointLight position={[1.8, 4.2, -1]} intensity={18} color="#DCE9F5" distance={12} decay={2} />
            <pointLight position={[0.5, 2.6, 2.5]} intensity={7} color="#FFE3C0" distance={9} decay={2} />
          </>
        )}
        {children}
        {grid && (
          <Grid
            position={[0, 0.001, 0]}
            args={[17, 11]}
            cellSize={0.5}
            cellThickness={0.6}
            cellColor="#1F2634"
            sectionSize={2}
            sectionThickness={1}
            sectionColor="#2A3448"
            fadeDistance={18}
            fadeStrength={1.4}
            infiniteGrid={false}
          />
        )}
        {controls ? (
          <OrbitControls
            target={target}
            makeDefault
            enableDamping
            dampingFactor={0.08}
            minDistance={0.4}
            maxDistance={14}
            maxPolarAngle={Math.PI / 2 - 0.02}
            autoRotate={autoRotate}
            autoRotateSpeed={1.1}
          />
        ) : (
          <CameraLookAt target={target} />
        )}
        {gizmo && (
          <GizmoHelper alignment="bottom-left" margin={[52, 52]}>
            <GizmoViewport axisColors={["#E5604F", "#5DBB63", "#4C8DFF"]} labelColor="#9AA5BA" />
          </GizmoHelper>
        )}
      </Canvas>
    </div>
  );
}
