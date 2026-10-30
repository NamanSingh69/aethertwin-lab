# Physical commissioning: scan, then fix the rig

All physical steps below remain unverified until the rig is connected. Simulation
and virtual targets are labelled separately. A useful lab twin is feasible, but
there is no defensible success percentage before a representative physical
pilot. The map estimates the observed extent; a fixed rig cannot monitor behind
occlusions or beyond its coverage.

1. Record the Pi model, OS, power supply, active cooling and microSD free space;
   record the Windows host, NVIDIA driver, actual GPU/VRAM and SSD capacity.
   Follow `RASPBERRY_PI_WINDOWS.md`. Complete and validate the raw-sensor bridge
   before a full survey. Keep sensor collection on the Pi and processing/storage
   on Windows. Do not install the x86 locks on the Pi. GPU matrix arithmetic is
   only a startup check, not a full pipeline benchmark.
2. Connect L2 Ethernet and the camera by USB3 data cable. Confirm device serials,
   SDK versions, power stability and sustained recording write rate. Enumerate
   actual depth/color/alignment profiles. Depth defaults to1280×800 at30fps;
   approved aligned output must match the measured camera calibration. L2 echo
   index/count are unavailable and must remain invalid.
3. Collect camera intrinsics/distortion and independent camera-to-LiDAR target
   correspondences throughout the useful volume. Solve a calibration candidate,
   inspect full six-degree observability and conditioning, then score additional
   held-out observations. A low fitted pixel error alone is insufficient.
   Camera profile, units, transform direction, sensor identities and timestamp
   policy belong to the calibration record. Mount rigidity must be checked.
4. Check measured IMU rate, gaps, bias/stationarity and retained clock residuals
   for the actual rig. Validate moving deskew with a measured trajectory or
   controlled motion. A fitted floor constrains only three pose directions; it
   cannot establish complete camera/LiDAR calibration. Incomplete clocks and
   missing modalities are explicit faults, never simulated replacements.
5. Clear moving objects for commissioning. Walk a slow scan with overlap around
   architecture, benches and clutter. Capture multiple views of hidden regions,
   close loops and retain original raw observations. Declare the recording
   static before reconstruction. Build a candidate twin and inspect registration
   fitness, floor alignment, observed coverage, unknown pockets and loop error.
   Keep saved geometry, inferred visual surfaces and measured occupancy distinct.
6. Independently check several distances and dimensions using a tape or another
   trusted reference. LiDAR estimates lab extent automatically but cannot verify
   its own absolute accuracy. Include thin cables, dark/reflective surfaces,
   foliage and close/far obstacles in the inspection.
7. Install the rig at its fixed mount. Relocalize against the commissioned map
   with constrained measured overlap. A saved last-station pose is an initial
   prior only. Inspect coverage from this fixed position and mark unobserved
   regions explicitly. Choose real map start/goal coordinates and geofences;
   simulator coordinates and demonstration zones are not live defaults.
8. Run semantics, scene flow, CA/CV tracking, risk, zones, traffic and planning
   together. Record crossing people/carts, occlusion, newly placed static hazards,
   irregular timing, blocked routes and sensor loss. Independently label a
   representative evaluation set, separating static/dynamic flow and class
   performance from synthetic scores. Unsupported accuracy remains unverified.
9. Identify the connected ESP32 board and pin wiring before selecting firmware
   configuration or flashing. Validate the protocol handshake, status/halo,
   watchdog and stale-data behavior. Firmware compilation and the virtual halo
   do not verify physical LEDs or output wiring. ESP32 status is advisory; it
   does not actuate a robot or replace an independently engineered interlock.
10. Start the private gateway with configured TLS and separate viewer/operator
    access. Verify reconnection, stale states, revision conflicts and local
    recording during server loss. Run30minutes with the complete live pipeline,
    measuring acquisition age, latency percentiles, memory, queues/drops, render
    frame times, recording throughput, Pi and GPU temperature and throttling.

The browser commissioning panel provides isolated diagnostic, calibration,
reconstruction and coverage/relocalization jobs for configured sources. Outputs
are candidates for review; no job automatically promotes calibration or maps.
Use the diagnostic CLI for an exclusive live rig session:

```text
python -m diagnostics --source live --config /path/to/measured-lab.yaml --twin /path/to/measured-twin --max-frames 64 --output /data/diagnostics/new-run
```

Keep the ordinary live service stopped while a separate live diagnostic owns
the sensors. Final acceptance requires independently checked physical results,
not a simulated fallback or an SDK import.
