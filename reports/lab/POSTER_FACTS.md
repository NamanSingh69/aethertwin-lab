# Fact sheet for the poster and showcase (2026-11-05). Use only these numbers; each has a source.

## Header (keep from the old poster `DOC-20261029-WA0007.pdf`)
Title "Lab Digital Twin: Autonomous Scanning and 3D Reconstruction"; team HIRANYA KUMAWAT (24155539), SUMEDHA G
(2405198), SUJAL AGARWAL (24051292), NAMAN SINGH (24052230), UTKARSH JHA (24155593), AASUTOSH SWAIN (24157002);
no "Guided by" line (owner, 21 Nov 2026: not needed, so the placeholder was dropped for good);
K-Explore (Robotics); Kalinga
Institute of Industrial Technology (Deemed to be University), Bhubaneswar; GROUP NO. 29; the KIIT logo.

## What changed since the old poster (it said "proposed project, nothing measured")
- The camera is an **Orbbec Gemini 336L** (old poster said 335L). RPLIDAR S2 was **not used**.
- Built rig: Unitree L2 4D LiDAR (upright, window 9.0 cm above the floor) + Gemini 336L (21 cm in front, 2.5 cm
  higher) on a small wheeled cart **pushed by hand (motors off)**; Raspberry Pi 5 capture node on the cart; raw
  sensor stream over the laptop's Wi-Fi hotspot to a Windows laptop (RTX 4070 Laptop GPU). Cart 22.9 x 15.2 cm.
- Software (all ours, Python 3.12 + TypeScript): raw-sensor transport protocol (Pi -> laptop, authenticated),
  LiDAR/IMU/RGB-D synchronizer, IMU deskew, ICP scan registration with pose graph, log-odds occupancy + TSDF,
  Poisson mesh twin, perception CNN + 9-DoF Kalman tracker, risk fields and protected zones, planner; FastAPI
  command center; React 19 + Three.js browser studio (binary scene protocol); "What is this?" explainer; a static
  public replay site (showcase). 713 automated Python tests (21 Nov 2026, `reports/test_suite/README.md`) + web unit and Playwright E2E tests, local CI.
- Localization: "parked" mode, the cart localizes once against the twin, then holds.

## Measured numbers (say exactly what each one is)
| Number | What it is | Source |
|---|---|---|
| 60 s, 716 frames, 0 lost, 5.17 MB/s | first Pi-to-laptop stream over Wi-Fi (bench) | reports/hardware/pi/README.md |
| 600 s, 7,171 frames, 0 lost, clocks within ±0.9 ms | long stationary stream (bench) | docs/LAB_DAY.md |
| 42 mm pose spread | static precision of a rig that never moved (bench), NOT accuracy | docs/LAB_DAY.md |
| 120 s, 1,435 sweeps at 11.98 Hz, 0 lost, IMU 251 Hz | lab recording on the cart, 5 Nov | reports/lab/README.md |
| 359 scans, 14,093 measured cells, 61,367 surface points | the lab twin built from that recording (one viewpoint; floor from the tape) | reports/lab/README.md |
| fitness 0.985, ICP RMSE 3.4 cm | live run localized against the twin: a FIT, NOT accuracy | reports/lab/README.md |
| 15 min live, 5,510 frames, p50 140 ms / p95 216 ms a frame, 0 drops, memory flat 1.51-1.64 GB | LiDAR-only live run on the real rig over Wi-Fi, 5 Nov | reports/lab/README.md |
| 381 -> 240 ms p50 a frame | exact (same-result) software speed-ups, 10-min rehearsals on recorded data | reports/rehearsal/README.md, fig7 |
| 30-min rehearsal, no errors | recorded real data replayed through the real transport (not live) | reports/rehearsal/README.md |

## Not measured / not done (must be stated, never implied)
Camera-LiDAR calibration (the live run was **LiDAR-only**); accuracy against independent ground truth; a
world-to-display latency; a 30-minute hardware-verified run (hardware_verified stays false); the 35 ms target
(not met); a moving commissioning scan (today's push failed QA); physical LED halo; autonomous driving.
Future work: calibration with a target, a full moving scan with loop closure, landmark accuracy vs tape,
latency with a 240 fps phone, autonomous navigation.

## Assets (all in the repo)
- Photos: reports/lab/photos/lab_cart_on_floor.jpg (the cart in the lab), lab_room_view.jpg, lab_live_run_walker.jpg
  and _walker2.jpg (a team member walking during the live run, the studio on the laptop behind),
  studio_lab_twin_replay.jpg (the studio showing the lab twin), step1_ruler_1..6.jpg (rig close-ups with tape).
- Figures: reports/submission/figures/fig1a..fig7 (see INDEX.md there); fig6 live rehearsal, fig7 latency progression.
- Old poster PDF (layout, logo, references): C:\Users\user\.claude\uploads\5589308e-8bed-4c67-bafe-2d01a62929ee\3f7db119-DOC-20261029-WA0007.pdf
