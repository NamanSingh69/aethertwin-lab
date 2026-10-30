"""Regenerate hash-locked Python 3.12 dependencies from delivered inputs.

CPU locks for Windows and Ubuntu, plus an Ubuntu CUDA 12.6 profile for GPU hosts
whose non-torch versions are pinned to the Ubuntu CPU lock.

Requires uv. Its PyTorch backend resolves the right hashes but does not reliably
emit the matching wheel index (and may emit another one), so set it explicitly.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
TARGETS={'windows':('x86_64-pc-windows-msvc','cpu'),'ubuntu':('x86_64-manylinux_2_35','cpu'),
    'ubuntu-cuda':('x86_64-manylinux_2_35','cu126')}
PYTORCH_INDEX=re.compile(r'--extra-index-url https://download\.pytorch\.org/\S+\n')


def cpu_pins():
    """Non-torch versions from the Ubuntu CPU lock, so the CUDA profile differs only in torch."""
    lines=(ROOT/'requirements-ubuntu.lock').read_text(encoding='utf-8').splitlines()
    return [line.split()[0] for line in lines if re.match(r'[A-Za-z0-9_.-]+==',line) and not line.startswith('torch==')]


def lock(target):
    path=ROOT/f'requirements-{target}.lock';platform,backend=TARGETS[target]
    with tempfile.TemporaryDirectory() as scratch:
        extra=[]
        if backend!='cpu':
            pins=Path(scratch)/'cpu-lock-pins.txt';pins.write_text('\n'.join(cpu_pins())+'\n',encoding='utf-8');extra=['-c',str(pins)]
        command=[sys.executable,'-m','uv','pip','compile','requirements.txt','-c','requirements-constraints.txt',*extra,
            '--python-version','3.12','--python-platform',platform,'--torch-backend',backend,
            '--emit-index-url','--generate-hashes','--output-file',path.name]
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    content=PYTORCH_INDEX.sub('',path.read_text(encoding='utf-8').replace('\r\n','\n'))
    # The temporary pins path is not reproducible; name it by its source instead.
    content=re.sub(r' -c \S*cpu-lock-pins\.txt',' -c <non-torch pins from requirements-ubuntu.lock>',content)
    index=f'--extra-index-url https://download.pytorch.org/whl/{backend}\n'
    content=content.replace('--index-url https://pypi.org/simple\n','--index-url https://pypi.org/simple\n'+index,1)
    path.write_text(content,encoding='utf-8',newline='\n')
    print(f'Wrote {path.name}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=[*TARGETS,'all'],default='all')
    args=parser.parse_args()
    for target in ('windows','ubuntu') if args.platform=='all' else [args.platform]:lock(target)
