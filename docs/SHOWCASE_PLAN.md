# Showcase plan: what we show and say (after the 5 Nov 2026 lab session)

Evidence for every number: [`reports/lab/README.md`](../reports/lab/README.md) (lab session) and
[`reports/rehearsal/README.md`](../reports/rehearsal/README.md) (rehearsals). Poster: `reports/poster/`.

## The stand (what is on the table)
1. **The rig**: the cart with the Unitree L2 (upright) and the Gemini 336L, the Pi 5 on board, motors off. Point at
   the parts as you explain the pipeline.
2. **Laptop, the studio** (Chrome, hardware acceleration on). Pick the tier that works and say which one it is:
   - **Live** (rig powered, Pi on the hotspot): `.venv-gpu\Scripts\python.exe -m command_center --mode live --config config\profiles\hotspot.yaml --twin %USERPROFILE%\aethertwin_twin_static --capture-host 192.168.137.37 --device cuda --parked --lidar-only --duration 1800 --open-browser --port 8090`
     (port 8080 is blocked by Windows on this laptop). Say "live, LiDAR-only". Visitors walk in front of the cart and
     appear as tracked entities.
   - **Recorded replay** (no rig): `.venv\Scripts\python.exe tools\rehearse_live.py --recording %USERPROFILE%\aethertwin_rec\self_mask_parked --twin %USERPROFILE%\aethertwin_twin_static --parked --device cuda --duration 1800 --open-browser`.
     Say "recorded real data from our rig, replayed; not live". The recording has nobody walking, so no tracks.
3. **Second screen or tablet**: the animated digital poster (`reports/poster/digital/index.html`), or the public
   replay site once it is deployed.
4. **The A3 poster** (`reports/poster/AetherTwin_poster_A3.pdf`).

## The 3-minute talk
1. Problem: floor plans and photos lose a lab's 3D geometry.
2. What we built: a sensing cart, a raw-sensor link from the Pi to the laptop over Wi-Fi, a pipeline (sync, deskew,
   registration, occupancy, twin, tracking, risk) and a browser studio. Open **"What is this?"** in the studio.
3. Show the twin of our lab with the live points on top; orbit; turn **Layers** on and off.
4. Results (measured, 5 Nov): 15-minute live run on the rig, LiDAR-only, 5,510 frames, **p50 140 ms / p95 216 ms a
   frame**, no drops, memory flat; localized against the twin with a 3.4 cm ICP fit (a fit, not accuracy); 120 s lab
   recording with zero lost sweeps; exact software speed-ups 381 -> 240 ms on recorded data.
5. Honest limits: no camera-LiDAR calibration yet (LiDAR-only), no accuracy against ground truth, no measured
   latency, not a 30-minute hardware-verified run, 35 ms target not met, the commissioning push failed QA so the twin
   is from one viewpoint.
6. Next: calibration with a target, a full moving scan with loop closure, landmark accuracy, latency, autonomy.

## Questions to expect
- "How accurate is it?" Not measured against ground truth yet; 3.4 cm is how well the live scan fits our own twin.
- "Is it real time?" About 6 processed frames a second live (140 ms median); the sensor sends 12, and we always
  process the newest one.
- "Why LiDAR-only?" The camera calibration needs a target session we did not have time for; the camera stream works
  (every sweep had a camera frame) but is not fused.
