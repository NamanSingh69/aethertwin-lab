"""Build the process-local Mesa / Filament 1.9.9 adapter with MSVC x64.

No Windows SDK, driver installation, registry change, or global PATH edit is
required. The original Mesa binaries must match the pinned release checksums.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
MESA_VERSION="26.2.4"
MESA_URL="https://github.com/pal1000/mesa-dist-win/releases/download/26.2.4/mesa3d-26.2.4-release-msvc.7z"
ARCHIVE_SHA256="351fc8c8b695878ffb3eaa044b3ead08672a48b1a045e3c3e3975811df0f6695"
DEPENDENCIES={
    "opengl32.dll":"75b295fc80df0eaa8f1264c28663f6932c8b72d36513eb092556608858b7f5e0",
    "libgallium_wgl.dll":"53e49adf87590d5b0cab8553b1e98084812040cc1fca8c487695b17b7af3702e",
}
CUSTOM_EXPORTS=("LabWglCompatibilityFixCount","LabWglNormalizeAttributes",
                "LabWglFramebufferFilterCount","LabWglApplyFramebufferPolicy","LabWglPixelFormatDescriptorSize")


def sha256(path):
    with Path(path).open("rb") as stream: return hashlib.file_digest(stream,"sha256").hexdigest()


def read_exports(path):
    """Read PE named exports, ordinals, and forwarders without executing DLLs."""
    data=Path(path).read_bytes()
    u16=lambda p:struct.unpack_from("<H",data,p)[0]
    u32=lambda p:struct.unpack_from("<I",data,p)[0]
    pe=u32(0x3c); count=u16(pe+6); opt=pe+24; opt_size=u16(pe+20)
    if data[pe:pe+4]!=b"PE\0\0" or u16(pe+4)!=0x8664 or u16(opt)!=0x20b:
        raise ValueError("Expected an x64 PE32+ DLL")
    sections=[]
    for i in range(count):
        p=opt+opt_size+40*i
        sections.append((u32(p+12),max(u32(p+8),u32(p+16)),u32(p+20)))
    def raw(rva):
        for base,size,offset in sections:
            if base<=rva<base+size: return offset+rva-base
        raise ValueError(f"Invalid PE RVA: {rva}")
    def string(rva):
        start=raw(rva); return data[start:data.index(0,start)].decode("ascii")
    export_rva=u32(opt+112); export_size=u32(opt+116); export=raw(export_rva)
    base=u32(export+16); n=u32(export+24)
    names=raw(u32(export+32)); ordinals=raw(u32(export+36)); functions=raw(u32(export+28))
    result={}
    for i in range(n):
        index=u16(ordinals+2*i); rva=u32(functions+4*index)
        result[string(u32(names+4*i))]={"ordinal":base+index,
            "forwarder":string(rva) if export_rva<=rva<export_rva+export_size else None}
    return result


def find_msvc():
    root=Path(os.environ.get("ProgramFiles(x86)",r"C:\Program Files (x86)"))
    vswhere=root/"Microsoft Visual Studio/Installer/vswhere.exe"
    if not vswhere.is_file(): raise FileNotFoundError("Install MSVC x64 Build Tools or pass --cl-dir")
    installation=Path(subprocess.check_output([str(vswhere),"-latest","-products","*","-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64","-property","installationPath"],text=True).strip())
    folders=list((installation/"VC/Tools/MSVC").glob("*/bin/Hostx64/x64"))
    if not folders: raise FileNotFoundError("No MSVC x64 toolchain found")
    return max(folders,key=lambda p:tuple(int(x) for x in p.parents[2].name.split(".")))


def build(mesa_dir,output,build_dir,cl_dir=None):
    mesa_dir=Path(mesa_dir).resolve(); output=Path(output).resolve(); build_dir=Path(build_dir).resolve()
    for filename,expected in DEPENDENCIES.items():
        source=mesa_dir/filename
        if not source.is_file() or sha256(source)!=expected:
            raise ValueError(f"Pinned Mesa dependency checksum mismatch: {source}")
    compiler=Path(cl_dir).resolve() if cl_dir else find_msvc()
    for name in ("cl.exe","lib.exe","link.exe"):
        if not (compiler/name).is_file(): raise FileNotFoundError(compiler/name)
    build_dir.mkdir(parents=True,exist_ok=True); output.mkdir(parents=True,exist_ok=True)
    original=read_exports(mesa_dir/"opengl32.dll")
    definitions=["LIBRARY opengl32","EXPORTS"]
    for name,entry in sorted(original.items()):
        target="" if name=="wglGetProcAddress" else f"=opengl32_mesa.{name}"
        definitions.append(f"    {name}{target} @{entry['ordinal']}")
    definitions.extend("    "+name for name in CUSTOM_EXPORTS)
    definition=build_dir/"opengl32.def"
    definition.write_text("\n".join(definitions)+"\n",encoding="ascii")
    def run(tool,*arguments):
        subprocess.run([str(compiler/tool),*map(str,arguments)],cwd=build_dir,check=True)
    for name in ("kernel32","gdi32"):
        run("lib.exe","/NOLOGO",f"/DEF:{HERE/(name+'.def')}","/MACHINE:X64",f"/OUT:{build_dir/(name+'.lib')}")
    obj=build_dir/"wgl_compat.obj"
    run("cl.exe","/nologo","/c","/O2","/W4","/GS-","/Zl",f"/Fo{obj}",HERE/"wgl_compat.c")
    # Build the export object separately. This avoids LINK's mixed-forwarder
    # validation error while retaining actual PE forwarder entries.
    run("lib.exe","/NOLOGO",f"/DEF:{definition}","/MACHINE:X64",f"/OUT:{build_dir/'opengl32_proxy.lib'}")
    run("link.exe","/NOLOGO","/DLL","/NODEFAULTLIB","/ENTRY:DllMain","/Brepro",
        "/DYNAMICBASE","/NXCOMPAT","/HIGHENTROPYVA",f"/OUT:{build_dir/'opengl32.dll'}",
        obj,build_dir/"opengl32_proxy.exp",build_dir/"kernel32.lib",build_dir/"gdi32.lib")
    generated=read_exports(build_dir/"opengl32.dll")
    for name,entry in original.items():
        assert generated[name]["ordinal"]==entry["ordinal"]
        expected=None if name=="wglGetProcAddress" else "opengl32_mesa."+name
        assert generated[name]["forwarder"]==expected,(name,generated[name],expected)
    assert set(generated)==set(original)|set(CUSTOM_EXPORTS)
    shutil.copy2(build_dir/"opengl32.dll",output/"opengl32.dll")
    shutil.copy2(mesa_dir/"opengl32.dll",output/"opengl32_mesa.dll")
    shutil.copy2(mesa_dir/"libgallium_wgl.dll",output/"libgallium_wgl.dll")
    sources=("wgl_compat.c","win32_minimal.h","kernel32.def","gdi32.def","build.py")
    manifest={"schema_version":1,"backend":"mesa_llvmpipe_filament19","display_name":"Software graphics (Mesa LLVMpipe)",
        "mesa_version":MESA_VERSION,"release_url":MESA_URL,"archive_sha256":ARCHIVE_SHA256,
        "files":{name:sha256(output/name) for name in ("opengl32.dll","opengl32_mesa.dll","libgallium_wgl.dll")},
        "adapter_sources":{name:sha256(HERE/name) for name in sources},
        "original_exports":len(original),"forwarded_exports":len(original)-1,
        "wrapped_exports":["wglGetProcAddress"],"custom_exports":list(CUSTOM_EXPORTS),
        "context_policy":"Only FLAGS_ARB=0x9126 is corrected to zero; other attributes stay unchanged.",
        "framebuffer_policy":"Restrict window-capable formats to Filament's native 32-bit color/depth24 descriptor.",
        "compiler":"MSVC "+compiler.parents[2].name,"binary_reproducible_link":True}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output":str(output),"named_exports":len(generated),"forwarded":len(original)-1},indent=2))
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesa-dir",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=PROJECT/"assets/rendering/mesa")
    parser.add_argument("--build-dir",type=Path,default=Path("work/wgl_compat_build"))
    parser.add_argument("--cl-dir",type=Path)
    args=parser.parse_args()
    build(args.mesa_dir,args.output,args.build_dir,args.cl_dir)
