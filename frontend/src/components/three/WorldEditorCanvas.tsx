import { useEffect, useRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls, type TransformControlsMode } from "three/examples/jsm/controls/TransformControls.js";
import { reportFrontendDiagnostic } from "../../lib/runtimeDiagnostics";

export type EditorTool = "camera" | "translate" | "rotate" | "scale";

export interface EditorPlacement {
  assetId: string;
  name: string;
  translation: number[];
  rotationZDeg?: number;
  scale: number[];
  baseScale?: number[];
}

interface PlacementPatch {
  translation?: number[];
  rotationZDeg?: number;
  scaleMultiplier?: number[];
}

interface Runtime {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  orbit: OrbitControls;
  transform: TransformControls;
  roots: Map<string, THREE.Group>;
  baseScales: Map<string, THREE.Vector3>;
  loading: Set<string>;
  robotObjects: Map<string, THREE.Object3D>;
  robotLoading: Set<string>;
  robotRoot: THREE.Group;
  raycaster: THREE.Raycaster;
  pointer: THREE.Vector2;
  resize: ResizeObserver;
  frameId: number;
}

const apiOrigin = ((import.meta.env.VITE_API_ORIGIN as string | undefined) ?? "").replace(/\/$/, "");

function finite(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function vector(values: number[] | undefined, fallback: number): [number, number, number] {
  return [finite(values?.[0], fallback), finite(values?.[1], fallback), finite(values?.[2], fallback)];
}

function disposeObject(root: THREE.Object3D) {
  root.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    child.geometry.dispose();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    materials.forEach((material) => material.dispose());
  });
}

/**
 * Real-time scene editor for the generated GLBs. OpenUSD/PhysX/Vulkan remain
 * the canonical authored and validation backends; editor input stays local so
 * pointer motion never waits on a server-rendered image.
 */
