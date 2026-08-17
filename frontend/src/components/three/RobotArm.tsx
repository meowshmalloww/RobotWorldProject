import * as THREE from "three";
import { useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import { M, variantMat, type RenderVariant } from "./materials";

/**
 * Mobile manipulator — rounded base + mast + 3-DOF arm + parallel gripper.
 *
 * Two drive modes:
 *  - static:  `pose` prop applied once
 *  - live:    `poseRef` sampled every frame via useFrame (zero React renders)
 *
 * Forward kinematics are computed analytically (fk) so cameras/tools can
 * share the same truth as the rendered joints.
 */

export interface ArmPose {
  yaw: number;      // base yaw (rad)
  shoulder: number; // pitch at shoulder (rad, 0 = straight up)
  elbow: number;    // relative pitch at elbow
  wrist: number;    // relative pitch at wrist
  grip: number;     // 0 open .. 1 closed
  door: number;     // coupled fridge door 0..1 (scripted contact)
}

export const ARM_DIMS = {
  mastH: 0.78,
  upperLen: 0.44,
  foreLen: 0.38,
  wristLen: 0.16,
};

/** Scripted evaluation timeline: approach → grasp → pull → release → retract. */
const K: [number, ArmPose][] = [
  [0.0, { yaw: 0.62, shoulder: 0.55, elbow: -1.5, wrist: 0.7, grip: 0, door: 0 }],
  [1.4, { yaw: 0.32, shoulder: 0.92, elbow: -0.9, wrist: 0.35, grip: 0, door: 0 }],
  [2.4, { yaw: 0.24, shoulder: 1.08, elbow: -0.62, wrist: 0.1, grip: 0.05, door: 0 }],
  [3.2, { yaw: 0.24, shoulder: 1.1, elbow: -0.6, wrist: 0.08, grip: 1, door: 0.02 }],
  [5.2, { yaw: 0.36, shoulder: 0.92, elbow: -0.85, wrist: 0.3, grip: 1, door: 0.45 }],
  [7.0, { yaw: 0.52, shoulder: 0.7, elbow: -1.15, wrist: 0.55, grip: 1, door: 1 }],
  [8.2, { yaw: 0.55, shoulder: 0.68, elbow: -1.2, wrist: 0.6, grip: 0, door: 1 }],
  [10.0, { yaw: 0.7, shoulder: 0.5, elbow: -1.55, wrist: 0.75, grip: 0, door: 1 }],
  [12.0, { yaw: 0.62, shoulder: 0.55, elbow: -1.5, wrist: 0.7, grip: 0, door: 1 }],
];

const smooth = (t: number) => t * t * (3 - 2 * t);

export function samplePose(t: number): ArmPose {
  if (t <= K[0][0]) return { ...K[0][1] };
  for (let i = 1; i < K.length; i++) {
    if (t <= K[i][0]) {
      const [t0, a] = K[i - 1];
      const [t1, b] = K[i];
      const k = smooth((t - t0) / (t1 - t0));
      const lerp = (x: number, y: number) => x + (y - x) * k;
      return {
        yaw: lerp(a.yaw, b.yaw),
        shoulder: lerp(a.shoulder, b.shoulder),
        elbow: lerp(a.elbow, b.elbow),
        wrist: lerp(a.wrist, b.wrist),
        grip: lerp(a.grip, b.grip),
        door: lerp(a.door, b.door),
      };
    }
  }
  return { ...K[K.length - 1][1] };
}

/** FK: gripper tip world position + orientation. */
export function fk(pose: ArmPose, basePos: THREE.Vector3): { pos: THREE.Vector3; quat: THREE.Quaternion } {
  const { mastH, upperLen, foreLen, wristLen } = ARM_DIMS;
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(0, pose.yaw, 0));
  const pitch = (q0: THREE.Quaternion, a: number) =>
    q0.clone().multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, a)));

  const shoulderQ = pitch(q, -pose.shoulder);
  const elbowQ = pitch(shoulderQ, -pose.elbow);
  const wristQ = pitch(elbowQ, -pose.wrist);

  const up = new THREE.Vector3(0, 1, 0);
  const pShoulder = basePos.clone().add(new THREE.Vector3(0, mastH, 0));
  const pElbow = pShoulder.clone().add(up.clone().applyQuaternion(shoulderQ).multiplyScalar(upperLen));
  const pWrist = pElbow.clone().add(up.clone().applyQuaternion(elbowQ).multiplyScalar(foreLen));
  const pTip = pWrist.clone().add(up.clone().applyQuaternion(wristQ).multiplyScalar(wristLen));
  return { pos: pTip, quat: wristQ };
}

const gripX = (g: number) => 0.035 - g * 0.02;

