"""Measured CLI acceptance; smoke weights never replace the release checkpoint."""
from __future__ import annotations
import argparse,hashlib,json,os,platform,subprocess,sys,time
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.browser_stability_run import source_hashes


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--group',choices=('learning','integration','all'),default='all')
    parser.add_argument('--resume',action='store_true',help='Preserve successful evidence and retry failed checks')
    args=parser.parse_args(argv);output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    cfg=yaml.safe_load((ROOT/'config/system_config.yaml').read_text())
    cfg['training'].update(train_seeds=[31],validation_seeds=[103],test_seeds=[203],frames_per_scene=2)
    small=output/'smoke_learning.yaml';small.write_text(yaml.safe_dump(cfg),encoding='utf-8')
    config=ROOT/'config/profiles/laptop.yaml';canonical=ROOT/'assets/checkpoints/synthetic.pt'
    digest=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
    before_checkpoint=digest(canonical)
    report=json.loads(args.report.read_text()) if args.report.exists() else {
        'schema_version':1,'platform':platform.platform(),'python':sys.version,'checks':{},'physical_hardware_verified':False}
    cases={
        'pretrain':(['--mode','pretrain','--config',str(small),'--max-frames','2','--epochs','1','--output',str(output/'mae_smoke.pt')],0),
        'train':(['--mode','train','--config',str(small),'--epochs','1','--output',str(output/'train_smoke.pt')],0),
        'evaluate':(['--mode','evaluate','--output',str(output/'evaluation.json')],0),
        'calibrate':(['--mode','calibrate','--input',str(ROOT/'assets/calibration/synthetic_pairs.npz'),'--output',str(output/'calibration_candidate.json')],0),
        'sim_record':(['--mode','sim','--config',str(config),'--headless','--max-frames','4','--record',str(output/'raw'),
                       '--output',str(output/'sim')],0),
        'replay':(['--mode','replay','--config',str(config),'--headless','--max-frames','4','--input',str(output/'raw'),
                   '--output',str(output/'replay')],0),
        'build_twin':(['--mode','build-twin','--config',str(config),'--commissioning-mode','stations','--input',str(ROOT/'assets/twin_seed7/commissioning'),
                       '--output',str(output/'rebuilt_twin')],0),
        'hil_test':(['--mode','hil-test','--config',str(config),'--max-frames','3','--output',str(output/'diagnostics_final')],0),
        'live_rejects_simulated_map':(['--mode','live','--headless','--twin',str(ROOT/'assets/twin_seed7'),
                                     '--max-frames','1','--output',str(output/'live_rejected')],1)}
    names=list(cases)
    if args.group=='learning':names=names[:4]
    elif args.group=='integration':names=names[4:]
    for name in names:
        previous=[]
        if name in report['checks']:
            if not args.resume:raise FileExistsError(f'Existing evidence for {name}; choose new output/report')
            old=report['checks'][name]
            if old['passed']:continue
            previous=[*old.get('previous_attempts',[]),{k:v for k,v in old.items() if k!='previous_attempts'}]
            previous_log=ROOT/old['log']
            if previous_log.exists():
                archived=previous_log.with_name(previous_log.stem+f'.attempt-{len(previous)}.log')
                previous_log.rename(archived);previous[-1]['log']=str(archived.relative_to(ROOT))
        command=[sys.executable,'main.py',*cases[name][0],'--threads','2'];log=output/f'{name}.log'
        hashes=source_hashes();started=time.monotonic()
        with log.open('w',encoding='utf-8') as stream:
            result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=900)
        report['checks'][name]={'command':command,'exit_code':result.returncode,'expected_exit_code':cases[name][1],
            'passed':result.returncode==cases[name][1],'wall_seconds':time.monotonic()-started,'log':str(log.relative_to(ROOT)),
            'source_sha256':hashes}
        if previous:report['checks'][name]['previous_attempts']=previous
        report['checkpoint_sha256']=before_checkpoint
        report['canonical_checkpoint_unchanged']=digest(canonical)==before_checkpoint
        args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'check':name,**{key:report['checks'][name][key] for key in ('exit_code','wall_seconds','passed')}}),flush=True)
        if not report['checks'][name]['passed']:return 2
    if 'sim_record' in report['checks'] and 'replay' in report['checks']:
        from tools.compare_replay import compare
        report['record_replay_equal']=compare(output/'sim',output/'replay',output/'record_replay_comparison.json',allow_source_mode_difference=True)
        args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
        if not report['record_replay_equal']:return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
