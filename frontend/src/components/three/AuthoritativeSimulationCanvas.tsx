import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { reportFrontendDiagnostic } from "../../lib/runtimeDiagnostics";

export interface RuntimeGeometry {
  id: string;
  name: string;
  kind: "plane" | "sphere" | "capsule" | "ellipsoid" | "cylinder" | "box" | "mesh" | "unknown";
  meshName?: string | null;
  assetVersionId?: string | null;
  sourcePbrTransform?: {
    uniformScale: number;
    translationM: number[];
    mapping: string;
  } | null;
  size: number[];
  rgba: number[];
  positionM: number[];
  quaternionWxyz: number[];
  bodyPositionM?: number[];
  bodyQuaternionWxyz?: number[];
}

interface Runtime {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  objects: Map<string, THREE.Object3D>;
  loading: Set<string>;
  contextObjects: Map<string, THREE.Object3D>;
  contextLoading: Set<string>;
  resize: ResizeObserver;
  frameId: number;
}

export interface SimulationContextPlacement {
  assetId: string;
  name: string;
  translation: number[];
  rotationZDeg?: number;
  scale: number[];
}

const apiOrigin = ((import.meta.env.VITE_API_ORIGIN as string | undefined) ?? "").replace(/\/$/, "");

function material(geometry: RuntimeGeometry) {
  const [r = 0.75, g = 0.78, b = 0.82, a = 1] = geometry.rgba;
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(r, g, b),
    transparent: a < 0.999,
    opacity: a,
    roughness: 0.64,
    metalness: 0.05,
    side: geometry.kind === "plane" ? THREE.DoubleSide : THREE.FrontSide,
  });
}

function primitive(entry: RuntimeGeometry): THREE.Object3D {
  const [x = 0.05, y = x, z = x] = entry.size;
  let shape: THREE.BufferGeometry;
  if (entry.kind === "plane") shape = new THREE.BoxGeometry(Math.max(x * 2, 3), Math.max(y * 2, 3), Math.max(z * 2, 0.006));
  else if (entry.kind === "box") shape = new THREE.BoxGeometry(x * 2, y * 2, z * 2);
  else if (entry.kind === "sphere") shape = new THREE.SphereGeometry(x, 24, 16);
  else if (entry.kind === "cylinder") shape = new THREE.CylinderGeometry(x, x, y * 2, 32);
  else if (entry.kind === "capsule") shape = new THREE.CapsuleGeometry(x, Math.max(0, y * 2), 8, 20);
  else if (entry.kind === "ellipsoid") {
    shape = new THREE.SphereGeometry(1, 24, 16);
    shape.scale(x, y, z);
  } else shape = new THREE.SphereGeometry(Math.max(x, 0.025), 16, 12);
  // MuJoCo cylinders/capsules point along local Z; Three.js primitives point Y.
  if (entry.kind === "cylinder" || entry.kind === "capsule") shape.rotateX(Math.PI / 2);
  const mesh = new THREE.Mesh(shape, material(entry));
  return mesh;
}

function applyPose(object: THREE.Object3D, entry: RuntimeGeometry) {
  const position = entry.assetVersionId && entry.bodyPositionM ? entry.bodyPositionM : entry.positionM;
  const quaternion = entry.assetVersionId && entry.bodyQuaternionWxyz ? entry.bodyQuaternionWxyz : entry.quaternionWxyz;
  object.position.set(position[0] ?? 0, position[1] ?? 0, position[2] ?? 0);
  const [w = 1, x = 0, y = 0, z = 0] = quaternion;
  object.quaternion.set(x, y, z, w);
}

function dispose(root: THREE.Object3D) {
  root.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    child.geometry.dispose();
    const values = Array.isArray(child.material) ? child.material : [child.material];
    values.forEach((value) => value.dispose());
  });
}

