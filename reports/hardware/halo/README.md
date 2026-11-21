# Halo status board: first physical link test (ESP32-S3)

Physical board on the bring-up laptop. The serial link is verified; the LEDs are not: no LED ring
is connected, so colours, brightness and direction were not observed. `hardware_verified` stays false.

## Board

Identified read-only before flashing (esptool 5.3.1 `chip-id`, `flash-id`), on 2026-11-04:

| | |
|---|---|
| Chip | ESP32-S3 (QFN56) revision v0.2, dual core 240 MHz, embedded PSRAM 8 MB |
| Flash | 16 MB, quad, 3.3 V (manufacturer 0x68, device 0x4018) |
| MAC | 02:00:00:00:00:01 |
| USB | CH343 USB-UART bridge (VID 1A86, PID 55D3) on the port labelled UART: Windows COM7 |

## Flash (on the user's explicit "Flash COM7")

- Firmware: `hardware_halo/firmware/aether_halo` at commit `74c2034`, built with
  `python hardware_halo/build_firmware.py --board esp32s3` (Arduino CLI 1.5.1, ESP32 core 3.3.12,
  FQBN `esp32:esp32:esp32s3:CDCOnBoot=default`). Source and binary hashes:
  `hardware_halo/firmware_verification_esp32s3.json` (that compile report keeps `flashed: false`;
  this page records the flash).
- Image: `aether_halo.ino.merged.bin`, SHA-256 `e6b870008c18838237755ba7bd95d8b56c7650c8740d07e115c2c2dd356273f1`.
- Command: `esptool --chip esp32s3 --port COM7 --baud 921600 write-flash 0x0 aether_halo.ino.merged.bin`.
  Result: 4194304 bytes written in 13.3 s, "Hash of data verified".

## Link test

`python tools/halo_link_test.py --port COM7 --output reports/hardware/halo/link_test_esp32s3.json`.
Result: [`link_test_esp32s3.json`](link_test_esp32s3.json), all checks passed:

- The board answered the handshake as `AETHER_HALO 1 PIXELS=24 PROFILE=ESP32_S3_DEVKITC`, and the
  real `HaloController` connected once with no errors.
- Over 8 s at 30 Hz the controller sent 241 packets. The board sent 8 heartbeats, one a second, with
  no heartbeat timeouts, and its accepted counter grew by 209 between the first and last heartbeat.
- Raw input on a fresh handshake: valid, corrupted, half a packet, a 0.2 s pause, valid. The board's
  counters moved by exactly +2 accepted, +1 rejected block and +1 inter-byte timeout, and it kept
  heartbeating. No firmware error lines.
- Opening the port did not reset the board: its uptime and counters carried on across runs.

## Reflash for the native USB port (current firmware)

The board was then moved to its native USB port (USB Serial/JTAG, VID 303A PID 1001, Windows COM8),
where the UART build cannot talk to the host. On the user's "Flash COM8" it was reflashed with
`build_firmware.py --board esp32s3-usb` (commit `ad52b59`, FQBN `esp32:esp32:esp32s3:CDCOnBoot=cdc`,
hashes in `hardware_halo/firmware_verification_esp32s3_usb.json`):

- Image: `aether_halo.ino.merged.bin`, SHA-256 `e2d5286e46c6199723ec9f5af1ccef77cbf469f3f2370c8348369e8f0b4a9195`;
  4194304 bytes written in 11.3 s, "Hash of data verified".
- Link test on COM8: [`link_test_esp32s3_usb.json`](link_test_esp32s3_usb.json), all checks passed.
  241 packets sent over 8 s at 30 Hz. The board sent 9 heartbeats and counted 240 packets between
  the first and last heartbeat. Raw input gave +2 accepted, +1 rejected and +1 inter-byte timeout;
  one connection, no errors.
- Difference from the UART port: opening this port resets the board (uptime restarted at about
  2 s for the raw phase). The host's handshake retries cover it; expect about a second of
  "disconnected" whenever the service opens the port.

Not covered: LED output and rendering of each state, the OLED (none fitted), behaviour under the
live pipeline, and long-run stability.
