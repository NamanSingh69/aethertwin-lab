# Rendering runtime provenance

The original Mesa files are from the unmodified x64 MSVC package in
[mesa-dist-win 26.2.4](https://github.com/pal1000/mesa-dist-win/releases/tag/26.2.4).
The upstream `opengl32.dll` is renamed `opengl32_mesa.dll`; its bytes are unchanged.
`libgallium_wgl.dll` is unchanged. The local `opengl32.dll` adapter is built from
the source in `native/wgl_compat`.

The archive, dependency, generated-adapter, and source SHA256 values are recorded
in `manifest.json`. No system-wide deployment tool from the distribution is run.

Upstream notices are preserved in `licenses`:

- `Mesa-license.rst`: Mesa 26.2.4's license notice, fetched from its tagged
  [source](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.4/docs/license.rst).
- `LLVM-LICENSE.txt`: LLVM license and LLVM exceptions, from the
  [LLVM project](https://github.com/llvm/llvm-project/blob/main/llvm/LICENSE.TXT).
- `mesa-dist-win-readme.md`: the distributor's tagged
  [release documentation](https://github.com/pal1000/mesa-dist-win/blob/26.2.4/readme.md).

See the individual notices for upstream copyright and licensing terms.