/** Interactive orbit view of poses computed by the authoritative MuJoCo model. */
export function AuthoritativeSimulationCanvas({
  geometries,
  contextPlacements = [],
}: {
  geometries: RuntimeGeometry[];
  contextPlacements?: SimulationContextPlacement[];
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<Runtime | null>(null);
  const geometriesRef = useRef<RuntimeGeometry[]>([]);

  useEffect(() => {
    geometriesRef.current = geometries;
  }, [geometries]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d0f13);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.01, 100);
    camera.up.set(0, 0, 1);
    // Frame the full authored counter and Panda at startup. The previous
    // validation-bench camera aimed too low and cropped the arm in the wider
    // kitchen world even though the physics transforms were correct.
    camera.position.set(1.9, -2.45, 1.75);
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.6));
    renderer.domElement.setAttribute("aria-label", "Authoritative interactive MuJoCo 3D simulation viewport");
    host.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0.0, 0.0, 0.78);
    controls.enableDamping = true;
    controls.zoomToCursor = true;
    controls.update();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x252b35, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 3.2);
    key.position.set(-2, -3, 5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x9dbbff, 1.4);
    fill.position.set(3, 2, 2);
    scene.add(fill);
    const resize = new ResizeObserver(() => {
      const width = Math.max(1, host.clientWidth);
      const height = Math.max(1, host.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    });
    resize.observe(host);
    const runtime: Runtime = {
      scene,
      camera,
      renderer,
      controls,
      objects: new Map(),
      loading: new Set(),
      contextObjects: new Map(),
      contextLoading: new Set(),
      resize,
      frameId: 0,
    };
    runtimeRef.current = runtime;
    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      runtime.frameId = window.requestAnimationFrame(animate);
    };
    runtime.frameId = window.requestAnimationFrame(animate);
    return () => {
      window.cancelAnimationFrame(runtime.frameId);
      resize.disconnect();
      controls.dispose();
      runtime.objects.forEach(dispose);
      runtime.contextObjects.forEach(dispose);
      renderer.dispose();
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const wanted = new Set(contextPlacements.map((entry) => entry.assetId));
    runtime.contextObjects.forEach((object, id) => {
      if (wanted.has(id)) return;
      runtime.scene.remove(object);
      dispose(object);
      runtime.contextObjects.delete(id);
    });
    const loader = new GLTFLoader();
    contextPlacements.forEach((entry) => {
      const existing = runtime.contextObjects.get(entry.assetId);
      if (existing) {
        existing.position.set(entry.translation[0] ?? 0, entry.translation[1] ?? 0, entry.translation[2] ?? 0);
        existing.rotation.z = THREE.MathUtils.degToRad(entry.rotationZDeg ?? 0);
        existing.scale.set(entry.scale[0] ?? 1, entry.scale[1] ?? 1, entry.scale[2] ?? 1);
        return;
      }
      if (runtime.contextLoading.has(entry.assetId)) return;
      runtime.contextLoading.add(entry.assetId);
      loader.load(`${apiOrigin}/api/assets/${encodeURIComponent(entry.assetId)}/files/model.glb`, (gltf) => {
        runtime.contextLoading.delete(entry.assetId);
        if (!runtimeRef.current || !wanted.has(entry.assetId)) return;
        const root = new THREE.Group();
        root.name = `${entry.name} · authored visual context`;
        root.userData.authoringVisualContext = true;
        root.position.set(entry.translation[0] ?? 0, entry.translation[1] ?? 0, entry.translation[2] ?? 0);
        root.rotation.z = THREE.MathUtils.degToRad(entry.rotationZDeg ?? 0);
        root.scale.set(entry.scale[0] ?? 1, entry.scale[1] ?? 1, entry.scale[2] ?? 1);
        gltf.scene.rotation.x = Math.PI / 2;
        root.add(gltf.scene);
        runtime.contextObjects.set(entry.assetId, root);
        runtime.scene.add(root);
      }, undefined, (error) => {
        runtime.contextLoading.delete(entry.assetId);
        reportFrontendDiagnostic({ source: "api", message: `Authored world context ${entry.assetId} failed: ${String(error)}` });
      });
    });
  }, [contextPlacements]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const loader = new OBJLoader();
    const gltfLoader = new GLTFLoader();
    const ids = new Set(geometries.map((entry) => entry.id));
    runtime.objects.forEach((object, id) => {
      if (ids.has(id)) return;
      runtime.scene.remove(object);
      dispose(object);
      runtime.objects.delete(id);
    });
    geometries.forEach((entry) => {
      const current = runtime.objects.get(entry.id);
      if (current) {
        applyPose(current, entry);
        return;
      }
      if (runtime.loading.has(entry.id)) return;
      if (entry.kind !== "mesh" || !entry.meshName) {
        const object = primitive(entry);
        object.name = entry.name;
        applyPose(object, entry);
        runtime.objects.set(entry.id, object);
        runtime.scene.add(object);
        return;
      }
      runtime.loading.add(entry.id);
      if (entry.assetVersionId && entry.sourcePbrTransform) {
        const pbrUrl = `${apiOrigin}/api/asset-versions/${encodeURIComponent(entry.assetVersionId)}/source.glb`;
        gltfLoader.load(pbrUrl, (gltf) => {
          runtime.loading.delete(entry.id);
          const latest = geometriesRef.current.find((item) => item.id === entry.id);
          if (!runtimeRef.current || !latest || !latest.sourcePbrTransform) return;
          const root = new THREE.Group();
          const canonical = new THREE.Group();
          const transform = latest.sourcePbrTransform;
          canonical.scale.setScalar(transform.uniformScale);
          canonical.rotation.x = Math.PI / 2;
          canonical.position.set(
            transform.translationM[0] ?? 0,
            transform.translationM[1] ?? 0,
            transform.translationM[2] ?? 0,
          );
          canonical.add(gltf.scene);
          root.add(canonical);
          root.name = latest.name;
          applyPose(root, latest);
          runtime.objects.set(entry.id, root);
          runtime.scene.add(root);
        }, undefined, (error) => {
          runtime.loading.delete(entry.id);
          reportFrontendDiagnostic({ source: "api", message: `PBR physics visual ${entry.assetVersionId} failed: ${String(error)}` });
        });
        return;
      }
      const visualUrl = entry.assetVersionId
        ? `${apiOrigin}/api/asset-versions/${encodeURIComponent(entry.assetVersionId)}/runtime-visual.obj`
        : `${apiOrigin}/api/runtime/franka-compiled-meshes/${encodeURIComponent(entry.meshName)}.obj`;
      loader.load(visualUrl, (object) => {
        runtime.loading.delete(entry.id);
        // OBJ requests complete independently while physics continues. Read
        // the newest sampled transform instead of the render whose closure
        // initiated this request; otherwise link parts freeze in different
        // oracle phases and the Panda appears exploded after the run ends.
        const latest = geometriesRef.current.find((item) => item.id === entry.id);
        if (!runtimeRef.current || !latest) return;
        const mat = material(latest);
        object.traverse((child) => {
          if (child instanceof THREE.Mesh) {
            const old = child.material;
            child.material = mat;
            const values = Array.isArray(old) ? old : [old];
            values.forEach((value) => value.dispose());
          }
        });
        object.name = latest.name;
        applyPose(object, latest);
        runtime.objects.set(entry.id, object);
        runtime.scene.add(object);
      }, undefined, (error) => {
        runtime.loading.delete(entry.id);
        reportFrontendDiagnostic({ source: "api", message: `Physics visual mesh ${entry.meshName} failed: ${String(error)}` });
      });
    });
  }, [geometries]);

  return <div className="world-editor-canvas" ref={hostRef} role="application" aria-label="Live authoritative Franka physics. Drag to orbit, right-drag to pan, and scroll to zoom." />;
}
