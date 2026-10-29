"""Explicit real six-job integration run; separate from quick pytest tests."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(output):
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["MKL_NUM_THREADS"] = "2"
    import numpy as np
    import cv2
    from core.configuration import load_config
    from command_center.commissioning import CommissioningManager

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = load_config(ROOT / "config/profiles/laptop.yaml")
    reference = ROOT / "assets/twin_seed7"
    before = {
        str(path.relative_to(reference)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in reference.rglob("*")
        if path.is_file()
    }
    rng = np.random.default_rng(931)
    points = rng.uniform([-1, -1, 2], [1, 1, 5], (64, 3))
    K = np.array([[600.0, 0, 320], [0, 600.0, 240], [0, 0, 1]])
    pixels = cv2.projectPoints(
        points,
        np.array([0.03, -0.02, 0.01]),
        np.array([0.05, -0.01, 0.03]),
        K,
        np.zeros(5),
    )[0].reshape(-1, 2)
    pairs = output / "synthetic_pairs.npz"
    np.savez_compressed(
        pairs,
        lidar_points=points[:48],
        image_points=pixels[:48],
        K=K,
        measured=np.array(False),
        synthetic=np.array(True),
        validation_lidar_points=points[48:],
        validation_image_points=pixels[48:],
    )
    manager = CommissioningManager(
        config,
        root=ROOT,
        output_root=output / "jobs",
        sources={
            "static-sim": {"mode": "sim", "static_scene": True, "fixed_position": True}
        },
        twins={"active": reference},
        correspondence_files={
            "synthetic-targets": {
                "path": pairs,
                "measured": True,
                "independent_validation": False,
            }
        },
        max_job_seconds=300,
        max_output_bytes=512 * 1024**2,
    )
    report = {
        "schema_version": 1,
        "source": "actual_spawned_commissioning_jobs",
        "synthetic": True,
        "hardware_verified": False,
        "configuration": config,
        "port8080_untouched": True,
        "jobs": [],
        "active_reference_unchanged": False,
    }
    began = time.monotonic()
    manager.start()
    try:
        for kind in (
            "discovery",
            "diagnostics",
            "calibration_candidate",
            "build_twin",
            "coverage",
            "relocalization",
        ):
            data = {"kind": kind, "max_frames": 4, "request_id": f"integration-{kind}"}
            if kind in ("diagnostics", "build_twin", "relocalization"):
                data["source_id"] = "static-sim"
            if kind in ("diagnostics", "coverage", "relocalization"):
                data["twin_id"] = "active"
            if kind == "calibration_candidate":
                data["correspondence_id"] = "synthetic-targets"
            job = manager.submit(data, "operator")
            start = time.monotonic()
            print(f"Started {kind}: {job['id']}", flush=True)
            while job["status"] not in ("completed", "failed", "cancelled"):
                time.sleep(0.2)
                job = manager.get(job["id"])
            job["elapsed_seconds"] = time.monotonic() - start
            report["jobs"].append(job)
            (output / "report.json").write_text(
                json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
            )
            print(
                f"Finished {kind}: {job['status']} {job.get('reason') or ''} ({job['elapsed_seconds']:.1f}s)",
                flush=True,
            )
    finally:
        manager.close()
    after = {
        str(path.relative_to(reference)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in reference.rglob("*")
        if path.is_file()
    }
    report["active_reference_unchanged"] = before == after
    report["elapsed_seconds"] = time.monotonic() - began
    report["checks"] = {
        "all_six_workers_completed": len(report["jobs"]) == 6
        and all(job["status"] == "completed" for job in report["jobs"]),
        "no_promotion": all(not job["promotion_performed"] for job in report["jobs"]),
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "report": str(output / "report.json"),
                **report["checks"],
                "active_reference_unchanged": report["active_reference_unchanged"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
