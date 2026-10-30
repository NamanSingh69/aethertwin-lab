# Cloud GPU benchmarks (Modal)

Produced by `tools/modal_bench.py` on 2026-10-29/30 (UTC). Every run used the
hash-locked `requirements-ubuntu-cuda.lock` (torch 2.14.1+cu126, CUDA runtime
12.6, cuDNN 9.10) on Modal's Debian slim Python 3.12 image, NVIDIA driver
610.57.04, 4 reserved CPU cores and 16 GiB. Inputs are the repository's
synthetic lab simulator (virtual LiDAR and RGB-D sensors), seed 7 unless stated.

**Limits.** These are Modal cloud GPUs running simulated sensors. They are not
RTX laptop, A2000 workstation, Raspberry Pi or lab results, and simulation says
nothing about physical accuracy. The A10 is an Ampere (GA102) part like the
A2000 (GA106) but much larger; any A2000 estimate drawn from it is unverified.
Container CPUs are shared cloud cores, so CPU-bound stage times will differ on
a workstation.

## Model forward pass (1,400 voxels, temporal branch on, 200 repeats)

p50 / p95 in ms; error is the maximum absolute output difference from fp32.

| GPU | fp32 | cudnn.benchmark | TF32 (error) | torch.compile (compile s) |
|---|---|---|---|---|
| T4 | 7.36 / 7.54 | no change | 7.32 / 7.49 (≈0; Turing has no TF32) | **5.19 / 5.41** (18.1 s) |
| L4 | 3.75 / 3.79 | no change | 3.49 / 3.58 (0.009) | **2.62 / 2.66** (18.0 s) |
| A10 | 4.20 / 4.27 | no change | 3.45 / 3.49 (0.009) | **2.88 / 2.92** (13.0 s) |

- `torch.compile` (default mode) is exact (error 0) and 30-31 % faster on every GPU.
- Automatic mixed precision does not run: the max-pool `scatter_reduce_` mixes
  fp32 and fp16. An experiment that cast to the buffer dtype
  (`experiments/latency_a10_amp_cast_experiment.json`) let bf16 run, but it was
  slower than fp32 (4.67 vs 4.32 ms) with error 0.095, and fp16 then overflowed
  elsewhere. At this model size the forward pass is launch-bound, so AMP is not
  pursued and the cast was not committed.
- ONNX Runtime and TensorRT were not measured (not in the locked environment).

## Whole simulator pipeline (60 frames, 4 threads, `--device cuda` vs `cpu`)

Processing latency p50 / p95 in ms, and per-stage p50 on CUDA:

| GPU | CUDA total | CPU total | occupancy | perception + tracking | planning (p95) | localization |
|---|---|---|---|---|---|---|
| T4 | 486 / 1328 | 617 / 1499 | 185 | 94 | 183 (948) | 11 |
| L4 | 385 / 1079 | 527 / 1176 | 152 | 68 | 150 (714) | 9 |
| A10 | 396 / 1071 | 496 / 1097 | 158 | 67 | 155 (679) | 8 |

The 35 ms processing target is far away and **not** a GPU problem: the model
is 4 ms of a ~390 ms frame. Occupancy (voxel hashing and ray clearing) and
planning (A*/spline/MPPI, with long p95 tails) dominate on CPU, so CUDA only
improves end-to-end latency by 20-25 %.

## Seed sweep (A10, 60 frames each)

| Seed | p50 / p95 ms | perception + tracking p50 |
|---|---|---|
| 7 | 385 / 1021 | 67 |
| 101, 102, 201, 202 | 2103-2148 / 2683-2830 | ~1755 |

Seed 7 is the only seed with a bundled measured twin (`assets/twin_seed7`);
the others build one at start-up, and their perception and tracking stage was
about 26x slower. Cause, found afterwards: distance queries against the
runtime-built (disk-tiled) twin re-read tiles and rebuilt KD-trees on every
batch. Fixed with cached exact trees; on the laptop CPU seed 101 dropped from
1,244 to 120 ms (seed 7: 115 ms). This sweep predates the fix.

## Stability (A10, seed 7, 900 s wall clock)

1,608 frames, processing p50 370 / p95 520 / max 1,604 ms; per stage p50 (p95):
occupancy 153 (278), planning 134 (177), perception + tracking 67 (79),
localization 8 (9). No dropped snapshots. Resident memory 6,596 MB at the first
frame and 6,707 MB at the last, with a flat second half (0.0 MB per frame by
least squares): no leak visible over 15 minutes. This is a software run on a
cloud GPU, like `artifacts/stability_30min/`, not a hardware endurance result.

## Regression suite on CUDA (T4)

`python -m pytest -q` from the CUDA lock: 334 passed, 1 skipped; CUDA matmul
smoke passed. This validates the Linux CUDA install profile; most tests
exercise CPU code paths.

## Cost

Estimated from list prices and wall time, each file's `estimated_cost_usd`;
the Modal billing report is authoritative. The whole set stayed well under the
USD 25 cap (see the PR description for the billed total).
