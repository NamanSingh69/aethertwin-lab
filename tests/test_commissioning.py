"""Commissioning uses isolated jobs; these tests avoid expensive live/model work."""

from pathlib import Path
import json
import queue
import time
import numpy as np
import pytest
from fastapi.testclient import TestClient

from command_center.commissioning import (
    CommissioningManager,
    _calibration,
    _tree_bytes,
    _choose_pose_hypothesis,
)
from command_center.service import create_app

OPERATOR = "commissioning-operator-credential-00001"
VIEWER = "commissioning-viewer-credential-0000001"


def test_relocalization_rejects_ambiguous_room_alias_and_prefers_supported_pose():
    correct = np.eye(4)
    alias = np.eye(4)
    alias[:3, 3] = [3, 5, 0]
    diagnostic = {"information_rank": 6, "fitness": 0.9, "rmse": 0.04}
    chosen = _choose_pose_hypothesis(
        [
            ("carried_input_pose", correct, diagnostic),
            ("fpfh_ransac", alias, {**diagnostic, "fitness": 0.6, "rmse": 0.09}),
        ]
    )
    assert chosen[0] == "carried_input_pose"
    np.testing.assert_array_equal(chosen[1], correct)
    with pytest.raises(ValueError, match="Ambiguous"):
        _choose_pose_hypothesis(
            [
                ("carried_input_pose", correct, diagnostic),
                ("fpfh_ransac", alias, {**diagnostic, "fitness": 0.88}),
            ]
        )
    with pytest.raises(ValueError, match="fully constrained"):
        _choose_pose_hypothesis(
            [("planar", correct, {**diagnostic, "information_rank": 3})]
        )


def wait_job(
    manager, job_id, statuses=("completed", "failed", "cancelled"), seconds=10
):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job = manager.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError("Commissioning job did not reach expected state")


def fast_worker(spec, events):
    output = Path(spec["output"])
    (output / "evidence.txt").write_text("isolated test evidence")
    (output / "result.json").write_text(
        json.dumps(
            {
                "success": True,
                "summary": {
                    "source": "test_injection",
                    "hardware_status": "not_tested",
                },
                "promotion_performed": False,
            }
        )
    )


def slow_worker(spec, events):
    (Path(spec["output"]) / "started.txt").write_text("isolated test worker")
    time.sleep(30)


def manager(tmp_path, **kwargs):
    return CommissioningManager(
        {"seed": 7, "simulation": {"mobile": False}},
        root=tmp_path,
        output_root=tmp_path / "jobs",
        **kwargs,
    )


def test_registry_roles_static_declaration_and_no_arbitrary_paths(tmp_path):
    instance = manager(
        tmp_path,
        sources={
            "runtime": {"mode": "sim", "static_scene": False},
            "live": {"mode": "live", "static_scene": True},
        },
        twins={"reference": tmp_path / "reference"},
    )
    with pytest.raises(PermissionError):
        instance.submit({"kind": "discovery"})
    with pytest.raises(ValueError, match="registered IDs"):
        instance.submit({"kind": "discovery", "input_path": "C:/elsewhere"}, "operator")
    with pytest.raises(ValueError, match="registered resource ID"):
        instance.submit(
            {"kind": "coverage", "twin_id": {"url": "http://evil"}}, "operator"
        )
    with pytest.raises(ValueError, match="declared-static"):
        instance.submit({"kind": "build_twin", "source_id": "runtime"}, "operator")
    with pytest.raises(ValueError, match="exclusive rig"):
        instance.submit(
            {"kind": "diagnostics", "source_id": "live", "twin_id": "reference"},
            "operator",
        )
    with pytest.raises(ValueError, match="fixed-position"):
        instance.submit(
            {"kind": "relocalization", "source_id": "runtime", "twin_id": "reference"},
            "operator",
        )
    instance.close()


def test_queue_capacity_cancel_and_idempotent_requests(tmp_path):
    instance = manager(tmp_path, queue_capacity=2)
    try:
        first = instance.submit(
            {"kind": "discovery", "request_id": "first"}, "operator"
        )
        assert (
            instance.submit({"kind": "discovery", "request_id": "first"}, "operator")[
                "id"
            ]
            == first["id"]
        )
        second = instance.submit({"kind": "discovery"}, "operator")
        with pytest.raises(ValueError, match="queue is full"):
            instance.submit({"kind": "discovery"}, "operator")
        with pytest.raises(PermissionError):
            instance.cancel(first["id"], "viewer")
        assert instance.cancel(first["id"], "operator")["status"] == "cancelled"
        assert instance.submit({"kind": "discovery"}, "operator")["status"] == "queued"
    finally:
        instance.close()


def test_actual_discovery_does_not_claim_hardware_and_artifacts_are_scoped(tmp_path):
    instance = manager(tmp_path)
    instance.start()
    try:
        job = instance.submit({"kind": "discovery"}, "operator")
        result = wait_job(instance, job["id"])
        assert result["status"] == "completed", result
        assert result["summary"]["hardware_status"] == "not_tested"
        assert result["summary"]["device_readiness"] == "not_established"
        assert result["promotion_performed"] is False
        assert instance.artifact_path(job["id"], "inventory.json").is_file()
        with pytest.raises(KeyError):
            instance.artifact_path(job["id"], "../../config.yaml")
    finally:
        instance.close()


