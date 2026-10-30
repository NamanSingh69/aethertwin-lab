"""Locate source-checkout or installed-wheel resources without assuming cwd."""
import os
import sysconfig
from pathlib import Path


def resource_root():
    override = os.environ.get("LAB_TWIN_DATA_DIR")
    code_root = Path(__file__).resolve().parents[1]
    candidates = ([Path(override)] if override else [code_root,
        code_root / "share/lidar-lab-digital-twin",
        Path(sysconfig.get_path("data")) / "share/lidar-lab-digital-twin"])
    for candidate in candidates:
        if (candidate / "config/system_config.yaml").is_file():
            return candidate
    raise FileNotFoundError("Lab assets are missing. Reinstall the wheel or set LAB_TWIN_DATA_DIR to the delivered project folder.")


def freeze_startup_heap():
    """Exclude everything allocated during start-up (libraries, model, twin) from later full GC passes.

    A full collection otherwise walks every tracked object (~300k after importing torch), which
    stalled whichever pipeline stage was running by up to ~100 ms. Call once, after initialization."""
    import gc
    gc.collect();gc.freeze()
