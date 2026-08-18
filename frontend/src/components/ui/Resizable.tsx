import { useCallback, useRef, useState } from "react";

/**
 * Drag handle between docked panels. `onDelta` receives the horizontal
 * (or vertical) pixel delta from drag start; the parent owns the size state.
 */
export function ResizeHandle({
  dir,
  onDrag,
}: {
  dir: "col" | "row";
  onDrag: (delta: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const start = useRef(0);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      start.current = dir === "col" ? e.clientX : e.clientY;
      setDragging(true);
      document.body.classList.add(dir === "col" ? "col-resizing" : "row-resizing");
    },
    [dir],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      const pos = dir === "col" ? e.clientX : e.clientY;
      onDrag(pos - start.current);
      start.current = pos;
    },
    [dragging, dir, onDrag],
  );

  const stop = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
      setDragging(false);
      document.body.classList.remove("col-resizing", "row-resizing");
    },
    [dragging],
  );

  return (
    <div
      className={`resize-${dir === "col" ? "v" : "h"} ${dragging ? "dragging" : ""}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      role="separator"
      aria-orientation={dir === "col" ? "vertical" : "horizontal"}
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

/** Panel width state with clamp. */
export function usePanelSize(initial: number, min: number, max: number) {
  const [size, setSize] = useState(initial);
  const apply = useCallback(
    (next: number) => setSize(Math.max(min, Math.min(max, next))),
    [min, max],
  );
  return [size, apply] as const;
}
