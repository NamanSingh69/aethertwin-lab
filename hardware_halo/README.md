# Aether Halo advisory display

The virtual 24-pixel halo is available with no device. Physical serial transport
is disabled by default. This subsystem drives status LEDs and an optional OLED;
it does not command a robot, relay, motor, servo, firmware update or interlock.
Its distance bands are display rules, not a calibrated collision probability.

## Host integration

```python
from hardware_halo import HaloController, halo_state

virtual = halo_state(snapshot)       # JSON-safe virtual display metadata
controller = HaloController({"enabled": False})
controller.publish(snapshot)        # latest-only, nonblocking
status = controller.status()
controller.close()
```

The status worker runs at 30 Hz independently of perception and serial I/O.
An optional second worker handles serial handshakes/writes. `status_hz` is the
configured display cadence; `source_update_hz` measures distinct published
snapshot arrivals. Repeating the last snapshot does not make its data newer.
The handoff stores only one snapshot; there is no unbounded packet queue.

Options are `enabled` (boolean, default false), `port` (one explicitly selected
port), `baudrate` (115200), `publish_hz` (30, allowed 1–60),
`handshake_timeout_seconds` (2), `stale_after_ms` (1000), `watchdog_ms` (2000),
and `brightness` (0.25, range 0–1). Physical mode optionally requires
`pyserial==3.5`. It is imported only when physical transport is enabled.
No port enumeration or arbitrary-port probing occurs.

`halo_state(snapshot, now=None, age=None, ...)` returns 24 RGB triples in
`pixels`, source, `state_name`, severity, nearest human identity/distance,
rig-relative azimuth, freshness flags, source age and display scaling. `now`
is Unix seconds. The optional `age` override is source age in seconds.
Default source age is wall time since `snapshot.processed_at_unix` plus
`snapshot.diagnostics.processing_ms` plus measured
`snapshot.diagnostics.acquisition_age_ms` for queue age; simulation/replay timestamps are never
compared with the wall clock. The source watchdog measures elapsed time since
a completed snapshot, separately from processing latency. Missing wall-clock
freshness is shown as stale/unknown. A disconnected source has its own state.

The nearest confident human track is transformed using
`R_world_rig.T @ (human_position - rig_position)`. Pixel 0 points along rig +X,
pixel 6 along rig +Y/left, pixel 12 behind and pixel 18 right. Mount the physical
ring with those same index directions. Azimuth is quantized to centidegrees
before rendering so the virtual and physical direction bins agree.
Distance below 3/2/1 metres gives caution/warning/critical; an active geofence
breach gives at least warning. Track TTC computed relative to a separate
simulated agent is deliberately not treated as rig-relative TTC.

Colors distinguish disconnected (blue), source watchdog (magenta), stale
observations (amber), and fresh severity (teal/yellow/orange/red). Fault states
use alternating pixels. Source is also carried explicitly and displayed on the
OLED, so simulation/replay cannot masquerade as live hardware. A separate
firmware link watchdog activates after 250 ms without a newer valid packet.
All runtime metadata keeps `physical_verified:false` until measured testing.

## Identity handshake and exact 16-byte packet

The host opens only the configured port and sends ASCII
`AETHER_HALO_HELLO 1\n`. It sends no binary telemetry until the receiver replies
with the exact line `AETHER_HALO 1 PIXELS=24 PROFILE=ESP32_DEVKIT_V1`.
Hello repeats at most every 250 ms during the bounded handshake, accommodating
USB-board startup. An incompatible device is closed and reported visibly;
virtual status continues. Firmware also refuses binary packets before handshake.

All multibyte fields are little-endian:

| Byte | Value |
|---|---|
| 0–1 | Magic `A7 54` |
| 2 | Protocol version 1 |
| 3 | Severity 0–3 in bits 0–1; stale bit 2; source watchdog bit 3; direction-valid bit 4; disconnected bit 5; bits 6–7 zero |
| 4–5 | Unsigned 16-bit sequence |
| 6–7 | Signed azimuth in centidegrees; `-32768` when unavailable |
| 8 | Risk/display scale 0–255 |
| 9 | Global brightness 0–255 |
| 10–11 | Source age in milliseconds, saturated at 65535; unknown also 65535 with stale/disconnected flags |
| 12 | Source: unspecified 0, simulation 1, replay 2, live 3, test 4 |
| 13 | Pixel count, exactly 24 |
| 14–15 | CRC-16/CCITT-FALSE over bytes 0–13: polynomial `0x1021`, initial `0xFFFF`, no reflection/final XOR |

Sequence acceptance uses `0 < (new - old) mod 65536 < 32768`, including wrap
from 65535 to 0. Duplicate, old, corrupt and unsupported packets do not refresh
the link watchdog. A new identity handshake resets the receiver sequence session.
There are no variable-length command or actuation fields.

## Firmware and explicit wiring profile

`firmware/aether_halo` targets a classic ESP32 DevKit V1 with ESP32-WROOM-32,
Arduino-ESP32 core **3.3.12**, FQBN `esp32:esp32:esp32`:

| Signal | Pin/profile |
|---|---|
| 24-pixel WS2812B data, GRB wire order | GPIO18, hardware RMT at 10 MHz |
| Optional SSD1306 128×64 I²C OLED | Address `0x3C`, SDA21, SCL22, 400 kHz |
| Host telemetry | USB UART, 115200 baud |

The ring requires an appropriate regulated LED supply and common ground; do
not assume a development board can power a ring at full brightness. Match the
LED data-level requirements of the chosen ring/level shifter. GPIO assignments
and ring orientation are explicit installation settings. They are not inferred
from the host's serial device name. `HALO_ENABLE_OLED=0` disables OLED setup.

The firmware uses the [official ESP32 RMT API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/rmt.html)
and Wire directly, with no third-party LED/OLED library. Unavailable OLED is
optional; RMT initialization failure prevents the compatible identity response.
The source includes the same CRC, sequence, pixel mapping and status rendering
tested by the desktop header cross-check.

## Compile and verification

```sh
python hardware_halo/build_firmware.py --setup
python -m pytest tests/test_hardware_halo.py -q
```

Setup obtains the official **Arduino CLI 1.5.1** archive, verifies its published
SHA256, and installs the pinned official Espressif core. The
[CLI configuration](https://docs.arduino.cc/arduino-cli/configuration) places all
tools, downloads and caches under this workspace's `work/halo_toolchain`.
No global PATH/Arduino installation is changed. The script issues core-install
and compile commands only, with post-install scripts skipped; there is no
upload, erase, flash or board-list step.
Compiled artifacts stay in that workspace tool directory. Source hashes,
binary hashes and compile result are retained in `firmware_verification.json`.

Tests cover the standard CRC vector, every packet-byte corruption, sequence
wrap/rejection, watchdog expiry, rotated-rig directions, near-boundary LED
quantization, simulation-clock separation, slow serial I/O, compatible and
incompatible mock handshakes, default-disabled transport and the actual C++
firmware header's pixel/CRC/sequence agreement. A successful compile establishes
build compatibility. Physical LEDs, OLED, serial reliability, electrical wiring
and human-visible behavior remain unverified without the actual board.
