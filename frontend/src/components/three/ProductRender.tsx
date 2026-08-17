import * as THREE from "three";

/**
 * Procedural product photo stand-in: draws a refrigerator product shot onto
 * a canvas — front view, angled view, open-door view depending on seed.
 * Used inside Source photo candidates (real rasterized renders, no hotlinks).
 */
export function drawFridgePhoto(canvas: HTMLCanvasElement, seed: number, view: "front" | "angle" | "open" | "kitchen") {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width, h = canvas.height;

  // studio backdrop
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  if (view === "kitchen") {
    bg.addColorStop(0, "#3A3F47");
    bg.addColorStop(0.7, "#2C3036");
    bg.addColorStop(1, "#22252A");
  } else {
    bg.addColorStop(0, "#4A5058");
    bg.addColorStop(0.75, "#34383F");
    bg.addColorStop(1, "#26292E");
  }
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  // floor line
  ctx.fillStyle = "rgba(0,0,0,0.25)";
  ctx.fillRect(0, h * 0.82, w, h * 0.18);

  const cx = w / 2;
  const fw = view === "angle" ? w * 0.3 : w * 0.34; // fridge width
  const fh = h * 0.62;
  const fy = h * 0.82 - fh;

  // shadow
  ctx.fillStyle = "rgba(0,0,0,0.4)";
  ctx.beginPath();
  ctx.ellipse(cx, h * 0.83, fw * 0.7, h * 0.03, 0, 0, Math.PI * 2);
  ctx.fill();

  const steel = ctx.createLinearGradient(cx - fw, 0, cx + fw, 0);
  steel.addColorStop(0, "#6E747D");
  steel.addColorStop(0.45, "#B9BFC7");
  steel.addColorStop(0.55, "#C8CED6");
  steel.addColorStop(1, "#5F656E");

  if (view === "open") {
    // carcass with open doors
    ctx.fillStyle = "#1C1F24";
    ctx.fillRect(cx - fw * 0.42, fy, fw * 0.84, fh); // dark interior
    ctx.fillStyle = "#DEE6EC";
    ctx.fillRect(cx - fw * 0.4, fy + fh * 0.02, fw * 0.8, fh * 0.06); // light strip
    // shelves
    ctx.fillStyle = "rgba(222,230,236,0.35)";
    for (let i = 1; i <= 3; i++) ctx.fillRect(cx - fw * 0.38, fy + (fh * 0.62 * i) / 3.4, fw * 0.76, 2);
    // doors swung out
    ctx.fillStyle = steel;
    ctx.save();
    ctx.transform(1, 0.06, -0.35, 1, 0, 0);
    ctx.fillRect(cx - fw * 1.05, fy + 2, fw * 0.4, fh - 4);
    ctx.restore();
    ctx.save();
    ctx.transform(1, -0.06, 0.35, 1, 0, 0);
    ctx.fillRect(cx + fw * 0.66, fy + 2, fw * 0.4, fh - 4);
    ctx.restore();
  } else if (view === "angle") {
    // perspective-ish body
    ctx.fillStyle = steel;
    ctx.beginPath();
    ctx.moveTo(cx - fw * 0.5, fy + 6);
    ctx.lineTo(cx + fw * 0.5, fy);
    ctx.lineTo(cx + fw * 0.5, fy + fh);
    ctx.lineTo(cx - fw * 0.5, fy + fh - 8);
    ctx.closePath();
    ctx.fill();
    // side face
    ctx.fillStyle = "#565C64";
    ctx.beginPath();
    ctx.moveTo(cx + fw * 0.5, fy);
    ctx.lineTo(cx + fw * 0.78, fy + 10);
    ctx.lineTo(cx + fw * 0.78, fy + fh + 6);
    ctx.lineTo(cx + fw * 0.5, fy + fh);
    ctx.closePath();
    ctx.fill();
    // door split
    ctx.strokeStyle = "rgba(20,22,26,0.6)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(cx, fy + 3);
    ctx.lineTo(cx, fy + fh - 5);
    ctx.stroke();
  } else {
    // front view
    ctx.fillStyle = steel;
    roundRect(ctx, cx - fw / 2, fy, fw, fh, 3);
    ctx.fill();
    // french split + freezer line
    ctx.strokeStyle = "rgba(20,22,26,0.55)";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(cx, fy + 2);
    ctx.lineTo(cx, fy + fh * 0.66);
    ctx.moveTo(cx - fw / 2, fy + fh * 0.66);
    ctx.lineTo(cx + fw / 2, fy + fh * 0.66);
    ctx.stroke();
    // handles
    ctx.fillStyle = "#3E434A";
    roundRect(ctx, cx - fw * 0.09, fy + fh * 0.12, fw * 0.035, fh * 0.42, 2); ctx.fill();
    roundRect(ctx, cx + fw * 0.055, fy + fh * 0.12, fw * 0.035, fh * 0.42, 2); ctx.fill();
    // dispenser
    ctx.fillStyle = "rgba(18,20,24,0.75)";
    roundRect(ctx, cx - fw * 0.36, fy + fh * 0.2, fw * 0.16, fh * 0.24, 2); ctx.fill();
  }

  // caption grain (very subtle noise)
  const rnd = (i: number) => {
    const x = Math.sin(seed * 127.1 + i * 311.7) * 43758.5453;
    return x - Math.floor(x);
  };
  ctx.fillStyle = "rgba(255,255,255,0.02)";
  for (let i = 0; i < 60; i++) {
    ctx.fillRect(rnd(i) * w, rnd(i + 100) * h, 1.5, 1.5);
  }
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export function FridgePhoto({ seed, view, width = 128, height = 104 }: { seed: number; view: "front" | "angle" | "open" | "kitchen"; width?: number; height?: number }) {
  return (
    <canvas
      className="render-cell"
      width={width}
      height={height}
      ref={(el) => {
        if (el) drawFridgePhoto(el, seed, view);
      }}
    />
  );
}

/** Tiny box render used in artifact previews. */
export function drawMeshPreview(canvas: HTMLCanvasElement, seed: number) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = "#10141C";
  ctx.fillRect(0, 0, w, h);
  const g = new THREE.Group(); // (kept to show intent; drawing is 2D)
  void g;
  const rnd = (i: number) => {
    const x = Math.sin(seed * 12.9 + i * 78.2) * 43758.5;
    return x - Math.floor(x);
  };
  // wireframe-ish cabinet glyph
  ctx.strokeStyle = "#5E7089";
  ctx.lineWidth = 1;
  const bx = w * 0.24, by = h * 0.2, bw = w * 0.44, bh = h * 0.56, off = w * 0.1;
  ctx.strokeRect(bx, by, bw, bh);
  ctx.strokeRect(bx + off, by - off * 0.6, bw, bh);
  ctx.beginPath();
  ctx.moveTo(bx, by); ctx.lineTo(bx + off, by - off * 0.6);
  ctx.moveTo(bx + bw, by); ctx.lineTo(bx + bw + off, by - off * 0.6);
  ctx.moveTo(bx, by + bh); ctx.lineTo(bx + off, by + bh - off * 0.6);
  ctx.moveTo(bx + bw, by + bh); ctx.lineTo(bx + bw + off, by + bh - off * 0.6);
  ctx.moveTo(bx + bw / 2, by); ctx.lineTo(bx + bw / 2, by + bh);
  ctx.stroke();
  ctx.fillStyle = "rgba(76,141,255,0.12)";
  ctx.fillRect(bx, by, bw, bh);
  void rnd;
}
