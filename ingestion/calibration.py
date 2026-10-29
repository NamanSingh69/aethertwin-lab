"""Observed 3D/2D extrinsics with an explicit local observability gate.

An exported calibration is a validated candidate, not an activation request.
A small reprojection error alone does not establish that six DoF are observed.
"""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import cv2
import numpy as np
from core.contracts import CameraCalibration
from core.se3_math import adjoint, inverse


class CalibrationDegeneracyError(ValueError):
    """Unusable correspondences/pose with machine-readable rejection diagnostics."""
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def _spectrum(matrix, rank_rtol):
    _, singular, vt = np.linalg.svd(matrix, full_matrices=False)
    tolerance = float(singular[0] * max(rank_rtol, np.finfo(float).eps * max(matrix.shape))) if len(singular) else 0.
    rank = int(np.count_nonzero(singular > tolerance))
    # None denotes unresolved/unbounded condition, without JSON Infinity.
    condition = float(singular[0] / singular[-1]) if rank == matrix.shape[1] else None
    return {'rank': rank, 'singular_values': singular.tolist(),
            'rank_tolerance': tolerance, 'condition_number': condition}, singular, vt


def _geometry_diagnostics(points, pixels, rank_rtol):
    spatial, _, _ = _spectrum(points - points.mean(axis=0), rank_rtol)
    image, _, _ = _spectrum(pixels - pixels.mean(axis=0), rank_rtol)
    return {'lidar_affine_rank': spatial['rank'], 'lidar_centered_singular_values_m': spatial['singular_values'],
            'image_affine_rank': image['rank'], 'image_centered_singular_values_pixels': image['singular_values']}


def _pose_jacobian(camera_points, projection_translation_jacobian):
    """du/dxi for exp(xi) T_camera_lidar, xi=[omega_camera, v_camera].

    OpenCV translation columns are du/dp_camera, including Brown distortion.
    Its additive Rodrigues columns are not the exported inverse SE(3) tangent.
    """
    points = np.asarray(camera_points, dtype=np.float64)
    projection = np.asarray(projection_translation_jacobian, dtype=np.float64).reshape(-1, 2, 3)
    skew = np.zeros((len(points), 3, 3))
    skew[:, 0, 1] = -points[:, 2]; skew[:, 0, 2] = points[:, 1]
    skew[:, 1, 0] = points[:, 2]; skew[:, 1, 2] = -points[:, 0]
    skew[:, 2, 0] = -points[:, 1]; skew[:, 2, 1] = points[:, 0]
    return np.concatenate((projection @ -skew, projection), axis=2).reshape(-1, 6)


def _observability(jacobian, camera_points, rank_rtol):
    # Normalize translation by RMS camera range so the conditioning policy is
    # dimensionless, instead of comparing numerical metres against radians.
    length = float(np.sqrt(np.mean(np.sum(camera_points ** 2, axis=1))))
    scale = np.diag([1., 1., 1., length, length, length])
    raw, _, _ = _spectrum(jacobian, rank_rtol)
    scaled, singular, vt = _spectrum(jacobian @ scale, rank_rtol)
    return {'parameter_count': 6, 'jacobian_rank': raw['rank'],
            'jacobian_singular_values': raw['singular_values'], 'jacobian_rank_tolerance': raw['rank_tolerance'],
            'jacobian_condition_number_mixed_units': raw['condition_number'],
            'scaled_jacobian_rank': scaled['rank'], 'scaled_jacobian_singular_values': scaled['singular_values'],
            'scaled_jacobian_rank_tolerance': scaled['rank_tolerance'],
            'scaled_jacobian_condition_number': scaled['condition_number'],
            'translation_scale_m': length, 'rank_rtol': float(rank_rtol),
            'jacobian_convention': 'left tangent of T_camera_lidar; [rx,ry,rz,tx,ty,tz], radians/metres',
            'scaling_convention': 'J_scaled = J @ diag(1,1,1,L,L,L); L = RMS inlier camera range'}, scale, singular, vt


