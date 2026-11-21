# Pi capture node: first bring-up (2026-11-04)

Physical Raspberry Pi 5 on the bring-up rig, reached from the laptop over its Windows hotspot
(Pi 192.168.137.37) with the L2 on the Pi's Ethernet (192.168.1.2 to 192.168.1.62).
`hardware_verified` stays false: this is device bring-up, not a pipeline result.

## Host

From [`pi_inventory_20261104T165941Z.txt`](pi_inventory_20261104T165941Z.txt) and the setup log:

| | |
|---|---|
| Board | Raspberry Pi 5 Model B Rev 1.1, 16 GB, bootloader 2026/05/11 |
| OS | Debian GNU/Linux 13 (trixie) based Raspberry Pi OS, kernel 6.18.50+rpt-rpi-2712, user `labpi` |
| Storage | 14 GB root on microSD, 4.4 GB free after setup |
| Python env | Python 3.13.5; numpy 2.5.4, opencv-python-headless 5.0.0.93, scipy 1.18.1, PyYAML 6.0.3, pyorbbecsdk2 2.1.2 (`--no-deps`) |
| Native | `lab_sensors_native` built against unilidar_sdk2 rev 0e3c51f (SDK 2.0.9) on aarch64 / GCC 14, imports |
| Time | chrony, stratum 3, 0.3 ms from NTP at setup |
| Power | **`throttled=0x50000`**: under-voltage and throttling have occurred since boot (not at the moment of reading). To fix before long runs. |

## L2 LiDAR

- Link: 100 Mb/s full duplex; ping 0.1 ms, no loss.
- Identity (Unitree example, [`unitree_example_lidar_udp.txt`](unitree_example_lidar_udp.txt), trimmed):
  hardware 2.2.1.1, firmware 2.8.11.1, SDK 2.0.9.
  - The example reported window dirt of 11.4 % and a time delay of 2.45 ms.
  - The example sends work mode 0 (factory default) and a LiDAR reset before streaming.
- Through the capture node's own driver ([`device_check_l2_run1.json`](device_check_l2_run1.json),
  [`device_check_l2_run2.json`](device_check_l2_run2.json); two consecutive 15 s runs, robot parked):

  | | Run 1 | Run 2 |
  |---|---|---|
  | First sweep after opening | 0.17 s | 0.17 s |
  | Sweeps | 181 at 23.78 Hz | 181 at 23.78 Hz |
  | Sweep period, median (range) | 42.05 ms (42.00–42.09) | 42.06 ms (42.00–42.16) |
  | Points per sweep, median (range) | 5169 (4885–5330) | 5175 (5012–5333) |
  | IMU | 3777 samples at 498.8 Hz | 3783 samples at 499.3 Hz |
  | Dropped clouds | 0 | 0 |
  | Stale clouds rejected | 1 | 1 |
  | Sweep timestamps strictly increasing | yes | yes |

- Accelerometer magnitude at rest: median 10.48 m/s², about 7 % above gravity (9.81). This looks
  like a bias to estimate before relying on IMU deskew; it has not been characterised yet.
- Range: 0.22 m to 12.3 m in this room. The returns nearest the LiDAR are the robot's own body;
  measure them with `tools/suggest_self_mask.py` and set `lidar_self_mask`.

## Problems found and fixed in code

1. **The vendor's `closeUDP()` segfaults** (SDK 2.0.9 aarch64), with or without stopping rotation
   first; a minimal C++ program on the Pi reproduces it. The binding no longer calls it.
2. **Stopping rotation on close cost 22 s** before clouds returned (IMU was immediate), longer than
   the node's 15 s start-up limit. The binding now leaves rotation running; the L2 spins from power-on.
3. **The first cloud from a fresh reader is stale**: it starts 16 s in the past and claims a 16 s
   duration, against 42 ms per real sweep. `LidarL2Driver` now rejects clouds longer than
   `max_sweep_seconds` (0.25 s) and reports `rejected_long_clouds`.
4. Debian 13 / Python 3.13 setup fixes (scipy, pyorbbecsdk2 `--no-deps`, `chrony-wait`), found
   before the first install.

