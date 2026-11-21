# Raspberry Pi capture and Windows GPU deployment

Current setup: Raspberry Pi 5 (16GB, Debian 13 Raspberry Pi OS), Unitree 4D
LiDAR L2, Orbbec **Gemini 336L** (on a USB 2 cable), an ESP32-S3 status board,
and the operator laptop (Windows 11, i9-13900H, RTX 4070 Laptop GPU) as the only
processing host, reached over its Wi-Fi hotspot. There is no A2000 desktop in
this setup; the wired addresses below are kept for a wired run. No Jetson is
required. This is a setup handoff, not a verified distributed deployment;
bring-up measurements are in [`reports/hardware/pi/`](../reports/hardware/pi/README.md).

## Responsibilities and implementation boundary

The Pi runs lightweight acquisition, timestamps and bounded buffers. Windows
runs inference, mapping, tracking, planning, training and SSD recording. The
browser renders scene state locally. ESP32 handles status/halo peripherals.

The raw-sensor transport (`ingestion/capture_node.py` on the Pi,
`ingestion/remote_source.py` on Windows, protocol in `ingestion/raw_transport.py`)
connects them; see "Raw-sensor transport" below. It is tested over loopback with
labelled synthetic devices, and on the real Pi, L2 and Gemini 336L for one 60 s
parked bring-up recording over the **Wi-Fi hotspot** (no calibration). It has not
run wired, moving, calibrated or for longer. The scene gateway is separate and
relays finished scenes, not measurements.

## Pi preparation

1. Use Raspberry Pi Imager on Windows. Select Raspberry Pi 5 and Raspberry Pi
   OS Lite **64-bit**, then the 32GB card. Writing erases that selected card.
2. Set hostname `aethertwin-pi`, a login user, a private password, Wi-Fi, India
   region/Kolkata time zone and SSH. Boot with adequate power and active cooling.
   Any user name works (the current rig uses `labpi`); the examples below
   write `aether`, so substitute yours. `deployment/lab/pi_bootstrap.sh service`
   renders the systemd unit for whoever runs it.
3. From Windows PowerShell: `ssh aether@aethertwin-pi.local`. If name resolution
   fails, use the Pi's actual address from the router's device list.
4. In the Pi session:

```sh
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git cmake build-essential python3-venv python3-dev libusb-1.0-0-dev libudev-dev usbutils
sudo reboot
```

Reconnect and inspect `uname -m` (expect `aarch64`), `df -h /`, and
`vcgencmd get_throttled` (ideally `0x0`). Keep long recordings off the SD card.

On the rig all of this, and the SDK, venv, native and time steps below, ran through
`deployment/lab/pi_bootstrap.sh` (`check|packages|sdks|env|native|network|time|inventory`), shipped
from the laptop with `PI=labpi@<lease> deployment/lab/lab_sync.sh push-pi`; log in
[`bootstrap.log`](../reports/hardware/pi/bootstrap.log).

**Debian 13 (trixie) / Python 3.13 notes** (current Imager images):
- Python is 3.13.5. The capture venv needs scipy (`requirements-pi.txt`), and `pyorbbecsdk2==2.1.2`
  is installed with `--no-deps`: its metadata pulls in av, a second (GUI) OpenCV, pygame and pynput,
  whose evdev has no wheel, and the SDK imports none of them at runtime.
- Installing chrony removes systemd-timesyncd and its wait unit, so the bootstrap enables
  `chrony-wait.service` for `time-sync.target`.

