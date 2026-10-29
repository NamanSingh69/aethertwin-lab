# Synthetic calibration example

`synthetic_pairs.npz` contains generated matched 3D points, image coordinates,
intrinsics and distortion for exercising the calibration solver. It is not a
measurement of any physical camera/LiDAR installation.
Its `measured=false` flag is preserved by the solver. `image_size:[640,480]`
identifies the synthetic profile; it must not be reused for a physical camera.

From the project folder:

```sh
python main.py --mode calibrate --input assets/calibration/synthetic_pairs.npz --output artifacts/synthetic_calibration.json
```

Replace the generated correspondences with independent measured observations
before calibrating a real installation. Live mode requires a measured calibration
identity; do not relabel this fixture or the bundled simulator extrinsics as measured.
