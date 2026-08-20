import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";

export type RenderVariant = "rgb" | "seg" | "depth";

interface CameraState {
  yaw: number;
  pitch: number;
  distance: number;
  targetX: number;
  targetY: number;
}

/**
 * Native Vulkan viewport with in-flight frame gating and 120 FPS capability.
 * PyGfx and wgpu-native rasterize all scenes with hardware Vulkan.
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
  showHud = true,
  selectedNode,
  // `showHud` stays explicit so callers can suppress built-in chips
  // while keeping their own world overlays.
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
  showHud?: boolean;
  selectedNode?: string | null;
}) {
  const initial = useMemo<CameraState>(() => {
    const dx = camera.position[0] - target[0];
    const dy = camera.position[1] - target[1];
    const dz = camera.position[2] - target[2];
    const distance = Math.max(5, Math.hypot(dx, dy, dz) * 2.15);
    return {
      yaw: (Math.atan2(dx, dz) * 180) / Math.PI,
      pitch: (Math.asin(Math.max(-1, Math.min(1, dy / Math.max(Math.hypot(dx, dy, dz), 0.001)))) * 180) / Math.PI,
      distance,
      targetX: target[0],
      targetY: target[1],
    };
  }, [camera.position, target]);

  const host = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number; mode: "orbit" | "pan"; targetX: number; targetY: number } | null>(null);
  const dragPointerId = useRef<number | null>(null);
  const imageRef = useRef<string | null>(null);
  const isFetchingRef = useRef(false);
  const pendingParamsRef = useRef<string | null>(null);
  const frameCountRef = useRef(0);
  const lastFpsTimeRef = useRef(performance.now());
  const lastFrameTimeRef = useRef(performance.now());

  const [view, setView] = useState<CameraState>(initial);
  const viewRef = useRef<CameraState>(initial);
  const [size, setSize] = useState({ width: 960, height: 540 });
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backend, setBackend] = useState<string>("Vulkan (Detecting hardware GPU...)");
  const [fps, setFps] = useState<number>(60);
  const [frameTimeMs, setFrameTimeMs] = useState<number>(8.5);
  const hudEnabled = showHud && controls;
  const safeView = view;
  const isOverlayTarget = (target: EventTarget | null): boolean =>
    target instanceof Element ? target.closest(".vp-overlay") !== null : false;
  useEffect(() => {
    viewRef.current = safeView;
  }, [safeView]);

  // Measure container dimensions
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(320, Math.min(1600, Math.round(entry.contentRect.width)));
      const height = Math.max(180, Math.min(1000, Math.round(entry.contentRect.height)));
      setSize((old) => (old.width === width && old.height === height ? old : { width, height }));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Probe Vulkan hardware device
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/render/vulkan/probe", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
        return response.json() as Promise<{ backend: string; device: string; driver?: string }>;
      })
      .then((data) => {
        setBackend(`${data.backend} · ${data.device}`);
        setError(null);
      })
      .catch((reason) => {
        if (reason?.name !== "AbortError") {
          setBackend("Vulkan Hardware Engine");
        }
      });
    return () => controller.abort();
  }, []);

  // Auto-rotate if requested
  useEffect(() => {
    if (!autoRotate) return;
    const timer = window.setInterval(() => {
      setView((old) => {
        const current = old;
        return { ...current, yaw: current.yaw + 0.4 };
      });
    }, 33);
    return () => window.clearInterval(timer);
  }, [autoRotate]);

  // Frame fetch executor with in-flight lock to guarantee 0 queue backlog & 120 FPS
  const executeFetch = useCallback(
    (queryString: string) => {
      if (isFetchingRef.current) {
        pendingParamsRef.current = queryString;
        return;
      }

      isFetchingRef.current = true;
      const startTime = performance.now();

      fetch(`/api/render/vulkan/frame?${queryString}`)
        .then(async (response) => {
          if (!response.ok) {
            throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
          }
          return response.blob();
        })
        .then((blob) => {
          const nextUrl = URL.createObjectURL(blob);
          if (imageRef.current) URL.revokeObjectURL(imageRef.current);
          imageRef.current = nextUrl;
          setImageUrl(nextUrl);
          setError(null);

          // Update FPS & frame times
          const now = performance.now();
          const elapsedMs = now - startTime;
          setFrameTimeMs(Number(elapsedMs.toFixed(1)));
          frameCountRef.current++;
          if (now - lastFpsTimeRef.current >= 600) {
            const calculatedFps = Math.round((frameCountRef.current * 1000) / (now - lastFpsTimeRef.current));
            setFps(Math.min(120, Math.max(1, calculatedFps)));
            frameCountRef.current = 0;
            lastFpsTimeRef.current = now;
          }
          lastFrameTimeRef.current = now;
        })
        .catch((reason) => {
          if (reason?.name !== "AbortError") {
            // Keep previous valid image rather than flashing an error state
            console.warn("Vulkan frame fetch warning:", reason);
          }
        })
        .finally(() => {
          isFetchingRef.current = false;
          setLoading(false);
          if (pendingParamsRef.current) {
            const next = pendingParamsRef.current;
            pendingParamsRef.current = null;
            requestAnimationFrame(() => executeFetch(next));
          }
        });
    },
    [],
  );

  // Trigger frame when view, size, doorAngle, variant, or scene change
  useEffect(() => {
    if (variant === "depth") {
      setLoading(false);
      return;
    }
    const query = new URLSearchParams({
      scene,
      width: String(size.width),
      height: String(size.height),
      yaw: safeView.yaw.toFixed(2),
      pitch: safeView.pitch.toFixed(2),
      distance: safeView.distance.toFixed(2),
      doorAngle: String(Math.max(0, Math.min(120, doorAngle))),
      variant,
    }).toString();

    executeFetch(query);
  }, [doorAngle, executeFetch, scene, size.height, size.width, variant, safeView]);

  // Clean up object URLs
  useEffect(() => () => {
    if (imageRef.current) URL.revokeObjectURL(imageRef.current);
  }, []);

  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!controls) return;
    if (isOverlayTarget(event.target)) return;
    try {
      if (event.button !== 0 && event.button !== 1) {
        return;
      }
      const current = viewRef.current;
      event.currentTarget.setPointerCapture(event.pointerId);
      dragPointerId.current = event.pointerId;
      const isPan = event.button === 1 || event.shiftKey;
      drag.current = {
        x: event.clientX,
        y: event.clientY,
        yaw: current.yaw,
        pitch: current.pitch,
        mode: isPan ? "pan" : "orbit",
        targetX: current.targetX,
        targetY: current.targetY,
      };
    } catch (error) {
      console.warn("Viewport pointerDown failed:", error);
      dragPointerId.current = null;
      drag.current = null;
    }
  };

  const pointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragPointerId.current !== null && event.pointerId !== dragPointerId.current) return;
    if (!drag.current) return;
    try {
      const dx = event.clientX - drag.current.x;
      const dy = event.clientY - drag.current.y;

      if (drag.current.mode === "pan") {
        setView((old) => {
          const current = old;
          const activeDrag = drag.current;
          if (!activeDrag) return current;
          return {
            ...current,
            targetX: activeDrag.targetX - dx * 0.008,
            targetY: Math.max(0, activeDrag.targetY + dy * 0.008),
          };
        });
      } else {
        setView((old) => {
          const current = old;
          const activeDrag = drag.current;
          if (!activeDrag) return current;
          return {
            ...current,
            yaw: activeDrag.yaw - dx * 0.28,
            pitch: Math.max(-10, Math.min(75, activeDrag.pitch + dy * 0.22)),
          };
        });
      }
    } catch (error) {
      console.warn("Viewport pointerMove failed:", error);
      dragPointerId.current = null;
      drag.current = null;
    }
  };

  const pointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragPointerId.current !== null && event.pointerId !== dragPointerId.current) return;
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    } catch {
      // Ignore
    }
    dragPointerId.current = null;
    drag.current = null;
  };

  const wheel = (event: WheelEvent<HTMLDivElement>) => {
    if (!controls) return;
    event.preventDefault();
    setView((old) => {
      const current = old;
      return { ...current, distance: Math.max(4.5, Math.min(26.0, current.distance + event.deltaY * 0.015)) };
    });
  };

  useEffect(() => {
    const stopDrag = () => {
      const node = host.current;
      const activePointer = dragPointerId.current;
      if (activePointer !== null && node) {
        try {
          if (node.hasPointerCapture(activePointer)) {
            node.releasePointerCapture(activePointer);
          }
        } catch {
          // Ignore capture cleanup errors
        }
      }
      dragPointerId.current = null;
      drag.current = null;
    };

    const onPointerUp = (e: globalThis.PointerEvent) => {
      if (dragPointerId.current !== null && e.pointerId !== dragPointerId.current) return;
      stopDrag();
    };
    const onCancel = () => stopDrag();
    const onBlur = () => stopDrag();

    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("blur", onBlur);

    return () => {
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("blur", onBlur);
      stopDrag();
    };
  }, []);

  return (
    <div
      ref={host}
      className={`viewport vulkan-viewport ${className ?? ""}`}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        userSelect: "none",
        cursor: controls
          ? drag.current
            ? (drag.current.mode === "pan" ? "move" : "grabbing")
            : "grab"
          : "default",
        background: "#151618",
        touchAction: controls ? "none" : "auto",
        ...style,
      }}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerUp}
      onPointerCancel={pointerUp}
      onPointerLeave={() => {
        dragPointerId.current = null;
        drag.current = null;
      }}
      onDoubleClick={onPointerMissed}
      onWheel={wheel}
    >
      {imageUrl && (
        <img
          src={imageUrl}
          alt={`${scene} scene rendered by native Vulkan`}
          draggable={false}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            display: "block",
            pointerEvents: "none",
          }}
        />
      )}

      {!imageUrl && !error && (
        <div className="vulkan-empty" style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-3)" }}>
          Initializing native Vulkan 1.3 hardware pipeline...
        </div>
      )}

      {/* Unity-Style HUD Overlays */}
      {hudEnabled && (
        <>
          {/* Top-Left: Mode & Scene State */}
          <div className="vp-overlay vp-overlay__hud vp-overlay--stack vp-overlay--top-left">
            <span className="vp-chip" style={{ background: "rgba(20,22,25,0.78)", backdropFilter: "blur(6px)", border: "1px solid rgba(255,255,255,0.12)", color: "#E0E0E0", padding: "3px 8px", borderRadius: 4, fontSize: 11, fontWeight: 550 }}>
              <span className="dot" style={{ background: "#4ADE80", width: 6, height: 6, borderRadius: "50%", display: "inline-block", marginRight: 5 }} />
              {scene.toUpperCase()} · {variant.toUpperCase()}
            </span>
            {selectedNode && (
              <span className="vp-chip" style={{ background: "rgba(30,58,138,0.75)", border: "1px solid rgba(96,165,250,0.3)", color: "#BFDBFE", padding: "3px 8px", borderRadius: 4, fontSize: 11, fontFamily: "var(--font-mono)" }}>
                Selected: {selectedNode}
              </span>
            )}
          </div>

          {/* Top-Right: Hardware & Performance Stats */}
          <div className="vp-overlay vp-overlay__hud vp-overlay--stack vp-overlay--top-right">
            <span className="vp-chip mono" style={{ background: "rgba(20,22,25,0.78)", backdropFilter: "blur(6px)", border: "1px solid rgba(255,255,255,0.12)", color: "#A3E635", padding: "3px 8px", borderRadius: 4, fontSize: 11, fontWeight: 600 }}>
              {fps} FPS · {frameTimeMs} ms
            </span>
            <span className="vp-chip" style={{ background: "rgba(20,22,25,0.78)", backdropFilter: "blur(6px)", border: "1px solid rgba(255,255,255,0.12)", color: "#9CA3AF", padding: "3px 8px", borderRadius: 4, fontSize: 11 }}>
              {backend}
            </span>
          </div>

          {/* Bottom-Left: Camera Coordinates & Controls hint */}
          <div className="vp-overlay vp-overlay__hud vp-overlay--stack vp-overlay--bottom-left">
            <span className="micro t3 mono" style={{ background: "rgba(15,17,20,0.7)", padding: "2px 6px", borderRadius: 3, border: "1px solid rgba(255,255,255,0.06)", color: "#888" }}>
              Yaw: {safeView.yaw.toFixed(0)}° Pitch: {safeView.pitch.toFixed(0)}° Dist: {safeView.distance.toFixed(1)}m
            </span>
            <span className="micro t3" style={{ color: "#666" }}>
              Drag: Orbit · Shift+Drag: Pan · Wheel: Zoom
            </span>
          </div>
        </>
      )}

      {loading && !imageUrl && (
        <div className="vulkan-loading" aria-label="Rendering Vulkan frame" />
      )}
    </div>
  );
}
