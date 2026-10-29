# Optional exact native voxel rays

Build using the Python environment that runs the application:

```sh
python native/voxel_rays/build.py
python -m pytest tests/test_native_voxel_rays.py -q
python -m native.voxel_rays.benchmark
```

Windows uses the locally installed MSVC x64 toolchain. Ubuntu uses GNU C or
Clang. The kernel has no vendor SDK, Python extension headers or allocation
dependency. Windows requires no Windows SDK or runtime-library installation.
The build records compiler, source/binary SHA256 and strict floating-point
policy. Platform-specific `lib/win32-amd64` and `lib/linux-x86_64` directories
coexist. The loader verifies the binary hash and ABI version before use.

`traverse_rays(origins, targets, size, backend='auto', max_cells=5_000_000)`
returns read-only `keys (M,3)`, `owners (M)`, `offsets (N+1)`, and `backend`.
Each input ray has an ordered sequence at `keys[offsets[i]:offsets[i+1]]`.
Both origin and endpoint cells are included; owners index the input rays.
`origins` accepts one common XYZ or an aligned origin for each ray. The
simultaneous-axis rule is exactly the reference's absolute `1e-12` threshold.
There is no temporal binning, ray sampling, range truncation or fast-math.

`traverse_beams(...)` returns sorted unique keys, matching the occupancy
module's current reference signature. An allocation-free in-place native heap
sort compacts a private key copy in signed lexicographic order, preserving the
ordered sequences and owners. Older compatible binaries use NumPy unique.
Multi-return endpoint selection remains
the caller's responsibility. Pass only the chosen final returns with their
acquisition origins. All observed hit keys, including intermediate echoes,
must be excluded from free evidence. The kernel never continues past a supplied
endpoint and does not assign semantic/free/occupied evidence itself.

`backend_status()` exposes availability and the build manifest or the exact
load failure. `auto` returns an explicitly labelled Python reference result if
the optional binary is unavailable. `native` raises `NativeUnavailable`.
Invalid inputs and capacity exhaustion raise errors; they never return partial
evidence. Capacity is an explicit memory bound, not hidden sampling. The native
kernel counts first, then fills preallocated arrays while releasing the Python
GIL through `ctypes.CDLL`. Private input snapshots keep those passes consistent;
fill capacity is the actual allocated row count. Coordinates beyond the exactly representable double
integer range are rejected.

The wrapper is standalone. It does not edit the occupancy module or silently
enable a new runtime backend. Integration should record backend status in
runtime diagnostics. `benchmark.py` temporarily substitutes traversal only
inside its own process and verifies exact final occupancy state for real
recorded LiDAR, depth and S2 observations before reporting component timing.

On this Windows laptop, `artifacts/native_voxel_rays.json` records five
measurements using three actual multimodal recording frames. Selected LiDAR
traversal median decreased from 136.5 ms to 18.47 ms. The complete three-frame
occupancy batch decreased from 1743.3 ms to 1068.0 ms. Its final 49,362 cells
matched exactly, including evidence and timestamps. These are component
measurements, not total application FPS; the tail timings show CPU variability.
