"""Regenerate hash-locked Python 3.12 CPU dependencies from delivered inputs.

Requires uv. Its PyTorch backend resolves CPU hashes but does not consistently
emit the separate wheel index, so include that index explicitly in both locks.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
TARGETS={'windows':'x86_64-pc-windows-msvc','ubuntu':'x86_64-manylinux_2_35'}


def lock(target):
    path=ROOT/f'requirements-{target}.lock'
    command=[sys.executable,'-m','uv','pip','compile','requirements.txt','-c','requirements-constraints.txt',
        '--python-version','3.12','--python-platform',TARGETS[target],'--torch-backend','cpu',
        '--emit-index-url','--generate-hashes','--output-file',path.name]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    content=path.read_text(encoding='utf-8')
    index='--extra-index-url https://download.pytorch.org/whl/cpu\n'
    if index not in content:
        content=content.replace('--index-url https://pypi.org/simple\n','--index-url https://pypi.org/simple\n'+index,1)
    path.write_text(content,encoding='utf-8',newline='\n')
    print(f'Wrote {path.name}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=[*TARGETS,'all'],default='all')
    args=parser.parse_args()
    for target in TARGETS if args.platform=='all' else [args.platform]:lock(target)
