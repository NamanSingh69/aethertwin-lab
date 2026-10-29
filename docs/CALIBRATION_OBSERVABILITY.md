# Camera–LiDAR calibration acceptance

`ingestion.calibration.calibrate_camera_lidar` still accepts paired LiDAR XYZ
landmarks in metres, corresponding image pixels, known camera intrinsics and
five Brown distortion coefficients. It returns `(CameraCalibration, report)`.
The optional keyword policies are `rank_rtol=1e-9`,
`max_condition_number=1e8`, and `pixel_noise_std=0.25` pixels. These settings and
the actual singular values are included in each schema-2 report.

Known 3D-to-2D planar board correspondences can constrain all six pose parameters.
They are accepted when the inlier reprojection Jacobian has rank six and meets
the conditioning limit. This differs from a floor plane-to-plane constraint,
which alone leaves translation within the floor and yaw unobservable. Fewer
than six distinct landmarks, coincident/collinear landmarks, rank-deficient
reprojection Jacobians and excessively ill-conditioned fits are rejected before
any calibration output is written. A small pixel residual does not bypass these
checks. Repeated identical 3D landmarks are averaged in image space and counted
once, so copied observations cannot create independent information.

The report's `geometry` and `inlier_geometry` include centered 3D and image
affine ranks and singular values. `observability` reports the actual six-column
Jacobian rank, singular values, rank tolerance and condition number. Its physical
Jacobian describes the left tangent of `T_camera_lidar`, with rotation in radians
and translation in metres. The acceptance condition number is dimensionless:
translation columns are multiplied by the RMS inlier camera range `L`, so
`J_scaled = J @ diag(1,1,1,L,L,L)`. Both physical and normalized Jacobians must
have rank six; the normalized condition must not exceed the configured limit.
An unresolved condition is represented as JSON `null`, never infinity.

`covariance_se3` is computed only after those checks using SVD, without a
pseudoinverse or inversion of squared normal-equation conditioning. It describes
the **left tangent of the exported `T_lidar_camera` in the LiDAR frame**, ordered
`[rx,ry,rz,tx,ty,tz]`, with units `[rad,rad,rad,m,m,m]`. The covariance is transported
through inversion using the SE(3) adjoint. The report states its convention and
noise model. Pixel variance is the greater of residual variance divided by
`2 * inlier_count - 6` and the configured pixel-noise floor squared. Therefore
an exact synthetic fit does not assert zero uncertainty. This local covariance
conditions on fixed intrinsics/distortion, independent pixel errors, the selected
inliers and the chosen PnP solution. It does not establish global uniqueness of
a planar PnP solution, account for LiDAR measurement uncertainty, or certify
physical calibration accuracy. `measured` is preserved input provenance, not a
sensor authenticity check.

`CalibrationDegeneracyError` remains a `ValueError` and carries `.diagnostics`:
the rejection `stage`, `reason`, geometry/observability results available at that
stage, `calibration_written: false`, and the rejected `candidate` identity. There
is no covariance for a rejected candidate. The candidate's `input_sha256` binds
the input observations, intrinsics, distortion, image dimensions, measured
declaration and numerical policy. Callers can retain these diagnostics without
publishing them as a usable calibration.

An explicit `output` requests an export, with `publication.active_promotion`
always false. Every fit and covariance check completes first. The complete JSON
is written and flushed to a temporary file in the output directory, then
atomically replaces the requested file. If that file already exists, its exact
bytes are retained at `<output-stem>.history/<sha256>.json`; the new report records
their hash, calibration identity when parseable, and archive path in
`publication.previous_output`. Failed fits/validation leave the preceding output
untouched. Failed final replacement also preserves that output and removes the
temporary file. This API does not change an active-calibration registry or
automatically activate the candidate. Concurrent writers to the same explicit
export path must be serialized by the caller.

The regression tests cover valid planar and volumetric correspondences,
collinearity, copied landmarks, poor but full-rank conditioning, actual deficient
reprojection rank, finite-difference Brown-distorted SE(3) Jacobians and inverse
pose covariance, RANSAC outliers, nonzero exact-fit uncertainty, atomic export,
history provenance and previous-output preservation. Physical-device calibration
and real-world accuracy remain unverified.
