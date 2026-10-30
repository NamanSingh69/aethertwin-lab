# Hardware setup guide (for the person at the bench)

This guide is for a human setting up the physical rig. The software side is
continued by an agent following `NEXT_AGENT.md`. Detailed command references are
in `docs/RASPBERRY_PI_WINDOWS.md` and `docs/PHYSICAL_COMMISSIONING.md`; this page
explains the order, the physical decisions and what to check with your own eyes.

Nothing in the repository has been verified on this hardware yet. Every step
below is a first-time bring-up; record what you observe (photos, serial numbers,
firmware versions, measured numbers) so the agent can put it in the evidence.

## 0. Parts checklist

| Item | Role | Notes |
|---|---|---|
| Windows desktop (NVIDIA A2000 12 GB, 32 GB RAM, SSD with ≥ 200 GB free) | Runs inference, mapping, tracking, planning, recording, the browser studio | GPU/VRAM are user-reported; the agent measures them |
| Raspberry Pi 5 (16 GB) + official 27 W USB-C supply + active cooler | Sensor capture node | Under-powered Pis throttle and drop USB devices |
| 32 GB microSD (A2 class) | Pi OS only | Never record long captures to the SD card |
| Optional USB3 SSD for the Pi | Short local buffers if needed | |
| Unitree 4D LiDAR L2 + its supplied power adapter and cable | 3D LiDAR + IMU over Ethernet | Ethernet does not power it |
| Orbbec Gemini 335L + USB3 **data** cable (short, ≤ 1.5 m) | RGB-D camera on the Pi | Charge-only or USB2 cables make it fail or run slow |
| Optional powered USB3 hub | Stable camera power if it disconnects | |
| Unmanaged gigabit Ethernet switch (≥ 5 ports) + 3 Cat6 patch cables | Private sensor network: L2, Pi, Windows | Keep it separate from the lab router |
| ESP32 DevKit V1 (ESP32-WROOM-32) + USB data cable | Advisory status/halo display | Other ESP32 boards need a different firmware profile |
| 24-pixel WS2812B ring (GRB order) | Halo LEDs | |
| 5 V regulated supply for the ring (≥ 2 A for 24 LEDs at full white; the host defaults to 0.25 brightness) | LED power | Do **not** power the ring from the DevKit's 5 V pin at full brightness |
| 74AHCT125 (or similar) 3.3 V→5 V level shifter, 330–470 Ω resistor on data, 1000 µF capacitor across ring power | Reliable LED data and inrush protection | Many rings work without a shifter, but it is the safe choice |
| Optional SSD1306 128×64 I²C OLED (address 0x3C) | Status text | Can be disabled with `HALO_ENABLE_OLED=0` |
| Rigid mount (aluminium plate/extrusion), M3/M4 hardware, thread-locker | Holds L2 and camera in a fixed relative pose | Calibration is only valid while nothing moves relative to each other |
| Tape measure / laser distance meter, printed checkerboard or ChArUco target on a flat board | Calibration and independent dimension checks | |

## 1. Build the rigid sensor mount (before any calibration)

1. Fix the L2 and the Gemini 335L to **one rigid plate**, both facing the same
   general direction, with the camera's field of view inside the LiDAR's.
   Keep the lens and LiDAR window unobstructed; route cables so they cannot pull
   on either sensor.
2. Use thread-locker; do not use tape, hot glue or adjustable clamps on the
   sensor-to-plate joints. If either sensor moves later, calibration is void.
3. Mark the rig's forward direction (+X) on the plate with an arrow. The halo's
   pixel 0 must point along this arrow, pixel 6 to the left (+Y), pixel 12
   backwards and pixel 18 to the right.
4. Photograph the mount from three sides with a ruler in frame and give the
   photos to the agent (they go into the commissioning record).

## 2. Network wiring (private sensor network)

```
 [L2 LiDAR]──┐
             ├──[gigabit switch]──[Windows desktop: 2nd Ethernet port or USB-Ethernet]
 [Pi 5]──────┘
 [Gemini 335L]──USB3──[Pi 5]        [ESP32]──USB──[Windows desktop]
```

| Device | Address (no gateway, no DNS) |
|---|---|
| L2 (factory default in the official examples) | 192.168.1.62 |
| Raspberry Pi 5 | 192.168.1.2 / 255.255.255.0 |
| Windows desktop (sensor adapter) | 192.168.1.10 / 255.255.255.0 |

- Keep this switch **off** the lab/office router. The desktop keeps internet on
  its other adapter (Wi-Fi or a second NIC).
- If your home/lab Wi-Fi also uses 192.168.1.x, finish all downloads first, then
  disconnect the Pi's Wi-Fi while testing, as described in
  `docs/RASPBERRY_PI_WINDOWS.md` ("Wiring and addresses").
- Windows: `Win+R` → `ncpa.cpl` → the switch-connected adapter → IPv4 →
  192.168.1.10, mask 255.255.255.0, gateway and DNS empty. Keep the firewall on;
  the agent will add narrow allow rules for the transport port when it exists.

## 3. Raspberry Pi preparation

Follow `docs/RASPBERRY_PI_WINDOWS.md` sections "Pi preparation", "L2 SDK and
camera SDK", "Wiring and addresses" and "Individual device checks". In short:

