"""Photo-capture appearance layer: a visual-only model of the lab placed in the twin frame.

A capture directory holds `appearance.json` and one model file. The model comes from
photographs or video (photogrammetry today; Gaussian splats are reserved), so it shows
how the lab looks, never what the sensors measured: planning, risk and tracking ignore it.
"""
import hashlib,json
from pathlib import Path
import numpy as np

SCHEMA_VERSION=1;KINDS={'gltf':'.glb'};ALIGNMENT_METHODS=('none','manual','measured_points','lidar_registration')


def file_sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1<<20),b''):digest.update(block)
    return digest.hexdigest()


def validate_transform(values):
    """Row-major 4x4 capture-to-world transform: rotation times one positive uniform scale, plus translation."""
    matrix=np.asarray(values,dtype=float)
    if matrix.shape!=(16,) or not np.isfinite(matrix).all():raise ValueError('transform must be 16 finite numbers (row-major 4x4)')
    matrix=matrix.reshape(4,4)
    if not np.allclose(matrix[3],[0,0,0,1]):raise ValueError('transform bottom row must be 0 0 0 1')
    linear=matrix[:3,:3];scale=np.cbrt(np.linalg.det(linear))
    if not scale>0:raise ValueError('transform must keep handedness and a positive scale')
    rotation=linear/scale
    if np.linalg.norm(rotation.T@rotation-np.eye(3))>1e-3:raise ValueError('transform must be a rotation with one uniform scale')
    return matrix


def load_appearance(root):
    """Validate a capture directory and return its manifest; raises ValueError on any problem."""
    root=Path(root).resolve();manifest_path=root/'appearance.json'
    try:manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as error:raise ValueError(f'Cannot read {manifest_path}: {error}') from error
    if manifest.get('schema_version')!=SCHEMA_VERSION:raise ValueError(f'appearance schema_version must be {SCHEMA_VERSION}')
    if manifest.get('kind') not in KINDS:raise ValueError(f"appearance kind must be one of {sorted(KINDS)}; Gaussian splats are not supported yet")
    if manifest.get('visual_only') is not True:raise ValueError('appearance captures are visual only; set "visual_only": true')
    name=manifest.get('file')
    if not isinstance(name,str) or Path(name).name!=name or Path(name).suffix!=KINDS[manifest['kind']]:
        raise ValueError(f"appearance file must be a plain {KINDS[manifest['kind']]} file name in the capture directory")
    model=root/name
    if not model.is_file():raise ValueError(f'appearance file {name} is missing')
    if manifest.get('sha256')!=file_sha256(model):raise ValueError(f'appearance file {name} does not match its sha256')
    validate_transform(manifest.get('transform'))
    alignment=manifest.get('alignment')
    if not isinstance(alignment,dict) or alignment.get('method') not in ALIGNMENT_METHODS:
        raise ValueError(f'alignment.method must be one of {ALIGNMENT_METHODS}')
    rmse=alignment.get('rmse_m')
    if alignment['method'] in ('measured_points','lidar_registration') and not (isinstance(rmse,(int,float)) and np.isfinite(rmse) and rmse>=0):
        raise ValueError('a measured alignment must report rmse_m in metres')
    return manifest


def package_appearance(model,output,*,transform=None,method='none',rmse_m=None,checked_points=None,provenance=None):
    """Copy a model into a new capture directory and write a validated appearance.json."""
    model=Path(model);output=Path(output)
    if model.suffix!='.glb':raise ValueError('only .glb models are supported')
    output.mkdir(parents=True,exist_ok=False);target=output/model.name;target.write_bytes(model.read_bytes())
    matrix=np.eye(4) if transform is None else validate_transform(np.asarray(transform,dtype=float).ravel())
    manifest={'schema_version':SCHEMA_VERSION,'kind':'gltf','file':target.name,'sha256':file_sha256(target),'units':'m',
        'coordinate_frame':'world_z_up','transform':[float(v) for v in matrix.ravel()],'visual_only':True,
        'alignment':{'method':method,'rmse_m':rmse_m,'checked_points':checked_points},'provenance':provenance or {}}
    (output/'appearance.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return load_appearance(output)
