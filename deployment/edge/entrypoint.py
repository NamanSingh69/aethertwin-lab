"""Fail visibly if the configured CUDA runtime cannot execute on this Jetson."""
from pathlib import Path
import json
import os
import sys

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root))
from tools.probe_jetson import probe


def main():
    report=probe();report['base_image']=os.environ.get('AETHERTWIN_BASE_IMAGE')
    if not report['runtime_checks_passed']:
        print(json.dumps(report,indent=2),file=sys.stderr);return 2
    from command_center.__main__ import main as serve
    return serve(sys.argv[1:])


if __name__=='__main__':raise SystemExit(main())
