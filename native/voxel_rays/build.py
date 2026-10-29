"""Build the optional exact ray kernel with local MSVC or GNU C; no SDK needed."""
from pathlib import Path
import argparse,hashlib,json,os,platform,shutil,subprocess,sys
HERE=Path(__file__).resolve().parent


def find_msvc():
    root=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))
    where=root/'Microsoft Visual Studio/Installer/vswhere.exe'
    if not where.is_file():return None
    location=subprocess.check_output([str(where),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
    candidates=list((Path(location)/'VC/Tools/MSVC').glob('*/bin/Hostx64/x64')) if location else []
    return max(candidates,key=lambda p:tuple(map(int,p.parents[2].name.split('.')))) if candidates else None


def build(output=None):
    platform_key=sys.platform+'-'+platform.machine().lower()
    output=Path(output or HERE/'lib'/platform_key).resolve();output.mkdir(parents=True,exist_ok=True)
    source=HERE/'voxel_rays.c';windows=sys.platform=='win32';compiler=find_msvc() if windows else None
    target=output/('voxel_rays.dll' if windows else 'libvoxel_rays.so')
    if compiler:
        temporary=HERE.parents[1]/'build/native_voxel_rays'/platform_key;temporary.mkdir(parents=True,exist_ok=True)
        obj=temporary/'voxel_rays.obj'
        subprocess.run([str(compiler/'cl.exe'),'/nologo','/c','/O2','/W4','/GS-','/Zl','/fp:strict',f'/Fo{obj}',str(source)],check=True)
        subprocess.run([str(compiler/'link.exe'),'/NOLOGO','/DLL','/NOENTRY','/NODEFAULTLIB','/Brepro','/DYNAMICBASE','/NXCOMPAT',f'/IMPLIB:{temporary/"voxel_rays.lib"}',f'/OUT:{target}',str(obj)],check=True)
        compiler_name='MSVC '+compiler.parents[2].name
    else:
        executable=shutil.which('gcc') or shutil.which('clang') or shutil.which('g++')
        if not executable:raise RuntimeError('No C compiler is available; the labelled Python reference remains usable')
        arguments=[executable,'-O3','-shared','-ffp-contract=off']
        if not windows:arguments.append('-fPIC')
        subprocess.run(arguments+[str(source),'-o',str(target)],check=True)
        compiler_name=subprocess.check_output([executable,'--version'],text=True).splitlines()[0]
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    report={'schema_version':1,'api_version':1,'platform':platform.platform(),'machine':platform.machine(),
        'compiler':compiler_name,'source_sha256':digest(source),'binary':target.name,'binary_sha256':digest(target),
        'floating_point':'strict double; no fast-math; absolute 1e-12 tie; no FMA contraction'}
    (output/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path)
    build(parser.parse_args().output)
