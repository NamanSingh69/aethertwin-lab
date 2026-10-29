"""Run the full application while requesting Windows stay awake for the run.

The request expires when this process ends and never changes a power setting.
Closing the lid or manually sleeping may still suspend the machine; such a run
is reported as interrupted rather than silently removing the gap from latency.
"""
import ctypes
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def active_seconds():
    if sys.platform != "win32":
        return None
    value = ctypes.c_ulonglong()
    if not ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(value)):
        raise ctypes.WinError()
    return value.value / 10_000_000


if __name__ == "__main__":
    import main
    args = sys.argv[1:] or ["--mode", "sim", "--duration", "1800", "--output", "artifacts/stability_30min"]
    parsed = main.parser().parse_args(args)
    output = parsed.output or Path.cwd() / "artifacts" / parsed.mode
    output.mkdir(parents=True,exist_ok=True)
    configuration=main.yaml.safe_load(parsed.config.read_text(encoding='utf-8'))
    checkpoint=parsed.checkpoint or main._asset(Path(configuration['perception']['checkpoint']))
    source_root=Path(__file__).resolve().parents[1]
    source_paths=[source_root/'main.py',*[p for folder in ('core','ingestion','slam_and_twin','perception_ml','tracking_and_navigation','visualization')
        for p in sorted((source_root/folder).glob('*.py'))]]
    (output/'run_provenance.json').write_text(json.dumps({
        'schema_version':1,'started_utc':datetime.now(timezone.utc).isoformat(),
        'checkpoint':str(checkpoint),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        'configuration_sha256':hashlib.sha256(parsed.config.read_bytes()).hexdigest(),
        'source_sha256':{p.relative_to(source_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
    },indent=2),encoding='utf-8')
    clock_start = time.monotonic()
    active_start = active_seconds()
    keep_display_on = not parsed.headless and parsed.mode in ('sim','replay','live')
    if sys.platform == "win32":
        # A visual benchmark needs both system and display requests. The
        # system-only request can expire under Modern Standby battery policy.
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 | (0x2 if keep_display_on else 0)):
            raise ctypes.WinError()
    try:
        result = main.main(args)
    finally:
        active_end = active_seconds()
        wall_elapsed = time.monotonic() - clock_start
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        sleep_seconds = max(0., wall_elapsed - (active_end-active_start)) if active_start is not None else None
        output.mkdir(parents=True, exist_ok=True)
        (output / "power_clock.json").write_text(json.dumps({
            "wall_seconds": wall_elapsed, "active_seconds": active_end-active_start if active_start is not None else None,
            "suspended_seconds": sleep_seconds, "interrupted_by_suspend": sleep_seconds > 5 if sleep_seconds is not None else None,
            "power_settings_changed": False,
            "display_awake_requested": keep_display_on if sys.platform=='win32' else False,
        }, indent=2), encoding="utf-8")
    raise SystemExit(result)
