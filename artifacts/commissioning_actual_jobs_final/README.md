# Actual commissioning worker integration

`report.json` contains all six real `CommissioningManager` spawned worker jobs.
They ran sequentially against the laptop configuration, four-frame synthetic
observations, registered synthetic target correspondences, and the existing
`assets/twin_seed7` reference. No port-8080 commands or hardware operations were
performed. Every active-reference file hash was unchanged after the run.

| Job | Result | Evidence |
| --- | --- | --- |
| Resource discovery | Completed | Native SDK import specifications unavailable; no device opened or readiness claimed |
| Diagnostics | Completed | Four ordinary perception frames; software checks passed; hardware checks false |
| Calibration candidate | Completed | Synthetic NPZ `measured:false` overrides the registry's measured declaration; solver covariance/observability retained; independent validation not tested |
| Candidate twin | Completed | Four designated static scans reconstructed into a new candidate bundle; source provenance synthetic and live-reference eligibility false |
| Coverage | Completed | 26,639 known cells: 4,877 occupied, 21,422 free, 340 ambiguous; whole-lab completeness not claimed without a registered surveyed region |
| Fixed-position review | Completed, review passed | Carried input pose chosen from observation-supported hypotheses; overlap 0.985–0.992 and point-to-plane residual 0.029–0.040 m; candidate never applied |

The earlier `commissioning_actual_jobs` run exposed a stable FPFH room alias.
The final implementation refines both carried-input and FPFH seeds, prefers
constrained overlap, and rejects separated similarly supported alternatives.
The regression suite tests that rejection. Full local rank and repeatability
still do not establish independent global pose accuracy.

These six jobs preceded the diagnostic runner's final resource-close repair and
emitted a temporary SQLite cleanup warning during diagnostics teardown. The
separate `artifacts/commissioning_diagnostic_clean_shutdown/report.json` reruns
the actual spawned diagnostic worker after that repair. This directory preserves
the original evidence rather than replacing its history.

No job promoted a map, pose or calibration. Physical sensor accuracy and device
synchronization remain unverified. Timings in the report were measured while the
parent's browser service ran and are not an isolated throughput benchmark.
