# RobotWorld production acceptance

Updated 2026-08-18. This is an evidence gate, not a marketing checklist.

| Gate | Required evidence | Current state |
|---|---|---|
| Desktop/API | owned FastAPI sidecar; `/api/health`; no proxy 502 | packaged API/UI smoke passed |
| Native scene renderer | hardware adapter; backend exactly `Vulkan`; PNG frame; browser 3D API `none` | passed on NVIDIA RTX 4080 Laptop GPU |
| Kitchen world | fresh seed; manipulable fruit/cup; cabinet/lid/switch joints; open vessels; finite MuJoCo rollout; hashes | passed environment gates; robot task blocked until compatible VLA adapter |
| Logistics world | fresh seed; randomized parcel size/mass/route/pose; open truck bays; finite MuJoCo rollout; hashes | passed environment gates; robot task blocked until compatible VLA adapter |
| SERP discovery | write-only key; one paid request; parsed organic rows | passed previously; rotate exposed key before public demo |
| Scraper Studio | real `c_*` collector rows; required-field score; bounded raw evidence | implementation complete; live collector ID pending |
| Scraper repair | approval state; human approval; rerun validates | implementation complete; live billable job pending |
| Asset compiler | provenance → GLB + OpenUSD + MJCF load/rollout | source tests passed; packaged sidecar imports verified |
| TRELLIS.2 | pinned real model → PBR GLB → validation → separate physical compile | gateway present; live endpoint pending; not used for articulation |
| VLA protocol | pinned model/embodiment hashes; declared RGB/state/action contract; fail-closed transport/safety | door-task contract tests pass |
| VLA acceptance adapter | selected checkpoint and robot can execute kitchen/logistics schemas | pending user model/robot selection |
| VLA closed loop | no oracle/manual grasp/fallback; held-out episodes; measured predicates | pending compatible model endpoint |
| SigNoz Community | local Foundry stack; OTLP trace/metric/log; correlated local query | integration complete; WSL2 Ubuntu/native Docker installation pending |
| Training | no local training or implied improvement | disabled by API and UI |
| Public submission | public repo; structured evidence; disclosure; 3–5 minute video | user-owned submission work pending |

## Verified Windows artifact

- Installer: `frontend/release/RobotWorld Setup 1.0.0.exe`
- Size: `209,292,346` bytes
- SHA-256: `F3552212174C5C9859AC4E9DBBB5074658A9152AADA3588E89D2E17BD41FBEC1`
- Packaged smoke: healthy API/SQLite, production HTML and hashed JavaScript
  asset served, hardware Vulkan frame rendered, and both randomized acceptance
  worlds compiled/persisted before the learned-policy gate blocked honestly.
- Disabled local Torch training code is absent from the packaged sidecar.
- Signing: Authenticode is not configured; Windows reports `NotSigned`. Obtain a
  trusted code-signing certificate before public distribution to avoid an
  unsigned-publisher warning.

## Bright Data source schema

A custom collector row must include verifiable values for the fields the
physical compiler uses:

```json
{
  "product_name": "...",
  "manufacturer": "...",
  "model_number": "...",
  "width_cm": 0,
  "height_cm": 0,
  "depth_cm": 0,
  "mass_kg": 0,
  "image_url": "https://...",
  "source_url": "https://..."
}
```

SERP is supplementary discovery. It is not a custom collector and does not
prove physical dimensions, part structure, or articulation. A failed or
incomplete source remains quarantined.

## Learned-policy gate

`asset_validation`, `environment_validation`, and `policy_evaluation` are
separate result types.

- Asset validation may use privileged geometry and a disclosed scripted oracle
  to answer whether a compiled object is physically solvable.
- Environment validation compiles randomized task worlds and tests their
  bodies, joints, containers, stability, and evidence hashes.
- Policy evaluation receives only its declared cameras, proprioception, and
  instruction. It cannot see evaluator-only target/state predicates.
- Every timeout, transport failure, stale response, model/embodiment/hash
  mismatch, invalid shape, non-finite output, unsafe action, or joint-limit
  breach fails the episode.
- A robot-task pass requires held-out closed-loop episodes, physics-derived
  terminal predicates held for the specified duration, and zero hard safety
  violations.

## Interactable-part release gate

A visual mesh is not an articulated asset. Every released interactable object
must include:

- evidence-linked semantic parts;
- separate visual and collision geometry where required;
- mass and inertia provenance or an explicit reviewed estimate;
- named joint type, axis, origin, range, damping, and friction;
- attachment/grasp frames and forbidden-contact rules;
- MJCF/OpenUSD compile, penetration, stability, articulation-range, and contact
  test evidence;
- a human review record for ambiguous or inferred parts.

TRELLIS.2 may supply visual geometry, but it does not satisfy this gate by
itself.

## Final live test sequence

1. Run repository tests and the native Vulkan probe.
2. Run both acceptance buttons on new seeds; retain manifests and MJCF hashes.
3. Rotate the exposed Bright Data key; run one paid SERP probe and one custom
   collector.
4. Build and validate one provenance-complete articulated asset.
5. Install self-hosted SigNoz, restart RobotWorld, and retain a correlated local
   trace.
6. Select a VLA and robot embodiment; implement and verify the exact task
   adapter.
7. Run held-out kitchen and logistics episodes with no fallback.
8. Record the successful predicates and all failures honestly. Do not label any
   pending external gate as passed.
