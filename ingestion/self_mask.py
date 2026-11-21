"""Drop LiDAR returns from the rig's own body (robot chassis, wheels, wiring, brackets) before processing.

The body is fixed in the LiDAR frame, so the mask is a list of axis-aligned boxes in that frame (metres)
plus an optional minimum range. It is applied where frames enter processing (LabPipeline.process and the
commissioning twin build), never in the capture node or the recorder: recordings keep every return, so a
recording can be replayed with a corrected mask. Every masked frame reports what was removed in
diagnostics['lidar_self_mask'].

    lidar_self_mask:
      enabled: true
      min_range: 0.0
      boxes:
        - {name: chassis, min: [-0.30, -0.12, -0.20], max: [0.12, 0.12, 0.02]}
"""
from __future__ import annotations
from dataclasses import replace
import numpy as np

MAX_BOXES=32


class LidarSelfMask:
    def __init__(self,boxes=(),min_range=0.):
        parsed=[]
        if len(boxes)>MAX_BOXES:raise ValueError(f'lidar_self_mask allows at most {MAX_BOXES} boxes')
        for index,box in enumerate(boxes):
            if not isinstance(box,dict) or 'min' not in box or 'max' not in box:raise ValueError(f'lidar_self_mask box {index} needs min and max')
            low=np.asarray(box['min'],float);high=np.asarray(box['max'],float)
            if low.shape!=(3,) or high.shape!=(3,) or not np.isfinite(low).all() or not np.isfinite(high).all() or np.any(low>=high):
                raise ValueError(f'lidar_self_mask box {index} must have finite 3-vectors with min < max')
            parsed.append((str(box.get('name',f'box{index}')),low,high))
        min_range=float(min_range)
        if not np.isfinite(min_range) or min_range<0:raise ValueError('lidar_self_mask min_range must be finite and >= 0')
        if not parsed and min_range==0:raise ValueError('lidar_self_mask is enabled but has no boxes and no min_range')
        self.boxes=tuple(parsed);self.min_range=min_range

    @classmethod
    def from_config(cls,config):
        """The configured mask, or None when the section is absent or disabled."""
        section=(config or {}).get('lidar_self_mask')
        if not section or not section.get('enabled',False):return None
        return cls(section.get('boxes') or (),section.get('min_range',0.))

    def describe(self):
        return {'boxes':[{'name':name,'min':low.tolist(),'max':high.tolist()} for name,low,high in self.boxes],'min_range':self.min_range,'frame':'lidar'}

    def body(self,xyz):
        """Boolean mask of returns on the rig's own body."""
        xyz=np.asarray(xyz,float);hit=np.zeros(len(xyz),bool)
        for _,low,high in self.boxes:hit|=np.all((xyz>=low)&(xyz<=high),axis=1)
        if self.min_range>0:hit|=np.einsum('ij,ij->i',xyz,xyz)<self.min_range*self.min_range
        return hit

    def apply(self,frame):
        hit=self.body(frame.lidar_points[:,:3]);keep=~hit
        report={**self.describe(),'removed':int(hit.sum()),'kept':int(keep.sum())}
        if 'lidar_self_mask' in frame.diagnostics:raise ValueError('LiDAR self-mask already applied to this frame')
        fields={'lidar_points':frame.lidar_points[keep],'diagnostics':{**frame.diagnostics,'lidar_self_mask':report}}
        for name in ('lidar_origins','beam_ids','painted_features'):
            value=getattr(frame,name)
            if value is not None:fields[name]=value[keep]
        return replace(frame,**fields)
