import { useEffect, useRef, useState } from "react";
import { useThree, useFrame } from "@react-three/fiber";
import * as THREE from "three";

/**
 * First-person fly camera — WASD to move, mouse-drag to look.
 * Shift = boost. Scroll = adjust speed.
 * Like Unity's fly camera or Unreal's spectator pawn.
 */
export function FlyControls({ enabled, speed = 4 }: { enabled: boolean; speed?: number }) {
  const { camera, gl } = useThree();
  const keys = useRef<Record<string, boolean>>({});
  const velocity = useRef(new THREE.Vector3());
  const yaw = useRef(0);
  const pitch = useRef(0);
  const [dragging, setDragging] = useState(false);
  const lastMouse = useRef({ x: 0, y: 0 });
  const boost = useRef(false);
  const speedRef = useRef(speed);

  useEffect(() => {
    if (!enabled) return;
    const el = gl.domElement;

    const onKeyDown = (e: KeyboardEvent) => {
      keys.current[e.code] = true;
      if (e.code === "ShiftLeft" || e.code === "ShiftRight") boost.current = true;
    };
    const onKeyUp = (e: KeyboardEvent) => {
      keys.current[e.code] = false;
      if (e.code === "ShiftLeft" || e.code === "ShiftRight") boost.current = false;
    };
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      setDragging(true);
      lastMouse.current = { x: e.clientX, y: e.clientY };
      el.style.cursor = "grabbing";
    };
    const onMouseUp = () => {
      setDragging(false);
      el.style.cursor = "grab";
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging) return;
      const dx = e.clientX - lastMouse.current.x;
      const dy = e.clientY - lastMouse.current.y;
      lastMouse.current = { x: e.clientX, y: e.clientY };
      yaw.current -= dx * 0.0035;
      pitch.current -= dy * 0.0035;
      const limit = Math.PI / 2 - 0.05;
      pitch.current = Math.max(-limit, Math.min(limit, pitch.current));
    };
    const onWheel = (e: WheelEvent) => {
      const delta = e.deltaY > 0 ? -0.5 : 0.5;
      speedRef.current = Math.max(1, Math.min(20, speedRef.current + delta));
    };

    el.style.cursor = "grab";
    el.tabIndex = 0;
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    el.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("mousemove", onMouseMove);
    el.addEventListener("wheel", onWheel, { passive: true });

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      el.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mouseup", onMouseUp);
      window.removeEventListener("mousemove", onMouseMove);
      el.removeEventListener("wheel", onWheel);
      el.style.cursor = "";
    };
  }, [enabled, dragging, gl]);

  useFrame((_, dt) => {
    if (!enabled) return;
    const s = speedRef.current * (boost.current ? 2.5 : 1) * dt;

    const quat = new THREE.Quaternion();
    quat.setFromEuler(new THREE.Euler(pitch.current, yaw.current, 0, "YXZ"));
    camera.quaternion.copy(quat);

    const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    const up = new THREE.Vector3(0, 1, 0);

    velocity.current.set(0, 0, 0);
    if (keys.current["KeyW"]) velocity.current.add(forward);
    if (keys.current["KeyS"]) velocity.current.sub(forward);
    if (keys.current["KeyD"]) velocity.current.add(right);
    if (keys.current["KeyA"]) velocity.current.sub(right);
    if (keys.current["KeyE"]) velocity.current.add(up);
    if (keys.current["KeyQ"]) velocity.current.sub(up);

    if (velocity.current.lengthSq() > 0) {
      velocity.current.normalize().multiplyScalar(s);
      camera.position.add(velocity.current);
    }
  });

  return null;
}
