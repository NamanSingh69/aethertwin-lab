# Diagnostic evidence

Source: **sim**. Software checks: **True**. Hardware checks: **False**.

| Stage | Result | Evidence/limit |
|---|---|---|
| sdk_device_discovery | not_tested | No physical device is opened for simulation/replay. SDK availability is not device verification. |
| source_initialization | passed | Selected source only; live failures never substitute simulation |
| pipeline_observations | passed | Ordinary deployed checkpoint and observation-only perception pipeline, no simulator label inputs |
| imu_observations | passed | Measured details in report.json |
| modality_coverage | passed | Profile/identity/coverage check; no extrinsic or semantic accuracy validation |
| floor_alignment | passed | Gravity-aligned world assumed; diagnostic plane only, no calibration/map promotion. |
| map_residuals | passed | Descriptive registration-conditioned observation centroids, including dynamics/new obstacles; not world-pose or real-lab accuracy validation. |
| floor_repeatability | passed | Conditional observed floor repeatability; not whole-lab geometry, neural accuracy or extrinsic calibration accuracy. |
| virtual_closed_loop | passed | Isolated software targets and standalone blocked grid; no physical target, sensor observation or active map is fabricated. |
| independent_extrinsic_calibration | not_tested | No independent measured calibration targets supplied; floor constrains only three degrees of freedom |
| physical_target_accuracy | not_tested | Virtual closed-loop targets do not establish physical detection/tracking/planning accuracy |

No calibration, map, firmware or network configuration was promoted or changed. Virtual target injections are separately labelled and isolated from the active map.

Pending real validation: connected-device acquisition and synchronization, independent measured extrinsic calibration, independently annotated real-lab semantic/flow accuracy, physical target/trajectory safety testing

Canonical report SHA256: `5c82328c197942f2bbb086bdb18a307f5c7fa559cf6237c0c7f633352b019f34`
