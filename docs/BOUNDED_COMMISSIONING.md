# Bounded commissioning and map storage

These are software foundations tested with analytic/simulated observations.
Physical L2/Gemini acquisition and Jetson hardware performance remain unverified.

`build-twin` streams its input once. It records original acquisitions under
`commissioning/`, prepares pointwise motion-compensated observations under
`prepared_frames/`, then replays those corrected observations for reconstruction
and conservative navigation carving. Raw recordings retain their original
coordinates; prepared recordings declare `lidar_sweep_end` and must not be
passed through deskew a second time.

## Commissioning configuration

`mapping.commissioning_mode` accepts `auto`, `mobile` or `stations`.
`mapping.commissioning_initial_velocity` is an optional world-frame m/s vector
at the first mobile sweep start. With no prior, the report explicitly identifies
an assumed-zero, unobservable bootstrap: an IMU cannot observe constant velocity.
IMU state is carried between chronological mobile sweeps and corrected by ICP
using elapsed sweep-end cadence. Already deskewed input is reused. Independent
stations reset an at-rest state only when explicitly selected or declared by
station calibration metadata. Gyro readings alone do not establish stationarity.

`mapping.commissioning_budgets` overrides these positive-integer defaults:

| Key | Default | Purpose |
|---|---:|---|
| max_frames | 10000 | Bound pose/provenance/manifest metadata |
| frame_buffer_bytes | 33554432 | Raw/prepared recorder array buffer |
| frame_disk_bytes | 68719476736 | Each raw/prepared recording disk allowance |
| max_keyframes | 32 | Active, globally anchored pose-graph window |
| registration_points | 5000 | Per-sweep registration sample cap |
| reconstruction_points | 120000 | Uniform measured-point preview reservoir |
| tile_size | 16 | Occupancy/surface page side in voxels |
| max_resident_tiles | 32 | LRU page count |
| max_resident_bytes | 67108864 | Byte-counted resident Python tile pages |
| sqlite_cache_bytes | 2097152 | SQLite page cache per store |
| max_disk_bytes | 4294967296 | Each occupancy/surface store including journal reserve |
| tsdf_tile_resolution | 48 | One resident uniform TSDF cube side in voxels |
| tsdf_disk_bytes | 2147483648 | TSDF index/mesh archive allowance |
| mesh_preview_vertices | 120000 | Display TSDF/Poisson preview vertex target |
| mesh_preview_triangles | 240000 | Display mesh triangle cap |
| max_in_memory_frames | 32 | Compatibility-only direct builder cap |
| max_in_memory_bytes | 134217728 | Compatibility-only direct observation cap |

The graph optimizes its active window and retires anchored poses. It does not
claim an unbounded global loop optimization. Full raw/prepared observations and
measured surface/occupancy data remain on disk. Point and mesh previews may be
incomplete and are labelled accordingly; previews never define collision/free
space. Poisson closures remain inferred visual geometry.

Cache byte limits describe cached Python pages at operation boundaries. One tile
codec buffer, one source observation plus immutable copies, image rectification
buffers, bounded native geometry and SQLite caches are additional bounded
working memory. They are not a claim that total process RSS equals the LRU
budget. SQLite reserves half its disk allowance for rollback journaling. A
single tile/frame or requested export that cannot fit its allowance fails
explicitly; history is not silently discarded.

TSDF processes all valid depth observations up to the declared 6 m optical-depth
limit, one uniform tile at a time. A disk index selects observing frames for each
tile. Brown distortion is rectified with unchanged K; nearest depth resampling
preserves invalid holes. Navigation instead casts the original distortion-aware
measured pixels, with their actual camera capture pose. Mesh previews are capped;
`iter_tsdf_meshes()` streams every nonempty persisted TSDF tile.

## Published bundle and compatibility

`bundle_manifest.json` schema 2 names:

- `commissioning/`: original version-1 NPZ recording (legacy-compatible).
- `prepared_frames/`: disk-backed corrected frame chunks.
- `surface_tiles/`: all measured LiDAR surface voxel centroids.
- `occupancy_tiles/`: full free/occupied/permeable evidence authority.
- `tsdf_tiles/`: all nonempty calibrated TSDF mesh tiles and observation index.
- `measured_map.npz`, PLY files: bounded compatibility/display previews.
- `planning_grid.npz`: a conservative, bounded local dense window.

