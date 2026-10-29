"""Versioned, bounded NPZ chunks. JSON metadata; never pickle sensor recordings."""
from dataclasses import asdict
from pathlib import Path
import json,os
import hashlib
import numpy as np
from core.contracts import MultiModalSensorFrame,IMUSample,SensorCapabilities,CameraCalibration
from dataclasses import replace
from collections.abc import Sequence
from pathlib import PureWindowsPath
import zipfile


def validate_manifest(manifest):
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=1:
        raise ValueError('Unsupported recording version')
    count=manifest.get('frame_count');chunks=manifest.get('chunks')
    if type(count) is not int or count<0 or not isinstance(chunks,list):
        raise ValueError('Invalid recording frame/chunk count')
    names=set();total=0
    for chunk in chunks:
        if not isinstance(chunk,dict):raise ValueError('Invalid recording chunk entry')
        name=chunk.get('file');size=chunk.get('frames')
        if (not isinstance(name,str) or not name or Path(name).name!=name
            or PureWindowsPath(name).name!=name or PureWindowsPath(name).drive
            or name in ('.','..') or not name.endswith('.npz')):
            raise ValueError('Unsafe recording chunk path')
        if name in names:raise ValueError('Duplicate recording chunk')
        if type(size) is not int or size<1:raise ValueError('Invalid recording chunk frame count')
        if 'bytes' in chunk and (type(chunk['bytes']) is not int or chunk['bytes']<1):
            raise ValueError('Invalid recording chunk byte count')
        digest=chunk.get('sha256')
        if digest is not None and (not isinstance(digest,str) or len(digest)!=64
            or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('Invalid recording checksum')
        names.add(name);total+=size
    if total!=count:raise ValueError('Recording frame count mismatch in manifest')
    return manifest


def frame_nbytes(frame):
    return sum(value.nbytes for value in vars(frame).values() if isinstance(value,np.ndarray))+len(frame.imu_samples)*56


class FrameRecorder:
    def __init__(self,path,chunk_size=16,metadata=None,*,max_buffer_bytes=64*1024**2,max_disk_bytes=64*1024**3):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        if (self.path/'manifest.json').exists():raise FileExistsError(f"Recording already exists: {self.path}")
        if next(self.path.glob('chunk_*.npz'),None) is not None or next(self.path.glob('chunk_*.npz.tmp'),None) is not None or (self.path/'manifest.json.tmp').exists():
            raise FileExistsError(f'Partial recording exists and is preserved for recovery: {self.path}; use a new directory')
        self.chunk_size=int(chunk_size);self.buffer=[];self.chunks=[];self.count=0;self.closed=False
        if self.chunk_size<1:raise ValueError('Recording chunk_size must be positive')
        self.metadata={} if metadata is None else dict(metadata)
        self.max_buffer_bytes=int(max_buffer_bytes);self.max_disk_bytes=int(max_disk_bytes)
        if min(self.max_buffer_bytes,self.max_disk_bytes)<1:raise ValueError('Recording budgets must be positive')
        self.buffer_bytes=0;self.peak_buffer_bytes=0;self.disk_bytes=0
        self.lock_path=self.path/'.recording.lock'
        try:
            descriptor=os.open(self.lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError as exc:raise FileExistsError(f'Recording is active or interrupted; existing data is preserved: {self.path}') from exc
        with os.fdopen(descriptor,'w') as stream:stream.write(json.dumps({'pid':os.getpid(),'format':'lidar-lab-npz'}))
        # A preceding writer may have completed between our initial inspection
        # and lease acquisition. Recheck while holding this exclusive lease.
        if (self.path/'manifest.json').exists() or next(self.path.glob('chunk_*.npz'),None) is not None or next(self.path.glob('chunk_*.npz.tmp'),None) is not None or (self.path/'manifest.json.tmp').exists():
            self.lock_path.unlink();raise FileExistsError(f'Existing or partial recording is preserved: {self.path}; use a new directory')
    def write(self,frame):
        if self.closed:raise RuntimeError("Recorder is closed")
        size=frame_nbytes(frame)
        if size>self.max_buffer_bytes:raise ValueError('One sensor frame exceeds recording buffer budget; adjust source profile or budget')
        if self.buffer and self.buffer_bytes+size>self.max_buffer_bytes:self.flush()
        self.buffer.append(frame);self.buffer_bytes+=size;self.peak_buffer_bytes=max(self.peak_buffer_bytes,self.buffer_bytes)
        if len(self.buffer)>=self.chunk_size:self.flush()
    def flush(self):
        if not self.buffer:return
        arrays={};metadata=[]
        for j,f in enumerate(self.buffer):
            fields={"frame_id":f.frame_id,"timestamp":f.timestamp,"camera_timestamp":f.camera_timestamp,"sweep_duration":f.sweep_duration,
                    "return_metadata_valid":f.return_metadata_valid,"raw_timestamps":f.raw_timestamps,"diagnostics":f.diagnostics,"capabilities":asdict(f.capabilities),"rplidar_timestamp":f.rplidar_timestamp}
            for name in ('lidar_points','sensor_pose_world','rgb','depth','beam_ids','painted_features','gemini_rgbd_cloud','rplidar_s2_points','imu_to_lidar','rplidar_origin_lidar','lidar_origins','camera_pose_in_lidar'):
                value=getattr(f,name)
                if value is not None:arrays[f'{j}_{name}']=value
            arrays[f'{j}_imu']=np.array([[s.timestamp,*s.gyro,*s.accel] for s in f.imu_samples],np.float64).reshape(-1,7)
            if f.calibration is not None:
                c=f.calibration;fields['calibration']={'K':c.K.tolist(),'T_lidar_camera':c.T_lidar_camera.tolist(),'distortion':c.distortion.tolist(),
                    'calibration_id':c.calibration_id,'measured':c.measured}
            metadata.append(fields)
        arrays['metadata_json']=np.array(json.dumps(metadata))
        name=f'chunk_{len(self.chunks):06d}.npz';temporary=self.path/(name+'.tmp')
        with temporary.open('wb') as stream:np.savez_compressed(stream,**arrays)
        size=temporary.stat().st_size
        if self.disk_bytes+size>self.max_disk_bytes:
            temporary.unlink();raise RuntimeError('Recording disk budget exhausted')
        temporary.replace(self.path/name);self.disk_bytes+=size
        digest=hashlib.sha256()
        with (self.path/name).open('rb') as stream:
            for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
        self.count+=len(self.buffer);self.chunks.append({'file':name,'frames':len(self.buffer),'sha256':digest.hexdigest(),'bytes':size})
        self.buffer.clear();self.buffer_bytes=0;self._manifest()
    def _manifest(self):
        content={'schema_version':1,'format':'lidar-lab-npz','frame_count':self.count,'chunks':self.chunks,'units':'SI','time_domain':'common_monotonic_seconds','metadata':self.metadata,
                 'storage_budgets':{'max_buffer_bytes':self.max_buffer_bytes,'peak_buffer_bytes':self.peak_buffer_bytes,'max_disk_bytes':self.max_disk_bytes,'compressed_chunk_bytes':self.disk_bytes}}
        temporary=self.path/'manifest.json.tmp';temporary.write_text(json.dumps(content,indent=2),encoding='utf-8');temporary.replace(self.path/'manifest.json')
    def close(self):
        if not self.closed:
            try:self.flush();self._manifest()
            finally:self.closed=True;self.lock_path.unlink(missing_ok=True)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class ReplaySource:
    def __init__(self,path,*,allow_incomplete=False,max_chunk_bytes=512*1024**2):
        self.path=Path(path)
        if type(max_chunk_bytes) is not int or max_chunk_bytes<1:raise ValueError('Invalid replay chunk memory budget')
        self.max_chunk_bytes=max_chunk_bytes
        manifest_path=self.path/'manifest.json'
        if manifest_path.stat().st_size>32*1024**2:raise ValueError('Recording manifest exceeds 32MiB limit')
        self.manifest=validate_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
        status=self.path/'recording_status.json'
        if not allow_incomplete and ((self.path/'.recording.lock').exists()
            or status.is_file() and not json.loads(status.read_text(encoding='utf-8')).get('complete',False)):
            raise ValueError('Recording is active, interrupted or incomplete; use explicit recovery handling')
    def __len__(self):return self.manifest['frame_count']
    def __iter__(self):
        count=0
        for chunk in self.manifest['chunks']:
            name=Path(chunk['file'])
            if name.is_absolute() or '..' in name.parts:raise ValueError("Unsafe recording chunk path")
            target=(self.path/name).resolve()
            if not target.is_relative_to(self.path.resolve()):raise ValueError('Recording chunk escapes its bundle')
            if 'bytes' in chunk and target.stat().st_size!=chunk['bytes']:raise ValueError('Recording chunk byte count mismatch')
            if 'sha256' in chunk:
                with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if digest!=chunk['sha256']:raise ValueError('Recording chunk checksum mismatch')
            with zipfile.ZipFile(target) as archive:
                entries=archive.infolist()
                if len(entries)>65536 or sum(item.file_size for item in entries)>getattr(self,'max_chunk_bytes',512*1024**2):
                    raise ValueError('Recording decompression exceeds replay memory budget')
                if len({item.filename for item in entries})!=len(entries):raise ValueError('Duplicate recording array member')
            with np.load(target,allow_pickle=False) as data:
                metadata=json.loads(str(data['metadata_json']))
                if len(metadata)!=int(chunk['frames']):raise ValueError('Recording frame count mismatch inside chunk')
                for j,fields in enumerate(metadata):
                    fields=dict(fields);c=fields.pop('calibration',None)
                    fields['capabilities']=SensorCapabilities(**fields['capabilities'])
                    if c:fields['calibration']=CameraCalibration(**c)
                    for key in ('lidar_points','sensor_pose_world','rgb','depth','beam_ids','painted_features','gemini_rgbd_cloud','rplidar_s2_points','imu_to_lidar','rplidar_origin_lidar','lidar_origins','camera_pose_in_lidar'):
                        name=f'{j}_{key}'
                        if name in data:fields[key]=data[name].copy()
                    fields['imu_samples']=tuple(IMUSample(float(a[0]),a[1:4],a[4:7]) for a in data[f'{j}_imu'])
                    count+=1;yield MultiModalSensorFrame(**fields)
        if count!=len(self):raise ValueError('Recording frame count mismatch at end of stream')
    def close(self):return None


class FrameReplaySequence(Sequence):
    """Restartable disk-backed frame sequence, optionally re-anchored by poses.

    No RGB-D frame cache is retained. Indexed access reads one containing chunk;
    normal iteration decompresses each frame once. Pose metadata is small and
    independently bounded by the commissioning frame-count budget.
    """
    def __init__(self,path,poses=None):
        self.path=Path(path);self.source=ReplaySource(path);self.poses=poses
        self.chunk_ends=np.cumsum([int(chunk['frames']) for chunk in self.source.manifest['chunks']],dtype=np.int64)
    def __len__(self):return len(self.source)
    def __iter__(self):
        for index,frame in enumerate(self.source):
            yield frame if self.poses is None else replace(frame,sensor_pose_world=self.poses[index])
    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        from itertools import islice
        # Skip whole chunks without decompressing unrelated RGB-D arrays.
        chunk_index=int(np.searchsorted(self.chunk_ends,index,side='right'))
        chunk=self.source.manifest['chunks'][chunk_index];size=int(chunk['frames'])
        offset=0 if chunk_index==0 else int(self.chunk_ends[chunk_index-1])
        source=ReplaySource.__new__(ReplaySource);source.path=self.path
        source.max_chunk_bytes=self.source.max_chunk_bytes
        source.manifest={**self.source.manifest,'frame_count':size,'chunks':[chunk]}
        frame=next(islice(iter(source),index-offset,None))
        return frame if self.poses is None else replace(frame,sensor_pose_world=self.poses[index])
