# Measured AetherTwin release

Windows laptop: AMD Ryzen7 8840HS, Radeon780M,31.3GB usable RAM, Python3.12, CPU PyTorch.
These are synthetic observations at reduced resolution. Physical sensors and Jetson performance remain unverified.

| Measurement | Final result |
|---|---:|
| Pipeline observations / wall duration | 741 / 1805.5s |
| End-to-end processing | 0.411frames/s |
| Processing median / p95 / maximum | 1734.9 / 3042.7 / 5432.4ms |
| Process memory first / last / peak | 459.6 / 531.0 / 591.0MiB |
| Final-half memory trend | -0.0535MiB/frame |
| Recorded frames / dropped frames | 741 / 0 |
| Encoder coalesced snapshots | 0 |
| Health request median / p95 | 16.0 / 281.0ms |
| Fixed1080p100,000-instance renderer | 59.46FPS mean |
| Lowest one-second render window | 52.58FPS |
| Recent render-frame p50 / p95 / p99 | 16.7 / 22.5 / 27.9ms |
| Stress-scene draw calls / triangles | 3 / 1,200,000 |

The35ms pipeline target remains future work. The fluid renderer has an independent
budget and does not disguise old observations as fresh data. Stress data is an
explicit static injection, separated from runtime perception. Its drawing buffer
was1920×1080 at pixel ratio1; the displayed canvas was1225×689. Frame percentiles
cover the final1200 intervals; FPS averages56 one-second windows after5s warmup.
Final colors were visually checked after correcting the instance material.

## Conditions and provenance

The30-minute run enabled all runtime algorithms, virtual halo, raw recording,
bounded tiled occupancy and browser telemetry. It used2400LiDAR beams,160×100RGB-D,
stride3,1400neural voxels,16spatial/12temporal neighbors,0.18m occupancy and0.25m
planning cells. Active map cache:128tiles with the same64MiB byte ceiling and4GiB
disk quota. Detailed configuration and source/checkpoint hashes are embedded in
the benchmark and run_provenance.json. Original calibrated observations were
recorded before deskew/preprocessing; the new capture contains no S2 stream.

Verification, installation/build work and multiple browser clients overlapped the
run. Interactive pause/resume held processing for38.74s; end-to-end FPS includes
that hold. One observer request preceded listener readiness; all1763 later samples
succeeded. The power clock found no standby interruption. Backend sources stayed
unchanged. Frontend availability warnings and the material color fix were rebuilt
during the interval; the final color-corrected renderer has a separate60s test.
This is a backend endurance result with final browser verification, rather than
a claim of30minutes on one immutable frontend build.

The bounded profile trades disk-backed map capacity for processing cost. Its
overall throughput is reported as measured. Component-specific native ray and
grouped-tile speedups are documented separately and are not overall FPS claims.
Training/reconstruction/pretraining are offline workflows with independent CLI
proofs. Tiny smoke-training weights never replace the canonical checkpoint.

Evidence: artifacts/aethertwin_endurance_final/,artifacts/browser_stress_1080p_100k.json,
reports/cross_platform_aethertwin_replay.json and reports/release_validation.json.
Full raw endurance captures remain in artifacts/aethertwin_endurance_final_raw/
in the workspace; the portable archive includes smaller sample recordings.
Earlier baselines, tuning attempts and the black-material screenshot are retained
as historical evidence and are not the final rendering acceptance result.
