"""Publish the validated generated checkpoint and refreshed synthetic example."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parents[1]


def promote():
    stage=ROOT/'artifacts/canonical_training'
    report=json.loads((stage/'evaluation.json').read_text(encoding='utf-8'))
    history=json.loads((stage/'synthetic.history.json').read_text(encoding='utf-8'))
    if len(history)!=24 or not report['semantic_beats_baseline'] or not report['dynamic_flow_beats_baseline']:
        raise ValueError('Canonical training/evaluation acceptance has not passed')
    for source,target in ((stage/'synthetic.pt',ROOT/'assets/checkpoints/synthetic.pt'),
                          (stage/'synthetic.history.json',ROOT/'assets/checkpoints/synthetic.history.json'),
                          (stage/'training_source.zip',ROOT/'assets/checkpoints/training_source.zip'),
                          (stage/'evaluation.json',ROOT/'artifacts/evaluation.json')):
        temporary=target.with_name(target.name+'.publishing')
        shutil.copyfile(source,temporary);temporary.replace(target)
    refreshed=(ROOT/'artifacts/updated_supervised_example').resolve()
    target=(ROOT/'assets/supervised_example').resolve()
    workspace=ROOT.parents[1].resolve()
    backup=(workspace/'work/provenance/supervised_example_initial').resolve()
    if refreshed.exists():
        if not refreshed.is_relative_to(ROOT/'artifacts') or not target.is_relative_to(ROOT/'assets') or not backup.is_relative_to(workspace/'work/provenance'):
            raise ValueError('Refusing synthetic dataset publication outside the verified workspace paths')
        if refreshed.is_symlink() or target.is_symlink() or backup.exists():
            raise ValueError('Refusing ambiguous or already-used dataset backup paths')
        target.rename(backup)
        try:refreshed.rename(target)
        except BaseException:
            backup.rename(target);raise
    data={'canonical_checkpoint_sha256':hashlib.sha256((stage/'synthetic.pt').read_bytes()).hexdigest(),
          'selected_epoch':report['checkpoint_metadata']['epoch'],
          'ground_truth_version':'clean_material_world_v1','training_epochs':len(history),
          'evaluation_frames':report['frames'],'semantic_miou':report['semantic_miou'],
          'dynamic_flow_epe_m':report['dynamic_flow_epe_m'],'synthetic_only':True}
    (ROOT/'reports/canonical_checkpoint.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps(data,indent=2))


if __name__=='__main__':promote()