**Power:** never power the Pi from a PC USB port (it under-volted and the camera dropped off USB).
On a supply below 5 A the Pi 5 limits all USB ports together to 600 mA, which the Gemini's IR laser
tripped on the rig's 3 A supply. The rig runs with `usb_max_current_enable=1` under `[all]` in
`/boot/firmware/config.txt` (original kept as `config.txt.before-aethertwin`; delete the line and
reboot to undo). An official 5 A supply makes it unnecessary. Details in the
[Pi report](../reports/hardware/pi/README.md#power-two-faults-on-the-way).

## L2 SDK and camera SDK

Download before switching to an isolated sensor network. On the Pi:

```sh
mkdir -p ~/aethertwin/vendor
cd ~/aethertwin/vendor
git clone https://github.com/unitreerobotics/unilidar_sdk2.git
cd unilidar_sdk2
git checkout 0e3c51f512e6b8ff60b8c32f160b412cb48445c2
cmake -S unitree_lidar_sdk -B unitree_lidar_sdk/build -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build unitree_lidar_sdk/build --parallel 2

cd ~/aethertwin/vendor
git clone --branch v2.1.2 --depth 1 https://github.com/orbbec/pyorbbecsdk.git
python3 -m venv ~/aethertwin/camera-env
source ~/aethertwin/camera-env/bin/activate
python -m pip install --upgrade pip
python -m pip install pyorbbecsdk2==2.1.2
cd ~/aethertwin/vendor/pyorbbecsdk
sudo bash scripts/env_setup/install_udev_rules.sh
```

The camera SDK's ARM64 wheel works on the Pi with Python 3.13 (on Debian 13 add
`--no-deps` to the install above, see the notes in "Pi preparation"). Reconnect the
camera after installing USB rules. Do not install this repository's Windows/Ubuntu
x86 locks on the Pi.