### Correction: the L2's device clock runs at about half speed

The table above uses L2 device time, which turned out to be wrong by about 2x. A minimal C++ program
against the vendor library (15 s windows, three runs, with and without `syncLidarTimeStamp()`) gave:

- **By the Pi's NTP-locked clock:** IMU 251.5/s and clouds 12.0/s.
- **By the device timestamps:** they advance at **0.503x** host time.
- **Consequence:** the "23.8 Hz / 42 ms / 499 Hz" above are device-time figures. In real time the L2
  delivers about 12 sweeps per second of about 84 ms each, with IMU at about 251 Hz.

**Fix:** `L2DeviceClock` fits device time onto the host clock, scale and offset, from IMU arrivals.
It is used in the capture node and the local driver, and per-point times and sweep durations are
rescaled. Rechecked through the driver ([`device_check_l2_clockfit.json`](device_check_l2_clockfit.json),
15 s):

| | |
|---|---|
| Sweeps | 181 at 11.95 Hz |
| Sweep period, median (range) | 83.7 ms (83.1–84.3) |
| Sweep duration, median | 83.6 ms, so sweeps are contiguous |
| IMU | 251.4 Hz |
| Clock warm-up before the first sweep | 1.2 s (the stale cloud falls inside it) |

`live.lidar_clock_scale` can fix the slope instead of fitting it.

## First end-to-end stream: Pi to laptop over the Wi-Fi hotspot

Bring-up mode (no calibration), `config/profiles/hotspot.yaml`: 640x400 at 30 fps, zlib depth, JPEG
colour, sweep-aligned camera forwarding. Robot parked. Recorded with
`tools/record_raw.py --bringup --duration 60`; the report is
[`capture_report_wifi_stationary_60s.json`](capture_report_wifi_stationary_60s.json). The recording
(573 MB) stays on the laptop.

| | |
|---|---|
| Synchronised frames | 716 in 60.2 s (11.9 Hz), one segment, one connection |
| Losses | 0 missing clouds, 0 missing camera frames, 0 IMU breaks or missing samples, 0 out-of-order drops, 0 clock jumps |
| Receiver rates | IMU 260.8/s, clouds 12.0/s, camera 14.9/s (sweep-aligned) |
| Wi-Fi bandwidth | 5.17 MB/s average (4.2–5.5 per 5 s window) |
| Pi-to-laptop clock | error bound ±4.9 ms (best round-trip delay 9.8 ms), residual 1.7 ms, jitter 13 ms |
| Arrival latency, p50 / p95 | IMU 18 / 46 ms, cloud 99 / 126 ms (includes the 84 ms sweep), camera 53 / 70 ms |
| Replay | all 716 frames have RGB and depth; ~5190 points and 44 IMU samples per sweep; sweeps 83.6 ms; camera within ±20 ms of the sweep end (median -3.3 ms); depth valid 72 %; timestamps increasing |

### 600 s stationary over Wi-Fi

[`capture_report_wifi_stationary_600s.json`](capture_report_wifi_stationary_600s.json); the recording
(5.6 GB) stays on the laptop.

| | |
|---|---|
| Synchronised frames | 7171 in 600.2 s (11.95 Hz), one segment, one connection |
| Losses | 0 missing clouds, camera frames or IMU samples; 0 IMU breaks, out-of-order drops or clock jumps |
| Receiver rates | IMU 249.9/s, clouds 11.95/s, camera 12.8/s (sweep-aligned) |
| Bandwidth | 4.33 MB/s average |
| Pi-to-laptop clock | error bound ±0.9 ms (best round-trip 1.8 ms), jitter 11 ms |
| Arrival latency, p50 / p95 | IMU 10 / 36 ms, cloud 99 / 116 ms, camera 55 / 68 ms |
| Pi | 65 °C with no cooler fitted. **19 under-voltage events, each about 8 s, between 18:20 and 18:22 Pi time, near the end of the run** (3 A charger with `usb_max_current_enable=1`); no camera disconnects and no data loss. An official 5 A supply and the active cooler are needed for the 30-minute run. |

