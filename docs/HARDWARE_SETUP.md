# Hardware setup guide (for the person at the bench)

This guide is for the human building and handling the physical rig. The agent continues the
software side following `docs/LAB_DAY.md` (the step list, with what is done) and `NEXT_AGENT.md`.
Commands are in `docs/RASPBERRY_PI_WINDOWS.md`; the procedure is `docs/PHYSICAL_COMMISSIONING.md`.

Device bring-up has happened; what was measured is in
[`reports/hardware/pi/README.md`](../reports/hardware/pi/README.md) and
[`reports/hardware/halo/README.md`](../reports/hardware/halo/README.md). Calibration, commissioning,
the LEDs, wired transport and long runs are **not** done, and nothing is hardware-verified. Keep
recording what you observe (photos, serials, measured numbers) so the agent can put it in the evidence.

## 0. Parts as used

| Item | Role | Notes |
|---|---|---|
| Operator laptop: Windows 11, i9-13900H, RTX 4070 Laptop GPU | The only processing host: inference, mapping, recording, the studio; runs the agent | Its Mobile Hotspot is the Pi's network |
| Raspberry Pi 5 16 GB, active cooler, microSD | Capture node (Debian 13 Raspberry Pi OS, user `labpi`) | Never record long captures to the SD card |
| Pi supply: 3 A USB-C wall adapter | Works only with the USB current setting in section 3; an official 5 A (27 W) supply is better | Never a PC USB port |
| Unitree 4D LiDAR L2 + its own power adapter | 3D LiDAR + IMU, Ethernet straight into the Pi | Ethernet does not power it |
| Orbbec **Gemini 336L** (not 335L) + a **USB 2** cable | RGB-D camera on the Pi | USB 2 limits it to 640x400 at 30 fps (section 2) |
| ESP32-S3 DevKitC-style board (16 MB flash, 8 MB PSRAM, two USB-C ports) | Advisory status board ("halo") on the laptop | Flashed and link-tested; no LED ring yet |
| 24-pixel WS2812B ring (GRB), 5 V supply ≥ 2 A, 74AHCT125 level shifter, 330–470 Ω resistor, 1000 µF capacitor | Halo LEDs | **Not yet obtained/wired** |
| Hand-pushed robot cart (2WD chassis) | Carries the L2, the camera and the Pi | Its Arduino Uno + L298N stay off, batteries removed, never connected to AetherTwin |
| Floor power strip | L2 adapter and Pi supply | Cords trail from the cart |
| Desktop monitor + Portronics USB-C hub (HDMI) | Calibration target (`tools/screen_checkerboard.html`) | Driven from the laptop |
| Rigid plate/bracket, M3/M4 hardware, thread-locker | Holds the L2 and camera in a fixed relative pose | Calibration is valid only while nothing moves |
| Tape measure | Mount offsets, monitor size, room dimensions for the accuracy check | |
| Optional: unmanaged gigabit switch + USB-Ethernet adapter for the laptop (RTL8153 / AX88179) + Cat6 | Wired transport | Use **if available**; not done yet |

## 1. Mount the sensors on the cart (before calibration)

1. Fix the L2 and the camera to **one rigid plate**, both facing forward, the camera's view inside the
   LiDAR's. Bolt the plate to the cart. Use thread-locker; no tape, hot glue or adjustable clamps on the
   sensor-to-plate joints. If either sensor moves later, calibration is void.
2. Put the L2 high on the cart so as little of the cart as possible is in its view. Whatever it does
   see (frame, wheels, cables) is measured once and masked (`LAB_DAY.md` step 2).
3. Mark forward (+X) on the plate with an arrow. The halo's pixel 0 points along it, pixel 6 to the
   left (+Y), pixel 12 backwards, pixel 18 to the right.
4. Fix the Pi with its cooler free to breathe; strain-relieve the L2 Ethernet, the camera USB cable and
   both power cords at the cart so a tug on a trailing cord never reaches a sensor. Lay cords flat and
   behind the cart; their length limits how far the cart can go.
5. Keep heavy parts low and push slowly; the motors are never used.
6. Tape-measure for the agent: LiDAR height above the floor; the cart's outer length, width and height;
   the LiDAR's offset from the footprint centre; the camera lens relative to the LiDAR (forward / left /
   up, cm). Photograph the mount from three sides with a ruler in frame.

## 2. Network and USB (as built)

```
 [L2 192.168.1.62]──Ethernet──[Pi 5 eth0 192.168.1.2]      [Gemini 336L]──USB 2──[Pi 5]
 [Pi 5 wlan0, DHCP 192.168.137.x] ~~Wi-Fi~~ [laptop Mobile Hotspot 192.168.137.1]
 [ESP32-S3]──native USB (COM8)──[laptop]                    [monitor]──HDMI──[USB-C hub]──[laptop]
```

- The L2 link came up at 100 Mb/s full duplex with no ping loss.
- The laptop's Mobile Hotspot (5 GHz if offered) carries everything from the Pi; Wi-Fi power save is
  off on the Pi's hotspot connection. A new DHCP lease changes the Pi's address; read it on the
  hotspot settings page.
- The camera has only a USB 2 cable: it runs at 480M and offers 640x400 at 30 fps for depth and
  colour (full 1280x800 depth is 5 fps only). The Wi-Fi budget needs 640x400 anyway.
