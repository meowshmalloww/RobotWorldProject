# RobotWorld model gateways

These services run beside the real GPU model environment; they are not part of
the Windows installer and contain no mock or fallback inference path.

## Optional GR00T reference adapter (not selected)

RobotWorld does not select or install GR00T by default. Keep this adapter only
as a reference if that exact checkpoint is chosen later; another VLA requires
its own native gateway and exact observation/action mapping.

1. Install and pin NVIDIA Isaac-GR00T on a supported Linux/NVIDIA host only if
   it is the explicitly selected checkpoint.
2. Fine-tune a `NEW_EMBODIMENT` checkpoint for `robotworld-4dof-v1`. The base
   checkpoint is not accepted as compatible with this custom arm.
3. Start NVIDIA's native server:

   ```bash
   uv run python gr00t/eval/run_gr00t_server.py \
     --embodiment-tag NEW_EMBODIMENT \
     --model-path /checkpoints/robotworld-groot \
     --device cuda:0 --host 127.0.0.1 --port 5555 --strict
   ```

4. Set the checkpoint revision and SHA-256 values, native modality/action key
   mappings, an API token, and the frozen RobotWorld environment hash. Then run
   `groot_gateway.py` behind an authenticated TLS/VPN path. The gateway refuses
   to declare compatibility until the embodiment flag, revision, and hashes are
   all populated.

RobotWorld first calls `/v1/capabilities` and `/v1/reset`, then sends actual
MuJoCo front/wrist RGB frames plus the five declared proprioceptive values to
`/v1/actions`. Any timeout, shape mismatch, stale response, non-finite value, or
safety-envelope violation fails the episode. There is no scripted fallback.

## TRELLIS.2 4B

Install Microsoft's official TRELLIS.2 repository and dependencies on Linux,
then run `trellis2_gateway.py`. It loads `microsoft/TRELLIS.2-4B` once, executes
the published `Trellis2ImageTo3DPipeline`, and exports the real PBR GLB. Configure
`ROBOTWORLD_GATEWAY_TOKEN` before exposing it beyond loopback.

TRELLIS.2 returns a static visual mesh. RobotWorld separately authors and tests
part semantics, collision proxies, mass, hinge articulation, joint limits, and
OpenUSD/MuJoCo physics.
