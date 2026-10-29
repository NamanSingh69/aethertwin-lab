"""Offline readiness checks and a repeatable field-session folder."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import platform
import re
import shutil
import sys
import time
import yaml
from core.configuration import load_config
from core.resources import resource_root
from .common import digest, write_json


def doctor(config_path=None, root=None, storage=None):
    root = Path(root or resource_root()).resolve()
    config_path = Path(config_path or root / "config/profiles/jetson_edge.yaml")
    cfg = load_config(config_path)
    checks = []
    def check(name, ok, detail, scope="software"):
        checks.append({"name": name, "status": "passed" if ok else "pending" if scope == "physical" else "failed",
                       "scope": scope, "detail": detail})
    check("Python 3.12", sys.version_info[:2] == (3, 12), platform.python_version())
    for module in ("numpy", "scipy", "torch", "open3d", "cv2", "fastapi", "mujoco"):
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        # MuJoCo is optional on a sensor-only edge installation.
        check(module, found, "Import specification only; execution is tested by check." if found else "Install the matching profile.",
              "optional" if module == "mujoco" else "software")
    checkpoint = Path(cfg["perception"]["checkpoint"])
    if not checkpoint.is_absolute():
        checkpoint = root / checkpoint
    check("Checkpoint", checkpoint.is_file(), str(checkpoint))
    checkpoint_hash = digest(checkpoint) if checkpoint.is_file() else None
    check("Browser assets", (root / "web/dist/index.html").is_file(), "Build with npm run build in web/ if missing.")
    check("Current rig", cfg.get("hardware", {}).get("edge_module") == "Jetson Orin Nano 8GB",
          "Expected: Orin Nano 8GB + Unitree 4D LiDAR L2 + Gemini 335L + ESP32.")
    check("No active 2D LiDAR", not cfg.get("simulation", {}).get("include_s2", False) and not cfg.get("live", {}).get("s2_port"),
          "Legacy recordings remain readable.")
    check("Live endpoints unset", cfg["planning"].get("start") is None and cfg["planning"].get("goal") is None,
          "Select start and goal on the commissioned map.")
    check("Live degradation disabled", cfg.get("live", {}).get("allow_degraded") is False,
          "Required sensor failures must be visible.")
    existing = Path(storage or root).resolve()
    while not existing.exists():
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    reserve = int(cfg.get("recording", {}).get("min_free_bytes", 2 * 1024**3))
    check("Storage reserve", disk.free >= reserve, f"{disk.free / 1024**3:.1f} GiB free; reserve {reserve / 1024**3:.1f} GiB.")
    cal_path = Path(cfg["live"]["calibration"])
    if not cal_path.is_absolute():
        cal_path = root / cal_path
    calibration = None
    try:
        calibration = json.loads(cal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    check("Measured calibration file", isinstance(calibration, dict) and calibration.get("measured") is True,
          "A measured flag is necessary, but independent calibration validation remains required.", "physical")
    for name in ("Jetson runtime and thermal checks", "Connected L2/Gemini profiles and clocks",
                 "Observed lab map and surveyed dimensions", "Real-lab labels and accuracy", "ESP32 wiring and watchdog"):
        check(name, False, "Requires connected hardware and recorded evidence.", "physical")
    return {"schema_version": 1, "created_at_unix": time.time(), "resource_root": str(root),
            "config": str(config_path.resolve()), "config_sha256": digest(config_path),
            "checkpoint_sha256": checkpoint_hash, "checks": checks,
            "software_ready": all(c["status"] == "passed" for c in checks if c["scope"] == "software"),
            "physical_ready": False, "hardware_accessed": False,
            "storage": {"free_bytes": disk.free, "reserve_bytes": reserve}}


def prepare_session(output, lab_id, rig_id="rig-01", root=None):
    for value in (lab_id, rig_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
            raise ValueError("Lab and rig identities must be 1..64 letters, digits, underscores or hyphens")
    root = Path(root or resource_root()).resolve()
    output = Path(output).resolve()
    # Never mix two surveys or replace an existing calibration/run.
    output.mkdir(parents=True, exist_ok=False)
    for name in ("calibration", "recordings", "maps", "labels", "evaluation", "diagnostics"):
        (output / name).mkdir()
    cfg = load_config(root / "config/profiles/jetson_edge.yaml")
    cfg["identity"] = {"lab_id": lab_id, "rig_id": rig_id}
    cfg["live"]["calibration"] = str(output / "calibration/measured.json")
    cfg["perception"]["checkpoint"] = str(root / cfg["perception"]["checkpoint"])
    (output / "lab.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    record = {"schema_version": 1, "lab_id": lab_id, "rig_id": rig_id,
              "created_at_unix": time.time(), "hardware": cfg["hardware"],
              "phase": "awaiting_hardware", "physical_verified": False,
              "config_sha256": digest(output / "lab.yaml"),
              "device_serials": {"lidar": None, "camera": None, "esp32_board": None},
              "calibration": None, "active_map": None,
              "pending": ["module/carrier and SDK profile discovery", "measured calibration", "static survey",
                          "independent dimensions", "fixed-mount localization", "real-data evaluation", "thermal endurance"]}
    write_json(output / "session.json", record)
    (output / "SESSION.md").write_text(f"""# {lab_id}: first lab survey

Hardware: Jetson Orin Nano 8GB, Unitree 4D LiDAR L2, Gemini 335L, ESP32.
No physical checks have passed yet. This folder contains no invented calibration or map.

1. Run `python -m maintenance doctor --config lab.yaml` from this folder.
2. On the Jetson, run `python -m maintenance hardware --require-compatible --output diagnostics/hardware.json`.
3. Review network addresses in lab.yaml; discover actual SDK camera profiles and serials.
4. Measure calibration and place the reviewed candidate at calibration/measured.json.
5. Capture a cleared-room survey (run from this session folder):
   `python -m maintenance capture --source live --config lab.yaml --motion mobile --scene-static --duration 120 --output recordings/survey-01`
6. Audit: `python -m maintenance inspect-recording recordings/survey-01 --require-physical --output diagnostics/survey-01.json`.
7. Build a candidate on the laptop: `lab-twin --mode build-twin --input recordings/survey-01 --commissioning-mode mobile --config lab.yaml --output maps/candidate-01`.
8. Inspect coverage, close loops, verify tape-measured dimensions, then relocalize the fixed rig.
9. Label independent lab recordings, evaluate, and record thermal/latency evidence before deployment.

Before camera calibration, explicit `--sensors lidar` captures only L2+IMU for a preliminary survey.
It does not pass multimodal acceptance. The normal live application still requires a measured twin.
`--scene-static` is your declaration that the room was cleared; software cannot establish that fact.
Captures always use a new directory. Keep failed captures for diagnosis; do not train on them blindly.
Paths to calibration/checkpoint in lab.yaml are absolute: review them when transferring this session to another computer.
No camera/LiDAR extrinsics, labels, geofences or free space are manufactured by preparation.
""", encoding="utf-8")
    return record