- **Wired, if a switch and adapter are available:** L2, Pi and the laptop's USB-Ethernet adapter on the
  switch, laptop at 192.168.1.10 (`deployment/lab/windows_sensor_adapter.ps1`, run as administrator).
  Keep that switch off any lab router.

## 3. Power (lessons from bring-up)

- **Never power the Pi from a PC USB port.** It under-volted and the camera dropped off USB.
- On a supply below 5 A the Pi 5 limits all its USB ports together to 600 mA, and the camera's IR laser
  tripped that. The fix on this Pi is `usb_max_current_enable=1` in `/boot/firmware/config.txt` (original
  kept as `config.txt.before-aethertwin`; delete the line and reboot to undo). An official 5 A supply
  makes it unnecessary.
- Check `vcgencmd get_throttled` reads `0x0` before and after every long run; anything else is a
  power or heat problem to fix first.
- The L2 always runs from its own adapter.

## 4. Raspberry Pi preparation (done)

Done once with `deployment/lab/pi_bootstrap.sh` (packages, SDKs, capture venv, native binding, static
`eth0`, chrony, inventory); see `docs/RASPBERRY_PI_WINDOWS.md`. You only need to repeat it for a new SD
card: Raspberry Pi Imager, 64-bit Raspberry Pi OS, your own login and password (the agent never needs
it), SSH with public-key authentication using the laptop's `~/.ssh/aethertwin_ed25519.pub`.

## 5. Device checks (done; repeat after changes)

The agent runs `tools/pi_device_check.py` on the Pi (L2 and camera through the capture node's own
drivers, one JSON report). You only watch that the L2 spins and the camera stays connected. Re-run it
after any cable, supply or mount change.

## 6. Status board (ESP32-S3)

The board has two USB-C ports and the firmware has a build for each
(`python hardware_halo/build_firmware.py --board ...`):

| Port | Windows | Build | Notes |
|---|---|---|---|
| labelled UART (CH343 bridge) | COM7 | `esp32s3` | Opening the port does not reset the board |
| native USB | COM8 | `esp32s3-usb` (**currently flashed**) | Opening the port resets the board (about 1 s "disconnected") |

The older ESP32 DevKit V1 build (`--board esp32`) is still there for that board. Flashing needs your
explicit "flash" and the port name.

Ring wiring for the S3 (not yet done):

| Connection | From | To |
|---|---|---|
| LED data | GPIO18 → level shifter → 330–470 Ω resistor | Ring DIN |
| LED power | 5 V regulated supply + | Ring 5V |
| Common ground | Supply −, ESP32 GND, ring GND, shifter GND | all joined |
| Capacitor | 1000 µF across ring 5V and GND, close to the ring | |
| OLED (optional) | 3V3, GND, GPIO8 (SDA), GPIO9 (SCL) | OLED VCC, GND, SDA, SCL |

Mount the ring flat with pixel 0 on the forward arrow. Power the ring supply **before** connecting
data, and never run the ring at full white from USB power. Then you watch each colour with the agent:
blue = disconnected, magenta = source watchdog, amber = stale, teal / yellow / orange / red = fresh
severities by distance (3 / 2 / 1 m). The halo is advisory only; it stops nothing.

## 7. Calibration with the monitor (needs you)

The target is the desktop monitor showing [`tools/screen_checkerboard.html`](../tools/screen_checkerboard.html),
an offline page that draws 8 x 6 squares (7 x 5 inner corners) centred on the monitor's whole front
face, because the LiDAR sees the face, not just the picture. Step by step in `docs/LAB_DAY.md` step 5:

1. Monitor on the laptop through the hub's HDMI; Windows scaling 100 %, browser zoom 100 %.
2. Tape-measure the lit picture area and each side of the frame in mm, enter them, **Show target**,
   check "across 7 squares" with the tape, then send the agent the **Copy numbers for Claude** line.
3. Keep the face 0.3 m or more from walls. While the agent records, make 12–20 holds of ~3 s
   (push the cart and swivel or tilt the monitor: near and far, left and right, high and low, tilted
   up to ~45°), keeping the whole monitor in the camera image. Keep 3–5 holds for held-out scoring
   (the tool sets aside every fourth hold).

## 8. Commissioning scan (needs you)

`docs/PHYSICAL_COMMISSIONING.md` steps 5–8, `docs/LAB_DAY.md` step 6:

1. Clear people from the area. Push the cart slowly (about 0.3 m/s) around it with overlap, past
   benches and clutter, back to the start. Avoid pointing at windows and mirrors for long.
2. Measure 5–10 real distances (wall to wall, bench length, door width); they are the independent
   accuracy check.
3. Park the cart where the live run will watch from; choose real start/goal points and protected
   zones with the agent (the simulator's are not valid for your room).
4. Walk through, push a chair or trolley, place a box: the agent records these for the tracking checks.

## 9. 30-minute live run

Leave the parked cart alone for 30 minutes while people move normally; keep the laptop on mains with
sleep off. The agent records latency, memory, drops, render times and temperatures and reports them
as a Wi-Fi result for these exact devices.

## Safety and honesty rules

- AetherTwin commands no robot, motor or relay. The cart's motor electronics stay unpowered and
  disconnected; planned routes are advisory and the halo is a display.
- Keep the sensor network private; only the browser gateway may ever be exposed, with TLS and
  separate viewer/operator credentials.
- Until a measurement exists, the UI says "Hardware not tested" and reports keep
  `hardware_verified: false`. That is intentional; do not ask the agent to change it without evidence.