def test_active_cancel_and_wall_timeout_stop_only_the_owned_job(tmp_path):
    for timeout, cancel in ((10, True), (1, False)):
        instance = CommissioningManager(
            {},
            root=tmp_path,
            output_root=tmp_path / str(timeout),
            max_job_seconds=timeout,
        )
        instance._worker_target = slow_worker
        instance.start()
        try:
            job = instance.submit({"kind": "discovery"}, "operator")
            wait_job(instance, job["id"], ("running",))
            if cancel:
                instance.cancel(job["id"], "operator")
            result = wait_job(instance, job["id"])
            assert result["status"] == ("cancelled" if cancel else "failed")
            assert (
                "cancelled" in result["reason"].lower()
                if cancel
                else "wall-time" in result["reason"]
            )
            assert result["promotion_performed"] is False
        finally:
            instance.close()


def test_aggregate_quota_counts_evidence_not_present_in_memory_history(tmp_path):
    output = tmp_path / "jobs"
    old = output / "older-job-not-in-history"
    old.mkdir(parents=True)
    existing = old / "calibration_candidate.json"
    existing.write_bytes(b"x" * 4096)
    instance = manager(tmp_path, max_output_bytes=4096)
    try:
        assert instance.describe()["jobs"] == []
        with pytest.raises(ValueError, match="Aggregate"):
            instance.submit({"kind": "discovery"}, "operator")
        assert existing.stat().st_size == 4096
    finally:
        instance.close()


def test_history_eviction_preserves_evidence_and_aggregate_usage(tmp_path):
    instance = manager(tmp_path, max_history=1)
    instance._worker_target = fast_worker
    instance.start()
    try:
        first = instance.submit({"kind": "discovery"}, "operator")
        wait_job(instance, first["id"])
        old_file = instance.output_root / first["id"] / "evidence.txt"
        second = instance.submit({"kind": "discovery"}, "operator")
        wait_job(instance, second["id"])
        assert old_file.read_text() == "isolated test evidence"
        assert len(instance.describe()["jobs"]) == 1
        assert _tree_bytes(instance.output_root) > old_file.stat().st_size * 2
    finally:
        instance.close()


def test_calibration_candidate_respects_explicit_synthetic_npz_provenance(tmp_path):
    import cv2

    rng = np.random.default_rng(17)
    points = rng.uniform([-1, -1, 2], [1, 1, 5], (40, 3))
    K = np.array([[600.0, 0, 320], [0, 600.0, 240], [0, 0, 1]])
    pixels = cv2.projectPoints(points, np.zeros(3), np.zeros(3), K, np.zeros(5))[
        0
    ].reshape(-1, 2)
    path = tmp_path / "synthetic_pairs.npz"
    np.savez_compressed(
        path,
        lidar_points=points[:28],
        image_points=pixels[:28],
        K=K,
        distortion=np.zeros(5),
        measured=np.array(False),
        validation_lidar_points=points[28:],
        validation_image_points=pixels[28:],
    )
    active = tmp_path / "active_calibration.json"
    active.write_text('{"active":"unchanged"}')
    output = tmp_path / "candidate"
    output.mkdir()
    result = _calibration(
        {
            "correspondence": {
                "id": "board",
                "path": str(path),
                "measured": True,
                "independent_validation": False,
            }
        },
        output,
        queue.Queue(),
    )
    assert result["measured"] is False
    assert result["calibration_id"].startswith("synthetic-")
    assert result["active"] is False and result["promotion_permitted"] is False
    assert result["hardware_status"] == "not_tested"
    assert result["validation"]["status"] == "not_tested"
    assert result["observability"]["jacobian_rank"] == 6
    assert result["observability"]["scaled_jacobian_rank"] == 6
    assert len(result["observability"]["scaled_jacobian_singular_values"]) == 6
    assert "max_scaled_condition_number" in result["observability"]
    assert "left tangent of exported T_lidar_camera" in result["covariance_convention"]
    assert "LiDAR frame" in result["covariance_convention"]
    assert result["covariance_units"] == ["rad", "rad", "rad", "m", "m", "m"]
    assert np.asarray(result["covariance_se3"]).shape == (6, 6)
    assert result["supplementary_rodrigues_rank"]["fitted_jacobian_rank"] == 6
    assert result["validation"]["point_reprojection_rmse_pixels"] < 0.01
    assert active.read_text() == '{"active":"unchanged"}'


def test_api_operator_viewer_origin_and_job_artifact_access(tmp_path):
    instance = manager(tmp_path)
    instance._worker_target = fast_worker
    app = create_app(
        commissioning=instance,
        operator_token=OPERATOR,
        viewer_token=VIEWER,
        local_development=False,
    )
    with TestClient(app, base_url="https://commission.test") as client:
        assert client.get("/api/v2/commissioning").status_code == 401
        assert client.post("/api/v2/session", json={"token": VIEWER}).status_code == 200
        assert (
            client.get("/api/v2/commissioning").json()["automatic_promotion"] is False
        )
        assert (
            client.post(
                "/api/v2/commissioning/jobs", json={"kind": "discovery"}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/v2/session", json={"token": OPERATOR}).status_code == 200
        )
        assert (
            client.post(
                "/api/v2/commissioning/jobs",
                json={"kind": "discovery"},
                headers={"Origin": "https://untrusted.invalid"},
            ).status_code
            == 403
        )
        response = client.post(
            "/api/v2/commissioning/jobs",
            json={"kind": "discovery", "request_id": "api-job"},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        result = wait_job(instance, job["id"])
        assert result["status"] == "completed", result
        downloaded = client.get(
            f"/api/v2/commissioning/jobs/{job['id']}/artifacts/evidence.txt"
        )
        assert (
            downloaded.status_code == 200
            and downloaded.text == "isolated test evidence"
        )
        assert (
            client.get(
                f"/api/v2/commissioning/jobs/{job['id']}/artifacts/unknown.txt"
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v2/commissioning/jobs",
                json={"kind": "discovery", "max_frames": 129},
            ).status_code
            == 409
        )
