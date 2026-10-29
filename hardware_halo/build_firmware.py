"""Workspace-local official Arduino CLI/core installation and compile-only check."""
from pathlib import Path
import argparse,hashlib,json,platform,re,shutil,subprocess,sys,tarfile,urllib.request,zipfile
HERE=Path(__file__).resolve().parent
CLI_VERSION='1.5.1'
CORE_VERSION='3.3.12'
FQBN='esp32:esp32:esp32'
INDEX='https://espressif.github.io/arduino-esp32/package_esp32_index.json'


def download(url,path):
    if sys.platform=='win32':
        quote=lambda value:"'"+str(value).replace("'","''")+"'"
        subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',f'Invoke-WebRequest -Uri {quote(url)} -OutFile {quote(path)}'],check=True)
    else:
        with urllib.request.urlopen(url,timeout=120) as response,path.open('wb') as stream:shutil.copyfileobj(response,stream)


def build(tool_root=None,setup=False):
    root=Path(tool_root or HERE.parents[2]/'work/halo_toolchain').resolve();root.mkdir(parents=True,exist_ok=True)
    executable=root/'cli'/('arduino-cli.exe' if sys.platform=='win32' else 'arduino-cli')
    if not executable.is_file():
        if not setup:raise FileNotFoundError('Run with --setup to obtain the official workspace-local Arduino tooling')
        system='Windows' if sys.platform=='win32' else 'Linux';architecture='64bit'
        archive_name=f'arduino-cli_{CLI_VERSION}_{system}_{architecture}'+('.zip' if sys.platform=='win32' else '.tar.gz')
        base=f'https://github.com/arduino/arduino-cli/releases/download/v{CLI_VERSION}'
        checksums=root/f'{CLI_VERSION}-checksums.txt';archive=root/archive_name
        download(f'{base}/{checksums.name}',checksums);download(f'{base}/{archive_name}',archive)
        expected=next(line.split()[0] for line in checksums.read_text().splitlines() if line.split()[-1].lstrip('*')==archive_name)
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('Official Arduino CLI archive checksum mismatch')
        executable.parent.mkdir(exist_ok=True)
        if sys.platform=='win32':
            with zipfile.ZipFile(archive) as source:executable.write_bytes(source.read('arduino-cli.exe'))
        else:
            with tarfile.open(archive) as source:
                member=next(item for item in source.getmembers() if Path(item.name).name=='arduino-cli')
                with source.extractfile(member) as stream:executable.write_bytes(stream.read())
            executable.chmod(0o755)
    config=root/'arduino-cli.yaml'
    config.write_text('board_manager:\n  additional_urls:\n    - '+INDEX+'\ndirectories:\n  data: '+json.dumps(str(root/'data'))+'\n  downloads: '+json.dumps(str(root/'downloads'))+'\n  user: '+json.dumps(str(root/'user'))+'\nbuild_cache:\n  path: '+json.dumps(str(root/'build_cache'))+'\nmetrics:\n  enabled: false\nnetwork:\n  cloud_api:\n    skip_board_detection_calls: true\n',encoding='utf-8')
    prefix=[str(executable),'--config-file',str(config)]
    if setup:
        subprocess.run(prefix+['core','update-index'],check=True)
        subprocess.run(prefix+['core','install',f'esp32:esp32@{CORE_VERSION}','--skip-post-install'],check=True)
    cli=json.loads(subprocess.check_output([str(executable),'version','--format','json'],text=True))
    if cli.get('VersionString')!=CLI_VERSION:raise ValueError('Workspace Arduino CLI version does not match the reviewed pin')
    platforms=json.loads(subprocess.check_output(prefix+['core','list','--format','json'],text=True))
    installed=next((item.get('installed_version') for item in platforms.get('platforms',[]) if item.get('id')=='esp32:esp32'),None)
    if installed!=CORE_VERSION:raise ValueError('Workspace ESP32 core version does not match the reviewed pin; run --setup')
    sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE/'firmware/aether_halo').iterdir() if p.is_file()}
    output=root/'compiled';output.mkdir(exist_ok=True)
    command=prefix+['compile','--fqbn',FQBN,'--build-path',str(root/'build'),'--output-dir',str(output),str(HERE/'firmware/aether_halo')]
    result=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (root/'compile.log').write_text(result.stdout,encoding='utf-8');(HERE/'firmware_compile.log').write_text(result.stdout,encoding='utf-8');print(result.stdout)
    source_changed=sources!={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE/'firmware/aether_halo').iterdir() if p.is_file()}
    flash=re.search(r'Sketch uses (\d+) bytes',result.stdout);ram=re.search(r'Global variables use (\d+) bytes',result.stdout)
    report={'schema_version':1,'status':'passed' if result.returncode==0 and not source_changed else 'failed','compile_only':True,'flashed':False,
        'hardware_verified':False,'cli_version':CLI_VERSION,'esp32_core_version':CORE_VERSION,'fqbn':FQBN,
        'board_profile':'ESP32_DEVKIT_V1','gpio':{'ws2812_grb_24':18,'oled_sda':21,'oled_scl':22},
        'platform':platform.platform(),'tool_root':str(root),'compile_log':'firmware_compile.log',
        'sources':sources,'source_changed_during_compile':source_changed,'cli_binary_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),
        'flash_bytes':int(flash.group(1)) if flash else None,'global_ram_bytes':int(ram.group(1)) if ram else None,
        'binaries':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.bin')}}
    (HERE/'firmware_verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if result.returncode or source_changed:raise RuntimeError('Official ESP32 firmware compilation failed or source changed during compilation; see firmware_compile.log')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--tool-root',type=Path);parser.add_argument('--setup',action='store_true')
    args=parser.parse_args();build(args.tool_root,args.setup)