export function RobotArm({
  pose,
  poseRef,
  position = [0, 0, 0] as [number, number, number],
  variant = "rgb",
}: {
  pose?: ArmPose;
  poseRef?: MutableRefObject<ArmPose>;
  position?: [number, number, number];
  variant?: RenderVariant;
}) {
  const white = variantMat(variant, M.robotWhite(), 60);
  const joint = variantMat(variant, M.robotJoint(), 61);
  const baseM = variantMat(variant, M.robotBase(), 62);
  const rubber = variantMat(variant, M.rubber(), 63);

  const { mastH, upperLen, foreLen, wristLen } = ARM_DIMS;

  const yawG = useRef<THREE.Group>(null);
  const shoulderG = useRef<THREE.Group>(null);
  const elbowG = useRef<THREE.Group>(null);
  const wristG = useRef<THREE.Group>(null);
  const fingerL = useRef<THREE.Group>(null);
  const fingerR = useRef<THREE.Group>(null);

  useFrame(() => {
    const p = poseRef?.current ?? pose;
    if (!p) return;
    if (yawG.current) yawG.current.rotation.y = p.yaw;
    if (shoulderG.current) shoulderG.current.rotation.z = -p.shoulder;
    if (elbowG.current) elbowG.current.rotation.z = -p.elbow;
    if (wristG.current) wristG.current.rotation.z = -p.wrist;
    if (fingerL.current) fingerL.current.position.x = -gripX(p.grip);
    if (fingerR.current) fingerR.current.position.x = gripX(p.grip);
  });

  const p0 = pose ?? K[0][1];

  return (
    <group position={position}>
      {/* mobile base */}
      <mesh position={[0, 0.14, 0]} castShadow receiveShadow material={baseM}>
        <cylinderGeometry args={[0.24, 0.27, 0.28, 24]} />
      </mesh>
      <mesh position={[0, 0.30, 0]} castShadow material={joint}>
        <cylinderGeometry args={[0.16, 0.2, 0.08, 24]} />
      </mesh>
      {/* base accent ring */}
      <mesh position={[0, 0.065, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.255, 0.008, 8, 40]} />
        <meshStandardMaterial color="#3BBFC9" emissive="#3BBFC9" emissiveIntensity={1.6} />
      </mesh>
      {[[-0.16, 0.14], [0.16, 0.14], [-0.16, -0.14], [0.16, -0.14]].map(([x, z], i) => (
        <mesh key={i} position={[x, 0.045, z]} rotation={[Math.PI / 2, 0, 0]} material={rubber}>
          <cylinderGeometry args={[0.045, 0.045, 0.035, 12]} />
        </mesh>
      ))}
      {/* mast */}
      <mesh position={[0, mastH / 2 + 0.3, 0]} castShadow material={white}>
        <cylinderGeometry args={[0.075, 0.09, mastH - 0.1, 16]} />
      </mesh>

      {/* arm chain */}
      <group position={[0, mastH, 0]} rotation={[0, p0.yaw, 0]} ref={yawG}>
        {/* shoulder housing */}
        <mesh castShadow material={joint}>
          <sphereGeometry args={[0.085, 20, 16]} />
        </mesh>
        <group rotation={[0, 0, -p0.shoulder]} ref={shoulderG}>
          {/* upper arm */}
          <mesh position={[0, upperLen / 2, 0]} castShadow material={white}>
            <capsuleGeometry args={[0.055, upperLen - 0.1, 6, 14]} />
          </mesh>
          <group position={[0, upperLen, 0]} rotation={[0, 0, -p0.elbow]} ref={elbowG}>
            <mesh castShadow material={joint}>
              <sphereGeometry args={[0.07, 18, 14]} />
            </mesh>
            {/* forearm */}
            <mesh position={[0, foreLen / 2, 0]} castShadow material={white}>
              <capsuleGeometry args={[0.046, foreLen - 0.09, 6, 14]} />
            </mesh>
            <group position={[0, foreLen, 0]} rotation={[0, 0, -p0.wrist]} ref={wristG}>
              <mesh castShadow material={joint}>
                <sphereGeometry args={[0.052, 16, 12]} />
              </mesh>
              {/* wrist stub */}
              <mesh position={[0, wristLen / 2, 0]} castShadow material={white}>
                <capsuleGeometry args={[0.032, wristLen - 0.05, 4, 12]} />
              </mesh>
              {/* gripper */}
              <group position={[0, wristLen, 0]}>
                <mesh castShadow material={joint}>
                  <boxGeometry args={[0.09, 0.045, 0.07]} />
                </mesh>
                <group position={[-gripX(p0.grip), 0.055, 0]} ref={fingerL}>
                  <mesh castShadow material={joint}>
                    <boxGeometry args={[0.014, 0.075, 0.05]} />
                  </mesh>
                  <mesh position={[0, 0.095, 0]} castShadow material={rubber}>
                    <boxGeometry args={[0.014, 0.02, 0.05]} />
                  </mesh>
                </group>
                <group position={[gripX(p0.grip), 0.055, 0]} ref={fingerR}>
                  <mesh castShadow material={joint}>
                    <boxGeometry args={[0.014, 0.075, 0.05]} />
                  </mesh>
                  <mesh position={[0, 0.095, 0]} castShadow material={rubber}>
                    <boxGeometry args={[0.014, 0.02, 0.05]} />
                  </mesh>
                </group>
                {/* wrist camera eye */}
                <mesh position={[0, 0.02, 0.045]}>
                  <circleGeometry args={[0.012, 12]} />
                  <meshStandardMaterial color="#1B1D20" emissive="#3BBFC9" emissiveIntensity={1.2} />
                </mesh>
              </group>
            </group>
          </group>
        </group>
      </group>
    </group>
  );
}
