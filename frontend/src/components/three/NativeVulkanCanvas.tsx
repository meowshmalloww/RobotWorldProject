import { useEffect, useRef, useState } from "react";

type View = { yaw: number; pitch: number; zoom: number };

export interface NativeVulkanCanvasProps {
  assetId?: string;
  framePath?: string;
  label: string;
  className?: string;
  style?: React.CSSProperties;
  onFrame?: (metrics: { fps: number; latencyMs: number }) => void;
}

/** Presents real backend Vulkan frames without an HTML image drag surface. */
export function NativeVulkanCanvas({ assetId, framePath, label, className, style, onFrame }: NativeVulkanCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mounted = useRef(true);
  const view = useRef<View>({ yaw: 34, pitch: 18, zoom: 1 });
  const drag = useRef<{ pointerId: number; x: number; y: number; yaw: number; pitch: number } | null>(null);
  const inFlight = useRef(false);
  const queued = useRef(false);
  const queuedHighQuality = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const drawFrameRef = useRef<(highQuality?: boolean) => void>(() => undefined);
  const frameCount = useRef(0);
  const fpsWindowAt = useRef(performance.now());
  const fpsRef = useRef(0);
  const onFrameRef = useRef(onFrame);
  const [dragging, setDragging] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState({ fps: 0, latencyMs: 0 });
  const origin = ((import.meta.env.VITE_API_ORIGIN as string | undefined) ?? "").replace(/\/$/, "");
  const renderPath = framePath ?? (assetId ? `/api/assets/${encodeURIComponent(assetId)}/render/vulkan` : "");

  useEffect(() => { onFrameRef.current = onFrame; }, [onFrame]);

  useEffect(() => {
    mounted.current = true;

    const drawFrame = async (highQuality = false) => {
      if (!mounted.current) return;
      if (inFlight.current) {
        queued.current = true;
        queuedHighQuality.current ||= highQuality;
        return;
      }
      const host = hostRef.current;
      const canvas = canvasRef.current;
      if (!host || !canvas) return;

      inFlight.current = true;
      const startedAt = performance.now();
      const rect = host.getBoundingClientRect();
      const scale = highQuality ? 1 : 0.58;
      const capWidth = highQuality ? 1280 : 720;
      const width = Math.max(320, Math.min(capWidth, Math.round(rect.width * scale)));
      const height = Math.max(180, Math.round(width * Math.max(0.4, rect.height / Math.max(rect.width, 1))));
      const pose = view.current;
      const params = new URLSearchParams({
        width: String(width), height: String(height),
        yaw: pose.yaw.toFixed(2), pitch: pose.pitch.toFixed(2), zoom: pose.zoom.toFixed(3),
      });
      const nextController = new AbortController();
      controller.current = nextController;

      try {
        if (!renderPath) throw new Error("No Vulkan render target was supplied");
        const response = await fetch(`${origin}${renderPath}?${params}`, {
          signal: nextController.signal,
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`Vulkan renderer returned HTTP ${response.status}`);
        const bitmap = await createImageBitmap(await response.blob());
        if (!mounted.current) { bitmap.close(); return; }
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        const context = canvas.getContext("2d", { alpha: false });
        if (!context) throw new Error("Canvas presentation context is unavailable");
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        bitmap.close();

        const now = performance.now();
        if (drag.current === null) {
          // A single settled render is not a frame stream. Reporting it as
          // "1 FPS" made an idle viewport look slow even when latency was low.
          frameCount.current = 0;
          fpsWindowAt.current = now;
          fpsRef.current = 0;
        } else {
          frameCount.current += 1;
          const windowMs = now - fpsWindowAt.current;
          if (windowMs >= 500) {
            fpsRef.current = Math.max(1, Math.round((frameCount.current * 1000) / windowMs));
            frameCount.current = 0;
            fpsWindowAt.current = now;
          }
        }
        const nextMetrics = { fps: fpsRef.current, latencyMs: Math.round(now - startedAt) };
        setMetrics(nextMetrics);
        setReady(true);
        setError(null);
        onFrameRef.current?.(nextMetrics);
      } catch (reason) {
        if (!(reason instanceof DOMException && reason.name === "AbortError") && mounted.current) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        inFlight.current = false;
        controller.current = null;
        if (queued.current && mounted.current) {
          const nextHighQuality = queuedHighQuality.current;
          queued.current = false;
          queuedHighQuality.current = false;
          window.requestAnimationFrame(() => drawFrame(nextHighQuality));
        }
      }
    };

    drawFrameRef.current = drawFrame;
    drawFrame(true);
    const observer = new ResizeObserver(() => drawFrame(true));
    if (hostRef.current) observer.observe(hostRef.current);
    return () => {
      mounted.current = false;
      observer.disconnect();
      controller.current?.abort();
    };
  }, [origin, renderPath]);

  const queuePose = (next: View, highQuality = false) => {
    view.current = next;
    if (inFlight.current) {
      queued.current = true;
      queuedHighQuality.current ||= highQuality;
      return;
    }
    drawFrameRef.current(highQuality);
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
    setDragging(false);
    queuePose(view.current, true);
  };

  return (
    <div
      ref={hostRef}
      className={className}
      aria-label={label}
      role="application"
      style={{ position: "relative", overflow: "hidden", background: "#161616", cursor: dragging ? "grabbing" : "grab", touchAction: "none", userSelect: "none", ...style }}
      onDragStart={(event) => event.preventDefault()}
      onContextMenu={(event) => event.preventDefault()}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, yaw: view.current.yaw, pitch: view.current.pitch };
        setDragging(true);
      }}
      onPointerMove={(event) => {
        const active = drag.current;
        if (!active || active.pointerId !== event.pointerId) return;
        event.preventDefault();
        queuePose({
          yaw: active.yaw - (event.clientX - active.x) * 0.35,
          pitch: Math.max(-35, Math.min(70, active.pitch + (event.clientY - active.y) * 0.2)),
          zoom: view.current.zoom,
        });
      }}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={() => { drag.current = null; setDragging(false); }}
      onWheel={(event) => {
        event.preventDefault();
        queuePose({ ...view.current, zoom: Math.max(0.55, Math.min(2.6, view.current.zoom + event.deltaY * 0.0015)) }, true);
      }}
    >
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block", pointerEvents: "none" }} />
      {!ready && !error && <div className="vp-render-state">Waiting for first Vulkan frame</div>}
      {error && <div className="vp-render-state vp-render-state--error">{error}</div>}
      <div className="vp-overlay vp-overlay__hud" style={{ left: 10, right: 10, bottom: 10, top: "auto", display: "flex", flexWrap: "wrap", gap: 6, pointerEvents: "none" }}>
        <span className="vp-chip">{label}</span>
        <span className="vp-chip">drag to orbit · wheel to zoom</span>
        <span className="vp-chip">{metrics.fps > 0 ? `${metrics.fps} FPS` : "idle"}{metrics.latencyMs > 0 ? ` · ${metrics.latencyMs} ms` : ""}</span>
      </div>
    </div>
  );
}
