"""Isolated commissioning jobs. Registered inputs only; candidate outputs never activate."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import copy
from dataclasses import dataclass
import hashlib
import importlib.util
import itertools
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import threading
import time
import traceback
import uuid
import zipfile
from fastapi import Request, HTTPException

KINDS = {
    "discovery": "Resource and SDK inventory",
    "diagnostics": "Observed pipeline diagnostic",
    "calibration_candidate": "Calibration candidate export",
    "build_twin": "Bounded static scan / candidate twin",
    "coverage": "Measured coverage review",
    "relocalization": "Fixed-position relocalization review",
}
TERMINAL = {"completed", "failed", "cancelled"}
ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")
JOB_ID = re.compile(r"[a-f0-9]{32}")


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def _write(path, data):
    encoded = json.dumps(data, indent=2, allow_nan=False, default=_json_safe)
    temporary = Path(path).with_suffix(Path(path).suffix + ".tmp")
    temporary.write_text(encoded + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path, limit=1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError("Registered JSON resource exceeds its metadata budget")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Registered JSON object required")
    return value


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _tree_bytes(root):
    total = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(folder) / name).is_symlink()]
        for name in files:
            path = Path(folder) / name
            if path.is_symlink():
                continue
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def _progress(events, text):
    try:
        events.put_nowait({"message": str(text)[:512], "at_unix": time.time()})
    except queue.Full:
        pass


def _source(spec, commissioning=False):
    entry = spec["source"]
    cfg = copy.deepcopy(spec["config"])
    if entry["mode"] == "sim":
        from ingestion.synthetic_lab_simulator import SyntheticLabSimulator

        source = SyntheticLabSimulator(
            cfg["seed"], cfg, cfg["simulation"].get("mobile", False)
        )
        return source, source.commissioning_frames() if commissioning else iter(source)
    if entry["mode"] == "replay":
        from ingestion.recorder_replayer import ReplaySource

        source = ReplaySource(entry["input_path"])
        return source, iter(source)
    raise ValueError(
        "Live jobs require exclusive rig ownership and are not exposed by this manager"
    )


def _inventory(spec, output, events):
    _progress(
        events,
        "Reading registered resources and SDK import specifications; no device is opened.",
    )
    packages = {}
    for name in ("lab_sensors_native", "pyorbbecsdk"):
        try:
            packages[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            packages[name] = False
    report = {
        "kind": "configured_resource_inventory",
        "source": "read_only_discovery",
        "sdk_packages": packages,
        "sources": [
            {
                "id": item["id"],
                "mode": item["mode"],
                "registered": True,
                "recording_available": bool(
                    item.get("input_path")
                    and (Path(item["input_path"]) / "manifest.json").is_file()
                ),
            }
            for item in spec["inventory"]["sources"]
        ],
        "twins": [
            {
                "id": item["id"],
                "reference_available": (
                    Path(item["path"]) / "measured_map.npz"
                ).is_file(),
            }
            for item in spec["inventory"]["twins"]
        ],
        "hardware_status": "not_tested",
        "device_readiness": "not_established",
        "limit": "Configured resources and import specifications are not physical device discovery or successful streaming.",
        "calibration_promoted": False,
        "active_map_modified": False,
    }
    _write(output / "inventory.json", report)
    return report


def _calibration(spec, output, events):
    import numpy as np
    import cv2
    from ingestion.calibration import calibrate_camera_lidar
    from core.se3_math import inverse

    entry = spec["correspondence"]
    path = Path(entry["path"])
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("Correspondence archive exceeds 16 MiB")
    with zipfile.ZipFile(path) as archive:
        if (
            len(archive.infolist()) > 32
            or sum(item.file_size for item in archive.infolist()) > 128 * 1024**2
        ):
            raise ValueError("Correspondence archive expands beyond its budget")
    _progress(events, "Fitting a new candidate from registered 3D/2D correspondences.")
    with np.load(path, allow_pickle=False) as archive:
        required = ("lidar_points", "image_points", "K")
        if any(name not in archive for name in required):
            raise ValueError("Correspondences require lidar_points, image_points and K")
        points = np.asarray(archive["lidar_points"], float)
        pixels = np.asarray(archive["image_points"], float)
        K = np.asarray(archive["K"], float)
        if (
            points.ndim != 2
            or points.shape[1:] != (3,)
            or not 6 <= len(points) <= 50000
        ):
            raise ValueError("Calibration requires 6..50000 paired observations")
        if (
            pixels.shape != (len(points), 2)
            or K.shape != (3, 3)
            or K[0, 0] <= 0
            or K[1, 1] <= 0
        ):
            raise ValueError("Invalid pixel/intrinsic shapes")
        distortion = (
            np.asarray(archive["distortion"], float)
            if "distortion" in archive
            else None
        )
        image_size = archive["image_size"].tolist() if "image_size" in archive else None

        def provenance(name, default):
            if name not in archive:
                return default
            declared = archive[name]
            if declared.shape != () or not isinstance(
                declared.item(), (bool, np.bool_)
            ):
                raise ValueError(f"NPZ {name} provenance must be a scalar boolean")
            return bool(declared.item())

        measured = entry.get("measured") is True and provenance("measured", True)
        measured = measured and not provenance("synthetic", False)
        if "source_mode" in archive and str(archive["source_mode"].item()) == "sim":
            measured = False
        independent = entry.get("independent_validation") is True and provenance(
            "independent_validation", True
        )
        validation_points = (
            np.asarray(archive["validation_lidar_points"], float)
            if "validation_lidar_points" in archive
            else None
        )
        validation_pixels = (
            np.asarray(archive["validation_image_points"], float)
            if "validation_image_points" in archive
            else None
        )
    calibration, fitted = calibrate_camera_lidar(
        points, pixels, K, distortion, image_size=image_size, measured=measured
    )
    camera_from_lidar = inverse(calibration.T_lidar_camera)
    rotation = cv2.Rodrigues(camera_from_lidar[:3, :3])[0]
    translation = camera_from_lidar[:3, 3]
    _, jacobian = cv2.projectPoints(points, rotation, translation, K, distortion)
    J = jacobian[:, :6]
    normalized = J / np.maximum(np.linalg.norm(J, axis=0), 1e-12)
    rank = int(np.linalg.matrix_rank(normalized, tol=1e-6))
    validation = {
        "status": "not_tested",
        "independent_declared": independent,
        "reason": "No independently declared held-out 3D/2D targets supplied",
    }
    if validation_points is not None or validation_pixels is not None:
        if (
            validation_points is None
            or validation_pixels is None
            or validation_points.shape != (len(validation_pixels), 3)
            or validation_pixels.shape != (len(validation_points), 2)
            or not 6 <= len(validation_points) <= 50000
        ):
            raise ValueError(
                "Held-out correspondences require 6..50000 paired observations"
            )
        if (
            not np.isfinite(validation_points).all()
            or not np.isfinite(validation_pixels).all()
        ):
            raise ValueError("Held-out correspondences must be finite")
        predicted, _ = cv2.projectPoints(
            validation_points, rotation, translation, K, distortion
        )
        errors = np.linalg.norm(predicted.reshape(-1, 2) - validation_pixels, axis=1)
        depths = validation_points @ camera_from_lidar[2, :3] + camera_from_lidar[2, 3]
        rmse = float(np.sqrt(np.mean(errors**2)))
        passed = rank == 6 and rmse <= 2.0 and bool(np.all(depths > 0))
        validation = {
            "status": ("passed" if passed else "failed")
            if independent
            else "not_tested",
            "independent_declared": independent,
            "observations": len(errors),
            "point_reprojection_rmse_pixels": rmse,
            "all_targets_in_front": bool(np.all(depths > 0)),
            "threshold_pixels": 2.0,
            "reason": "Descriptive held-out error; independence must be declared separately"
            if not independent
            else "Held-out local fit gate only; physical accuracy remains unverified",
        }
    candidate = {
        **fitted,
        "kind": "camera_lidar_calibration_candidate",
        "candidate_only": True,
        "active": False,
        "promotion_permitted": False,
        "hardware_status": "not_tested",
        "physical_verified": False,
        "correspondence_id": entry["id"],
        "correspondence_archive_sha256": _digest(path),
        "measured": measured,
        "provenance_is_declaration": True,
        "supplementary_rodrigues_rank": {
            "fitted_jacobian_rank": rank,
            "parameters": 6,
            "limit": "Local Rodrigues/translation Jacobian rank is not independent real-lab calibration validation.",
        },
        "validation": validation,
    }
    _write(output / "calibration_candidate.json", candidate)
    return candidate


def _build_twin(spec, output, events):
    from core.pipeline import build_commissioned_assets

    cfg = copy.deepcopy(spec["config"])
    available = max(1, spec["remaining_output_bytes"] - 32 * 1024**2)
    per_store = max(1, available // 8)
    budgets = cfg.setdefault("mapping", {}).setdefault("commissioning_budgets", {})
    budgets["max_frames"] = min(
        int(budgets.get("max_frames", 10000)), spec["max_frames"]
    )
    for key in ("frame_disk_bytes", "max_disk_bytes", "tsdf_disk_bytes"):
        budgets[key] = min(int(budgets.get(key, per_store)), per_store)
    tile_budgets = cfg["mapping"].setdefault("tile_budgets", {})
    tile_budgets["max_disk_bytes"] = min(
        int(tile_budgets.get("max_disk_bytes", per_store)), per_store
    )
    source, frames = _source(spec, commissioning=True)
    count = 0

    def bounded():
        nonlocal count
        for frame in itertools.islice(frames, spec["max_frames"]):
            count += 1
            _progress(
                events,
                f"Commissioning acquisition {count}; registered static source {spec['source']['id']}.",
            )
            yield frame

    assets = None
    try:
        assets = build_commissioned_assets(
            bounded(),
            output / "candidate_twin",
            cfg,
            progress=lambda text: _progress(events, text),
        )
        report = {
            "kind": "candidate_twin_review",
            "actual_frames": count,
            "source_id": spec["source"]["id"],
            "source_mode": spec["source"]["mode"],
            "static_scene_declared": True,
            "active_map_modified": False,
            "promoted": False,
            "candidate_manifest": "candidate_twin/bundle_manifest.json",
            "hardware_status": "not_tested",
            "reconstruction": assets[0].report,
        }
        _write(output / "build_review.json", report)
        return report
    finally:
        source.close()
        if assets:
            for item in assets[:2]:
                if hasattr(item, "close"):
                    item.close()


def _coverage(spec, output, events):
    import numpy as np
    from slam_and_twin.tiled_map import DiskBackedVoxelMap
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap

    path = Path(spec["twin"]["path"])
    _progress(events, "Reading measured occupancy without clearing unknown space.")
    authority = (
        DiskBackedVoxelMap.load(path / "occupancy_tiles")
        if (path / "occupancy_tiles/manifest.json").is_file()
        else SparseMultiReturnVoxelMap.load(path / "static_occupancy.npz")
    )
    known = occupied = free = 0
    lower = upper = None
    roi = spec["twin"].get("review_bounds")
    roi_count = 0
    first = last = None
    if roi is not None:
        bounds = np.asarray(roi, float)
        if (
            bounds.shape != (6,)
            or not np.isfinite(bounds).all()
            or np.any(bounds[[1, 3, 5]] <= bounds[[0, 2, 4]])
        ):
            raise ValueError(
                "Registered review bounds must be finite [xmin,xmax,ymin,ymax,zmin,zmax]"
            )
        first = np.floor(bounds[[0, 2, 4]] / authority.voxel_size).astype(np.int64)
        last = np.ceil(bounds[[1, 3, 5]] / authority.voxel_size).astype(np.int64) - 1
    try:
        if hasattr(authority, "iter_observed"):
            batches = (
                (coords, values) for _, coords, values in authority.iter_observed()
            )
        else:

            def legacy_batches():
                entries = iter(authority.cells.items())
                while chunk := list(itertools.islice(entries, 8192)):
                    yield (
                        np.asarray([key for key, _ in chunk], np.int64).reshape(-1, 3),
                        np.asarray([value for _, value in chunk], float).reshape(-1, 3),
                    )

            batches = legacy_batches()
        for coords, values in batches:
            if not len(coords):
                continue
            known += len(coords)
            occupied += int(np.sum(values[:, 0] > 0.4))
            free += int(np.sum(values[:, 0] < -0.2))
            lo = coords.min(0)
            hi = coords.max(0)
            lower = lo if lower is None else np.minimum(lower, lo)
            upper = hi if upper is None else np.maximum(upper, hi)
            if first is not None:
                roi_count += int(
                    np.sum(np.all((coords >= first) & (coords <= last), axis=1))
                )
        observed = (
            None
            if lower is None
            else [
                float(v)
                for pair in zip(
                    lower * authority.voxel_size, (upper + 1) * authority.voxel_size
                )
                for v in pair
            ]
        )
        total_roi = (
            None if first is None else math.prod(int(v) for v in last - first + 1)
        )
        report = {
            "kind": "measured_occupancy_coverage_review",
            "twin_id": spec["twin"]["id"],
            "known_cells": known,
            "occupied_cells": occupied,
            "free_cells": free,
            "ambiguous_evidence_cells": known - occupied - free,
            "observed_bounds": observed,
            "voxel_size_m": authority.voxel_size,
            "registered_review_bounds": roi,
            "roi_known_cells": roi_count if roi is not None else None,
            "roi_total_cells": total_roi,
            "coverage_fraction": roi_count / total_roi if total_roi else None,
            "whole_lab_completeness_claimed": False,
            "unknown_is_free": False,
            "hardware_status": "not_tested",
            "active_map_modified": False,
            "limit": "Coverage fraction requires a separately registered surveyed ROI. Observed extents alone do not establish whole-lab completeness.",
        }
        _write(output / "coverage_review.json", report)
        return report
    finally:
        if hasattr(authority, "close"):
            authority.close()


def _choose_pose_hypothesis(hypotheses):
    """Reject a repeatable room alias when equally supported alternatives disagree."""
    import numpy as np
    from scipy.spatial.transform import Rotation

    if not hypotheses:
        raise ValueError("No supported relocalization initialization")
    supported = [
        item
        for item in hypotheses
        if item[2]["information_rank"] == 6
        and item[2]["fitness"] >= 0.2
        and math.isfinite(item[2]["rmse"])
    ]
    if not supported:
        raise ValueError("No fully constrained relocalization hypothesis")
    supported.sort(key=lambda item: (-item[2]["fitness"], item[2]["rmse"]))
    name, pose, diagnostic = supported[0]
    for other_name, other_pose, other in supported[1:]:
        separated = (
            np.linalg.norm(pose[:3, 3] - other_pose[:3, 3]) > 0.5
            or Rotation.from_matrix(pose[:3, :3].T @ other_pose[:3, :3]).magnitude()
            > 0.35
        )
        if (
            separated
            and abs(diagnostic["fitness"] - other["fitness"]) < 0.05
            and abs(diagnostic["rmse"] - other["rmse"]) < 0.03
        ):
            raise ValueError(
                "Ambiguous relocalization: similarly supported separated pose hypotheses; no candidate published"
            )
    return name, pose, diagnostic


def _relocalization(spec, output, events):
    import numpy as np
    from scipy.spatial.transform import Rotation
    from slam_and_twin.icp_pose_graph import (
        global_registration,
        prepare_reference,
        point_to_plane_icp,
    )
    from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer

    path = Path(spec["twin"]["path"])
    with zipfile.ZipFile(path / "measured_map.npz") as archive:
        if (
            len(archive.infolist()) > 32
            or sum(item.file_size for item in archive.infolist()) > 128 * 1024**2
        ):
            raise ValueError(
                "Registered relocalization reference exceeds its 128 MiB expanded budget"
            )
    with np.load(path / "measured_map.npz", allow_pickle=False) as archive:
        target = np.asarray(archive["points"], float)
    budget = min(
        30000,
        max(
            1000,
            int(
                spec["config"]
                .get("diagnostics", {})
                .get("relocalization_points", 10000)
            ),
        ),
    )
    if len(target) > budget:
        target = target[np.linspace(0, len(target) - 1, budget).astype(int)]
    reference = prepare_reference(target, 0.12)
    source, frames = _source(spec)
    records = []
    candidate_poses = []
    initialization = global_failure = None
    first_pose = pose = None
    deskewer = None
    identities = set()
    try:
        for index, raw in enumerate(itertools.islice(frames, spec["max_frames"])):
            _progress(
                events,
                f"Relocalization review observation {index + 1}; candidate pose will not be applied.",
            )
            identities.add(
                (
                    raw.capabilities.sensor_id,
                    None if raw.calibration is None else raw.calibration.calibration_id,
                )
            )
            if len(identities) > 1:
                raise ValueError(
                    "Rig/calibration identity changed during fixed-position review"
                )
            if deskewer is None:
                deskewer = ContinuousTimeIMUDeskewer(
                    initial_pose=raw.sensor_pose_world, imu_to_lidar=raw.imu_to_lidar
                )
            frame = deskewer.deskew_frame(raw)
            points = frame.lidar_points[:, :3]
            if len(points) > budget:
                points = points[np.linspace(0, len(points) - 1, budget).astype(int)]
            if pose is None:
                seeds = [("carried_input_pose", frame.sensor_pose_world)]
                global_failure = None
                try:
                    global_pose, _ = global_registration(points, target)
                    seeds.append(("fpfh_ransac", global_pose))
                except ValueError as error:
                    global_failure = str(error)
                hypotheses = []
                for name, seed in seeds:
                    try:
                        candidate, diagnostic = point_to_plane_icp(
                            points, None, initial=seed, reference=reference
                        )
                        hypotheses.append((name, candidate, diagnostic))
                    except ValueError:
                        continue
                initialization, pose, diagnostics = _choose_pose_hypothesis(hypotheses)
            else:
                pose, diagnostics = point_to_plane_icp(
                    points, None, initial=pose, reference=reference
                )
            deskewer.correct_state(pose)
            if first_pose is None:
                first_pose = pose.copy()
            drift = float(np.linalg.norm(pose[:3, 3] - first_pose[:3, 3]))
            angular = float(
                Rotation.from_matrix(first_pose[:3, :3].T @ pose[:3, :3]).magnitude()
            )
            rank = int(diagnostics["information_rank"])
            accepted = (
                rank == 6
                and diagnostics["fitness"] >= 0.2
                and diagnostics["rmse"] <= 0.15
                and drift <= 0.15
                and angular <= 0.1
            )
            records.append(
                {
                    "frame_id": raw.frame_id,
                    "timestamp": raw.timestamp,
                    "fitness": diagnostics["fitness"],
                    "rmse_m": diagnostics["rmse"],
                    "information_rank": rank,
                    "translation_change_from_first_m": drift,
                    "rotation_change_from_first_rad": angular,
                    "review_gate_passed": accepted,
                    "covariance_rot_xyz_trans_xyz": (
                        np.linalg.pinv(diagnostics["information"])
                        * diagnostics["rmse"] ** 2
                    ).tolist()
                    if rank == 6
                    else None,
                }
            )
            candidate_poses.append(pose.tolist())
        review_passed = bool(records) and all(
            item["review_gate_passed"] for item in records
        )
        report = {
            "kind": "fixed_position_relocalization_review",
            "source_id": spec["source"]["id"],
            "twin_id": spec["twin"]["id"],
            "fixed_position_declared": True,
            "actual_frames": len(records),
            "reference_points_used": len(target),
            "initialization": initialization,
            "global_initialization_failure": global_failure,
            "hypothesis_policy": {
                "selection": "higher constrained overlap, then lower residual",
                "ambiguity_translation_m": 0.5,
                "ambiguity_rotation_rad": 0.35,
                "fitness_margin": 0.05,
                "rmse_margin_m": 0.03,
            },
            "reference_sha256": _digest(path / "measured_map.npz"),
            "records": records,
            "review_status": "passed" if review_passed else "failed",
            "candidate_poses_world": candidate_poses if review_passed else None,
            "candidate_published": review_passed,
            "hardware_status": "not_tested",
            "active_pose_modified": False,
            "limit": "Registration-conditioned repeatability against a bounded measured reference; not independent world-pose accuracy or proof of physical stationarity.",
        }
        _write(output / "relocalization_review.json", report)
        return report
    finally:
        source.close()


def _job_worker(spec, events):
    output = Path(spec["output"])
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["MKL_NUM_THREADS"] = "2"
    for name in list(os.environ):
        if name.startswith("AETHERTWIN_") and name.endswith("_TOKEN"):
            os.environ.pop(name, None)
    try:
        kind = spec["kind"]
        if kind == "discovery":
            summary = _inventory(spec, output, events)
        elif kind == "calibration_candidate":
            summary = _calibration(spec, output, events)
        elif kind == "build_twin":
            summary = _build_twin(spec, output, events)
        elif kind == "coverage":
            summary = _coverage(spec, output, events)
        elif kind == "relocalization":
            summary = _relocalization(spec, output, events)
        elif kind == "diagnostics":
            import torch

            torch.set_num_threads(2)
            from diagnostics import run_diagnostics

            source = spec["source"]
            summary = run_diagnostics(
                copy.deepcopy(spec["config"]),
                source=source["mode"],
                input_path=source.get("input_path"),
                twin_path=spec["twin"]["path"],
                output=output,
                max_frames=spec["max_frames"],
                root=spec["root"],
            )
        else:
            raise ValueError("Unsupported commissioning job")
        _write(
            output / "result.json",
            {"success": True, "summary": summary, "promotion_performed": False},
        )
    except BaseException as error:
        (output / "worker_error.log").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        _write(
            output / "result.json",
            {
                "success": False,
                "reason": f"{type(error).__name__}: {error}",
                "promotion_performed": False,
            },
        )


class CommissioningManager:
    def __init__(
        self,
        config,
        *,
        root,
        output_root,
        sources=None,
        twins=None,
        correspondence_files=None,
        queue_capacity=2,
        max_history=32,
        max_job_seconds=300,
        max_output_bytes=512 * 1024**2,
    ):
        if not all(
            math.isfinite(value) and value >= 1
            for value in (
                queue_capacity,
                max_history,
                max_job_seconds,
                max_output_bytes,
            )
        ):
            raise ValueError("Commissioning budgets must be positive")
        self.config = copy.deepcopy(config)
        self.root = Path(root).resolve()
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.queue_capacity = int(queue_capacity)
        self.max_history = int(max_history)
        self.max_job_seconds = float(max_job_seconds)
        self.max_output_bytes = int(max_output_bytes)
        self.sources = self._registry(sources or {}, "source")
        self.twins = self._registry(twins or {}, "twin")
        self.correspondences = self._registry(
            correspondence_files or {}, "correspondence"
        )
        for entry in [
            *self.sources.values(),
            *self.twins.values(),
            *self.correspondences.values(),
        ]:
            value = entry.get("path") or entry.get("input_path")
            if value:
                path = Path(value)
                if (
                    self.output_root == path
                    or self.output_root.is_relative_to(path)
                    or path.is_relative_to(self.output_root)
                ):
                    raise ValueError(
                        "Job outputs must not overlap registered inputs or reference bundles"
                    )
        self._condition = threading.Condition()
        self._jobs = OrderedDict()
        self._pending = deque()
        self._requests = OrderedDict()
        self._stop = threading.Event()
        self._thread = None
        self._active = None
        self._process = None
        self._events = None
        self._worker_target = _job_worker
        self._bytes = 0

    def _registry(self, values, kind):
        if not isinstance(values, dict) or len(values) > 64:
            raise ValueError("At most 64 entries per commissioning registry")
        result = {}
        for name, value in values.items():
            if not isinstance(name, str) or not ID.fullmatch(name):
                raise ValueError("Registered resource IDs must be bounded names")
            entry = dict(value) if isinstance(value, dict) else {"path": value}
            entry["id"] = name
            entry["label"] = str(entry.get("label", name))[:120]
            if kind == "source":
                if entry.get("mode") not in ("sim", "replay", "live"):
                    raise ValueError(
                        "Registered source mode must be sim, replay or live"
                    )
                if entry["mode"] == "replay" and not entry.get("input_path"):
                    raise ValueError("Replay registry requires a recording path")
                entry["static_scene"] = entry.get("static_scene") is True
                entry["fixed_position"] = entry.get("fixed_position") is True
            elif not entry.get("path"):
                raise ValueError("Registered reference/correspondence path required")
            for field in ("path", "input_path"):
                if entry.get(field) is not None:
                    path = Path(entry[field])
                    entry[field] = str(
                        (self.root / path).resolve()
                        if not path.is_absolute()
                        else path.resolve()
                    )
            result[name] = entry
        return result

    def describe(self):
        with self._condition:
            resources = {
                "sources": [
                    {
                        key: copy.deepcopy(value)
                        for key, value in entry.items()
                        if key
                        in ("id", "label", "mode", "static_scene", "fixed_position")
                    }
                    for entry in self.sources.values()
                ],
                "twins": [
                    {"id": entry["id"], "label": entry["label"]}
                    for entry in self.twins.values()
                ],
                "correspondence_files": [
                    {
                        "id": entry["id"],
                        "label": entry["label"],
                        "measured_declared": entry.get("measured") is True,
                        "independent_validation_declared": entry.get(
                            "independent_validation"
                        )
                        is True,
                    }
                    for entry in self.correspondences.values()
                ],
            }
            return {
                "schema_version": 1,
                "enabled": True,
                **resources,
                "kinds": [
                    {"kind": key, "label": value} for key, value in KINDS.items()
                ],
                "limits": {
                    "queue_capacity": self.queue_capacity,
                    "history_capacity": self.max_history,
                    "max_frames": 128,
                    "max_job_seconds": self.max_job_seconds,
                    "aggregate_output_bytes": self.max_output_bytes,
                    "measured_output_bytes": self._bytes,
                    "concurrent_jobs": 1,
                },
                "live_jobs_enabled": False,
                "live_job_reason": "Exclusive rig ownership is not configured; pausing acquisition is insufficient.",
                "automatic_promotion": False,
                "jobs": [self._public(job) for job in reversed(self._jobs.values())],
            }

    def _public(self, job):
        result = {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if not key.startswith("_")
        }
        result["logs"] = list(job["_logs"])
        result["artifacts"] = [
            dict(
                item,
                url=f"/api/v2/commissioning/jobs/{job['id']}/artifacts/{item['name']}",
            )
            for item in job.get("_artifacts", [])[:256]
        ]
        result["artifact_count"] = len(job.get("_artifacts", []))
        return result

    def get(self, job_id):
        with self._condition:
            if job_id not in self._jobs:
                raise KeyError("Unknown commissioning job")
            return self._public(self._jobs[job_id])

    def submit(self, data, actor_role="viewer"):
        if actor_role != "operator":
            raise PermissionError("Operator permission required")
        if not isinstance(data, dict) or set(data) - {
            "kind",
            "source_id",
            "twin_id",
            "correspondence_id",
            "max_frames",
            "request_id",
        }:
            raise ValueError(
                "Commissioning requests accept registered IDs and bounded frame counts only"
            )
        kind = data.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("Unknown commissioning job kind")
        for field in ("source_id", "twin_id", "correspondence_id"):
            if field in data and (
                not isinstance(data[field], str) or not ID.fullmatch(data[field])
            ):
                raise ValueError("Choose a bounded registered resource ID")
        maximum = data.get("max_frames", 8)
        if type(maximum) is not int or not 1 <= maximum <= 128:
            raise ValueError("Frame budget must be 1..128")
        source = None
        twin = None
        correspondence = None
        if kind in ("diagnostics", "build_twin", "relocalization"):
            source = self.sources.get(data.get("source_id"))
            if source is None:
                raise ValueError("Choose a registered source ID")
            if source["mode"] == "live":
                raise ValueError(
                    "Live jobs require an exclusive rig lease; use the documented console workflow after closing active acquisition"
                )
            if kind == "build_twin" and not source["static_scene"]:
                raise ValueError(
                    "Candidate mapping requires a registered declared-static commissioning source"
                )
            if kind == "relocalization" and not source["fixed_position"]:
                raise ValueError(
                    "Relocalization review requires an explicitly declared fixed-position source"
                )
        if kind in ("diagnostics", "coverage", "relocalization"):
            twin = self.twins.get(data.get("twin_id"))
            if twin is None:
                raise ValueError("Choose a registered reference twin ID")
        if kind == "calibration_candidate":
            correspondence = self.correspondences.get(data.get("correspondence_id"))
            if correspondence is None:
                raise ValueError("Choose a registered correspondence-file ID")
        request_id = data.get("request_id")
        if request_id is not None and (
            not isinstance(request_id, str) or not ID.fullmatch(request_id)
        ):
            raise ValueError("Invalid commissioning request ID")
        canonical = json.dumps(data, sort_keys=True)
        current_bytes = _tree_bytes(self.output_root)
        if current_bytes >= self.max_output_bytes:
            raise ValueError(
                "Aggregate commissioning artifact quota exhausted; retain/export/archive existing evidence before new work"
            )
        with self._condition:
            if self._stop.is_set():
                raise ValueError("Commissioning manager is closed")
            if request_id in self._requests:
                old, job_id = self._requests[request_id]
                if old != canonical:
                    raise ValueError("Request ID already used for a different job")
                if job_id not in self._jobs:
                    raise ValueError(
                        "Earlier job history expired; evidence remains retained"
                    )
                return self.get(job_id)
            if len(self._pending) >= self.queue_capacity:
                raise ValueError("Commissioning job queue is full")
            job_id = uuid.uuid4().hex
            output = self.output_root / job_id
            output.mkdir()
            spec = {
                "kind": kind,
                "config": copy.deepcopy(self.config),
                "root": str(self.root),
                "output": str(output),
                "max_frames": maximum,
                "source": copy.deepcopy(source),
                "twin": copy.deepcopy(twin),
                "correspondence": copy.deepcopy(correspondence),
                "inventory": {
                    "sources": list(self.sources.values()),
                    "twins": list(self.twins.values()),
                },
                "remaining_output_bytes": self.max_output_bytes - current_bytes,
            }
            job = {
                "id": job_id,
                "kind": kind,
                "label": KINDS[kind],
                "status": "queued",
                "source_id": data.get("source_id"),
                "twin_id": data.get("twin_id"),
                "correspondence_id": data.get("correspondence_id"),
                "created_at_unix": time.time(),
                "started_at_unix": None,
                "finished_at_unix": None,
                "max_frames": maximum,
                "promotion_performed": False,
                "reason": None,
                "_spec": spec,
                "_logs": deque(maxlen=64),
                "_artifacts": [],
                "_cancel": False,
            }
            self._jobs[job_id] = job
            self._trim_history()
            self._pending.append(job_id)
            if request_id:
                self._requests[request_id] = (canonical, job_id)
                while len(self._requests) > 128:
                    self._requests.popitem(last=False)
            self._bytes = current_bytes
            self._condition.notify_all()
            return self._public(job)

    def cancel(self, job_id, actor_role="viewer"):
        if actor_role != "operator":
            raise PermissionError("Operator permission required")
        with self._condition:
            if job_id not in self._jobs:
                raise KeyError("Unknown commissioning job")
            job = self._jobs[job_id]
            if job["status"] in TERMINAL:
                return self._public(job)
            job["_cancel"] = True
            if job_id in self._pending:
                self._pending.remove(job_id)
                job.update(
                    status="cancelled",
                    reason="Cancelled before execution",
                    finished_at_unix=time.time(),
                )
                self._trim_history()
            self._condition.notify_all()
            return self._public(job)

    def start(self):
        with self._condition:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="commissioning-job-controller", daemon=False
            )
            self._thread.start()

    def _trim_history(self):
        while len(self._jobs) > self.max_history:
            old = next(
                (key for key, item in self._jobs.items() if item["status"] in TERMINAL),
                None,
            )
            if old is None:
                break
            self._jobs.pop(old)

    def _run(self):
        while not self._stop.is_set():
            with self._condition:
                self._condition.wait_for(
                    lambda: self._pending or self._stop.is_set(), timeout=0.5
                )
                if self._stop.is_set():
                    break
                if not self._pending:
                    continue
                job_id = self._pending.popleft()
                job = self._jobs[job_id]
                self._active = job_id
                job.update(status="running", started_at_unix=time.time())
            self._execute(job)
            with self._condition:
                self._active = None
                self._trim_history()

    def _execute(self, job):
        current = _tree_bytes(self.output_root)
        if current >= self.max_output_bytes:
            with self._condition:
                job.update(
                    status="failed",
                    reason="Aggregate commissioning artifact quota exhausted; existing evidence retained",
                    finished_at_unix=time.time(),
                )
            return
        job["_spec"]["remaining_output_bytes"] = self.max_output_bytes - current
        context = mp.get_context("spawn")
        events = context.Queue(maxsize=32)
        process = context.Process(
            target=self._worker_target,
            args=(job["_spec"], events),
            name="isolated-commissioning-job",
        )
        self._process = process
        self._events = events
        reason = None
        cancelled = False
        try:
            process.start()
            began = time.monotonic()
            while process.is_alive():
                try:
                    event = events.get(timeout=0.1)
                    with self._condition:
                        job["_logs"].append(event)
                except queue.Empty:
                    pass
                self._bytes = _tree_bytes(self.output_root)
                with self._condition:
                    cancelled = job["_cancel"] or self._stop.is_set()
                if cancelled:
                    reason = "Job cancelled; candidate outputs were not promoted"
                elif time.monotonic() - began > self.max_job_seconds:
                    reason = f"Job exceeded its {self.max_job_seconds:g} second wall-time budget"
                elif self._bytes > self.max_output_bytes:
                    reason = "Aggregate commissioning output quota exceeded; retained partial evidence was not promoted"
                if reason:
                    process.terminate()
                    break
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=3)
            if reason is None and _tree_bytes(self.output_root) > self.max_output_bytes:
                reason = "Aggregate commissioning output quota exceeded; outputs retained without promotion"
            output = Path(job["_spec"]["output"])
            if reason:
                state = "cancelled" if cancelled else "failed"
                summary = None
            else:
                result = _read_json(output / "result.json", 4 * 1024**2)
                state = (
                    "completed"
                    if process.exitcode == 0 and result.get("success")
                    else "failed"
                )
                reason = result.get("reason")
                summary = result.get("summary")
            artifacts = []
            for path in sorted(output.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    resolved = path.resolve()
                    if not resolved.is_relative_to(output):
                        raise ValueError("Unexpected output artifact path")
                    artifacts.append(
                        {
                            "name": path.relative_to(output).as_posix(),
                            "bytes": path.stat().st_size,
                        }
                    )
                    if len(artifacts) > 4096:
                        raise ValueError(
                            "Commissioning artifact index exceeds 4096 files"
                        )
            with self._condition:
                job.update(
                    status=state,
                    reason=reason,
                    summary=summary,
                    finished_at_unix=time.time(),
                )
                job["_artifacts"] = artifacts
        except Exception as error:
            with self._condition:
                job.update(
                    status="failed",
                    reason=f"{type(error).__name__}: {error}",
                    finished_at_unix=time.time(),
                )
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            events.close()
            events.join_thread()
            self._process = self._events = None
            self._bytes = _tree_bytes(self.output_root)

    def artifact_path(self, job_id, name):
        with self._condition:
            if job_id not in self._jobs:
                raise KeyError("Unknown commissioning job")
            job = self._jobs[job_id]
            if name not in {item["name"] for item in job.get("_artifacts", [])}:
                raise KeyError("Unknown commissioning artifact")
            root = Path(job["_spec"]["output"])
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise KeyError("Unknown commissioning artifact")
            return path

    def close(self):
        self._stop.set()
        with self._condition:
            for job_id in list(self._pending):
                self._jobs[job_id].update(
                    status="cancelled",
                    reason="Service shutdown before execution",
                    finished_at_unix=time.time(),
                )
            self._pending.clear()
            if self._active and self._active in self._jobs:
                self._jobs[self._active]["_cancel"] = True
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=8)
            if self._thread.is_alive():
                raise RuntimeError("Commissioning controller did not stop")


def attach_commissioning_routes(app, manager, require_role, check_origin):
    from fastapi.responses import FileResponse
    from .service import bounded_json

    @app.get("/api/v2/commissioning")
    async def commissioning_inventory(request: Request):
        require_role(request)
        return manager.describe()

    @app.post("/api/v2/commissioning/jobs", status_code=202)
    async def commissioning_submit(request: Request):
        role = require_role(request, True)
        check_origin(request)
        data = await bounded_json(request, 8192)
        try:
            return await asyncio.to_thread(manager.submit, data, role)
        except (ValueError, PermissionError) as error:
            raise HTTPException(
                409 if isinstance(error, ValueError) else 403, str(error)
            ) from error

    @app.get("/api/v2/commissioning/jobs/{job_id}")
    async def commissioning_job(job_id: str, request: Request):
        require_role(request)
        try:
            return manager.get(job_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/api/v2/commissioning/jobs/{job_id}/cancel")
    async def commissioning_cancel(job_id: str, request: Request):
        role = require_role(request, True)
        check_origin(request)
        try:
            return await asyncio.to_thread(manager.cancel, job_id, role)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/v2/commissioning/jobs/{job_id}/artifacts/{name:path}")
    async def commissioning_artifact(job_id: str, name: str, request: Request):
        require_role(request)
        try:
            path = await asyncio.to_thread(manager.artifact_path, job_id, name)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        return FileResponse(
            path, media_type="application/octet-stream", filename=path.name
        )
