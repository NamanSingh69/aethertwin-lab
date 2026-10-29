# Actual pipeline gateway acceptance

Passed on Windows with the canonical checkpoint, the shipped measured-from-simulation twin, and the first four ordinary sensor frames of `assets/sample_recording`. The edge ran the actual `BrowserRuntime` and `LabPipeline`; the gateway ran in the separate minimal environment with Torch, Open3D, MuJoCo and OpenCV absent.

Both listeners used random `127.0.0.1` ports, scoped fixture credentials and verified TLS with the generated local CA. No production sources or existing server ports were changed. The ephemeral TLS private key was removed after shutdown.

| Check | Observed result |
|---|---|
| Browser wire reconstruction | Exact voxel centers/colors/flow, tracks, calibration/map identity, path, risk, traffic and RGB-D inlay match to actual edge output |
| Keyframe then delta | Sequences 1 and 2; 1,400 voxels and four tracks each |
| Controls | Seven revisions acknowledged and applied, including pause, resume, set start/goal and step |
| Changed goal | Observed safe segment from `(-1.375,-4.375,0.625)` to `(-2.375,-4.375,0.625)` metres |
| Trajectory export | CSV exactly equals the validated local remaining trajectory |
| Edge connection interruption | Owned upstream WebSocket closed; gateway detected failure and reconnected to the same actual source |
| Gateway outage | Local perception and recording advanced from two frames to three while the gateway process was stopped |
| Gateway restart | New browser connection received current sequence-three keyframe with exact state |
| Bounded completion | Sequence four, 1,400 voxels, six tracks; one upstream stream and no scene coalescing |
| Raw recording | Four frames reopened successfully; raw LiDAR, RGB and depth exactly matched original observations; zero drops |
| Shutdown | Edge pipeline worker stopped cleanly |

`report.json` contains all checks, configuration, acknowledgements/application history, packet hashes and checkpoint identity. The four `.atwn` captures are the actual browser packets, not generated fixtures. `trajectory.csv` is the actual exported route. The recording and edge outputs are retained in their subdirectories.

Elapsed time was 33.266 seconds with two PyTorch threads, concurrent with the separate final port-8080 endurance run. These timings are not an isolated throughput benchmark. Physical sensors and hardware reconnection remain unverified. The reference browser accumulator and actual network WebSocket were tested; visual browser interaction is covered separately.

To reproduce into a new output directory from the project root:

```powershell
& '..\..\work\locked_venv\Scripts\python.exe' tools/gateway_pipeline_evidence.py --output artifacts/gateway_actual_pipeline_new
```