export function WorldEditorCanvas({
  placements,
  robotGeometries = [],
  robotSpawn,
  selectedAssetId,
  tool,
  onSelect,
  onCommit,
  onRobotCommit,
  onFrame,
}: {
  placements: EditorPlacement[];
  robotGeometries?: AuthoringRobotGeometry[];
  robotSpawn?: { positionM: number[]; quaternionWxyz: number[] };
  selectedAssetId: string;
  tool: EditorTool;
  onSelect: (assetId: string, name: string) => void;
  onCommit: (assetId: string, patch: PlacementPatch) => void;
  onRobotCommit?: (patch: { positionM: number[]; quaternionWxyz: number[] }) => void;
  onFrame?: (metric: { fps: number; latencyMs: null }) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<Runtime | null>(null);
  const placementsRef = useRef(placements);
  const selectedRef = useRef(selectedAssetId);
  const toolRef = useRef(tool);
  const onSelectRef = useRef(onSelect);
  const onCommitRef = useRef(onCommit);
  const onRobotCommitRef = useRef(onRobotCommit);
  const onFrameRef = useRef(onFrame);
  const keysRef = useRef(new Set<string>());
  const draggedRef = useRef(false);

  placementsRef.current = placements;
  selectedRef.current = selectedAssetId;
  toolRef.current = tool;
  onSelectRef.current = onSelect;
  onCommitRef.current = onCommit;
  onRobotCommitRef.current = onRobotCommit;
  onFrameRef.current = onFrame;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111419);
    scene.fog = new THREE.FogExp2(0x111419, 0.018);

    const camera = new THREE.PerspectiveCamera(48, 1, 0.01, 500);
    camera.up.set(0, 0, 1);
    camera.position.set(4.6, -6.5, 4.1);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    renderer.domElement.setAttribute("aria-label", "Interactive RobotWorld scene viewport");
    host.appendChild(renderer.domElement);
    const onContextLost = (event: Event) => {
      event.preventDefault();
      reportFrontendDiagnostic({ source: "window", message: "World editor WebGL context was lost; GPU resources must be restored." });
    };
    renderer.domElement.addEventListener("webglcontextlost", onContextLost);

    const orbit = new OrbitControls(camera, renderer.domElement);
    orbit.target.set(0, 0, 0.7);
    orbit.enableDamping = true;
    orbit.dampingFactor = 0.09;
    orbit.screenSpacePanning = true;
    orbit.zoomToCursor = true;
    // Positive rotate speed keeps drag direction matched to the cursor:
    // drag up orbits up, drag right orbits right (a negative value inverts both).
    orbit.rotateSpeed = 0.85;
    orbit.panSpeed = 0.9;
    orbit.minDistance = 0.08;
    orbit.maxDistance = 120;
    orbit.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
    orbit.mouseButtons.MIDDLE = THREE.MOUSE.PAN;
    orbit.mouseButtons.RIGHT = THREE.MOUSE.PAN;
    orbit.update();

    const transform = new TransformControls(camera, renderer.domElement);
    transform.setColors(0xf05b5b, 0x58c978, 0x5f8cff, 0xffc857);
    transform.setSize(0.82);
    transform.setSpace("world");
    scene.add(transform.getHelper());
    const robotRoot = new THREE.Group();
    robotRoot.name = "Franka Panda fixed-base spawn";
    robotRoot.userData.robotSpawn = true;
    scene.add(robotRoot);
    transform.addEventListener("dragging-changed", (event) => {
      orbit.enabled = !event.value;
      if (event.value) draggedRef.current = true;
    });
    transform.addEventListener("mouseUp", () => {
      const root = transform.object as THREE.Group | undefined;
      const assetId = root?.userData.assetId as string | undefined;
      if (!root) return;
      if (root.userData.robotSpawn) {
        const quaternion = root.quaternion;
        onRobotCommitRef.current?.({
          positionM: [root.position.x, root.position.y, root.position.z],
          quaternionWxyz: [quaternion.w, quaternion.x, quaternion.y, quaternion.z],
        });
        window.setTimeout(() => { draggedRef.current = false; }, 0);
        return;
      }
      if (!assetId) return;
      const activeTool = toolRef.current;
      if (activeTool === "translate") {
        onCommitRef.current(assetId, { translation: [root.position.x, root.position.y, root.position.z] });
      } else if (activeTool === "rotate") {
        onCommitRef.current(assetId, { rotationZDeg: THREE.MathUtils.radToDeg(root.rotation.z) });
      } else if (activeTool === "scale") {
        const base = runtimeRef.current?.baseScales.get(assetId) ?? new THREE.Vector3(1, 1, 1);
        onCommitRef.current(assetId, {
          scaleMultiplier: [
            Math.max(0.02, root.scale.x / Math.max(base.x, 1e-6)),
            Math.max(0.02, root.scale.y / Math.max(base.y, 1e-6)),
            Math.max(0.02, root.scale.z / Math.max(base.z, 1e-6)),
          ],
        });
      }
      window.setTimeout(() => { draggedRef.current = false; }, 0);
    });

    scene.add(new THREE.HemisphereLight(0xdce8ff, 0x252a31, 1.8));
    const key = new THREE.DirectionalLight(0xffffff, 3.1);
    key.position.set(-4, -5, 9);
    key.castShadow = false;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x8fb5ff, 1.2);
    fill.position.set(6, 2, 4);
    scene.add(fill);

    const grid = new THREE.GridHelper(40, 80, 0x505866, 0x2a3039);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -0.002;
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
    gridMaterials.forEach((material) => { material.transparent = true; material.opacity = 0.62; });
    scene.add(grid);
    scene.add(new THREE.AxesHelper(0.75));

    const runtime: Runtime = {
      scene,
      camera,
      renderer,
      orbit,
      transform,
      roots: new Map(),
      baseScales: new Map(),
      loading: new Set(),
      robotObjects: new Map(),
      robotLoading: new Set(),
      robotRoot,
      raycaster: new THREE.Raycaster(),
      pointer: new THREE.Vector2(),
      resize: new ResizeObserver(() => undefined),
      frameId: 0,
    };
    runtimeRef.current = runtime;

    const resize = () => {
      const width = Math.max(1, host.clientWidth);
      const height = Math.max(1, host.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    };
    runtime.resize = new ResizeObserver(resize);
    runtime.resize.observe(host);
    resize();

    let pointerStart = { x: 0, y: 0 };
    const onPointerDown = (event: PointerEvent) => {
      host.focus({ preventScroll: true });
      pointerStart = { x: event.clientX, y: event.clientY };
    };
    const onPointerUp = (event: PointerEvent) => {
      if (draggedRef.current || Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 4) return;
      const rect = renderer.domElement.getBoundingClientRect();
      runtime.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      runtime.raycaster.setFromCamera(runtime.pointer, camera);
      const candidates = [...runtime.roots.values(), runtime.robotRoot];
      const hit = runtime.raycaster.intersectObjects(candidates, true)[0];
      let current: THREE.Object3D | null = hit?.object ?? null;
      while (current && !current.userData.assetId && !current.userData.robotSpawn) current = current.parent;
      const assetId = current?.userData.assetId as string | undefined;
      if (assetId) {
        const placement = placementsRef.current.find((item) => item.assetId === assetId);
        onSelectRef.current(assetId, placement?.name ?? assetId);
      } else if (current?.userData.robotSpawn || current?.parent?.userData.robotSpawn) {
        onSelectRef.current("robot-spawn", "Franka Panda fixed-base spawn");
      }
    };
    const onContextMenu = (event: Event) => event.preventDefault();
    const onKeyDown = (event: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes((event.target as HTMLElement | null)?.tagName ?? "")) return;
      keysRef.current.add(event.key.toLowerCase());
      if (toolRef.current === "camera" && ["w", "a", "s", "d", "q", "e"].includes(event.key.toLowerCase())) event.preventDefault();
    };
    const onKeyUp = (event: KeyboardEvent) => keysRef.current.delete(event.key.toLowerCase());
    const clearKeys = () => keysRef.current.clear();
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointerup", onPointerUp);
    renderer.domElement.addEventListener("contextmenu", onContextMenu);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", clearKeys);

    const clock = new THREE.Clock();
    let frames = 0;
    let sampleStarted = performance.now();
    const forward = new THREE.Vector3();
    const right = new THREE.Vector3();
    const motion = new THREE.Vector3();
    const animate = (now: number) => {
      const delta = Math.min(clock.getDelta(), 0.05);
      if (toolRef.current === "camera" && keysRef.current.size > 0) {
        camera.getWorldDirection(forward);
        forward.z = 0;
        if (forward.lengthSq() < 1e-6) forward.set(0, 1, 0);
        forward.normalize();
        right.crossVectors(forward, camera.up).normalize();
        motion.set(0, 0, 0);
        if (keysRef.current.has("w")) motion.add(forward);
        if (keysRef.current.has("s")) motion.sub(forward);
        if (keysRef.current.has("d")) motion.add(right);
        if (keysRef.current.has("a")) motion.sub(right);
        if (keysRef.current.has("e")) motion.z += 1;
        if (keysRef.current.has("q")) motion.z -= 1;
        if (motion.lengthSq() > 0) {
          const speed = Math.max(0.8, camera.position.distanceTo(orbit.target) * 0.65) * delta;
          motion.normalize().multiplyScalar(speed);
          camera.position.add(motion);
          orbit.target.add(motion);
        }
      }
      orbit.update(delta);
      renderer.render(scene, camera);
      frames += 1;
      if (now - sampleStarted >= 500) {
        const fps = Math.round((frames * 1000) / (now - sampleStarted));
        onFrameRef.current?.({ fps, latencyMs: null });
        frames = 0;
        sampleStarted = now;
      }
      runtime.frameId = window.requestAnimationFrame(animate);
    };
    runtime.frameId = window.requestAnimationFrame(animate);

    return () => {
      window.cancelAnimationFrame(runtime.frameId);
      runtime.resize.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      renderer.domElement.removeEventListener("contextmenu", onContextMenu);
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", clearKeys);
      transform.detach();
      transform.dispose();
      orbit.dispose();
      runtime.roots.forEach(disposeObject);
      runtime.robotObjects.forEach(disposeObject);
      renderer.dispose();
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const wanted = new Set(placements.map((placement) => placement.assetId));
    runtime.roots.forEach((root, id) => {
      if (wanted.has(id)) return;
      if (runtime.transform.object === root) runtime.transform.detach();
      runtime.scene.remove(root);
      disposeObject(root);
      runtime.roots.delete(id);
      runtime.baseScales.delete(id);
    });

    const loader = new GLTFLoader();
    placements.forEach((placement) => {
      const finalScale = vector(placement.scale, 1);
      const baseScale = vector(placement.baseScale ?? placement.scale, 1);
      runtime.baseScales.set(placement.assetId, new THREE.Vector3(...baseScale));
      const existing = runtime.roots.get(placement.assetId);
      if (existing) {
        if (!runtime.transform.dragging) {
          existing.position.set(...vector(placement.translation, 0));
          existing.rotation.set(0, 0, THREE.MathUtils.degToRad(finite(placement.rotationZDeg, 0)));
          existing.scale.set(...finalScale);
        }
        return;
      }
      if (runtime.loading.has(placement.assetId)) return;
      runtime.loading.add(placement.assetId);
      const url = `${apiOrigin}/api/assets/${encodeURIComponent(placement.assetId)}/files/model.glb`;
      loader.load(url, (gltf) => {
        runtime.loading.delete(placement.assetId);
        if (!runtimeRef.current || !wanted.has(placement.assetId)) return;
        const root = new THREE.Group();
        root.name = placement.name;
        root.userData.assetId = placement.assetId;
        root.position.set(...vector(placement.translation, 0));
        root.rotation.z = THREE.MathUtils.degToRad(finite(placement.rotationZDeg, 0));
        root.scale.set(...finalScale);
        gltf.scene.rotation.x = Math.PI / 2;
        gltf.scene.traverse((child) => {
          child.userData.assetId = placement.assetId;
          if (child instanceof THREE.Mesh) {
            child.castShadow = false;
            child.receiveShadow = false;
          }
        });
        root.add(gltf.scene);
        runtime.roots.set(placement.assetId, root);
        runtime.scene.add(root);
        if (selectedRef.current === placement.assetId && toolRef.current !== "camera") runtime.transform.attach(root);
      }, undefined, (error) => {
        runtime.loading.delete(placement.assetId);
        console.error(`Could not load GLB for ${placement.assetId}`, error);
        reportFrontendDiagnostic({
          source: "api",
          message: `World editor could not load GLB ${placement.assetId}: ${error instanceof Error ? error.message : String(error)}`,
        });
      });
    });
  }, [placements]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    if (robotSpawn) {
      runtime.robotRoot.position.set(...vector(robotSpawn.positionM, 0));
      const [w = 1, x = 0, y = 0, z = 0] = robotSpawn.quaternionWxyz;
      runtime.robotRoot.quaternion.set(x, y, z, w);
      runtime.robotRoot.updateMatrixWorld(true);
    }
    const wanted = new Set(robotGeometries.map((entry) => entry.id));
    runtime.robotObjects.forEach((object, id) => {
      if (wanted.has(id)) return;
      object.parent?.remove(object);
      disposeObject(object);
      runtime.robotObjects.delete(id);
    });
    const loader = new OBJLoader();
    robotGeometries.forEach((entry) => {
      const existing = runtime.robotObjects.get(entry.id);
      if (existing) {
        applyRobotLocalPose(existing, entry, runtime.robotRoot);
        return;
      }
      if (!entry.meshName || runtime.robotLoading.has(entry.id)) return;
      runtime.robotLoading.add(entry.id);
      loader.load(`${apiOrigin}/api/runtime/franka-compiled-meshes/${encodeURIComponent(entry.meshName)}.obj`, (object) => {
        runtime.robotLoading.delete(entry.id);
        if (!runtimeRef.current || !wanted.has(entry.id)) return;
        const [r = 0.93, g = 0.94, b = 0.96, a = 1] = entry.rgba;
        const robotMaterial = new THREE.MeshStandardMaterial({
          color: new THREE.Color(r, g, b),
          transparent: a < 0.999,
          opacity: a,
          roughness: 0.58,
          metalness: 0.04,
        });
        object.traverse((child) => {
          if (child instanceof THREE.Mesh) child.material = robotMaterial;
        });
        object.name = entry.name;
        object.userData.robotPreview = true;
        applyRobotLocalPose(object, entry, runtime.robotRoot);
        runtime.robotObjects.set(entry.id, object);
        runtime.robotRoot.add(object);
      }, undefined, (error) => {
        runtime.robotLoading.delete(entry.id);
        reportFrontendDiagnostic({
          source: "api",
          message: `World editor could not load compiled Franka mesh ${entry.meshName}: ${String(error)}`,
        });
      });
    });
  }, [robotGeometries, robotSpawn]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    if (tool === "camera") {
      runtime.transform.detach();
      runtime.orbit.enabled = true;
      return;
    }
    runtime.transform.setMode(tool as TransformControlsMode);
    runtime.transform.setSpace(tool === "scale" ? "local" : "world");
    const root = selectedAssetId === "robot-spawn" ? runtime.robotRoot : runtime.roots.get(selectedAssetId);
    if (root && !(selectedAssetId === "robot-spawn" && tool !== "translate")) runtime.transform.attach(root);
    else runtime.transform.detach();
  }, [selectedAssetId, tool, placements]);

  return (
    <div
      ref={hostRef}
      className="world-editor-canvas"
      tabIndex={0}
      role="application"
      aria-label="3D world editor. In Camera mode use W A S D to move and Q E to move vertically."
    />
  );
}

export interface AuthoringRobotGeometry {
  id: string;
  name: string;
  meshName?: string | null;
  rgba: number[];
  positionM: number[];
  quaternionWxyz: number[];
}

function applyRobotLocalPose(object: THREE.Object3D, entry: AuthoringRobotGeometry, root: THREE.Group) {
  const worldPosition = new THREE.Vector3(
    finite(entry.positionM[0], 0),
    finite(entry.positionM[1], 0),
    finite(entry.positionM[2], 0),
  );
  const [w = 1, x = 0, y = 0, z = 0] = entry.quaternionWxyz;
  const worldQuaternion = new THREE.Quaternion(x, y, z, w);
  const worldMatrix = new THREE.Matrix4().compose(worldPosition, worldQuaternion, new THREE.Vector3(1, 1, 1));
  const localMatrix = root.matrixWorld.clone().invert().multiply(worldMatrix);
  localMatrix.decompose(object.position, object.quaternion, object.scale);
}
