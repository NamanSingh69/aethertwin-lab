#!/usr/bin/env bash
# Prepare the Raspberry Pi 5 capture node. Run as the Imager-created login user (any name, not root) over SSH:
#   bash ~/aethertwin/src/deployment/lab/pi_bootstrap.sh [check|packages|sdks|env|native|network|time|service|unit|inventory|all]
# The systemd unit is rendered for that user and $HOME (`unit` prints it without installing). Choose the
# node's config for the unit with CAPTURE_CONFIG=config/profiles/hotspot.yaml (Wi-Fi hotspot) when running `service`.
# Every step is idempotent and prints what it did. Nothing here touches the SD card layout, firmware or
# the L2/camera settings. Steps that need root use `sudo -n` and stop with a clear message if sudo
# would prompt for a password (the agent never types one).
set -euo pipefail
STEP=${1:-all}
ROOT=$HOME/aethertwin; SRC=$ROOT/src; VENDOR=$ROOT/vendor; ENV=$ROOT/capture-env; EVIDENCE=$ROOT/evidence
HERE=$(cd "$(dirname "$0")" && pwd); UNIT_TEMPLATE=$HERE/../capture_node/aethertwin-capture.service
UNITREE_REV=0e3c51f512e6b8ff60b8c32f160b412cb48445c2   # pinned in docs/RASPBERRY_PI_WINDOWS.md and native/README.md
ORBBEC_TAG=v2.1.2
PI_ADDRESS=192.168.1.2/24
mkdir -p "$ROOT" "$VENDOR" "$EVIDENCE"
log(){ printf '\n== %s ==\n' "$*"; }
need_sudo(){ local me; me=$(id -un); sudo -n true 2>/dev/null || { echo "sudo needs a password here. Run once yourself: echo \"$me ALL=(ALL) NOPASSWD:ALL\" | sudo tee /etc/sudoers.d/010-$me-nopasswd && sudo chmod 440 /etc/sudoers.d/010-$me-nopasswd"; exit 3; }; }
# Only plain names and paths go into the unit (sed replacement, systemd specifiers and quoting stay safe).
safe_value(){ case "$1" in ''|*[!A-Za-z0-9._/-]*) echo "Unsupported characters for the systemd unit in '$1' (allowed: A-Z a-z 0-9 . _ / -)"; exit 2;; esac; }

check(){
  log check
  [ "$(uname -m)" = aarch64 ] || { echo "Expected aarch64 (Raspberry Pi OS 64-bit), got $(uname -m)"; exit 2; }
  . /etc/os-release; echo "OS: $PRETTY_NAME  kernel $(uname -r)"; tr -d '\0' </proc/device-tree/model; echo
  [ -d "$SRC/ingestion" ] || { echo "Source tree missing at $SRC (the agent copies it with deployment/lab/lab_sync.sh push-pi)"; exit 2; }
  need_sudo; echo "sudo: passwordless OK"
  command -v vcgencmd >/dev/null && { vcgencmd get_throttled; vcgencmd measure_temp; }
  df -h / | tail -1
}

packages(){
  log packages; need_sudo
  sudo -n apt-get update
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -y full-upgrade
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y git cmake build-essential python3-venv python3-dev \
    libusb-1.0-0-dev libudev-dev usbutils chrony ethtool
  [ -f /var/run/reboot-required ] && echo "REBOOT REQUIRED (kernel or firmware updated): sudo reboot, then rerun from 'sdks'" || true
}

