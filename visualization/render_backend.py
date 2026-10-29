"""Explicit rendering backend selection confined to the current process.

Mesa's Windows deployment is documented at
https://docs.mesa3d.org/drivers/llvmpipe.html . This loader does not install a
driver, write registry values, or change the graphics configuration of another
application. Select it before importing Open3D or other OpenGL users.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import os
import sys

_handles=[]
_configured=None
_pinned_dependencies={
    "opengl32_mesa.dll":"75b295fc80df0eaa8f1264c28663f6932c8b72d36513eb092556608858b7f5e0",
    "libgallium_wgl.dll":"53e49adf87590d5b0cab8553b1e98084812040cc1fca8c487695b17b7af3702e",
}


def validate_mesa_runtime(directory):
    """Verify the prepared runtime before any native library is loaded."""
    directory=Path(directory).expanduser().resolve()
    manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version")!=1 or manifest.get("backend")!="mesa_llvmpipe_filament19":
        raise ValueError("Unsupported Mesa compatibility runtime manifest")
    for filename in ("opengl32.dll","opengl32_mesa.dll","libgallium_wgl.dll"):
        path=directory/filename
        if not path.is_file(): raise FileNotFoundError(path)
        expected=manifest.get("files",{}).get(filename)
        if filename in _pinned_dependencies and expected!=_pinned_dependencies[filename]:
            raise ValueError(f"Unrecognized Mesa dependency version: {filename}")
        with path.open("rb") as stream: actual=hashlib.file_digest(stream,"sha256").hexdigest()
        if actual!=expected: raise ValueError(f"Rendering library checksum mismatch: {filename}")
    return manifest


def configure_rendering(backend="native",mesa_dir=None):
    """Return displayed backend metadata; Mesa is always an explicit opt-in.

    ``mesa_dir`` must contain the validated, source-built WGL compatibility
    adapter and its pinned Mesa dependencies. DLLs are preloaded by absolute
    path and remain referenced for this process's lifetime.
    """
    global _configured
    if backend not in ("native","mesa"):
        raise ValueError("Rendering backend must be native or mesa")
    if _configured is not None:
        if _configured["requested"]!=backend:
            raise RuntimeError("Rendering backend cannot be changed after initialization")
        if backend=="mesa" and Path(mesa_dir).resolve()!=Path(_configured["mesa_dir"]):
            raise RuntimeError("Rendering runtime directory cannot be changed after initialization")
        return dict(_configured)
    if backend=="native":
        _configured={"requested":"native","name":"Native Open3D rendering"}
        return dict(_configured)
    if sys.platform!="win32":
        raise RuntimeError("The portable Mesa DLL backend is for Windows only")
    if mesa_dir is None:
        raise ValueError("Explicit Mesa rendering requires --mesa-dir")
    directory=Path(mesa_dir).expanduser().resolve()
    if ctypes.sizeof(ctypes.c_void_p)!=8:
        raise RuntimeError("The Mesa compatibility runtime requires x64 Python")
    if version("open3d")!="0.19.0":
        raise RuntimeError("This Mesa compatibility backend is verified for Open3D 0.19.0; install the pinned requirements")
    manifest=validate_mesa_runtime(directory)
    if "open3d" in sys.modules:
        raise RuntimeError("Select the rendering backend before importing Open3D")
    kernel32=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel32.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype=ctypes.c_void_p
    if kernel32.GetModuleHandleW("opengl32.dll"):
        raise RuntimeError("OpenGL is already loaded; select Mesa before importing Open3D")
    os.environ["GALLIUM_DRIVER"]="llvmpipe"
    os.environ["LIBGL_ALWAYS_SOFTWARE"]="1"
    _handles.append(os.add_dll_directory(str(directory)))
    _handles.append(ctypes.WinDLL(str(directory/"libgallium_wgl.dll")))
    _handles.append(ctypes.WinDLL(str(directory/"opengl32.dll")))
    _configured={"requested":"mesa","name":"Software graphics (Mesa LLVMpipe)",
                 "mesa_version":manifest["mesa_version"],"mesa_dir":str(directory)}
    return dict(_configured)
