from pathlib import Path
import argparse,json,time
import yaml
from core.resources import resource_root
from .runner import run_diagnostics


def main():
    root=resource_root();parser=argparse.ArgumentParser(description='Observed pipeline diagnostics; no calibration or hardware promotion')
    parser.add_argument('--config',type=Path,default=root/'config/system_config.yaml');parser.add_argument('--source',choices=('sim','replay','live'),default='sim')
    parser.add_argument('--input',type=Path);parser.add_argument('--twin',type=Path);parser.add_argument('--max-frames',type=int,default=8)
    parser.add_argument('--output',type=Path,default=Path('artifacts/diagnostics')/str(time.time_ns()));parser.add_argument('--threads',type=int,default=2)
    args=parser.parse_args()
    if args.threads<1:parser.error('Threads must be positive')
    import torch
    torch.set_num_threads(args.threads)
    from core.configuration import load_config
    result=run_diagnostics(load_config(args.config),args.source,args.input,args.twin,args.output,args.max_frames,root)
    print(json.dumps({'software_checks_passed':result['software_checks_passed'],'hardware_checks_passed':result['hardware_checks_passed'],
        'stages':{name:stage['status'] for name,stage in result['stages'].items()},'output':str(args.output)},indent=2))
    return 0 if result['software_checks_passed'] else 2


if __name__=='__main__':raise SystemExit(main())