sdks(){
  log sdks
  if [ ! -d "$VENDOR/unilidar_sdk2/.git" ]; then git clone https://github.com/unitreerobotics/unilidar_sdk2.git "$VENDOR/unilidar_sdk2"; fi
  git -C "$VENDOR/unilidar_sdk2" fetch -q origin && git -C "$VENDOR/unilidar_sdk2" checkout -q "$UNITREE_REV"
  echo "unilidar_sdk2 at $(git -C "$VENDOR/unilidar_sdk2" rev-parse HEAD)"
  ls "$VENDOR/unilidar_sdk2/unitree_lidar_sdk/lib/" || true
  cmake -S "$VENDOR/unilidar_sdk2/unitree_lidar_sdk" -B "$VENDOR/unilidar_sdk2/unitree_lidar_sdk/build" -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null
  cmake --build "$VENDOR/unilidar_sdk2/unitree_lidar_sdk/build" --parallel 3 >/dev/null && echo "Unitree examples built"
  if [ ! -d "$VENDOR/pyorbbecsdk/.git" ]; then git clone --branch "$ORBBEC_TAG" --depth 1 https://github.com/orbbec/pyorbbecsdk.git "$VENDOR/pyorbbecsdk"; fi
  need_sudo
  (cd "$VENDOR/pyorbbecsdk" && sudo -n bash scripts/env_setup/install_udev_rules.sh) && echo "Orbbec udev rules installed (replug the camera)"
}

python_env(){
  log 'env (capture venv)'
  [ -x "$ENV/bin/python" ] || python3 -m venv "$ENV"
  "$ENV/bin/python" -m pip install -q --upgrade pip
  "$ENV/bin/python" -m pip install -r "$SRC/deployment/capture_node/requirements-pi.txt"
  # Its declared extras (av, GUI opencv, pygame, pynput) are demo-only and pynput needs evdev built from source.
  "$ENV/bin/python" -m pip install --no-deps pyorbbecsdk2==2.1.2
  "$ENV/bin/python" -m pip check | grep -v '^pyorbbecsdk2 ' || true
  "$ENV/bin/python" -c "import numpy,cv2,yaml,scipy,pyorbbecsdk;print('numpy',numpy.__version__,'opencv',cv2.__version__,'scipy',scipy.__version__,'pyorbbecsdk OK')"
}

native(){
  log native
  local site; site=$("$ENV/bin/python" -c 'import sysconfig;print(sysconfig.get_paths()["platlib"])')
  cmake -S "$SRC/native" -B "$SRC/build/native" -DPython_EXECUTABLE="$ENV/bin/python" \
    -DUNITREE_SDK="$VENDOR/unilidar_sdk2/unitree_lidar_sdk" -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$SRC/build/native" --parallel 3
  cmake --install "$SRC/build/native" --prefix "$site" >/dev/null
  "$ENV/bin/python" -c "import lab_sensors_native as m;print('lab_sensors_native imported from',m.__file__)"
}

network(){
  log network; need_sudo
  local iface; iface=$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="ethernet"{print $1;exit}')
  [ -n "$iface" ] || { echo "No Ethernet device found"; exit 2; }
  if ! nmcli -t -f NAME connection show | grep -qx aether-lidar; then
    sudo -n nmcli connection add type ethernet ifname "$iface" con-name aether-lidar ipv4.method manual \
      ipv4.addresses "$PI_ADDRESS" ipv4.never-default yes ipv6.method disabled
  fi
  sudo -n nmcli connection up aether-lidar || echo "aether-lidar not up yet (is the cable in the switch?)"
  ip -4 addr show "$iface"; ethtool "$iface" 2>/dev/null | grep -E 'Speed|Duplex|Link detected' || true
}

time_sync(){
  log time; need_sudo
  sudo -n systemctl enable --now chrony
  # The capture node unit waits for time-sync.target; this makes that target wait for a real sync.
  # Installing chrony removes systemd-timesyncd (and with it systemd-time-wait-sync) on Debian 13, so use
  # chrony's own wait unit; fall back to the timesyncd one on systems that still have it.
  sudo -n systemctl enable chrony-wait.service 2>/dev/null || sudo -n systemctl enable systemd-time-wait-sync 2>/dev/null \
    || echo "no time-wait unit available: time-sync.target will not wait for a real sync"
  chronyc tracking || true
}

