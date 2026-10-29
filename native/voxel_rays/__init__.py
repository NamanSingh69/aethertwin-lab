"""Exact optional native rays with a labelled, bounded reference fallback."""
from dataclasses import dataclass
from pathlib import Path
import ctypes,hashlib,json,sys,platform
import numpy as np

HERE=Path(__file__).resolve().parent
LIBRARY_DIRECTORY=HERE/'lib'/(sys.platform+'-'+platform.machine().lower())
_LIBRARY=None
_STATUS={'available':False,'backend':'python_reference','reason':'Native kernel has not been loaded'}


class NativeUnavailable(RuntimeError):pass


def _load():
    global _LIBRARY,_STATUS
    if _LIBRARY is not None:return _LIBRARY
    try:
        manifest=json.loads((LIBRARY_DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
        binary=LIBRARY_DIRECTORY/manifest['binary']
        if binary.parent.resolve()!=LIBRARY_DIRECTORY.resolve():raise ValueError('Invalid native binary path')
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=manifest['binary_sha256']:raise ValueError('Native binary hash mismatch')
        library=ctypes.CDLL(str(binary.resolve()));library.lab_voxel_ray_api_version.restype=ctypes.c_int
        if library.lab_voxel_ray_api_version()!=1:raise ValueError('Unsupported native voxel-ray ABI')
        pointer=ctypes.c_void_p
        library.lab_voxel_trace.argtypes=[pointer,pointer,ctypes.c_int64,ctypes.c_double,pointer,pointer,pointer,ctypes.c_int64]
        library.lab_voxel_trace.restype=ctypes.c_int
        if hasattr(library,'lab_voxel_unique'):
            library.lab_voxel_unique.argtypes=[pointer,ctypes.c_int64,pointer];library.lab_voxel_unique.restype=ctypes.c_int
        _LIBRARY=library;_STATUS={'available':True,'backend':'native','reason':'','native_unique':hasattr(library,'lab_voxel_unique'),'build':manifest}
    except (OSError,ValueError,KeyError,AttributeError) as error:
        _STATUS={'available':False,'backend':'python_reference','reason':str(error)}
    return _LIBRARY


def backend_status():
    _load();return dict(_STATUS)


@dataclass(frozen=True)
class TraversalResult:
    keys: np.ndarray
    owners: np.ndarray
    offsets: np.ndarray
    backend: str
    def unique_keys(self):
        library=_load() if self.backend=='native' else None
        if library is not None and hasattr(library,'lab_voxel_unique'):
            keys=self.keys.copy();count=np.zeros(1,np.int64)
            code=library.lab_voxel_unique(keys.ctypes.data,len(keys),count.ctypes.data)
            if code:raise ValueError(f'Native unique rejected keys (status {code})')
            return keys[:int(count[0])].copy()
        return np.unique(self.keys,axis=0)


def traverse_rays(origins,targets,size,backend='auto',max_cells=5_000_000):
    """Ray-major sequences, including both cells, with each input-ray owner.

    The caller selects final returns and excludes ALL observed hit cells from
    free evidence. No ray extends past its supplied final endpoint. Per-return
    acquisition origins are supported; there is no temporal/range binning.
    """
    if backend not in ('auto','native','reference'):raise ValueError('Ray backend must be auto, native or reference')
    targets=np.asarray(targets,dtype=np.float64)
    if targets.ndim!=2 or targets.shape[1]!=3:raise ValueError('Ray targets require shape (N,3)')
    origins=np.asarray(origins,dtype=np.float64)
    if origins.shape==(3,):origins=np.broadcast_to(origins,targets.shape)
    if origins.shape!=targets.shape or not np.isfinite(origins).all() or not np.isfinite(targets).all():raise ValueError('Origins must be finite common XYZ or aligned (N,3)')
    if not np.isfinite(size) or size<=0:raise ValueError('Ray voxel size must be positive and finite')
    if isinstance(max_cells,bool) or not isinstance(max_cells,(int,np.integer)) or not 1<=max_cells<=np.iinfo(np.int64).max:raise ValueError('Ray capacity must be a positive int64 integer')
    if np.any(np.abs(targets/size)>2**53-1) or np.any(np.abs(origins/size)>2**53-1):raise ValueError('Ray cell indices exceed exact double integer range')
    # The count/fill ABI releases the GIL. Private snapshots prevent an external
    # writer changing a ray between those passes; fill capacity is also bounded
    # by the actual allocated output, never merely the requested maximum.
    targets=np.array(targets,order='C',copy=True);origins=np.array(origins,order='C',copy=True);n=len(targets)
    library=None if backend=='reference' else _load()
    if backend=='native' and library is None:raise NativeUnavailable(_STATUS['reason'])
    offsets=np.zeros(n+1,np.int64)
    if library is not None:
        def run(keys=None,owners=None):
            code=library.lab_voxel_trace(origins.ctypes.data,targets.ctypes.data,n,float(size),offsets.ctypes.data,
                None if keys is None else keys.ctypes.data,None if owners is None else owners.ctypes.data,int(max_cells if keys is None else len(keys)))
            if code:raise ValueError(f'Native ray traversal rejected input (status {code}; capacity {max_cells})')
        run();keys=np.empty((int(offsets[-1]),3),np.int64);owners=np.empty(len(keys),np.int64);run(keys,owners)
        selected='native'
    else:
        from slam_and_twin.log_odds_octomap import traverse_voxels
        sequences=[];owners_list=[];total=0
        for ray,(origin,target) in enumerate(zip(origins,targets)):
            sequence=[]
            for key in traverse_voxels(origin,target,size):
                if total>=max_cells:raise ValueError(f'Reference ray capacity {max_cells} exceeded')
                sequence.append(key);total+=1
            sequences.extend(sequence);owners_list.extend([ray]*len(sequence));offsets[ray+1]=total
        keys=np.asarray(sequences,np.int64).reshape(-1,3);owners=np.asarray(owners_list,np.int64);selected='python_reference'
    for value in (keys,owners,offsets):value.setflags(write=False)
    return TraversalResult(keys,owners,offsets,selected)


def traverse_beams(origins,targets,size,backend='auto',max_cells=5_000_000):
    """Drop-in unique-key result for the current batched occupancy reference."""
    return traverse_rays(origins,targets,size,backend,max_cells).unique_keys()
