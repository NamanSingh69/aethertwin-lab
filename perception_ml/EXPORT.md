# Perception preprocessing and tensor export

The packaged checkpoint remains unchanged. CPU spatial preparation produces one
`SpatialIndexPack` per frame, with local neighbors, deterministic FPS centers,
two radius-neighborhood supports, propagation neighbors, temporal neighbors and
descriptor neighbors. The current-coordinate KD-tree is shared. Temporal
neighbors use localized common-world coordinates, preserving ego-motion
compensation. Arrays grow with the local neighbor counts; there is no whole
cloud pairwise distance matrix.

`prepare_frame(..., previous_coords=None, camera_feature_height=None,
camera_feature_width=None)` includes the tensor pack under
`PreparedFrame.inputs['spatial_indices']`. Training, evaluation and
`InferenceEngine.infer` use this prepared path. Direct legacy network/layer calls
without a pack retain their CPU preparation fallback. Checkpoint tensor names
and shapes are compatible. The exported tensor module requires prepared
indices, and does not execute those fallbacks.

`InferenceEngine` accepts `camera_feature_height` and `camera_feature_width`;
both default to `None`, which preserves native processing and canonical
checkpoint behavior. Both must otherwise be integers at least two. Pixel
projections and visibility tests retain original calibrated image coordinates.
The feature input is resized with aligned image corners, and normalized grid
sampling maps those coordinates onto the feature map. Training resizes semantic
targets with nearest-neighbor sampling. Reducing this resolution changes neural
results and requires an accuracy measurement before adoption.

`infer(..., dense_rgbd=cloud)` reuses already densified LiDAR-frame depth points,
avoiding another backprojection. Supply rows at the configured `rgbd_stride`,
in row-major valid-depth order, using the frame's effective capture pose. Input
may have XYZ or XYZRGB columns. Row count and finiteness are checked. Alternatively
`already_fused=True` accepts a world-coordinate LiDAR-plus-depth input. Do not
supply both options. The caller owns the correspondence between reused data and
the current frame; an arbitrary equal-sized cloud is not validated geometrically.

The bounded `DepthRayCache` stores undistorted sample rays under calibration ID,
intrinsics, distortion, image shape and stride. Depth values and capture poses
are applied on each call. Cache entries contain no frame or label references.
`sensor_synchronizer.py` is unchanged.

## Export

From the project directory:

```sh
python -m perception_ml.tensor_export --checkpoint assets/checkpoints/synthetic.pt --recording assets/sample_recording --output artifacts/tensor_export --threads 2
```

This uses actual recorded sensor observations and writes `perception_tensor.pt2`,
`camera_tensor.pt2`, and `export.json`. Load modules with
`torch.export.load(path).module()`. `tensor_arguments(prepared, previous_coords,
previous_features)` supplies the voxel module's ordered tensor inputs.

These are explicit shape profiles: current and preceding voxel counts, point
count and camera resolution are recorded, and the exported program enforces
those shapes. The profile includes or excludes temporal history as recorded.
Empty frames and missing/expired history are gated by the Python runtime; they
are not silently sent to an incompatible profile. Calibration, raw image
geometry, descriptor generation, voxelization and spatial-index preparation
remain outside the tensor graph.

Export publication requires eager/exported agreement on the representative
input and a second input with changed features. Camera export has a separate
agreement gate. The FP32 tolerance is a maximum absolute error of `2e-5`.
The utility records component latency independently of numerical correctness.
These artifacts do not themselves supply a TensorRT engine or Jetson runtime.
The report explicitly marks that deployment unsupported or unverified.

FP16 on CPU produces an explicit unsupported report and no exported model.
On CUDA, requested FP16 additionally requires a measured comparison against
FP32 within `3e-3`, then export agreement and latency measurements. CUDA/Jetson
has not been available on this laptop, so FP16 is not enabled by default.

## Measured component result

`python -m perception_ml.benchmark_preprocessing` reproduces the comparison.
The recorded Windows run in `artifacts/perception_preprocessing.json` used
1,400 voxels, 2,013 input points, temporal history and two CPU threads. Canonical
model output error was exactly zero. Median neural time including repeated
legacy searches was 210.3 ms; prepared tensor execution was 185.0 ms, or
203.1 ms including its index preparation. The tail measurements were noisy.
Cached backprojection was bit-identical and measured 0.158 ms against 0.597 ms.
These are component measurements, not whole-platform frame-rate claims.