Wired and moving recordings are still to do. Everything here is Wi-Fi and labelled as such.

## IMU at rest

From the 60 s stationary recording ([`imu_bias_still_60s.json`](imu_bias_still_60s.json),
`tools/imu_bias_from_recording.py`): 15076 samples at 251.3 Hz, level within 0.16°.

- **Accelerometer:** mean (0.025, -0.015, 10.521) m/s². By the gravity-magnitude method the bias is
  (0.0017, -0.0010, **0.7147**) m/s². With one upright orientation only the vertical axis is loaded,
  so bias and scale are not separated.
- **Gyro:** bias below 1e-5 rad/s on every axis; noise std 0.005 rad/s on x and y.
- Before this, the parked pipeline integrated IMU errors into the station pose without limit. It now
  re-anchors every sweep at the station with zero velocity.
- `live.imu_accel_bias` (default null) applies a measured bias to deskew, for moving scans.

## Capture node as a service

`aethertwin-capture.service` is installed and enabled for `labpi`, with
`/etc/default/aethertwin-capture` set to `config/profiles/hotspot.yaml` and `--bringup`. It starts on
boot once time sync is reached, and binds wlan0's lease. Drop `--bringup` from that file once a
measured calibration is installed. The Orbbec SDK cannot write its own log file under the unit's
read-only filesystem protection; this is harmless.

## Camera

A **Gemini 336L** is attached (USB 2bc5:0807, serial CPC0000000XX, firmware 1.4.60, hardware 0.1),
not the 335L the docs assume. Only a USB 2 cable is available, so it runs at **480M (USB 2.1)**.
Over USB 2 it still offers 640x400 at 30 fps for both depth (Y16) and colour; full 1280x800 depth
is 5 fps only. The hotspot profile (`config/profiles/hotspot.yaml`) uses 640x400 at 30 fps.

### Power: two faults on the way

1. **Desktop USB port as the Pi's supply.** The camera dropped off USB at stream start, with
   "Undervoltage detected!" in the kernel log; `throttled=0x50000`.
2. **3 A wall supply.** The voltage held (5.02–5.05 V, `throttled=0x0`), but the Pi 5 limits all
   USB ports together to 600 mA on a supply below 5 A.
   - Colour alone streamed at about 28 fps.
   - Depth alone, or depth with colour, made the camera disconnect within seconds; it took about
     26 s to restart.
   - Depth with the IR laser switched off streamed 238 frames in 8 s with no disconnect. So the
     laser's draw trips the USB limit.

**Fix (on the user's go-ahead):** `usb_max_current_enable=1` added under `[all]` in
`/boot/firmware/config.txt`; the original was backed up to `config.txt.before-aethertwin`. This
allows 1.6 A on USB with this 3 A supply. After the change: 5.05 V, `throttled=0x0`, no
disconnects. Undo it by deleting the line and rebooting. An official 5 A supply would make it
unnecessary.

### Camera check through the node's driver

From [`device_check_gemini_usb2.json`](device_check_gemini_usb2.json): 30 s in bring-up mode
(no calibration), laser on, 640x400, hardware D2C alignment.

| | |
|---|---|
| Frames | 900 at 29.96 fps, RGB 400x640x3 plus aligned depth 400x640 |
| Depth valid fraction per frame | median 0.704 (0.58–0.71) |
| Depth | median 2.47 m (p5 0.54 m, p95 3.18 m), matching the L2's median range of 2.46 m from the same spot |
| Colour-to-depth timestamp offset | 0.1 ms |
| Incomplete framesets | none skipped; SDK full-frame aggregation on |
| USB disconnects, power flags | none, `throttled=0x0`, 5.05 V |

The first full check failed with "Incomplete Gemini RGB-D frameset". The driver now requests full
framesets from the SDK, skips and counts any incomplete one (`incomplete_framesets_skipped`), and
fails only when no complete frameset arrives within the timeout.

The setup log ([`bootstrap.log`](bootstrap.log)) includes the Wi-Fi drop and a power cut during the
first runs; NUL bytes left by the power cut were stripped.
