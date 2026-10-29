"""Validated binary scene packets with per-client recoverable voxel deltas."""
from __future__ import annotations

import json
import math
import struct
import time
import uuid
from dataclasses import dataclass
import numpy as np

from visualization.websocket_broadcaster import frame_message, _json_value

PREFIX=struct.Struct('<4sHHI')
MAX_PACKET=16*1024*1024
MAX_HEADER=1024*1024
DTYPES={'<f4':np.dtype('<f4'),'<f8':np.dtype('<f8'),'<i4':np.dtype('<i4')}
SHAPES={'voxels.keys':2,'voxels.centers':2,'voxels.colors':2,'voxels.flow':2,'voxels.deleted':2,
        'floor_risk.centers':2,'floor_risk.risk':1,'floor_risk.colors':2,
        'traffic_heatmap.values':2,'path':2,'gemini_inlay':2}


def encode_packet(metadata,sections):
    """Section buffers are little-endian and eight-byte aligned."""
    header=dict(metadata);directory=[];parts=[];offset=0
    if set(sections)-SHAPES.keys():raise ValueError('Unknown binary section')
    for name,value in sections.items():
        kind='<i4' if name in ('voxels.keys','voxels.deleted') else ('<f8' if name=='path' else '<f4')
        array=np.ascontiguousarray(value,dtype=DTYPES[kind])
        if array.ndim!=SHAPES[name]:raise ValueError(f'Invalid {name} dimensions')
        if array.ndim==2 and name!='traffic_heatmap.values' and array.shape[1]!=(6 if name=='gemini_inlay' else 3):
            raise ValueError(f'Invalid {name} columns')
        if not np.isfinite(array).all():raise ValueError(f'Nonfinite {name}')
        padding=(-offset)%8
        if padding:parts.append(bytes(padding));offset+=padding
        data=array.tobytes()
        directory.append({'name':name,'dtype':kind,'shape':list(array.shape),'offset':offset,'length':len(data)})
        parts.append(data);offset+=len(data)
    header['protocol_version']=2;header['sections']=directory
    data=json.dumps(_json_value(header),allow_nan=False,separators=(',',':')).encode('utf-8')
    if len(data)>MAX_HEADER:raise ValueError('Scene header exceeds limit')
    start=PREFIX.size+len(data);padding=(-start)%8
    if start+padding+offset>MAX_PACKET:raise ValueError('Scene packet exceeds limit')
    return PREFIX.pack(b'ATWN',2,0,len(data))+data+bytes(padding)+b''.join(parts)


