# Raw-sensor transport evidence

**Label: loopback with synthetic devices on the development laptop. This is not
a Raspberry Pi, network, Unitree L2 or Gemini 335L result.** It shows that the
protocol, queues, clock mapping and receiver sustain the intended rates without
loss on one host. The Pi's CPU, the gigabit link and the real devices are
untested.

## `loopback_soak_windows_laptop.json` (2026-11-02)

`python tools/raw_transport_soak.py --duration 600 --sample-seconds 30 --color-codec device --out ...`

- Host: Windows 11 (10.0.26300), Intel i9-13900H (20 logical CPUs), 16 GB RAM, Python
  3.12.10, numpy 2.5.4, OpenCV 5.0.0. The capture node ran as a separate process
  (`python -m ingestion.capture_node --synthetic`), the receiver was
  `RemoteSensorSource` behind `BufferedAcquisition`, as in live mode.
- Synthetic load: IMU 250 Hz, sweeps 10 Hz with 6,400 points, RGB-D 1280x800 at
  30 fps; depth `raw` (lossless uint16), colour forwarded as pre-encoded MJPG
  device bytes (`device`), 1 MiB camera fragments.
- 600 s: 5,997 synchronised frames delivered (9.99 Hz); 150,000 IMU samples, 6,000
  sweeps and 18,000 camera frames received; **zero** IMU sequence breaks, missing
  sweeps, missing or incomplete camera frames, node-side drops, receiver
  overflows, acquisition drops, stalls, clock jumps or reconnects (one segment).
- Bandwidth 64.6 MB/s mean (65.3 max), about half of gigabit.
- Arrival latency (host receive time minus mapped capture time, last window):
  IMU p50 8.6 / p95 15.7 / max 19.5 ms; camera p50 12.2 / p95 20.4 / max 31.2 ms;
  sweeps p50 111 ms, which includes the 100 ms sweep itself (measured from sweep
  start). Camera decode p95 4.4 ms.
- Clock: best ping delay 0.32 ms (error bound 0.16 ms), jitter 0.23 ms.
- Memory: receiver RSS 143 -> 141 MB (max 160), node 87 -> 85 MB: no growth.

## `loopback_soak_windows_laptop_final_code.json` (2026-11-02, after the review fixes)

Same command and settings on the final code, but **the laptop was not idle**: the
ESP32 firmware compile, WSL builds and a dependency-lock resolution ran during it.
600 s: 5,992 frames (9.97 Hz); 150,005 IMU samples and 6,000 sweeps with zero
sequence breaks or missing sweeps; one segment, no stalls, clock jumps or
reconnects. The node dropped 8 of 18,000 camera frames oldest-first (0.04 %),
each counted and reported as `camera_missing`; nothing was filled in. Bandwidth
64.5 MB/s. Arrival latency IMU p50 10.4 / p95 20.1 / max 28.9 ms, camera p50 17.1
/ p95 30.1 / max 47.2 ms. Memory: receiver 116 -> 141 MB (max 145), node 85 ->
83 MB. The difference from the first run is attributed to the concurrent load,
not measured separately; the Pi will have its own CPU budget.

## Codec costs (same run, synthetic images: smooth depth plus 3 mm noise)

| Codec | Bytes per frame | Encode | Decode | MB/s at 30 fps |
|---|---|---|---|---|
| depth raw | 2.05 MB | 0.5 ms | 0.5 ms | 61 |
| depth zlib (byte planes) | ~1.0 MB | ~30 ms | ~9 ms | ~30 |
| depth png | ~0.86 MB | ~30 ms | ~11 ms | ~26 |

Exact figures are in the JSON (`codec_bench`). Real depth has different noise and
holes; re-measure on the Pi with real frames before choosing a compressed codec.

## `hotspot_codec_matrix_windows_laptop.json` (2026-11-04): codecs and profiles for the Wi-Fi hotspot

**Label: SYNTHETIC devices over LOOPBACK on the development laptop (i9-13900H, 20
logical CPUs, Windows 11, Python 3.12.10). Offered load and CPU only. Not a
Wi-Fi, Raspberry Pi, L2 or Gemini result.** Loopback is unconstrained, so
"offered" is what the node would push into a link; whether a real hotspot link
carries it is not measured. Synthetic depth is a smooth surface plus 3 mm noise
(compresses about 2x) and synthetic colour a gradient (JPEG far smaller than a
real scene), so real sizes will differ, colour most of all.

`python tools/raw_transport_matrix.py --duration 60 --out ...` (one
`tools/raw_transport_soak.py` run per row; the 848x480 row was rerun alone with
`--reuse` because a pytest run overlapped its first attempt). Every row: IMU
250 Hz, sweeps 10 Hz with 6,400 points, camera 30 fps from the device, 64 KiB
fragments, 60 s. CPU is percent of one logical core (psutil, mean of 5 s
windows after the first) for the node process and the receiver process.
"Fused" is synchronised frames delivered per second (the sweeps are 10 Hz).

