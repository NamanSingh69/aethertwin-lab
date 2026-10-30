# Photoreal lab view

The studio can show the lab two ways besides its normal data view:

1. **Photoreal quality** (Render quality → Photoreal). Image-based lighting from a
   procedural room, soft shadows, ambient occlusion, physical materials and a
   concrete floor, applied to the measured twin. It changes how surfaces look,
   not their geometry. Presentation only, like the cinematic tier.
2. **Photo capture layer.** A textured 3D model of the real lab, made from photos
   or video, placed in the twin's coordinate frame. It shows what the lab looks
   like. Mapping, planning, risk and tracking never read it.

Both are labelled in the studio as visual only. A capture is **not** a
measurement: its geometry comes from photogrammetry, and how well it lines up
with the sensor map is reported separately (see Alignment).

## 1. Capture the video

- Phone camera is fine; 4K if possible, focus and exposure locked.
- Walk slowly and smoothly; no fast turns. Blurred frames ruin reconstruction.
- Two loops of the room: one at chest height, one low or high. Close passes
  around important equipment. Every surface should appear in many frames from
  different angles.
- Avoid people walking through, moving screens, mirrors and open doors. Turn on
  all lights. Large blank walls reconstruct poorly; leave some objects in view.
- Put 2-3 objects of known size in view, or tape down printed markers, and
  measure the distance between them with a tape. These give scale and an
  alignment check.
- 2-5 minutes is enough for one lab.

Record the phone model, date and lens in the provenance fields below.

## 2. Reconstruct a textured mesh

Any photogrammetry pipeline that exports **glTF binary (.glb)** works, for
example: extract frames (`ffmpeg -i lab.mp4 -vf fps=2 frames/%05d.jpg`), solve
camera poses (COLMAP or GLOMAP), densify and texture (OpenMVS, Meshroom or
RealityScan), then export `.glb` with textures. Decimate to roughly 1-3 million
triangles and 4K textures so it loads in a browser. Meshopt compression
(`gltfpack -cc`) is supported; Draco is not.

Gaussian splats give the most realistic result from video, but the studio does
not render them yet. The manifest reserves the `kind` field for them; adding a
splat renderer is planned once a real capture exists to test against.

## 3. Align it to the twin

The capture comes out in arbitrary units and orientation. The transform in the
manifest maps it into the twin's `world_z_up` frame in metres (row-major 4x4,
rotation with one uniform scale). Record how it was obtained:

| `alignment.method` | Meaning | `rmse_m` |
| --- | --- | --- |
| `none` | Not aligned; shown where the reconstruction put it | not required |
| `manual` | Moved by eye in a 3D tool | not required; the studio says "aligned by eye" |
| `measured_points` | Fitted to tape-measured or surveyed points | required, plus `checked_points` |
| `lidar_registration` | Registered to the LiDAR map (for example ICP) | required, residual over the overlap |

Report the residual of points that were **not** used for the fit when you can.

## 4. Package and serve

```bash
python tools/package_lab_appearance.py lab.glb captures/lab-2026-11 --transform-json transform.json --method measured_points --rmse-m 0.04 --checked-points 6 --device "phone model" --tool "COLMAP + OpenMVS" --captured 2026-11-02
```

```bash
python -m command_center --mode sim --appearance captures/lab-2026-11
```

The service checks the manifest and the model's SHA-256 at startup and refuses to
start if they do not match. In the studio, tick **Photo capture** in the layer
list; the caption under the list shows the alignment summary. The capture renders
unlit, because its textures already contain the room's lighting. It works in
every quality tier.

The capture is served only to signed-in sessions, like the map, and only the two
declared files are reachable. The gateway does not forward captures yet.

## Honesty rules

- A capture never changes `hardware_status` or any accuracy claim.
- Keep the provenance (device, tool, date) with the capture directory.
- Do not describe a capture as measured geometry unless an alignment with a
  reported residual backs it, and then quote the residual.
