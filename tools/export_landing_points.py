"""Sample a twin's measured surface points for the showcase landing page.

Writes ``web/src/landing/twin-points.bin`` (little-endian int16 x, y, z in
centimetres, floor at z = 0) and ``twin-points.json`` (count, extents, source).
The points are a seeded random subset of ``measured_map.npz``; nothing is
smoothed, filled in or moved. The files hold geometry only, no paths or ids.

    python tools/export_landing_points.py --twin %USERPROFILE%/aethertwin_twin_static \
        --recording self_mask_parked --recorded 2026-11-05
"""
from __future__ import annotations

import argparse, json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "web" / "src" / "landing"


def sample(points: np.ndarray, floor: np.ndarray, count: int, seed: int) -> np.ndarray:
    """Seeded subset in the floor frame, as int16 centimetres."""
    homogeneous = np.c_[points, np.ones(len(points))] @ floor.T
    chosen = np.random.default_rng(seed).choice(len(points), size=min(count, len(points)), replace=False)
    cm = np.rint(homogeneous[np.sort(chosen), :3] * 100.0)
    if np.abs(cm).max() > 32767: raise ValueError("twin larger than the int16 centimetre range")
    return cm.astype("<i2")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--twin", type=Path, required=True); parser.add_argument("--recording", required=True)
    parser.add_argument("--recorded", required=True, help="recording date, YYYY-MM-DD")
    parser.add_argument("--count", type=int, default=12000); parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    data = np.load(args.twin / "measured_map.npz")
    cm = sample(data["points"], data["floor_transform"], args.count, args.seed)
    # Where the rig stood: the median scan pose, in the same floor frame.
    sensor = np.rint((np.append(np.median(data["poses"][:, :3, 3], axis=0), 1.0) @ data["floor_transform"].T)[:3] * 100.0)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "twin-points.bin").write_bytes(cm.tobytes())
    meta = {
        "format": "int16le xyz centimetres, floor at z=0", "count": int(len(cm)), "total_measured_points": int(len(data["points"])),
        "voxel_size_m": float(data["voxel_size"]), "min_cm": cm.min(0).tolist(), "max_cm": cm.max(0).tolist(), "sensor_cm": [int(v) for v in sensor],
        "recording": args.recording, "recorded": args.recorded, "seed": args.seed,
        "note": "seeded random subset of the twin's measured surface points; one viewpoint, LiDAR-only",
    }
    (args.output / "twin-points.json").write_bytes((json.dumps(meta, indent=2) + "\n").encode())
    print(f"wrote {len(cm)} of {len(data['points'])} points to {args.output}")


if __name__ == "__main__":
    main()