| Row | Offered MB/s (Mbit/s) | Camera msgs/s | Message p50 | Node encode p50/p95 | Node CPU | Receiver CPU | Fused Hz | Result |
|---|---|---|---|---|---|---|---|---|
| wired default 1280x800, depth raw, colour device | 63.7 (509) | 28.7 | 2,096 kB | 0.8/3.0 ms | 23 % | 106 % | 9.57 | ok (45 camera frames dropped at the node) |
| 1280x800 zlib, device | 25.6 (204) | 23.2 | 1,061 kB | 35/52 ms | 118 % | 76 % | 4.80 | **failed** after 5 s: acquisition error |
| 1280x800 png, device | n/a | 21.6 | 911 kB | 33/63 ms | n/a | n/a | 4.96 | **failed** after 4 s: acquisition error |
| 1280x800 zlib, jpeg 80, sweep | 11.95 (96) | 9.8 | 1,050 kB | 37/65 ms | 53 % | 53 % | 9.80 | ok |
| 848x480 zlib, jpeg 80, sweep | 5.80 (46) | 9.8 | 422 kB | 12/14 ms | 18 % | 17 % | 9.81 | ok |
| 640x400 raw, jpeg 80, all | 17.5 (140) | 29.5 | 523 kB | 0.9/1.7 ms | 15 % | 16 % | 9.81 | ok |
| 640x400 zlib, jpeg 80, all | 9.6 (77) | 29.6 | 267 kB | 8.2/12.6 ms | 34 % | 27 % | 9.82 | ok |
| 640x400 png, jpeg 80, sweep | 3.90 (31) | 9.9 | 228 kB | 8.9/15.3 ms | 15 % | 16 % | 9.82 | ok |
| **hotspot profile: 640x400 zlib, jpeg 80, sweep** | **4.22 (34)** | 9.8 | 267 kB | 8.2/11.0 ms | 16 % | 13 % | 9.81 | ok |
| fallback 424x240 zlib, jpeg 80, sweep | 2.67 (21) | 9.8 | 110 kB | 3.3/4.8 ms | 8 % | 8 % | 9.81 | ok |

What it shows (on this host, with these synthetic images):

- L2 points and IMU alone are about 1.55 MB/s (12 Mbit/s): 6,400 points x 24 bytes
  x 10 Hz plus 250 x 64 bytes. Every hotspot option pays that first.
- `camera_forward: sweep` cut camera messages from ~29.5/s to ~9.8/s with no
  loss of fused frames (9.81 Hz in every sweep row, zero missing sweeps or
  camera frames, one segment): the frames it skips are the ones the host's
  20 ms synchroniser could not use. The skipped frames are never encoded, which
  is where most of the node CPU saving comes from.
- Lossless depth compression at 1280x800 and 30 fps did not keep up in Python on
  this i9 (35 ms per frame against a 33 ms frame period): fused output halved and
  acquisition failed within 5 s. A Pi 5 is slower; compressed 1280x800 at 30 fps
  is not an option there.
- `png` depth is ~15 % smaller than `zlib` but costs a little more to encode and
  its p95 is worse; `zlib` was chosen for the Pi's CPU. Re-check both with real
  frames on the Pi (holes and noise change the ratio).
- The hotspot profile offers 4.2 MB/s (34 Mbit/s), inside the 5 MB/s
  (40 Mbit/s) budget in `config/profiles/hotspot.yaml`; that budget is an
  assumption (about half of an assumed 80 Mbit/s sustained TCP from the Pi's
  onboard Wi-Fi on 5 GHz), to be replaced by an iperf3 measurement on the cart.
  If the link is slower (2.4 GHz, range), the 424x240 row offers 2.7 MB/s
  (21 Mbit/s); it needs its own calibration at that size and a check that the
  Gemini 335L offers it.
- Node CPU here is the i9's; on the Pi the zlib encode and the colour JPEG
  encode (plus the MJPG decode the driver then needs for every frame, which this
  synthetic node does not do) must be measured from the node's status lines.

## Findings that changed the design

- Python 3.12 on Windows implements `time.monotonic()` with GetTickCount64
  (15.6 ms steps). The receiver's ping exchange uses
  `raw_transport.host_monotonic()` (QueryPerformanceCounter shifted onto the
  monotonic epoch); otherwise the offset would be quantised.
- With 256 KiB camera fragments the receiver's per-message overhead (and GIL
  handoffs) lost ~15 % of camera frames at the node; 1 MiB fragments removed the
  loss. An IMU batch now queues behind at most one fragment on the node (an
  estimate of about 8 ms on gigabit; not measured on a network).
- Re-encoding colour on the node cost a full core with synthetic images; the
  node now forwards the device's own colour bytes, and the Gemini driver has a
  forward-only mode that skips RGB decoding and the metres conversion on the Pi.

## Still to measure on hardware

Pi CPU and temperature while streaming, real L2 IMU rate and gaps, real camera
MJPG sizes, gigabit throughput with the L2 on the same switch, clock residuals
between the Pi and Windows, and 1- and 10-minute stationary and moving
recordings that replay on Windows.
