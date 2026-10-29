"""Create a source/data archive with per-file SHA256 inventory.

Caches, intermediate builds and earlier failed/diagnostic runs stay outside the
release archive. The workspace retains those diagnostic files for audit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SKIP={'__pycache__','.pytest_cache','.ruff_cache','.venv','build','.git','node_modules','.vite'}
ARTIFACT_DIRS={'sample_recording_run','sample_replay_run','stability_30min','final_viewer','final_mobile','install_smoke',
    'aethertwin_endurance_final','windows_aethertwin','ubuntu_aethertwin','browser_acceptance','native_pipeline_smoke',
    'tensor_export','diagnostics_release','tiled_runtime_optimization','tiled_runtime_batch_final',
    'gateway_actual_pipeline','commissioning_actual_jobs_final','commissioning_diagnostic_clean_shutdown','cache128_probe'}
ARTIFACT_FILES={'evaluation.json','annotated_evaluation.json','annotated_fine_tune.pt',
    'annotated_fine_tune.history.json','render_verified_backend.json','render_verified_backend.png',
    'runtime_scene_preview.png','runtime_scene_preview.json','runtime_scene_preview_hud.png',
    'browser_control_smoke.json','browser_stress_1080p_100k.json','browser_stress_1080p_100k.png',
    'browser_studio.png','perception_preprocessing.json','jetson_probe_windows.json'}


def release_files():
    for directory,folders,files in os.walk(ROOT,topdown=True,followlinks=False):
        parent=Path(directory);relative_parent=parent.relative_to(ROOT)
        folders[:]=sorted(name for name in folders if name not in SKIP and not name.endswith('.egg-info')
            and not (parent/name).is_symlink()
            and not (parent==ROOT and name=='dist')
            and not (relative_parent==Path('artifacts') and name not in ARTIFACT_DIRS))
        for name in sorted(files):
            path=parent/name;relative=path.relative_to(ROOT)
            if path.is_symlink() or name=='SOURCE_MANIFEST.json':continue
            if name.startswith('.env') and name not in ('.env.example','.env.template'):continue
            if relative.parts[0]=='artifacts':
                if len(relative.parts)<2 or relative.parts[1] not in ARTIFACT_DIRS|ARTIFACT_FILES:continue
                if relative.parts[1] in ('windows_aethertwin','ubuntu_aethertwin') and path.suffix=='.pt':continue
            yield path,relative


def build(output):
    output=Path(output).resolve()
    if output.is_relative_to(ROOT):raise ValueError('The archive must be outside the project to prevent recursive inclusion')
    files=list(release_files())
    inventory={'schema_version':1,'project':'lidar_lab_digital_twin','files':{}}
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path,relative in files:
            data=path.read_bytes()
            inventory['files'][relative.as_posix()]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            archive.writestr('lidar_lab_digital_twin/'+relative.as_posix(),data)
        manifest=json.dumps(inventory,indent=2)+'\n'
        archive.writestr('lidar_lab_digital_twin/SOURCE_MANIFEST.json',manifest)
    (ROOT/'SOURCE_MANIFEST.json').write_text(manifest,encoding='utf-8')
    report={'archive':output.name,'files':len(files)+1,'bytes':output.stat().st_size,
        'sha256':hashlib.file_digest(output.open('rb'),'sha256').hexdigest()}
    output.with_suffix('.sha256.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT.parent/'lidar_lab_digital_twin.zip')
    args=parser.parse_args();build(args.output)