def decode_packet(packet):
    """Validate untrusted dimensions/offsets before constructing array views."""
    if len(packet)<PREFIX.size or len(packet)>MAX_PACKET:raise ValueError('Invalid scene packet size')
    magic,version,flags,length=PREFIX.unpack_from(packet)
    if magic!=b'ATWN' or version!=2 or flags!=0:raise ValueError('Unsupported scene packet')
    if length>MAX_HEADER or PREFIX.size+length>len(packet):raise ValueError('Invalid scene header length')
    try:header=json.loads(packet[PREFIX.size:PREFIX.size+length])
    except (ValueError,UnicodeDecodeError,RecursionError) as exc:raise ValueError('Invalid scene JSON') from exc
    if not isinstance(header,dict) or header.get('protocol_version')!=2:raise ValueError('Invalid scene header')
    if header.get('kind') not in ('keyframe','delta'):raise ValueError('Invalid scene message kind')
    if not isinstance(header.get('scene'),dict) or not isinstance(header.get('stream_id'),str) or not 1<=len(header['stream_id'])<=128:
        raise ValueError('Invalid scene identity/metadata')
    sent=header.get('sent_at_unix')
    if type(sent) not in (int,float) or not math.isfinite(sent):raise ValueError('Invalid scene send clock')
    if type(header.get('sequence')) is not int or header['sequence']<1:raise ValueError('Invalid scene sequence')
    if header['kind']=='delta' and (type(header.get('base_sequence')) is not int or not 1<=header['base_sequence']<header['sequence']):
        raise ValueError('Invalid delta base')
    sections=header.get('sections')
    if not isinstance(sections,list) or len(sections)>len(SHAPES):raise ValueError('Invalid section directory')
    start=(PREFIX.size+length+7)//8*8;arrays={};ranges=[]
    for entry in sections:
        if not isinstance(entry,dict):raise ValueError('Invalid section entry')
        name=entry.get('name');kind=entry.get('dtype');shape=entry.get('shape')
        if name not in SHAPES or name in arrays or kind not in DTYPES:raise ValueError('Invalid section name/type')
        expected_kind='<i4' if name in ('voxels.keys','voxels.deleted') else ('<f8' if name=='path' else '<f4')
        if kind!=expected_kind:raise ValueError('Section dtype does not match its contract')
        if not isinstance(shape,list) or len(shape)!=SHAPES[name] or any(type(x) is not int or x<0 or x>1000000 for x in shape):
            raise ValueError('Invalid section shape')
        if len(shape)==2 and name!='traffic_heatmap.values' and shape[1]!=(6 if name=='gemini_inlay' else 3):
            raise ValueError('Invalid section columns')
        offset=entry.get('offset');size=entry.get('length');expected=math.prod(shape)*DTYPES[kind].itemsize
        if type(offset) is not int or type(size) is not int or offset<0 or size!=expected or offset%8 or start+offset+size>len(packet):
            raise ValueError('Section outside packet or inconsistent length')
        if any(size and upper>offset and offset+size>lower for lower,upper in ranges):raise ValueError('Overlapping sections')
        ranges.append((offset,offset+size))
        array=np.frombuffer(packet,dtype=DTYPES[kind],count=math.prod(shape),offset=start+offset).reshape(shape)
        if not np.isfinite(array).all():raise ValueError('Nonfinite binary section')
        array.flags.writeable=False;arrays[name]=array
    required={'voxels.keys','voxels.centers','voxels.colors','voxels.flow'}
    if not required<=arrays.keys():raise ValueError('Missing voxel sections')
    n=len(arrays['voxels.keys'])
    if any(len(arrays[name])!=n for name in required):raise ValueError('Unaligned voxel sections')
    if len(np.unique(arrays['voxels.keys'],axis=0))!=n:raise ValueError('Duplicate voxel keys')
    return header,arrays


@dataclass(frozen=True)
class EncodedScene:
    sequence:int
    keyframe:bytes
    incremental:bytes
    scene:dict