1. Raspberry Pi Imager → Raspberry Pi 5 → **Raspberry Pi OS Lite 64-bit** →
   the microSD card. In the settings dialog set hostname `aethertwin-pi`, user
   `aether`, a private password, your Wi-Fi (for downloads), locale/time zone,
   and enable SSH with password or (better) your public key.
2. Boot with the official supply and the active cooler fitted. From Windows:
   `ssh aether@aethertwin-pi.local` (or the IP from your router).
3. Run the apt commands from the doc, reboot, and check:
   `uname -m` → `aarch64`; `vcgencmd get_throttled` → `0x0`
   (anything else means power or heat problems; fix before continuing).
4. Build the pinned Unitree SDK and install `pyorbbecsdk2==2.1.2` exactly as in
   the doc, including the Orbbec udev rules; unplug/replug the camera afterwards.
5. Give the Pi its static wired address with `nmcli` (doc gives the command).
6. Enable time sync: the Pi and the desktop must agree on time. Install
   `chrony` on the Pi (`sudo apt install -y chrony`) and let the agent configure
   the desktop as the Pi's time source on the private network; record
   `chronyc tracking` output.

## 4. First device checks (with you watching)

1. Power the L2 from its own adapter, plug it into the switch, wait ~30 s.
   On the Pi: `./unitree_lidar_sdk/bin/example_lidar_udp` from the SDK folder.
   You must see **both** point-cloud and IMU messages streaming. Ctrl+C to stop.
2. Plug the camera into a **blue USB3 port** on the Pi. `lsusb -t` must show
   `5000M` for it. Run the Orbbec `01_hello_camera.py` example; note the serial
   number and firmware version it prints.
3. Leave both running for 10 minutes and run `vcgencmd get_throttled` again. If
   it is not `0x0`, or the camera disconnects, add the powered USB3 hub and/or
   improve cooling.

Hand the outputs (copy/paste or screenshots) to the agent.

## 5. ESP32 halo wiring (do not flash yet)

The firmware targets an **ESP32 DevKit V1 (ESP32-WROOM-32)**. Confirm your board
model from the chip markings before anything is flashed.

| Connection | From | To |
|---|---|---|
| LED data | ESP32 GPIO18 → (level shifter) → 330–470 Ω resistor | Ring DIN |
| LED power | 5 V regulated supply + | Ring 5V / VCC |
| Common ground | Supply −, ESP32 GND, ring GND, shifter GND | all joined |
| Capacitor | 1000 µF across ring 5V and GND, close to the ring | |
| OLED (optional) | ESP32 3V3, GND, GPIO21 (SDA), GPIO22 (SCL) | OLED VCC, GND, SDA, SCL |
| Host | ESP32 USB | Windows desktop USB |

Mount the ring flat on the rig with pixel 0 on the forward arrow. Power the ring
supply **before** connecting LED data, and never run the ring at full white from
USB power. Then tell the agent the board model, the ring's LED part number and
the Windows COM port (Device Manager → Ports). The agent builds the firmware,
asks you before flashing, and checks the colours with you:
blue = disconnected, magenta = source watchdog, amber = stale, teal / yellow /
orange / red = fresh severities by distance (3 / 2 / 1 m). The halo is an
advisory display only; it does not stop a robot or replace any safety interlock.

## 6. Calibration session (needs you physically)

The agent will tell you when the transport works and short recordings replay.
Then:

1. Camera intrinsics: move the printed checkerboard/ChArUco through the whole
   image (corners, edges, tilted ±45°, near and far) for 2–3 minutes while the
   agent records.
2. Camera-to-LiDAR extrinsics: place the flat target (or several boxes with
   sharp corners) at 8–15 positions spread through the useful volume (near,
   far, left, right, high, low). Hold each pose still for 3 s.
3. Keep a separate set of 3–5 poses for **held-out** validation; the agent must
   score the calibration on poses it did not fit.
4. Measure 5–10 real distances with a tape/laser (wall to wall, bench length,
   door width, ceiling height) and write them down; these become the
   independent accuracy check.

## 7. Commissioning scan and fixed install

Follow `docs/PHYSICAL_COMMISSIONING.md` steps 5–8 with the agent:

1. Clear people and carts from the lab. Walk the rig slowly (≈ 0.3 m/s) around
   the room with overlap, past benches and clutter, closing a loop back to the
   start. Avoid pointing at windows/mirrors for long.
2. Mount the rig at its permanent position; the agent relocalizes it against
   the scan and marks areas it cannot see.
3. Choose real start/goal points and protected zones with the agent (the
   simulator's coordinates and demo zones are not valid for your lab).
4. Walk through the lab, push a cart, place a new box: the agent records these
   for the tracking and safety checks.

## 8. 30-minute live run

With everything running from the desktop, leave the system live for 30 minutes
while people move normally. Do not touch the rig. The agent records latency,
memory, drops, render frame times, recording throughput and temperatures and
reports them as a hardware result for your exact devices.

## Safety and honesty rules

- This system does not command any robot, motor or relay. Planned routes are
  advisory, and the halo is a display.
- Keep the sensor network private; the browser gateway is the only thing that
  may be exposed, and only with TLS and separate viewer/operator credentials.
- Until a measurement exists, the UI says "Hardware not tested" and reports keep
  `hardware_verified: false`. That is intentional; do not ask the agent to
  change it without the measured evidence.