The publication is staged and renamed atomically after handles close. A preceding
bundle is retained. `map_revision` includes authority hashes beyond the preview:
full occupancy/surface databases, raw/prepared manifests containing every chunk
SHA-256, and the TSDF index containing every mesh SHA-256. The manifest also hashes
preview geometry and its transforms. `read_commissioning_provenance()` validates
all these files and returns declaration-only source/calibration provenance.
Synthetic origin overrides an independently marked measured calibration file.
Unknown legacy bundles remain usable for simulation/replay but are not eligible
as a verified live reference. A checksum is integrity evidence, not a signature
or proof of physical acquisition.

`load_commissioned_assets(path)` preserves `(twin, static_map, planning_grid)` and
loads legacy NPZ bundles as before. Close `twin` and paged maps when finished.

## Map interfaces

`slam_and_twin.tiled_map.DiskBackedVoxelMap` (alias `TiledVoxelMap`) supports:

- `len(map.cells)`, `query(points, return_known=True)`, `is_known(points)`.
- `iter_observed(bounds)` and `iter_occupied_points(bounds=...)` without a global
  materialization or forced write-back. Bounds order is xmin,xmax,ymin,ymax,zmin,zmax.
- `occupied_points(..., max_points=262144, bounds=None)` with an explicit cap.
- `observed_bounds`, `flush()`, `close()`, `load(path, readonly=False)` for resume.
- `save('export.npz')`: a bounded-memory legacy NPZ export.
- Native/reference ray backend and traversal allocation budgets.

Coordinates are int64 tuples, resolved exactly within signed tiles. Hashes are
not voxel identity. Unknown cells and expired evidence stay unknown. Expiry
queries take effect immediately; physical deletion inspects capped tile sets at
configurable intervals without flushing the entire dirty cache.

`twin.iter_measured_points(bounds)` streams full measured centroids;
`twin.iter_tsdf_meshes(bounds)` streams full mesh geometry. `prepared_frames` is a
restartable `FrameReplaySequence` for disk builds and a bounded tuple for small
direct builds. Indexed access does not retain an RGB-D frame cache.

## Automatic extents and requested planning regions

Initial commissioning derives bounds from observed voxel volumes, not the old
lab rectangle. `planning.max_local_grid_cells` (default 131072) limits the local
window. `planning.window_center` optionally positions it; the full authority stays
paged outside that window. `bounds_mode: configured` explicitly requests an
operator-selected initial window.

`slam_and_twin.planning_region.load_observed_planning_region(map,start,goal,planning)`
pages observations around a request. `planning.region_margin_m` is a scalar or
XYZ vector (default 2 m). If endpoints plus margin cannot fit
`max_local_grid_cells`, it raises `MapBudgetExceeded` as a resource limit. A
fitting region retains unknown/outside-observed cells as blocked; only the
planner decides whether a physical route exists.

The browser asset exporter consumes all persisted TSDF meshes lazily, splits
individual display tiles to a triangle budget, writes per-tile LODs and uses
unique revision/source tile IDs. It never merges the full world into one mesh.
Its manifest bounds include remote tiles beyond the preview. Empty TSDF archives
fall back to the labelled legacy/bounded preview.

## Complete background queries and local registration

The point preview is a uniform reservoir, not guaranteed coverage of every
region. Runtime consumers must use `twin.distances(points, radius=2.0)` for
schema-2 bundles. It searches full measured-centroid tiles with one bounded
KD-tree at a time, without another downsample. Results are exact within the
radius; missing neighbors return positive infinity. The 2 m default preserves
the neural context's existing clipping. Foreground thresholds must not exceed
the search radius; use a suitable radius or `None` for a slower globally exact
streamed search.

`twin.load_local_points(world_pose_or_center, radius=8.0, max_points=100000)`
loads exact measured centroids in the requested sphere, in floor-aligned world
coordinates. A point-cap overflow raises `MapBudgetExceeded`; it never silently
subsamples registration geometry. Transform these world points explicitly if
an ICP instance uses the pre-floor commissioning world frame.

The recorder refuses complete recordings, orphan chunk files, temporary chunk
files and active/interrupted writer leases. It does not overwrite recoverable
partial recordings when a manifest is missing. Use a new output directory and
inspect/recover the preceding data deliberately. Successful close removes the
exclusive lease; incomplete chunk/manifest files remain protected.

Requested planning regions include at least one cell of endpoint padding, even
when `region_margin_m` is zero. Display manifests include revision/source tile
identities and cumulative bounds. Integrity verification checks every recording
chunk and mesh tile in addition to the authority databases.
