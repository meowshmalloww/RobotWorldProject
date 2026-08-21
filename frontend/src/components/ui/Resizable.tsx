import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Drag handle between docked panels. `onDelta` receives the horizontal
 * (or vertical) pixel delta from drag start; the parent owns the size state.
 *
 * Move events are consumed once at the window level. Attaching a React
 * onPointerMove as well double-fired every delta (panels resized at 2x the
 * cursor speed and felt buggy), so the element itself only owns capture.
 */
export function ResizeHandle({
  dir,
  onDrag,
  onReset,
}: {
  dir: "col" | "row";
  onDrag: (delta: number) => void;
  onReset?: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const start = useRef(0);
  const active = useRef(false);
  const pointerId = useRef<number | null>(null);
  const handleRef = useRef<HTMLDivElement | null>(null);

  const stopDrag = useCallback(
    () => {
      if (!active.current) return;

      if (pointerId.current !== null && handleRef.current?.hasPointerCapture(pointerId.current)) {
        try {
          handleRef.current.releasePointerCapture(pointerId.current);
        } catch {
          // Ignore capture failures to avoid pointer state edge-case crashes.
        }
      }

      pointerId.current = null;
      active.current = false;
      setDragging(false);
      document.body.classList.remove("col-resizing", "row-resizing");
    },
    [],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Ensure no stale drag state if another input started while this one did not close cleanly.
      stopDrag();

      e.preventDefault();
      pointerId.current = e.pointerId;
      active.current = true;
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
        // Some platforms reject capture for this pointer type; continue resizing via move events.
      }
      start.current = dir === "col" ? e.clientX : e.clientY;
      setDragging(true);
      document.body.classList.add(dir === "col" ? "col-resizing" : "row-resizing");
    },
    [dir, stopDrag],
  );

  const onPointerUp = useCallback(() => stopDrag(), [stopDrag]);

  const onPointerLeave = useCallback(() => {
    // Pointer capture keeps move events targeting this element even outside
    // its bounds; only drop the drag when capture was never granted.
    if (pointerId.current === null) stopDrag();
  }, [stopDrag]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      if (!active.current) return;
      if (pointerId.current !== null && e.pointerId !== pointerId.current) return;
      const pos = dir === "col" ? e.clientX : e.clientY;
      try {
        onDrag(pos - start.current);
      } catch (error) {
        console.error("Resize drag handler failed:", error);
        stopDrag();
        return;
      }
      start.current = pos;
    };
    const onUp = () => stopDrag();
    const onCancel = () => stopDrag();
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("blur", onCancel);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("blur", onCancel);
      if (active.current) {
        stopDrag();
      }
    };
  }, [dir, dragging, onDrag, stopDrag]);

  const onPointerCancel = useCallback(() => {
    stopDrag();
  }, [stopDrag]);

  return (
    <div
      ref={handleRef}
      className={`resize-${dir === "col" ? "v" : "h"} ${dragging ? "dragging" : ""}`}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onPointerLeave={onPointerLeave}
      onDoubleClick={onReset}
      role="separator"
      aria-orientation={dir === "col" ? "vertical" : "horizontal"}
      title={onReset ? "Drag to resize · double-click to reset" : "Drag to resize"}
      style={{ touchAction: "none" }}
    />
  );
}

/** Collapse a docked panel to a slim labelled rail. */
export function PanelRail({
  label,
  side,
  onExpand,
}: {
  label: string;
  side: "left" | "right" | "bottom";
  onExpand: () => void;
}) {
  return (
    <button
      className="panel-rail"
      onClick={onExpand}
      title={`Expand ${label}`}
      style={side === "bottom" ? { width: "auto", height: 26, flexDirection: "row", writingMode: "horizontal-tb" } : undefined}
    >
      <span className="rail-label" style={side === "bottom" ? { writingMode: "horizontal-tb" } : undefined}>{label}</span>
    </button>
  );
}

/**
 * Panel width/height state with clamp. Sizes are additionally bounded against
 * the current viewport (55% of the window on the panel's axis) so a persisted
 * large panel can never push the viewport or neighbouring panels off-screen.
 */
export function usePanelSize(initial: number, min: number, max: number, storageKey?: string, axis: "col" | "row" = "col") {
  const viewportMax = useCallback(
    (requested: number) => {
      const limit = axis === "col" ? Math.floor(window.innerWidth * 0.55) : Math.floor(window.innerHeight * 0.55);
      return Math.max(min, Math.min(max, limit, requested));
    },
    [axis, max, min],
  );
  const [size, setSize] = useState(() => {
    if (!storageKey) return Math.min(initial, viewportMax(max));
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? viewportMax(stored) : Math.min(initial, viewportMax(max));
  });
  const apply = useCallback(
    (next: number | ((prev: number) => number)) =>
      setSize((prev) => {
        const value = typeof next === "function" ? next(prev) : next;
        return viewportMax(value);
      }),
    [viewportMax],
  );
  useEffect(() => {
    if (storageKey) window.localStorage.setItem(storageKey, String(size));
  }, [size, storageKey]);
  useEffect(() => {
    const onResize = () => setSize((prev) => viewportMax(prev));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [viewportMax]);
  return [size, apply] as const;
}
