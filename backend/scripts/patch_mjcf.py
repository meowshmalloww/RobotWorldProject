"""One-off patch: add class="robot" to robot geoms in mjcf.py."""
import os
import sys

p = os.path.join(os.path.dirname(__file__), "..", "app", "services", "mjcf.py")
s = open(p, encoding="utf8").read()
names = [
    "robot_base", "robot_mast", "shoulder_housing", "upper_arm", "elbow_housing",
    "forearm", "wrist_housing", "wrist_stub", "palm", "finger_l_pad", "finger_r_pad",
    "finger_l_hook_a", "finger_l_hook_b", "finger_r_hook_a", "finger_r_hook_b",
]
count = 0
for n in names:
    old = f'<geom name="{n}"'
    new = f'<geom name="{n}" class="robot"'
    if old in s and new not in s:
        s = s.replace(old, new)
        count += 1
open(p, "w", encoding="utf8").write(s)
print(f"patched {count} geoms")
