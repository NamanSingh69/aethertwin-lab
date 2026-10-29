# Optional physical-sensor bindings

Simulation and replay do not import or require this module. The bindings use the
official Unitree SDK2 and Slamtec SDK interfaces. No sensor is emulated in live mode.
Both adapters compiled and imported successfully on Ubuntu 24.04 / Python 3.12.3
with GNU C++ 13.2.0, CMake 4.4.4 and pybind11 3.1.0. Physical devices have **not**
been validated on this workstation. See `verification.json` for the build evidence.

Use Ubuntu 24.04, a C++17 toolchain, Python development headers, CMake, and `pybind11`. Obtain the vendor sources
separately and retain their licenses and exact Git revisions in your lab build log:

- https://github.com/unitreerobotics/unilidar_sdk2
- https://github.com/Slamtec/rplidar_sdk

API review references for this implementation: Unitree revision
`0e3c51f512e6b8ff60b8c32f160b412cb48445c2`; Slamtec revision
`99478e5fb90de3b4a6db0080acacd373f8b36869`; Orbbec Python wheel `2.1.2`.
The official Orbbec `pyorbbecsdk2==2.1.2` Linux Python 3.12 wheel also imported
successfully, and every profile, pipeline and color-format API used by the driver
was checked on the real native module (see `orbbec_verification.json`). This does
not establish device compatibility or successful acquisition.

Build with the same Python environment that runs the platform:

```sh
python -m pip install cmake pybind11
cmake -S native -B build/native -DPython_EXECUTABLE=$(which python) \
  -DUNITREE_SDK=/path/to/unilidar_sdk2/unitree_lidar_sdk
cmake --build build/native --parallel
cmake --install build/native --prefix /path/to/environment/lib/python3.12/site-packages
```

For S2, build the official SDK as a position-independent static library and pass
`-DWITH_SLAMTEC=ON -DSLAMTEC_SDK=/path/to/rplidar_sdk/sdk
-DSLAMTEC_LIBRARY=/absolute/path/to/libsl_lidar_sdk.a`. S2 defaults to 1,000,000 baud.
Use `-DWITH_UNITREE=OFF` for a Windows S2-only build, with the vendor Windows library.
The Python S2 adapter accepts `transport="udp", udp_port=8089` to select the
official SDK UDP channel for a compatible interface or gateway; `port` then names
the target IP. Ordinary S2 acquisition defaults to serial. This channel option
does not imply that every S2 hardware variant has native Ethernet support.

The Unitree SDK factory's public base class lacks a virtual destructor and a
destroy function. The binding closes the transport and stops scanning but keeps
that vendor allocation for process lifetime, avoiding undefined deletion. Create
one L2 reader per process. This is a known SDK ownership limitation, not per-frame
growth. Both cloud and IMU use the device clock after requesting SDK clock sync;
the Python adapter maps its epoch to monotonic time and retains raw stamps.

The L2 API exposes xyz, intensity, acquisition offset and ring. Ring is not an echo
index. The Python frame sets echo metadata validity false. Camera extrinsics must
be measured and supplied; identity fallback is rejected. The Gemini 335L adapter
uses `pyorbbecsdk2` (import name `pyorbbecsdk`) and enumerates depth/color profiles.
No firmware flashing, persistent network settings or robot motion is performed.

The L2 adapter declares the SDK factory IMU-to-LiDAR transform: parallel axes,
with IMU origin `[-0.007698, -0.014655, 0.00667]` metres in the LiDAR frame.
A measured installation override can replace it. This direction follows the
SDK's explicit origin coordinates, avoiding the ambiguous prose transform label.
The deskewer corrects angular-acceleration and centripetal lever-arm terms.

Gemini color negotiation accepts RGB, BGR, MJPG or YUYV at the requested dimensions
and frame rate; compressed/packed data is decoded into RGB. Depth remains Y16.
Source depth defaults to 1280×800 Y16 at 30 fps. Color dimensions and frame rate
are negotiated independently: color defaults to the measured manifest's
`image_size:[width,height]`, with the depth frame rate. Explicit color width,
height and frame-rate options are also supported. Each source stream must exist
exactly as requested; profiles are never silently substituted.

For every candidate pair the adapter calls the official
`Pipeline.get_d2c_depth_profile_list(color_profile, align_mode)`. Automatic mode
prefers hardware alignment and considers software only when its exact source
depth/color pair is SDK-approved. Hardware alignment requests SDK depth-output
scaling; software uses `AlignFilter` targeting the selected color profile and
matching its resolution. Explicit hardware/software modes do not switch modes.
The source depth can therefore remain 1280×800 while color and aligned output
are 1920×1080, when that pair is reported as supported. No manual image resizing
or camera-matrix rescaling is used.

The first actual aligned frameset is validated before initialization succeeds.
Both delivered images and their stream-profile intrinsic dimensions must match
the measured `image_size`. Each output profile's focal lengths, principal point
and distortion must agree with the measured calibration (default tolerances:
1 pixel and 1e-4). The same checks run on every frameset; a mismatch stops capture
with a clear error. Measured K, optical-frame extrinsics and calibration identity
are retained. Source/available/aligned profiles, actual device/system/global
timestamps with availability flags, and calibration comparisons are propagated
into frame diagnostics. Missing required timestamps, unsupported D2C APIs or
pairs, missing modalities and acquisition failures are explicit errors with
pipeline cleanup. Physical mixed-profile acquisition remains unverified.

The API implementation was checked against the pinned
[Orbbec Python 2.1.2 aligned-stream example](https://github.com/orbbec/pyorbbecsdk/blob/v2.1.2/examples/beginner/03_color_and_depth_aligned.py)
and [SDK 2.9.3 pipeline source](https://github.com/orbbec/OrbbecSDK_v2/blob/v2.9.3/src/pipeline/Pipeline.cpp).
The source shows that pipeline software-alignment configuration is not the
software transform itself, so the adapter uses the documented `AlignFilter`.
Actual delivered frame profiles are checked because pipeline camera parameters
describe the enabled source profiles. Native API presence and simulated SDK
regressions are recorded in `orbbec_verification.json`; they do not establish
successful physical acquisition.
Raw device microsecond timestamps, color-depth timestamp gap, software clock-fit
residual and cross-sensor pairing error are retained. Software clock alignment
does not assert hardware exposure synchronization.
The aligned RGB-D frameset currently uses its reported color capture time for
motion compensation. The color/depth timestamp gap stays visible; independent
depth-exposure motion accuracy has not been established with physical hardware.

Raw LiDAR endpoints and optional per-return `lidar_origins` use the LiDAR frame at
each acquisition time. Deskew transforms both to the sweep-end LiDAR frame and
marks `diagnostics.coordinate_reference="lidar_sweep_end"`. Such snapshots can be
recorded for analysis, but the runtime rejects deskewing them twice. Normal
runtime recording stores raw acquisitions before deskew. Measured camera
calibration stays unchanged; `camera_pose_in_lidar` separately stores the camera
capture pose in the sweep-end frame. RGB-D and S2 clouds and their ray origins
use the corresponding observed IMU capture poses, without extrapolation. S2
currently has one scan capture timestamp rather than per-beam acquisition times;
that timing limitation is explicit in its diagnostics and needs physical tests.