class SceneEncoder:
    def __init__(self,voxel_size=.18,max_voxels=20000,keyframe_interval=30):
        if voxel_size<=0 or min(max_voxels,keyframe_interval)<1:raise ValueError('Invalid scene limits')
        self.voxel_size=float(voxel_size);self.max_voxels=int(max_voxels);self.keyframe_interval=int(keyframe_interval)
        self.sequence=0;self.previous={};self.stream_id=uuid.uuid4().hex;self.previous_identity=None

    def encode(self,state):
        full_scene=frame_message(state,max_voxels=self.max_voxels)
        scene=dict(full_scene)
        for name in ('voxels','floor_risk','traffic_heatmap'):scene[name]=dict(full_scene[name])
        sections={}
        for section in ('voxels','floor_risk','traffic_heatmap'):
            names={'voxels':('centers','colors','flow'),'floor_risk':('centers','risk','colors'),
                   'traffic_heatmap':('values',)}[section]
            for name in names:
                value=scene[section].pop(name)
                shape=(-1,3) if name in ('centers','colors','flow') else (-1,)
                array=np.asarray(value,dtype=np.float32)
                if section!='traffic_heatmap':array=array.reshape(shape)
                sections[f'{section}.{name}']=array
        sections['path']=np.asarray(scene.pop('path') or [],dtype=np.float64).reshape(-1,3)
        inlay=state.gemini_inlay_points
        if inlay is not None:
            stride=max(1,int(np.ceil(len(inlay)/5000)))
            sections['gemini_inlay']=np.asarray(inlay[::stride],np.float32)
            scene['gemini_inlay']={'source_count':len(inlay),'sampling_stride':stride}
        centers=sections['voxels.centers'];raw_keys=np.floor(centers.astype(float)/self.voxel_size)
        if np.any(np.abs(raw_keys)>np.iinfo(np.int32).max):raise ValueError('Voxel key overflow')
        keys=raw_keys.astype(np.int32)
        _,selection=np.unique(keys,axis=0,return_index=True);selection.sort();keys=keys[selection]
        for name in ('centers','colors','flow'):sections['voxels.'+name]=sections['voxels.'+name][selection]
        sections['voxels.keys']=keys;scene['voxels']['voxel_size']=self.voxel_size
        # Inspector payloads are deliberately bounded independently of algorithms.
        scene['track_source_count']=len(scene['tracks']);scene['tracks']=scene['tracks'][:256]
        self.sequence+=1
        metadata={'kind':'keyframe','sequence':self.sequence,'base_sequence':None,'stream_id':self.stream_id,
                  'sent_at_unix':time.time(),'scene':scene}
        keyframe=encode_packet(metadata,sections)
        current={tuple(key):(sections['voxels.centers'][i].tobytes()+sections['voxels.colors'][i].tobytes()+
                             sections['voxels.flow'][i].tobytes()) for i,key in enumerate(keys)}
        identity=(state.lab_id,state.rig_id,state.map_revision,state.calibration_id,state.source_mode)
        force=self.previous_identity!=identity or (self.sequence-1)%self.keyframe_interval==0
        if force:incremental=keyframe
        else:
            selected=np.array([i for i,key in enumerate(keys) if self.previous.get(tuple(key))!=current[tuple(key)]],dtype=np.int64)
            delta=dict(sections)
            for name in ('keys','centers','colors','flow'):delta['voxels.'+name]=sections['voxels.'+name][selected]
            delta['voxels.deleted']=np.asarray([key for key in self.previous if key not in current],np.int32).reshape(-1,3)
            incremental=encode_packet({**metadata,'kind':'delta','base_sequence':self.sequence-1},delta)
        self.previous=current;self.previous_identity=identity
        return EncodedScene(self.sequence,keyframe,incremental,full_scene)


class SceneAccumulator:
    """Reference client used to verify gap recovery and exact delta reconstruction."""
    def __init__(self,max_voxels=100000):
        if max_voxels<1:raise ValueError('Voxel cache limit must be positive')
        self.max_voxels=int(max_voxels);self.sequence=0;self.stream_id=None;self.voxels={};self.scene=None;self.sections={}
    def apply(self,packet):
        header,arrays=decode_packet(packet)
        if header['kind']=='delta' and (header['stream_id']!=self.stream_id or header['base_sequence']!=self.sequence):
            raise ValueError('Resynchronization required')
        if len(arrays['voxels.keys'])>self.max_voxels:raise ValueError('Voxel cache budget exceeded')
        retained=set() if header['kind']=='keyframe' else set(self.voxels)
        retained.difference_update(map(tuple,arrays.get('voxels.deleted',[])))
        retained.update(map(tuple,arrays['voxels.keys']))
        if len(retained)>self.max_voxels:raise ValueError('Voxel cache budget exceeded')
        if header['kind']=='keyframe':self.voxels.clear()
        for key in arrays.get('voxels.deleted',[]):self.voxels.pop(tuple(key),None)
        for i,key in enumerate(arrays['voxels.keys']):
            self.voxels[tuple(key)]=tuple(arrays['voxels.'+name][i].copy() for name in ('centers','colors','flow'))
        self.sequence=header['sequence'];self.stream_id=header['stream_id'];self.scene=header['scene'];self.sections=arrays
        return self
