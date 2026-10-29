"""Shared raw-observation recording without blocking acquisition."""
from pathlib import Path
import shutil
from .async_recording import AsyncRecorder
from ingestion.recorder_replayer import FrameRecorder


def tree_bytes(path):
    path=Path(path)
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0


def start_raw_recording(path,twin_path,mobile,metadata=None,options=None,remaining_bytes=None):
    path=Path(path);twin_path=Path(twin_path)
    options=options or {}
    if path.resolve().is_relative_to(twin_path.resolve()):
        raise ValueError('A recording must be outside its source twin bundle')
    if (path/'manifest.json').exists():
        raise FileExistsError('Refusing to overwrite an existing recording')
    quota=int(options.get('max_disk_bytes',16*1024**3) if remaining_bytes is None else remaining_bytes)
    capture_quota=quota-tree_bytes(twin_path)
    if capture_quota<1:raise RuntimeError('Recording disk quota cannot hold the matching twin bundle')
    raw=FrameRecorder(path,chunk_size=options.get('chunk_frames',4),
        max_buffer_bytes=options.get('chunk_bytes',64*1024**2),max_disk_bytes=capture_quota,
        metadata={'motion_mode':'mobile' if mobile else 'stationary',
                  'recording_policy':'synchronized_raw_frames',**(metadata or {})})
    try:shutil.copytree(twin_path,path/'twin',dirs_exist_ok=True)
    except BaseException:raw.close();raise
    return AsyncRecorder(raw,max_frames=options.get('queued_frames',8),
        max_bytes=options.get('queued_bytes',64*1024**2),required=options.get('required',True))