render_unit(){
  # The unit runs as whoever runs this script (e.g. labpi), from that user's $HOME/aethertwin.
  local user; user=$(id -un)
  [ "$(id -u)" != 0 ] || { echo "Run as the login user that owns $ROOT, not as root or with sudo"; exit 2; }
  safe_value "$user"; safe_value "$HOME"
  case "$HOME" in /*) ;; *) echo "HOME must be an absolute path, not '$HOME'"; exit 2;; esac
  [ -f "$UNIT_TEMPLATE" ] || { echo "Unit template missing at $UNIT_TEMPLATE"; exit 2; }
  sed -e "s#@USER@#$user#g" -e "s#@HOME@#$HOME#g" "$UNIT_TEMPLATE"
}

unit(){ render_unit; }

service(){
  log service; need_sudo
  local rendered; rendered=$(mktemp); render_unit >"$rendered"
  if grep -q '@[A-Z]*@' "$rendered"; then echo "Unrendered placeholder left in the unit"; rm -f "$rendered"; exit 2; fi
  sudo -n install -m 644 "$rendered" /etc/systemd/system/aethertwin-capture.service; rm -f "$rendered"
  grep -E '^(User|WorkingDirectory|ExecStart)=' /etc/systemd/system/aethertwin-capture.service
  if [ -n "${CAPTURE_CONFIG:-}" ]; then
    safe_value "$CAPTURE_CONFIG"
    [ -f "$SRC/$CAPTURE_CONFIG" ] || { echo "No config at $SRC/$CAPTURE_CONFIG"; exit 2; }
    printf 'AETHERTWIN_CAPTURE_CONFIG=%s\n' "$CAPTURE_CONFIG" | sudo -n tee /etc/default/aethertwin-capture >/dev/null
  fi
  echo "Node config: $(cat /etc/default/aethertwin-capture 2>/dev/null || echo 'config/system_config.yaml (wired default)')"
  sudo -n systemctl daemon-reload
  # restart (not just --now) so a changed user, unit or config takes effect on a running node.
  if [ -f "$SRC/config/capture_node.key" ] && [ "${ENABLE:-0}" = 1 ]; then sudo -n systemctl enable aethertwin-capture; sudo -n systemctl restart aethertwin-capture; systemctl --no-pager status aethertwin-capture | head -5
  else echo "Unit installed, not enabled (run with ENABLE=1 once the key and the measured calibration are in place)"; fi
}

inventory(){
  log inventory
  local out="$EVIDENCE/pi_inventory_$(date -u +%Y%m%dT%H%M%SZ).txt"
  {
    echo "date_utc: $(date -u -Iseconds)"; . /etc/os-release; echo "os: $PRETTY_NAME"; echo "kernel: $(uname -r)"
    echo "model: $(tr -d '\0' </proc/device-tree/model)"
    echo "source_commit: $(cat "$SRC/SOURCE_COMMIT" 2>/dev/null || echo unknown)"
    command -v vcgencmd >/dev/null && { echo "throttled: $(vcgencmd get_throttled)"; echo "temp: $(vcgencmd measure_temp)"; echo "eeprom: $(vcgencmd bootloader_version | head -1)"; }
    echo "--- lsusb -t"; lsusb -t; echo "--- lsusb"; lsusb
    echo "--- ip -4 addr"; ip -4 addr; echo "--- chronyc tracking"; chronyc tracking 2>&1 || true
    echo "--- unilidar_sdk2"; git -C "$VENDOR/unilidar_sdk2" rev-parse HEAD 2>&1 || true
    echo "--- python env"; "$ENV/bin/python" --version 2>&1 || true; "$ENV/bin/python" -m pip freeze 2>&1 || true
    echo "--- native"; "$ENV/bin/python" -c "import lab_sensors_native as m;print(m.__file__)" 2>&1 || true
    echo "--- free -h"; free -h; echo "--- df -h"; df -h /
  } >"$out" 2>&1
  echo "$out"
}

case "$STEP" in
  check) check;; packages) packages;; sdks) sdks;; env) python_env;; native) native;; network) network;;
  time) time_sync;; service) service;; unit) unit;; inventory) inventory;;
  all) check; packages; sdks; python_env; native; network; time_sync; service; inventory;;
  *) echo "unknown step $STEP"; exit 2;;
esac
