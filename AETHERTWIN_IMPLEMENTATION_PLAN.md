# AetherTwin approved execution plan

Approved by the user on 2026-10-28. Implementation goal is active. This supersedes
the earlier review-only addendum plan; the original SPEC.md remains verbatim.

## Fixed product decisions

- Jetson Orin Nano 8GB, Unitree L2 3D LiDAR/IMU, Gemini335L RGB-D and ESP32.
- No active 2D LiDAR. Preserve compatibility with existing recordings.
- Scan the lab, then place the rig at a fixed station. Saved map coverage and
  currently visible live coverage are distinct.
- Private remote access. Local service is loopback by default; configured
  credentials are required for network exposure.
- The first physical milestone includes all requested modules together.
- Windows remains the local simulation/replay/training/visualization host.
  Ubuntu24.04 is the live target; ARM64 GPU dependencies must match JetPack.
- 35ms is a future target. Measure backend latency and renderer frame time
  separately. No invented FPS, accuracy or hardware pass badges.

## Implementation gates

1. Correct moving commissioning, raw observation preservation, disk-backed
   prepared frames, bounded reconstruction, observation-only map provenance.
2. Bound acquisition/recording/map/network queues and caches. Page map tiles,
   derive observed extents and keep unknown space blocked.
3. Optimize exact traversal, calibration rays and shared preprocessing; prepare
   spatial indices outside tensor forward. Gate optional exports by parity.
4. Deliver React/TypeScript/Vite/Three.js command center using actual pipeline
   snapshots, GLB tiles/LOD, instancing, worker decoding and accessible controls.
5. Deliver versioned keyframes/deltas, resynchronization, authenticated roles,
   acknowledged commands, local/private service and server relay deployment.
6. Deliver ESP32 firmware/protocol, virtual halo, handshake, CRC and watchdog;
   produce truthful software/HIL diagnostic reports.
7. Verify regressions, full CLI integration, browser interactions, malformed
   network inputs, slow clients, model equivalence and 100k-instance rendering.
8. Repeat30-minute endurance measurements, refresh locks/wheels/archive/source
   manifests and deliver setup/commissioning/recovery instructions.

## Completion boundary

Every software gate requires evidence from the final source. Physical Jetson,
sensor and ESP32 tests, real calibration/coverage and real-lab learned accuracy
remain explicitly unverified until the rig is connected and measured. Software
simulation cannot pass a missing hardware stage. No physical actuation,
automatic firmware flashing or unconfigured external publication is included.
