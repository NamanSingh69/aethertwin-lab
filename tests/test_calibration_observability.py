"""Calibration acceptance must follow measured reprojection observability."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import ingestion.calibration as calibration
from core.se3_math import inverse, se3_exp, transform_points


K = np.array([[700., 0, 320], [0, 710., 240], [0, 0, 1.]])
D = np.array([.015, -.003, .0004, -.0002, .0001])


def observations(kind='volumetric', thin=1.):
    if kind == 'volumetric':
        p = np.random.default_rng(2).uniform([-1, -.8, 2.5], [1, .8, 5.5], (60, 3))
        r = np.array([.12, -.08, .03]); t = np.array([.3, -.15, .4])
    else:
        x, y = np.meshgrid(np.linspace(-.8, .8, 6), np.linspace(-.6, .6, 5))
        p = np.c_[x.ravel(), y.ravel() * thin, np.zeros(x.size)]
        r = np.zeros(3) if thin < 1 else np.array([.12, -.08, .03])
        t = np.array([0, 0, 4.])
    uv = cv2.projectPoints(p, r, t, K, D)[0].reshape(-1, 2)
    T = np.eye(4); T[:3, :3] = cv2.Rodrigues(r)[0]; T[:3, 3] = t
    return p, uv, T


@pytest.mark.parametrize('kind,spatial_rank', [('planar', 2), ('volumetric', 3)])
def test_valid_planar_and_volume_observe_six_pose_parameters(kind, spatial_rank):
    p, uv, expected = observations(kind)
    result, report = calibration.calibrate_camera_lidar(p, uv, K, D, image_size=(640, 480))
    np.testing.assert_allclose(result.T_lidar_camera @ expected, np.eye(4), atol=2e-6)
    assert report['geometry']['lidar_affine_rank'] == spatial_rank
    assert report['observability']['jacobian_rank'] == 6
    assert report['observability']['scaled_jacobian_rank'] == 6
    assert report['observability']['scaled_jacobian_condition_number'] < 1e8
    assert len(report['observability']['jacobian_singular_values']) == 6
    covariance = np.asarray(report['covariance_se3'])
    assert np.isfinite(covariance).all() and np.linalg.eigvalsh(covariance).min() > 0
    assert report['covariance_noise_model']['used_variance_pixels2'] == .25 ** 2
    assert report['reprojection_rmse_pixels'] < 1e-5
    assert report['candidate']['status'] == 'validated'
    assert not report['publication']['active_promotion']
    json.dumps(report, allow_nan=False)


def test_collinear_landmarks_have_unobservable_motion_and_are_rejected():
    p = np.c_[np.linspace(-1., 1., 12), np.zeros(12), np.full(12, 4.)]
    uv, jac = cv2.projectPoints(p, np.zeros(3), np.zeros(3), K, D)
    physical = calibration._pose_jacobian(p, jac[:, 3:6])
    observability, *_ = calibration._observability(physical, p, 1e-9)
    assert observability['jacobian_rank'] == 5
    with pytest.raises(calibration.CalibrationDegeneracyError, match='collinear') as error:
        calibration.calibrate_camera_lidar(p, uv.reshape(-1, 2), K, D)
    assert error.value.diagnostics['geometry']['lidar_affine_rank'] == 1
    assert not error.value.diagnostics['calibration_written']
    assert 'covariance_se3' not in error.value.diagnostics


def test_duplicate_count_does_not_create_independent_landmarks():
    p, uv, _ = observations()
    with pytest.raises(calibration.CalibrationDegeneracyError, match='distinct') as error:
        calibration.calibrate_camera_lidar(np.repeat(p[:5], 20, axis=0), np.repeat(uv[:5], 20, axis=0), K, D)
    diagnostics = error.value.diagnostics
    assert diagnostics['observations'] == 100 and diagnostics['unique_observations'] == 5
    assert diagnostics['candidate']['status'] == 'rejected'
    assert len(diagnostics['candidate']['input_sha256']) == 64


def test_repeating_an_observation_does_not_invent_extra_covariance_information():
    p, uv, _ = observations('planar')
    _, original = calibration.calibrate_camera_lidar(p, uv, K, D)
    _, repeated = calibration.calibrate_camera_lidar(np.repeat(p, 4, axis=0), np.repeat(uv, 4, axis=0), K, D)
    assert repeated['duplicate_observations'] == 3 * len(p)
    assert repeated['inliers'] == original['inliers'] == len(p)
    np.testing.assert_allclose(repeated['covariance_se3'], original['covariance_se3'], rtol=1e-7, atol=1e-12)


@pytest.mark.parametrize('thin,expected_rank,reason', [(1e-6, 6, 'conditioning'), (1e-8, 5, 'six pose')])
def test_tiny_board_span_is_rejected_even_with_nearly_zero_reprojection_error(thin, expected_rank, reason):
    p, uv, _ = observations('planar', thin)
    with pytest.raises(calibration.CalibrationDegeneracyError, match=reason) as error:
        calibration.calibrate_camera_lidar(p, uv, K, D)
    diagnostics = error.value.diagnostics
    assert diagnostics['stage'] == 'observability'
    assert diagnostics['geometry']['lidar_affine_rank'] == 2  # Planarity itself is accepted.
    assert diagnostics['observability']['jacobian_rank'] == expected_rank
    assert diagnostics['observability']['scaled_jacobian_rank'] == expected_rank
    if expected_rank == 6:
        assert diagnostics['observability']['scaled_jacobian_condition_number'] > 1e8
    else:
        assert diagnostics['observability']['scaled_jacobian_condition_number'] is None
    assert 'covariance_se3' not in diagnostics
    json.dumps(diagnostics, allow_nan=False)


def test_brown_distorted_physical_jacobian_matches_se3_finite_difference():
    p, _, T = observations()
    camera_points = transform_points(T, p)
    _, cv_jac = cv2.projectPoints(p, cv2.Rodrigues(T[:3, :3])[0], T[:3, 3], K, D)
    jacobian = calibration._pose_jacobian(camera_points, cv_jac[:, 3:6])
    numerical = np.empty_like(jacobian)
    for axis in range(6):
        delta = np.zeros(6); delta[axis] = 1e-7
        positive = transform_points(se3_exp(delta), camera_points)
        negative = transform_points(se3_exp(-delta), camera_points)
        pixel_positive = cv2.projectPoints(positive, np.zeros(3), np.zeros(3), K, D)[0].ravel()
        pixel_negative = cv2.projectPoints(negative, np.zeros(3), np.zeros(3), K, D)[0].ravel()
        numerical[:, axis] = (pixel_positive - pixel_negative) / 2e-7
    np.testing.assert_allclose(jacobian, numerical, rtol=1e-6, atol=1e-5)


def test_covariance_is_for_exported_inverse_pose_in_lidar_tangent_frame():
    p, uv, _ = observations()
    result, report = calibration.calibrate_camera_lidar(p, uv, K, D)
    jacobian = np.empty((2 * len(p), 6))
    for axis in range(6):
        delta = np.zeros(6); delta[axis] = 1e-7
        positive = transform_points(inverse(se3_exp(delta) @ result.T_lidar_camera), p)
        negative = transform_points(inverse(se3_exp(-delta) @ result.T_lidar_camera), p)
        plus = cv2.projectPoints(positive, np.zeros(3), np.zeros(3), K, D)[0].ravel()
        minus = cv2.projectPoints(negative, np.zeros(3), np.zeros(3), K, D)[0].ravel()
        jacobian[:, axis] = (plus - minus) / 2e-7
    expected = .25 ** 2 * np.linalg.inv(jacobian.T @ jacobian)
    np.testing.assert_allclose(report['covariance_se3'], expected, rtol=2e-6, atol=1e-12)
    assert 'LiDAR frame' in report['covariance_convention']


def test_covariance_pixel_noise_floor_prevents_zero_uncertainty_in_exact_fit():
    p, uv, _ = observations('planar')
    _, first = calibration.calibrate_camera_lidar(p, uv, K, D, pixel_noise_std=.25)
    _, second = calibration.calibrate_camera_lidar(p, uv, K, D, pixel_noise_std=.5)
    np.testing.assert_allclose(second['covariance_se3'], 4 * np.asarray(first['covariance_se3']), rtol=1e-12)


def test_ransac_inliers_remain_supported_with_outlying_pixel_correspondences():
    p, uv, _ = observations()
    uv[:8] += np.array([80, -90])
    result, report = calibration.calibrate_camera_lidar(p, uv, K, D)
    assert 40 < report['inliers'] < len(p)
    assert report['observability']['jacobian_rank'] == 6
    assert result.measured and np.isfinite(report['covariance_se3']).all()


@pytest.mark.parametrize('kind,thin', [('duplicate', 1), ('collinear', 1), ('planar', 1e-6), ('planar', 1e-8)])
def test_degenerate_candidate_preserves_prior_export_bytes(tmp_path, kind, thin):
    output = tmp_path / 'measured.json'
    prior = b'{"calibration_id":"previous-measured", "retain":"exact bytes"}\n'
    output.write_bytes(prior)
    p, uv, _ = observations('planar', thin)
    if kind == 'duplicate':
        p, uv = np.repeat(p[:3], 10, axis=0), np.repeat(uv[:3], 10, axis=0)
    elif kind == 'collinear':
        p[:, 1] = 0
    with pytest.raises(calibration.CalibrationDegeneracyError) as error:
        calibration.calibrate_camera_lidar(p, uv, K, D, output)
    assert output.read_bytes() == prior
    assert not error.value.diagnostics['calibration_written']
    assert error.value.diagnostics['candidate']['status'] == 'rejected'
    assert list(tmp_path.iterdir()) == [output]


def test_valid_candidate_is_atomic_export_and_archives_previous_exact_bytes(tmp_path, monkeypatch):
    p, uv, _ = observations()
    output = tmp_path / 'measured.json'
    _, first = calibration.calibrate_camera_lidar(p, uv, K, D, output, image_size=(640, 480))
    prior = output.read_bytes()
    replace = calibration.os.replace
    replacements = []

    def inspect_replace(source, destination):
        parsed = json.loads(Path(source).read_bytes())
        assert parsed['candidate']['status'] == 'validated'
        replacements.append(Path(destination))
        replace(source, destination)

    monkeypatch.setattr(calibration.os, 'replace', inspect_replace)
    uv = cv2.projectPoints(p, np.array([.08, -.04, .01]), np.array([.2, -.1, .3]), K, D)[0].reshape(-1, 2)
    _, second = calibration.calibrate_camera_lidar(p, uv, K, D, output, image_size=(640, 480))
    previous = second['publication']['previous_output']
    assert previous['sha256'] == hashlib.sha256(prior).hexdigest()
    assert Path(previous['retained_path']).read_bytes() == prior
    assert previous['calibration_id'] == first['calibration_id']
    assert first['candidate']['candidate_id'] != second['candidate']['candidate_id']
    assert first['calibration_id'] != second['calibration_id']
    assert json.loads(output.read_bytes()) == second
    assert not second['publication']['active_promotion']
    assert output.resolve() == replacements[-1]
    assert not list(tmp_path.glob('*.tmp'))


def test_failed_atomic_replacement_preserves_prior_and_cleans_temporary_file(tmp_path, monkeypatch):
    p, uv, _ = observations()
    output = tmp_path / 'measured.json'
    prior = b'{"calibration_id":"keep-this-one"}\n'
    output.write_bytes(prior)
    replace = calibration.os.replace

    def fail_final_replace(source, destination):
        if Path(destination) == output.resolve():
            raise OSError('simulated publication failure')
        replace(source, destination)

    monkeypatch.setattr(calibration.os, 'replace', fail_final_replace)
    with pytest.raises(OSError, match='publication failure'):
        calibration.calibrate_camera_lidar(p, uv, K, D, output)
    assert output.read_bytes() == prior
    archived = tmp_path / 'measured.history' / f'{hashlib.sha256(prior).hexdigest()}.json'
    assert archived.read_bytes() == prior
    assert not list(tmp_path.rglob('*.tmp'))
