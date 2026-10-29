"""Native compatibility policy tests execute in an isolated process, not the GUI."""
from pathlib import Path
import ctypes
import hashlib
import importlib.util
import json
import subprocess
import sys
import pytest
from visualization.render_backend import validate_mesa_runtime

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"assets/rendering/mesa"


def _build_module():
    spec=importlib.util.spec_from_file_location("wgl_build",ROOT/"native/wgl_compat/build.py")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_runtime_is_pinned_and_all_original_exports_are_preserved():
    manifest=validate_mesa_runtime(RUNTIME)
    build=_build_module()
    original=build.read_exports(RUNTIME/"opengl32_mesa.dll")
    adapter=build.read_exports(RUNTIME/"opengl32.dll")
    assert len(original)==379
    assert set(adapter)==set(original)|set(build.CUSTOM_EXPORTS)
    for name,entry in original.items():
        assert adapter[name]["ordinal"]==entry["ordinal"]
        target=None if name=="wglGetProcAddress" else "opengl32_mesa."+name
        assert adapter[name]["forwarder"]==target
    assert manifest["forwarded_exports"]==378


def test_tampered_runtime_fails_before_native_loading(tmp_path):
    manifest=json.loads((RUNTIME/"manifest.json").read_text())
    (tmp_path/"manifest.json").write_text(json.dumps(manifest))
    (tmp_path/"opengl32.dll").write_bytes(b"tampered rendering library")
    with pytest.raises(ValueError,match="checksum mismatch"):
        validate_mesa_runtime(tmp_path)


def test_unknown_dependency_version_is_rejected(tmp_path):
    manifest=json.loads((RUNTIME/"manifest.json").read_text())
    payload=b"small adapter stand-in for validation-only test"
    manifest["files"]["opengl32.dll"]=hashlib.sha256(payload).hexdigest()
    manifest["files"]["opengl32_mesa.dll"]="0"*64
    (tmp_path/"opengl32.dll").write_bytes(payload)
    (tmp_path/"opengl32_mesa.dll").write_bytes(b"unrecognized Mesa loader")
    (tmp_path/"manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match="Unrecognized Mesa dependency"):
        validate_mesa_runtime(tmp_path)


@pytest.mark.skipif(sys.platform!="win32",reason="Executes an x64 Windows DLL")
def test_native_flags_and_framebuffer_policy_contracts():
    code=r'''
import ctypes,json,sys
from pathlib import Path
library=ctypes.WinDLL(str(Path(sys.argv[1]).resolve()))
Int=ctypes.c_int; Ptr=ctypes.POINTER(Int)
normalize=library.LabWglNormalizeAttributes
normalize.argtypes=[Ptr,Ptr,ctypes.c_uint]; normalize.restype=Int
cases=[
([0x2091,4,0x2092,1,0x2094,0x9126,0],[0x2091,4,0x2092,1,0x2094,0,0]),
([0x2094,0,0],[0x2094,0,0]),([0x2094,1,0],[0x2094,1,0]),
([0x2094,2,0],[0x2094,2,0]),([0x2094,3,0],[0x2094,3,0]),
([0x9126,1,0],[0x9126,1,0]),([0x9999,0x9126,0],[0x9999,0x9126,0]),
([0x2094,0x1234,0],[0x2094,0x1234,0]),([0],[0])]
for before,expected in cases:
    source=(Int*len(before))(*before); output=(Int*32)(*([1234567]*32))
    count=normalize(source,output,32)
    assert list(output[:count])==expected,(before,list(output[:count]))
    assert output[count]==1234567
output=(Int*2)(55,66)
assert normalize((Int*3)(0x2094,1,0),output,1)==0 and list(output)==[55,66]
assert normalize(None,output,2)==1 and output[0]==0 and output[1]==66
policy=library.LabWglApplyFramebufferPolicy
policy.argtypes=[Int,Int,ctypes.c_uint,Ptr,Ptr]; policy.restype=Int
policies=[
(1,2,[0x2001,0x2010,0x9999],[1,1,77],[0,1,77],1),
(2,2,[0x2001,0x2010],[1,1],[1,1],0),
(1,0,[0x2001,0x2010],[1,1],[1,1],0),
(1,2,[0x2001],[1],[1],0),
(1,2,[0x2010],[1],[1],0),
(1,2,[0x2001,0x2010],[0,1],[0,1],0),
(1,2,[0x9999],[91],[91],0)]
for format,target,attributes,before,expected,changed in policies:
    a=(Int*len(attributes))(*attributes); values=(Int*len(before))(*before)
    assert policy(format,target,len(attributes),a,values)==changed
    assert list(values)==expected
assert library.LabWglPixelFormatDescriptorSize()==40
assert library.LabWglCompatibilityFixCount()==0
assert library.LabWglFramebufferFilterCount()==0
print(json.dumps({'normalization_cases':len(cases),'framebuffer_cases':len(policies),'passed':True}))
'''
    result=subprocess.run([sys.executable,"-c",code,str(RUNTIME/"opengl32.dll")],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)["passed"] is True
