# Raspberry Pi capture and Windows GPU deployment

Current decision: Raspberry Pi 5 (16GB), 32GB microSD, Unitree 4D LiDAR L2,
Orbbec Gemini 335L, ESP32, and a Windows A2000 workstation with 32GB RAM.
The GPU/VRAM specification is user-reported, not yet measured. No Jetson is
required. This is a setup handoff, not a verified distributed deployment.

## Responsibilities and implementation boundary

The Pi runs lightweight acquisition, timestamps and bounded buffers. Windows
runs inference, mapping, tracking, planning, training and SSD recording. The
browser renders scene state locally. ESP32 handles status/halo peripherals.

**Pending:** a raw-sensor transport and Windows receiver connecting the Pi to
the existing pipeline. The current gateway relays finished scene snapshots,
not L2/IMU/RGB-D measurements. No integrated Pi live-start command exists yet.

## Pi preparation

1. Use Raspberry Pi Imager on Windows. Select Raspberry Pi 5 and Raspberry Pi
   OS Lite **64-bit**, then the 32GB card. Writing erases that selected card.
2. Set hostname `aethertwin-pi`, user `aether`, a private password, Wi-Fi, India
   region/Kolkata time zone and SSH. Boot with adequate power and active cooling.
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

The camera SDK advertises ARM64 wheels and Python 3.8–3.13. Actual Pi behaviour
must be tested. Reconnect the camera after installing USB rules. Do not install
this repository's Windows/Ubuntu x86 locks on the Pi.

## Wiring and addresses

Use a gigabit switch connecting L2, Pi and Windows. Camera connects to a Pi USB3
port using a USB3 data cable. Use the L2's supplied rated power arrangement and
the Pi's appropriate supply; Ethernet is not a power connection. A powered USB3
hub may be needed if camera power is unstable.

| Device | Wired address |
|---|---|
| L2, official example defaults | 192.168.1.62 |
| Pi | 192.168.1.2/24 |
| Windows | 192.168.1.10/24 |

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

Retain serial number, firmware and reported profiles. Intended depth profile is
1280x800 at30fps, subject to enumeration and calibration. Uncompressed 16-bit
depth at that profile is about61MB/s; additional uncompressed colour can exceed
gigabit capacity. The bridge needs bounded queues, lossless depth compression,
appropriate colour compression and measured resolution/rate budgets.

## Windows GPU environment

Install the NVIDIA driver for the actual card and check `nvidia-smi`. In a
PowerShell window opened in the source root, with Python3.12 available:

```powershell
py -3.12 -m venv .venv-gpu
.\.venv-gpu\Scripts\python.exe -m pip install --require-hashes -r requirements-windows.lock
.\.venv-gpu\Scripts\python.exe -m pip install --force-reinstall --no-deps torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
.\.venv-gpu\Scripts\python.exe -m pip check
.\.venv-gpu\Scripts\python.exe -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0)); x=torch.randn(512,512,device='cuda'); y=x@x; torch.cuda.synchronize(); print('GPU calculation passed')"
.\.venv-gpu\Scripts\python.exe main.py --mode sim --device cuda --headless --max-frames 10 --output artifacts/gpu-smoke
.\.venv-gpu\Scripts\python.exe -m command_center --mode sim --device cuda --open-browser
```

This replaces CPU PyTorch with the same-version CUDA wheel in a new environment.
The CUDA variant is not covered by the supplied CPU lock hashes and requires
its own validation/lock. Other pipeline stages still use CPU. These commands
are a simulation test, not proof of a connected Pi or real sensor accuracy.

## Before surveying the whole lab

Complete and validate the raw-sensor bridge; make a short recording and replay
it successfully on Windows. Rigidly mount both sensors, measure extrinsics,
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
