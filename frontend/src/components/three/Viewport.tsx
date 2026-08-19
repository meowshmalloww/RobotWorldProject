import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent, type WheelEvent } from "react";

export type RenderVariant = "rgb" | "seg" | "depth";

interface CameraState {
  yaw: number;
  pitch: number;
  distance: number;
}

/**
 * Native Vulkan viewport. The React client presents PNG frames only; pygfx and
 * wgpu-native perform all scene rasterization in the FastAPI process with the
 * backend forced to Vulkan. This component deliberately has no WebGL fallback.
 */
export function Viewport({
  camera = { position: [2.6, 2.1, 1.8] as [number, number, number], fov: 42 },
  target = [-0.2, 0.9, -2.6] as [number, number, number],
  variant = "rgb",
  controls = true,
  onPointerMissed,
  className,
  style,
  autoRotate = false,
  scene = "kitchen",
  doorAngle = 0,
}: {
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
  style?: CSSProperties;
  fov?: number;
  autoRotate?: boolean;
  fly?: boolean;
  scene?: "kitchen" | "factory";
  doorAngle?: number;
}) {
  const initial = useMemo<CameraState>(() => {
    const dx = camera.position[0] - target[0];
    const dy = camera.position[1] - target[1];
    const dz = camera.position[2] - target[2];
    const distance = Math.max(5, Math.hypot(dx, dy, dz) * 2.15);
    return {
      yaw: Math.atan2(dx, dz) * 180 / Math.PI,
      pitch: Math.asin(Math.max(-1, Math.min(1, dy / Math.max(Math.hypot(dx, dy, dz), 0.001)))) * 180 / Math.PI,
      distance,
    };
  }, [camera.position, target]);
  const host = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number } | null>(null);
  const imageRef = useRef<string | null>(null);
  const [view, setView] = useState(initial);
  const [renderView, setRenderView] = useState(initial);
  const [size, setSize] = useState({ width: 960, height: 540 });
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backend, setBackend] = useState<string>("checking");

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(320, Math.min(1400, Math.round(entry.contentRect.width)));
      const height = Math.max(180, Math.min(900, Math.round(entry.contentRect.height)));
      setSize((old) => old.width === width && old.height === height ? old : { width, height });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setRenderView(view), 70);
    return () => window.clearTimeout(timer);
  }, [view]);

  useEffect(() => {
    if (!autoRotate) return;
    const timer = window.setInterval(() => setView((old) => ({ ...old, yaw: old.yaw + 0.35 })), 40);
    return () => window.clearInterval(timer);
  }, [autoRotate]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/render/vulkan/probe", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
        return response.json() as Promise<{ backend: string; device: string }>;
      })
      .then((data) => setBackend(`${data.backend} · ${data.device}`))
      .catch((reason) => {
        if (reason?.name !== "AbortError") setError(`Vulkan unavailable: ${reason instanceof Error ? reason.message : String(reason)}`);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (variant === "depth") {
      setLoading(false);
      setError("Depth view requires a calibrated sensor stream; no synthetic depth frame is substituted.");
      return;
    }
    const query = new URLSearchParams({
      scene,
      width: String(size.width),
      height: String(size.height),
      yaw: renderView.yaw.toFixed(2),
      pitch: renderView.pitch.toFixed(2),
      distance: renderView.distance.toFixed(2),
      doorAngle: String(Math.max(0, Math.min(120, doorAngle))),
      variant,
    });
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      fetch(`/api/render/vulkan/frame?${query}`, { signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
          return response.blob();
        })
        .then((blob) => {
          const next = URL.createObjectURL(blob);
          if (imageRef.current) URL.revokeObjectURL(imageRef.current);
          imageRef.current = next;
          setImageUrl(next);
          setError(null);
        })
        .catch((reason) => {
          if (reason?.name !== "AbortError") setError(reason instanceof Error ? reason.message : String(reason));
        })
        .finally(() => setLoading(false));
    }, 35);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [doorAngle, renderView, scene, size, variant]);

  useEffect(() => () => {
    if (imageRef.current) URL.revokeObjectURL(imageRef.current);
  }, []);

  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!controls) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, yaw: view.yaw, pitch: view.pitch };
  };
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    setView((old) => ({
      ...old,
      yaw: drag.current!.yaw - (event.clientX - drag.current!.x) * 0.24,
      pitch: Math.max(-8, Math.min(70, drag.current!.pitch + (event.clientY - drag.current!.y) * 0.18)),
    }));
  };
  const pointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
  };
  const wheel = (event: WheelEvent<HTMLDivElement>) => {
    if (!controls) return;
    setView((old) => ({ ...old, distance: Math.max(4, Math.min(28, old.distance + event.deltaY * 0.012)) }));
  };

  return (
    <div
      ref={host}
      className={`viewport vulkan-viewport ${className ?? ""}`}
      style={style}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerUp}
      onPointerCancel={pointerUp}
      onDoubleClick={onPointerMissed}
      onWheel={wheel}
    >
      {imageUrl && <img src={imageUrl} alt={`${scene} scene rendered by native Vulkan`} draggable={false} />}
      {!imageUrl && !error && <div className="vulkan-empty">Initializing native Vulkan renderer…</div>}
      {error && <div className="vulkan-error"><strong>Viewport unavailable</strong><span>{error}</span></div>}
      <div className="vulkan-badge"><span className={`dot ${error ? "bad" : ""}`} /> {backend}</div>
      {loading && imageUrl && <div className="vulkan-loading" aria-label="Rendering frame" />}
    </div>
  );
}