def _write_atomic(path, data):
    """A replacement is visible only after complete same-directory file flush."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _publish(report, output):
    path = Path(output).resolve()
    publication = {'status': 'exported', 'output_path': str(path), 'atomic_replace': True,
                   'active_promotion': False, 'previous_output': None}
    if path.exists():
        previous = path.read_bytes()
        digest = hashlib.sha256(previous).hexdigest()
        archive = path.parent / f'{path.stem}.history' / f'{digest}.json'
        if archive.exists():
            if archive.read_bytes() != previous:
                raise ValueError('Calibration history conflict; previous output has been preserved')
        else:
            _write_atomic(archive, previous)
        try:
            previous_id = json.loads(previous).get('calibration_id')
        except (ValueError, AttributeError, UnicodeDecodeError):
            previous_id = None
        publication['previous_output'] = {'sha256': digest, 'calibration_id': previous_id, 'retained_path': str(archive)}
    report['publication'] = publication
    _write_atomic(path, (json.dumps(report, indent=2, allow_nan=False) + '\n').encode('utf-8'))


def calibrate_camera_lidar(lidar_points, image_points, K, distortion=None, output=None, *,
                           image_size=None, measured=True, rank_rtol=1e-9,
                           max_condition_number=1e8, pixel_noise_std=.25):
    """Fit observed landmarks and export only a fully observable candidate.

    A planar board (3D affine rank two) may fully constrain known 3D/2D PnP.
    Exact repeated 3D landmarks are averaged in image space and counted once.
    Covariance conditions on fixed K/distortion and independent pixel errors.
    Failures never replace output; successful exports archive preceding bytes.
    """
    p = np.asarray(lidar_points, np.float64); uv = np.asarray(image_points, np.float64)
    K = np.asarray(K, np.float64)
    if p.ndim != 2 or p.shape[1] != 3 or uv.shape != (len(p), 2) or len(p) < 6:
        raise ValueError('At least six paired 3D/2D observations required')
    d = np.zeros(5) if distortion is None else np.asarray(distortion, np.float64)
    if not isinstance(measured, (bool, np.bool_)):
        raise ValueError('Calibration measured provenance must be boolean')
    if not np.isfinite([rank_rtol, max_condition_number, pixel_noise_std]).all() or not (0 < rank_rtol < 1) or max_condition_number < 1 or pixel_noise_std <= 0:
        raise ValueError('Calibration requires 0 < rank_rtol < 1, condition limit >= 1 and positive pixel noise')
    if image_size is not None:
        size = np.asarray(image_size)
        if size.shape != (2,) or not np.isfinite(size).all() or np.any(size < 1) or np.any(size != np.round(size)):
            raise ValueError('Calibration image_size must be positive [width,height] integers')
        image_size = tuple(map(int, size))
    if not all(np.isfinite(value).all() for value in (p, uv, K, d)):
        raise ValueError('Calibration observations must be finite')
    CameraCalibration(K, np.eye(4), d)
    if not np.allclose(K[2], [0, 0, 1], atol=1e-12, rtol=0) or not np.allclose([K[0, 1], K[1, 0]], 0, atol=1e-12, rtol=0):
        raise ValueError('Calibration requires canonical zero-skew pinhole K')

    policy = {'rank_rtol': float(rank_rtol), 'max_scaled_condition_number': float(max_condition_number),
              'pixel_noise_std_floor_pixels': float(pixel_noise_std)}
    digest = hashlib.sha256()
    for value in (p, uv, K, d):
        digest.update(str(value.shape).encode('ascii')); digest.update(np.ascontiguousarray(value, dtype='<f8').tobytes())
    digest.update(json.dumps({'image_size': image_size, 'measured': bool(measured), **policy}, sort_keys=True).encode('utf-8'))
    input_digest = digest.hexdigest()
    candidate = {'candidate_id': f'board-candidate-{input_digest[:24]}', 'input_sha256': input_digest,
                 'status': 'fitting', 'measured_declaration': bool(measured),
                 'method': 'OpenCV solvePnPRansac + solvePnPRefineLM', 'active_promotion': False}
    original_count = len(p)
    p_unique, inverse_rows, counts = np.unique(p, axis=0, return_inverse=True, return_counts=True)
    uv_unique = np.zeros((len(p_unique), 2), np.float64)
    np.add.at(uv_unique, inverse_rows, uv); uv_unique /= counts[:, None]
    diagnostic = {'candidate': candidate, 'policy': policy, 'observations': original_count,
                  'unique_observations': len(p_unique), 'duplicate_observations': original_count - len(p_unique)}

    def reject(message, stage):
        diagnostic['stage'] = stage; candidate['status'] = 'rejected'
        diagnostic['reason'] = message; diagnostic['calibration_written'] = False
        raise CalibrationDegeneracyError(message, diagnostic)

    if len(p_unique) < 6:
        reject('Calibration failed: at least six distinct 3D landmarks required', 'geometry')
    p, uv = p_unique, uv_unique
    diagnostic['geometry'] = _geometry_diagnostics(p, uv, rank_rtol)
    if diagnostic['geometry']['lidar_affine_rank'] < 2:
        reject('Calibration failed: collinear/coincident 3D landmarks do not constrain a full pose', 'geometry')
    try:
        ok, r, t, inliers = cv2.solvePnPRansac(p, uv, K, d, iterationsCount=300, reprojectionError=2., confidence=.999)
        if not ok or inliers is None or len(inliers) < 6:
            reject('Calibration failed: insufficient geometric support', 'ransac')
        idx = inliers[:, 0]
        diagnostic['inlier_geometry'] = _geometry_diagnostics(p[idx], uv[idx], rank_rtol)
        if diagnostic['inlier_geometry']['lidar_affine_rank'] < 2:
            reject('Calibration failed: inlier landmarks are collinear/coincident', 'geometry')
        r, t = cv2.solvePnPRefineLM(p[idx], uv[idx], K, d, r, t)
        predicted, jac = cv2.projectPoints(p[idx], r, t, K, d)
        rotation = cv2.Rodrigues(r)[0]
    except cv2.error as exc:
        reject(f'Calibration failed in OpenCV pose fitting: {exc}', 'solver')
    camera_points = p[idx] @ rotation.T + np.asarray(t).reshape(3)
    residual = (predicted.reshape(-1, 2) - uv[idx]).ravel()
    if not all(np.isfinite(value).all() for value in (rotation, camera_points, residual, jac)) or np.any(camera_points[:, 2] <= 0):
        reject('Calibration failed: non-finite or behind-camera inlier solution', 'pose_validation')
    physical_jacobian = _pose_jacobian(camera_points, jac[:, 3:6])
    observability, scale, singular, vt = _observability(physical_jacobian, camera_points, rank_rtol)
    observability['max_scaled_condition_number'] = float(max_condition_number)
    diagnostic['observability'] = observability
    if observability['jacobian_rank'] != 6 or observability['scaled_jacobian_rank'] != 6:
        reject('Calibration failed: reprojection Jacobian does not observe all six pose parameters', 'observability')
    if observability['scaled_jacobian_condition_number'] > max_condition_number:
        reject('Calibration failed: reprojection geometry exceeds the conditioning limit', 'observability')

    T = np.eye(4); T[:3, :3] = rotation; T[:3, 3] = np.asarray(t).reshape(3)
    exported = inverse(T)
    dof = max(len(residual) - 6, 1)
    residual_variance = float(residual @ residual / dof)
    noise_variance = max(residual_variance, float(pixel_noise_std) ** 2)
    # SVD avoids squaring the condition number in J.T@J. Inverse-pose tangent
    # transport is -Ad(T_lidar_camera); its sign cancels in covariance.
    factor = adjoint(exported) @ scale @ vt.T @ np.diag(np.sqrt(noise_variance) / singular)
    covariance = factor @ factor.T
    covariance = (covariance + covariance.T) * .5
    if not np.isfinite(covariance).all():
        reject('Calibration failed: non-finite pose covariance', 'covariance')
    identity = json.dumps({'K': K.tolist(), 'T_lidar_camera': exported.tolist(),
                           'distortion': d.tolist(), 'image_size': image_size}, sort_keys=True).encode('utf-8')
    calibration_id = ('measured' if measured else 'synthetic') + '-board-v1-' + hashlib.sha256(identity).hexdigest()[:16]
    result = CameraCalibration(K, exported, d, calibration_id=calibration_id, measured=bool(measured))
    candidate['status'] = 'validated'
    report = {'schema_version': 2, 'K': K.tolist(), 'distortion': d.tolist(), 'T_lidar_camera': exported.tolist(),
              'measured': bool(measured), 'calibration_id': result.calibration_id,
              'reprojection_rmse_pixels': float(np.sqrt(np.mean(residual ** 2))),
              'reprojection_point_rmse_pixels': float(np.sqrt(np.mean(np.sum(residual.reshape(-1, 2) ** 2, axis=1)))),
              'inliers': len(idx), 'observations': original_count, 'unique_observations': len(p),
              'duplicate_observations': original_count - len(p), 'geometry': diagnostic['geometry'],
              'inlier_geometry': diagnostic['inlier_geometry'], 'observability': observability,
              'covariance_se3': covariance.tolist(),
              'covariance_convention': 'left tangent of exported T_lidar_camera; [rx,ry,rz,tx,ty,tz] in LiDAR frame',
              'covariance_units': ['rad', 'rad', 'rad', 'm', 'm', 'm'],
              'covariance_noise_model': {'residual_variance_pixels2': residual_variance,
                  'degrees_of_freedom': dof, 'pixel_noise_std_floor_pixels': float(pixel_noise_std),
                  'used_variance_pixels2': noise_variance,
                  'conditional_on': 'fixed K/distortion, independent pixel errors, inlier selection and local PnP solution'},
              'candidate': candidate, 'publication': {'status': 'not_written', 'active_promotion': False}}
    if image_size is not None:
        report['image_size'] = list(image_size)
    if output is not None:
        _publish(report, output)
    return result, report
