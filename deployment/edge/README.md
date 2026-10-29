> Historical NVIDIA deployment recipe. The active plan uses Raspberry Pi capture
> and Windows GPU processing; see [current setup](../../docs/RASPBERRY_PI_WINDOWS.md).

# Jetson edge runtime

Status: the Windows/Ubuntu x86 software is tested separately. The physical
Orin Nano ARM64 runtime remains **unverified** until the device is available.
No GPU image digest is invented or presented as tested.

1. On the Jetson, run `python tools/probe_jetson.py --output runtime-probe.json`.
   Record the actual module/carrier, installed JetPack/L4T, Python, CUDA and
   PyTorch combination. A failed GPU probe must not silently select CPU.
2. Select the official NVIDIA image matching that installation, Python3.12 and
   Orin SM87; resolve its immutable digest. The NVIDIA compatibility matrix and
   actual runtime checks are both required. Do not automatically flash or
   upgrade the board to satisfy an assumed version.
3. Prepare a local Open3D0.19.0 ARM64/Python3.12 wheel and completed build
   manifest as described below. An ARM64 wheel is not supplied by this delivery;
   no compatible PyPI wheel is assumed. The container build requires the local
   artifact and fails if it is missing or does not match its hash/ABI metadata.
4. Build the Dockerfile from the project root with the reviewed
   `JETSON_BASE_IMAGE`, `OPEN3D_WHEEL`, and `OPEN3D_WHEEL_MANIFEST` arguments.
   The wheel is validated and installed with its SHA256 before capturing vendor
   constraints. The base's package versions are constraints,
   so an incompatible dependency change fails instead of replacing vendor
   CUDA/PyTorch libraries. Keep the resolved image digest and resulting package
   lock/environment report with the device commissioning record.
5. Build the Unitree native binding from the exact official SDK revision in
   `native/README.md`, using the target ARM64 toolchain and `WITH_SLAMTEC=OFF`.
   Mount/install that module on the Python path in the runtime. The exact native
   ray kernel is built for the target automatically; host x86 DLL/SO files are
   not evidence of an ARM64 build.
6. Configure the measured physical twin and camera calibration. A simulated or
   unknown-provenance twin is rejected in live mode. Raw observations retain
   their calibration and timestamps.
7. Provide the real USB3 camera/serial device permissions and LiDAR interface
   configuration for this host. No generic privileged container, subnet scan,
   firewall change, world-writable USB rule or device flashing is performed.
8. Start with the edge configuration and a loopback service. For another host,
   configure the private connection, TLS/trusted gateway origin and scoped
   viewer/operator credentials before network binding.

The profile caps acquisition, IMU history, local planning, map cache and disk
storage. Raw-camera resolution and feature-network resolution are distinct and
reported. Training, simulation rendering and dense reconstruction stay on the
laptop/server. Orin Nano has no NVENC; camera preview is optional, not a
continuous full-resolution encoding requirement.

Required field checks still include device identity, supported profiles, USB
stability/power, synchronization residuals, measured calibration, map coverage,
real learned accuracy, thermal behavior and 30-minute performance. Importing an
SDK or passing GPU matrix arithmetic does not pass those checks.

## Required local Open3D wheel

Use the official Open3D0.19.0 source commit
`1e7b17438687a0b0c1e5a7187321ac7044afe275`. Its
[Docker build script](https://github.com/isl-org/Open3D/blob/1e7b17438687a0b0c1e5a7187321ac7044afe275/docker/docker_build.sh)
provides `openblas-arm64-py312` and copies the resulting wheel to the caller's
working directory. The pinned
[OpenBLAS Dockerfile](https://github.com/isl-org/Open3D/blob/1e7b17438687a0b0c1e5a7187321ac7044afe275/docker/Dockerfile.openblas)
builds that release. The
[CMake options](https://github.com/isl-org/Open3D/blob/1e7b17438687a0b0c1e5a7187321ac7044afe275/CMakeLists.txt)
default Open3D CUDA, PyTorch and TensorFlow custom operations to disabled; this
platform uses Open3D on CPU and NVIDIA PyTorch separately for inference.

Run this on an ARM64 machine with Docker already configured, or select an
existing remote ARM64 Docker context. The helper does not install Docker, QEMU,
emulation handlers, OS packages or firmware. Keep the external upstream checkout
outside the deliverable project, and use a new empty wheel output directory:

```sh
git clone https://github.com/isl-org/Open3D.git /work/Open3D-v0.19.0
git -C /work/Open3D-v0.19.0 checkout --detach 1e7b17438687a0b0c1e5a7187321ac7044afe275
python deployment/edge/open3d_wheel.py build \
  --source /work/Open3D-v0.19.0 --output deployment/edge/wheels
```

`--docker-context <existing-name>` selects an already configured builder without
changing Docker's saved default context. An x86 Docker engine is rejected unless
`--configured-arm64-runner` explicitly declares that the caller already arranged
ARM64 execution support. That option does not create or configure support, and
the build must still produce AArch64 ELF libraries and an ARM64 Docker image.

The helper validates the clean source commit/origin, records recipe hashes,
captures the build log, actual GCC/CMake/Python toolchain, Docker image identity,
wheel filename/tags/SHA256/size, and a CPU-only Open3D import in the builder.
`open3d-build.json` is usable only after all checks pass; failed build evidence
is retained with `status: failed`. The official recipe downloads OS/Conda
dependencies, so the pinned source alone is **not** a fully hash-locked,
bit-reproducible build toolchain. Retain the resulting builder image and manifest
with the wheel. The manifest binds bytes and records a builder declaration; it
is not a signed attestation of source authenticity.

Inspect the actual wheel filename, then pass it and its manifest explicitly:

```sh
python deployment/edge/open3d_wheel.py validate \
  --wheel deployment/edge/wheels/<actual-wheel-filename>.whl \
  --manifest deployment/edge/wheels/open3d-build.json
docker build -f deployment/edge/Dockerfile \
  --build-arg JETSON_BASE_IMAGE='<reviewed-nvcr.io-image>@sha256:<actual-digest>' \
  --build-arg OPEN3D_WHEEL='deployment/edge/wheels/<actual-wheel-filename>.whl' \
  --build-arg OPEN3D_WHEEL_MANIFEST='deployment/edge/wheels/open3d-build.json' \
  -t aethertwin-edge:commissioning .
```

Installation checks CPython3.12, Linux/AArch64, glibc compatibility, distribution
version, ABI/platform tags, and every bundled ELF shared-library architecture.
It then installs only that file with `--no-index --no-deps --require-hashes`.
Runtime dependencies are resolved afterward against the captured vendor
constraints, protecting the NVIDIA PyTorch build. No x86 wheel is relabelled,
and no CPU Torch package is substituted. Full ARM64 container build, wheel
import on the selected Jetson base, sensor acquisition and thermal/performance
validation remain untested in this x86 environment.
