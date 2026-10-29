# Windows software graphics compatibility

This source-built adapter enables Open3D 0.19's PBR renderer on the tested
Windows laptop using **Software graphics (Mesa LLVMpipe)**. The prepared
three-DLL runtime is in `assets/rendering/mesa`; it occupies about 62 MB.
It applies only to the process that explicitly selects it. It does not install
a graphics driver, change the registry, edit system files, or modify Open3D's
binary code.

## Why the adapter is needed

Open3D 0.19 pins Windows
[Filament 1.9.9](https://github.com/isl-org/Open3D/blob/v0.19.0/3rdparty/filament/filament_download.cmake).
Its [WGL context implementation](https://github.com/google/filament/blob/v1.9.9/filament/backend/src/opengl/PlatformWGL.cpp)
passes the profile-mask enum `0x9126` as the value of the context-flags attribute
`0x2094`. Mesa rejects that malformed pair. The adapter changes that exact value
to zero. All other context attributes, including valid flags, are preserved.

The same Filament implementation independently chooses a 32-bit color,
24-bit depth framebuffer, while
[Open3D's GLFW window setup](https://github.com/isl-org/Open3D/blob/v0.19.0/cpp/open3d/visualization/gui/GLFWWindowSystem.cpp)
can choose a different pixel format. WGL cannot reuse a rendering context with
that incompatible window surface. The adapter restricts GLFW's window-capable
format enumeration to the native format selected by Filament's descriptor.
Other queried attributes and matching-format results are preserved.

The adapter keeps all 379 original Mesa loader export names and ordinals.
378 exports are genuine PE forwarders to the unchanged `opengl32_mesa.dll`.
Only `wglGetProcAddress` is wrapped; it supplies the two compatibility routines.
Five additional `LabWgl*` exports expose diagnostic counters and testable pure
policy functions. No placeholder OpenGL functions are substituted.

## Sources and checksums

- [Mesa LLVMpipe deployment documentation](https://docs.mesa3d.org/drivers/llvmpipe.html)
  documents application-local Windows DLL deployment.
- [mesa-dist-win 26.2.4 release](https://github.com/pal1000/mesa-dist-win/releases/tag/26.2.4)
  supplies the original x64 MSVC binaries used here.
- [Microsoft DLL search-order documentation](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order)
  describes process-local loading by absolute path.

The upstream `mesa3d-26.2.4-release-msvc.7z` archive SHA256 is:

```text
351fc8c8b695878ffb3eaa044b3ead08672a48b1a045e3c3e3975811df0f6695
```

`assets/rendering/mesa/manifest.json` records individual runtime hashes, adapter
source hashes, compiler identity, and export coverage. The Python loader checks
all runtime hashes before loading any native code, including hard-pinned hashes
for the two original Mesa files. License/source notices are preserved in the
runtime's `licenses` directory.

## Rebuild

Use Python 3.12 and an installed MSVC x64 compiler. This small no-CRT adapter
needs no additional Windows SDK; `win32_minimal.h` declares only the public Win32
ABI functions it uses. It uses ordinary MSVC-generated code and PE forwarders.

1. Download the exact release archive linked above and verify its SHA256.
2. Extract the release's `x64` directory into a local working directory.
3. From the project root, run:

```powershell
python native/wgl_compat/build.py --mesa-dir C:/path/to/mesa/x64 --build-dir work/wgl_compat_build
```

The builder locates MSVC through `vswhere`, validates original dependency hashes,
builds the adapter, verifies export names/ordinals/forwarders, and writes the
prepared runtime and manifest. `--cl-dir` can explicitly select the x64 MSVC
tool directory; `--output` can select another runtime directory.

## Verification

```powershell
python -m pytest tests/test_render_backend.py tests/test_telemetry.py -q
python tools/render_component.py --backend mesa --output artifacts/render_verified_backend.png
```

The generated-sphere component test produced 4,118 foreground pixels, 3,323
distinct colors, and pixel standard deviation 22.77, and exited normally after
destroying the render context. It reported one malformed-attribute correction
and framebuffer compatibility filtering. The test reads a newly generated
scene through `render_to_image`; it does not capture desktop pixels or operate
other applications. It does not establish interactive keyboard/mouse acceptance.

The backend must be configured before importing Open3D. Importing NumPy,
PyTorch, and YAML first was explicitly tested with the ordinary redirected
virtual-environment Python executable; a private interpreter is not required.