**L2 SDK behaviour on the Pi** (SDK 2.0.9, aarch64; handled in code, details in the
[Pi report](../reports/hardware/pi/README.md#problems-found-and-fixed-in-code)):
- `closeUDP()` segfaults, so the binding never calls it.
- Stopping rotation on close cost ~22 s before clouds returned, so the L2 is left spinning.
- The first cloud from a fresh reader is stale (16 s long); `LidarL2Driver` rejects clouds longer
  than `max_sweep_seconds` and counts them in `rejected_long_clouds`.
- The device clock advances at 0.503x real time: in real time the L2 gives ~12 sweeps/s of ~84 ms and
  IMU at ~251 Hz. `L2DeviceClock` fits device time onto the host clock from IMU arrivals;
  `live.lidar_clock_scale` fixes the slope instead.
- The accelerometer reads ~10.48 m/s² at rest; that bias is not characterised yet.

## Wiring and addresses

Use a gigabit switch connecting L2, Pi and Windows. Camera connects to a Pi USB3
port using a USB3 data cable. Use the L2's supplied rated power arrangement and
the Pi's appropriate supply; Ethernet is not a power connection. A powered USB3
hub may be needed if camera power is unstable.

As built on the cart: the L2 plugs straight into the Pi's Ethernet (link 100 Mb/s
full duplex), the camera is on a USB 2 cable (480M, see "Individual device checks"),
and the Pi reaches the laptop over Wi-Fi ("Wi-Fi hotspot mode" below). The switch
is needed only for a wired run, with the laptop's USB-Ethernet adapter as the
Windows host.

| Device | Wired address |
|---|---|
| L2, official example defaults | 192.168.1.62 |
| Pi | 192.168.1.2/24 |
| Windows (laptop adapter in a wired run) | 192.168.1.10/24 |

No gateway/DNS is needed on this isolated wired network. If Wi-Fi also uses
192.168.1.x, finish downloads and establish wired SSH before disconnecting Wi-Fi
for the initial test. Do not change a shared lab router to resolve this conflict.

On Pi, inspect `nmcli device status`. For an Ethernet interface named `eth0`:

```sh
sudo nmcli connection add type ethernet ifname eth0 con-name aether-lidar ipv4.method manual ipv4.addresses 192.168.1.2/24 ipv4.never-default yes ipv6.method disabled
sudo nmcli connection up aether-lidar
```

Replace `eth0` with the observed interface name if different. On Windows run
`ncpa.cpl`, open the switch-connected Ethernet adapter's IPv4 properties, set
192.168.1.10 and mask 255.255.255.0, and leave gateway/DNS blank. Reconnect using
`ssh aether@192.168.1.2`. Keep firewall protection enabled.

## Individual device checks

On the Pi, with the L2 powered:

```sh
cd ~/aethertwin/vendor/unilidar_sdk2
./unitree_lidar_sdk/bin/example_lidar_udp
```

This official example sets the work mode and resets the sensor. Require both
point-cloud and IMU messages, not just successful initialization. Its ports are
L2 UDP6101 and Pi UDP6201. Stop the example with Ctrl+C.

For the camera, inspect `lsusb -t` for USB3 speed (typically 5000M), then:

```sh
source ~/aethertwin/camera-env/bin/activate
cd ~/aethertwin/vendor/pyorbbecsdk
python examples/beginner/01_hello_camera.py
```

Retain serial number, firmware and reported profiles. Then check both devices
through the capture node's own drivers, one JSON report each (`--skip l2` or
`--skip gemini` for one device; the rig's reports are in `reports/hardware/pi/`):

```sh
cd ~/aethertwin/src
~/aethertwin/capture-env/bin/python tools/pi_device_check.py --config config/profiles/hotspot.yaml --seconds 15 --output ~/aethertwin/evidence/device_check.json
```

On the rig the camera has only a USB 2 cable, so `lsusb -t` shows 480M. Over
USB 2 the Gemini 336L offers 640x400 at 30 fps for depth and colour (with hardware
D2C alignment), but 1280x800 depth at 5 fps only; the hotspot profile uses
640x400. The wired default profile (1280x800 at 30 fps) would need a USB 3 data
cable and has not run. Uncompressed 16-bit
depth at that profile is about61MB/s; additional uncompressed colour can exceed
gigabit capacity. The bridge needs bounded queues, lossless depth compression,
appropriate colour compression and measured resolution/rate budgets.

## Raw-sensor transport (Pi capture node to Windows)

What it does: the Pi runs the L2 SDK binding and the Gemini SDK and forwards,
per message type, every IMU sample, every L2 sweep (xyz, intensity, per-return
time offset, ring) and every aligned RGB-D frame, with the raw device timestamps
and the Pi's monotonic capture time. Windows assembles sweeps with the same
`LidarL2Driver` logic and synchronises with the same `SensorSynchronizer` as a
local connection, so `main.py --mode live` and the studio consume it the same
way. Remote mode adds its own reconnect and segment behaviour (below), which has
not yet run on the real devices.

- **Wire:** versioned, length-prefixed binary messages over one TCP connection
  (port 7600). Camera frames are sent in 1 MiB fragments after IMU and LiDAR, so
  a slow camera cannot starve them: an IMU batch queues behind at most one
  fragment on the node (an estimate of about 8 ms on gigabit; kernel send
  buffers can add more; not yet measured on a network).
- **Security:** the node binds only to 192.168.1.2 (never to all interfaces),
  accepts only listed peers (192.168.1.10; on the Wi-Fi hotspot 192.168.137.1,
  see "Wi-Fi hotspot mode" below), and both ends prove a shared 32-byte
  key (mutual HMAC-SHA256 challenge). Every later message carries an HMAC tag and
  a strict sequence number, so injected, replayed or reordered messages close the
  connection. There is no encryption: keep the sensor network private.
- **Queues and drops:** IMU is never dropped silently; a node-side overflow is
  counted, announced, and breaks the sample sequence, which makes Windows start a
  new segment. Sweeps and camera frames drop oldest-first and are counted on both
  ends. All counters appear in each frame's `diagnostics.raw_transport`.
- **Time:** Windows maps the Pi's monotonic clock onto its own with a ping
  exchange every 0.5 s (lowest-delay sample, slewed at most 0.1 ms/s). A clock
  step, a backwards or future capture time, a broken IMU sequence, a stall
  (3 s without any message) or a reconnect starts a **new segment**: buffers
  are discarded and no sweep or IMU interval spans the boundary
  (`diagnostics.acquisition_segment`, `segment_start`). Repeated short sessions
  (five within 5 s each) become a fatal error instead of an endless loop. The
  host-to-node mapping does not need chrony, but **the Pi's clock must be set
  before the node starts and must not step while capturing**: the L2 SDK
  synchronises the LiDAR to the Pi's UTC once and the node's epoch-to-monotonic
  offset is fixed at start. Enable time sync on the Pi
  (`sudo systemctl enable systemd-time-wait-sync`, or `chrony-wait.service` on
  Debian 13 with chrony, as `pi_bootstrap.sh time` does, so the unit's
  `time-sync.target` waits); `raw_transport.node_utc_steps` counts steps and
  `raw_transport.clock.utc_offset_seconds` shows how well the hosts' UTC agree.
- **Faults:** a failed LiDAR or camera on the Pi is an explicit fault that stops
  live processing with the node's error; nothing is simulated. Windows refuses a
  node running `--synthetic` devices and a node whose calibration file differs
  (SHA-256) from its own.
- **Bandwidth (1280x800 at 30 fps):** depth is sent `raw` by default (lossless,
  61 MB/s, no CPU on the Pi). Colour is forwarded as the device's own bytes
  (`device`; with MJPG that is a few MB/s and decodes exactly as a local
  connection would). Together about half of gigabit. `png`/`zlib` depth are
  also lossless and were about 2.4x smaller on synthetic images, at about 30 ms
  per frame on a laptop i9, so measure them on the Pi with real frames first. `jpeg` colour is lossy and
  says so in the frame diagnostics.

Measured so far: laptop loopback with synthetic devices in `reports/raw_transport/`
(**not** a Pi or network result), and the first real-device run over the Wi-Fi
hotspot in [`reports/hardware/pi/`](../reports/hardware/pi/README.md#first-end-to-end-stream-pi-to-laptop-over-the-wi-fi-hotspot).
Hardware recordings are made with `tools/record_raw.py` (`docs/LAB_DAY.md`).

### Set it up

1. Create the shared key on Windows (it is git-ignored) and copy it to the Pi
   over SSH:

```powershell
.\.venv\Scripts\python.exe -m ingestion.capture_node --generate-key config\capture_node.key
scp config\capture_node.key aether@192.168.1.2:/home/aether/aethertwin/src/config/capture_node.key
ssh aether@192.168.1.2 chmod 600 /home/aether/aethertwin/src/config/capture_node.key
```

   From Git Bash on the rig's laptop the same copy is
   `PI=labpi@<lease> deployment/lab/lab_sync.sh push-secret config/capture_node.key`.

2. On the Pi, clone this repository to `~/aethertwin/src` (same commit as
   Windows), create `~/aethertwin/capture-env` from
   `deployment/capture_node/requirements-pi.txt`, build the native L2 binding into
   it (`native/README.md`), and copy the **same measured calibration file** that
   Windows uses to `config/` (the node validates the camera against it and
   Windows checks that both files are identical).
3. Try it in the foreground first, then install the systemd unit. The unit in
   `deployment/capture_node/` is a template; the bootstrap fills in the user that
   runs it and that user's home (it refuses root), and `unit` prints the result
   without installing:

```sh
cd ~/aethertwin/src
~/aethertwin/capture-env/bin/python -m ingestion.capture_node --config config/system_config.yaml --status-interval 5
bash deployment/lab/pi_bootstrap.sh unit            # review: User=, WorkingDirectory=, ExecStart=
ENABLE=1 bash deployment/lab/pi_bootstrap.sh service
journalctl -u aethertwin-capture -f
```

4. On Windows, set `live.source: remote` (or pass `--source remote` to
   `main.py`, `--live-source remote` to `command_center`) and run live mode as
   usual; add `--record DIR` to keep the raw segments on the SSD and replay them
   with `--mode replay --input DIR`.

### Wi-Fi hotspot mode (moving cart)

When the rig rides on the cart, the L2 stays on the Pi's Ethernet port (static
192.168.1.2 to the L2 at 192.168.1.62, unchanged) and the Pi reaches the laptop
over the laptop's Windows Mobile Hotspot: laptop 192.168.137.1, Pi on a DHCP
lease such as 192.168.137.37 on `wlan0`. `config/profiles/hotspot.yaml` (used
on both ends) sets this up. It has run once on the real devices: 60 s parked, in
bring-up mode, 716 synchronised frames with no loss at 5.17 MB/s
([report](../reports/hardware/pi/README.md#first-end-to-end-stream-pi-to-laptop-over-the-wi-fi-hotspot)).
Moving, calibrated and longer runs are not done; the budget and matrix numbers
below are synthetic loopback on the laptop.

- **Budget:** at most 5 MB/s (40 Mbit/s) offered. That is about half of an
  *assumed* 80 Mbit/s sustained TCP from the Pi's single-stream onboard Wi-Fi
  on the 5 GHz hotspot band; a moving cart loses rate with distance and
  obstruction, and 2.4 GHz is far slower. Measure the real link first (below) and
  re-choose if it is lower. The wired default offers ~64 MB/s, so it cannot run
  over Wi-Fi unchanged.
- **What the profile changes:** camera 640x400 at 30 fps (a quarter of the
  pixels; keep 30 fps because Windows fuses a sweep only with a camera frame
  within 20 ms of its end), depth `zlib` (lossless), colour `jpeg` quality 80
  (lossy, labelled), `camera_forward: sweep` (the node sends only the frames
  within 20 ms of a predicted sweep end, about 10 of 30 per second, and never
  encodes the rest), 64 KiB fragments (IMU waits behind at most ~13 ms of camera
  at 5 MB/s), `bind_interface: wlan0`, `allowed_peers: [192.168.137.1]`, and
  `live.capture_host: null` (Windows asks for `--capture-host`).
- **Calibration:** a calibration is measured at one image size, so the profile
  reads `config/calibration_extrinsics_640x400.json` (same file on both ends,
  measured at 640x400; none exists yet). The Gemini 336L delivers 640x400 depth
  plus colour at 30 fps with hardware D2C alignment over USB 2
  (`reports/hardware/pi/device_check_gemini_usb2.json`); the driver refuses an
  unsupported pair and lists the supported profiles. On the rig, sweep-aligned
  forwarding sent 14.9 camera frames/s with ~12 sweeps/s.
- **Bandwidth on the synthetic loopback matrix** (laptop i9, not Wi-Fi or Pi;
  table in `reports/raw_transport/README.md`): the profile offers 4.2 MB/s
  (34 Mbit/s), of which ~1.55 MB/s is L2 points and IMU, and still fused
  every sweep (9.8 Hz). 848x480 would offer 5.8 MB/s (46 Mbit/s); a 424x240
  fallback for a weak or 2.4 GHz link offers 2.7 MB/s (21 Mbit/s) and needs
  its own calibration. Compressed depth at 1280x800 and 30 fps could not keep
  up even on the i9.

Set it up:

1. On the laptop: Settings > Network & internet > Mobile hotspot, choose the
   5 GHz band if offered, turn it on. On the Pi, join it yourself and type the
   password at the prompt (never store it in the repository):
   `sudo nmcli --ask device wifi connect "<hotspot SSID>"`. The lease:
   `ip -4 addr show wlan0` on the Pi, or the hotspot page's device list on the
   laptop. Turn Wi-Fi power save off for that connection (done on the rig):
   `sudo nmcli connection modify "<connection name>" 802-11-wireless.powersave 2`,
   then `sudo nmcli connection up "<connection name>"`; `iw dev wlan0 get power_save`
   should print `off`.
2. Measure the link the way the data flows (Pi to laptop). With `iperf3`
   installed on both (`sudo apt-get install iperf3` on the Pi): on the Pi
   `iperf3 -s`, on the laptop `iperf3 -c 192.168.137.37 -R -t 60` (`-R` makes
   the Pi send; the laptop opens the connection, so no Windows firewall rule).
   Repeat while pushing the cart around the survey area. Record the result
   next to the soak reports.
3. On the Pi, foreground first, then the service with the hotspot config:

```sh
cd ~/aethertwin/src
~/aethertwin/capture-env/bin/python -m ingestion.capture_node --config config/profiles/hotspot.yaml --status-interval 5
CAPTURE_CONFIG=config/profiles/hotspot.yaml ENABLE=1 bash deployment/lab/pi_bootstrap.sh service
```

   Until a measured 640x400 calibration exists, run the foreground node with
   `--bringup` and record with `tools/record_raw.py --config
   config/profiles/hotspot.yaml --bringup --capture-host <lease>`; the service
   runs without `--bringup` and needs the calibration and the key in place.
   The node binds whatever address `wlan0` holds at start. `--bind
   192.168.137.37` pins one address instead, `--bind-interface wlan1` names
   another adapter, `--allow ADDRESS` (repeatable) replaces the peer list. For
   the service put extra flags in `/etc/default/aethertwin-capture` as
   `AETHERTWIN_CAPTURE_ARGS=--bind 192.168.137.37`. Without a lease the node
   exits (`bind_failed`) and systemd retries every 3 s; if the lease changes
   while running it exits (`address_changed`) and restarts on the new address.
   Back to wired: `CAPTURE_CONFIG=config/system_config.yaml ENABLE=1 bash
   deployment/lab/pi_bootstrap.sh service`.
4. On the laptop (it connects out to the Pi, so Windows needs no inbound rule):

```powershell
.\.venv\Scripts\python.exe -m command_center --mode live --config config\profiles\hotspot.yaml --capture-host 192.168.137.37
```

   `main.py` and `tools/record_raw.py` take the same `--capture-host`. A new
   lease means a new `--capture-host`.

What to watch (measured so far only for the 60 s parked run above; moving runs
not yet): the node status
lines (`camera_message_bytes_p50`, `camera_encode_ms_p95`, `camera_skipped`,
`camera_dropped`) and Pi CPU/temperature; Windows `diagnostics.raw_transport`
(`bandwidth_bytes_per_second`, `stalls`, `clock_jumps`, `segment`). Wi-Fi
latency jitter is far larger than on the cable: the clock mapping uses the
lowest-delay ping, but a jittery link may trip `capture_clock_jump_seconds`
(20 ms) or the 3 s stall timeout and start new segments. If the link falls below
the offered load, the node drops camera frames oldest-first (counted) and IMU
and sweeps keep priority, but data already in the Pi's kernel send buffer still
delays them. If the camera's selected colour format turns out to be MJPG,
`color_codec: device` forwards it without decoding and re-encoding on the Pi;
check the size it sends first.

Before the hardware exists you can rehearse the whole path on one machine:
`python -m ingestion.capture_node --synthetic --bind 127.0.0.1 --allow 127.0.0.1
--key-file config\capture_node.key` and `tools/raw_transport_soak.py`.

## Windows GPU environment

Install the NVIDIA driver for the actual card and check `nvidia-smi`. In a
PowerShell window opened in the source root, with Python3.12 available:

```powershell
py -3.12 -m venv .venv-gpu
.\.venv-gpu\Scripts\python.exe -m pip install --upgrade pip
.\.venv-gpu\Scripts\python.exe -m pip install --require-hashes -r requirements-windows-cuda.lock
.\.venv-gpu\Scripts\python.exe -m pip check
.\.venv-gpu\Scripts\python.exe -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0)); x=torch.randn(512,512,device='cuda'); y=x@x; torch.cuda.synchronize(); print('GPU calculation passed')"
.\.venv-gpu\Scripts\python.exe main.py --mode sim --device cuda --headless --max-frames 10 --output artifacts/gpu-smoke
.\.venv-gpu\Scripts\python.exe -m command_center --mode sim --device cuda --open-browser
```

`requirements-windows-cuda.lock` is hash-locked and differs from the CPU lock
only in `torch==2.14.1+cu126` (regenerate with
`python tools/lock_dependencies.py --platform windows-cuda`, which needs uv). It
was validated on the development laptop (RTX 4070 Laptop GPU, driver 572.60):
clean install with `--require-hashes`, `pip check` clean, CUDA 12.6 / cuDNN
9.10.2, pytest 369 passed / 2 skipped, see `reports/gpu/windows_cuda_lock_laptop.json`.
That laptop is the rig's processing host. On any other GPU, install a current
NVIDIA driver and check that `nvidia-smi` reports CUDA 12.6 or newer before installing. Occupancy and planning still run on the
CPU. These commands are a simulation test, not proof of a connected Pi or real
sensor accuracy.

## Before surveying the whole lab

Validate the raw-sensor transport on the real devices (rates, drops,
bandwidth and clock residuals from `diagnostics.raw_transport`); make a short
recording and replay it successfully on Windows. Rigidly mount both sensors, measure extrinsics,
verify full calibration observability and timing, and test stationary and moving
captures. The included calibration is explicitly simulation-only. Follow
[physical commissioning](PHYSICAL_COMMISSIONING.md), independently checking
dimensions and unknown/occluded coverage. Identify the ESP32 board and wiring
before flashing. Add authenticated private viewer access only after local
operation works; keep raw sensor ports private.

## Primary references checked for this handoff

- [Raspberry Pi installation](https://www.raspberrypi.com/documentation/computers/getting-started.html)
- [Unitree SDK pinned revision](https://github.com/unitreerobotics/unilidar_sdk2/tree/0e3c51f512e6b8ff60b8c32f160b412cb48445c2)
- [Orbbec Python SDK v2.1.2](https://github.com/orbbec/pyorbbecsdk/tree/v2.1.2)
- [PyTorch version-specific packages](https://pytorch.org/get-started/previous-versions/)
