"""Revisioned bounded commands applied only at a processing boundary."""
from __future__ import annotations
from collections import deque
import copy
from dataclasses import dataclass
import json
import queue
import threading
import time
import numpy as np

ACTIONS={'pause','resume','step','set_start_goal','set_geofences','seek','select_source'}


@dataclass(frozen=True)
class Command:
    request_id:str
    map_revision:str
    action:str
    payload:dict
    revision:int


def _point(value,name):
    array=np.asarray(value,dtype=float)
    if array.shape!=(3,) or not np.isfinite(array).all():raise ValueError(f'{name} requires finite XYZ metres')
    return array.tolist()


def validate_payload(action,payload):
    if not isinstance(payload,dict):raise ValueError('Command payload must be an object')
    p=copy.deepcopy(payload)
    if action=='set_start_goal':p={'start':_point(p.get('start'),'start'),'goal':_point(p.get('goal'),'goal')}
    elif action=='set_geofences':
        zones=p.get('geofences')
        if not isinstance(zones,list) or len(zones)>64:raise ValueError('At most 64 geofences are supported')
        names=set();clean=[]
        for z in zones:
            if not isinstance(z,dict):raise ValueError('Geofence must be an object')
            name=z.get('name')
            if not isinstance(name,str) or not name.strip() or len(name)>96 or name in names:raise ValueError('Geofence names must be unique and nonempty')
            names.add(name);extent=_point(z.get('half_extents'),'half_extents')
            if min(extent)<=0:raise ValueError('Geofence half extents must be positive')
            yaw=float(z.get('yaw',0));severity=z.get('severity_level',2)
            if not np.isfinite(yaw) or type(severity) is not int or not 1<=severity<=3:raise ValueError('Invalid geofence yaw/severity')
            clean.append({'name':name,'center':_point(z.get('center'),'center'),'half_extents':extent,'yaw':yaw,'severity_level':severity})
        p={'geofences':clean}
    elif action=='seek':
        index=p.get('frame_index')
        if type(index) is not int or not 0<=index<=10000000:raise ValueError('Seek requires a nonnegative frame index')
        p={'frame_index':index}
    elif action=='select_source':
        if p.get('source') not in ('sim','replay','live'):raise ValueError('Unknown source')
        if 'recording_id' in p and (not isinstance(p['recording_id'],str) or len(p['recording_id'])>128):raise ValueError('Invalid recording identity')
    elif action in ('pause','resume','step'):
        if p:raise ValueError('Playback action has no payload')
    else:raise ValueError('Unsupported action')
    return p


class ControlPlane:
    def __init__(self,map_revision='unversioned',capacity=64,actions=None):
        self.map_revision=str(map_revision);self.revision=0;self.applied_revision=0
        self.actions=set(ACTIONS if actions is None else actions)
        self.pending=queue.Queue(maxsize=capacity);self.history=deque(maxlen=128);self._lock=threading.Lock()
        self._requests={};self._request_order=deque()

    def submit(self,data):
        request_id=data.get('request_id') if isinstance(data,dict) else None
        rejected=lambda why:{'request_id':request_id,'status':'rejected','revision':self.revision,'reason':why}
        try:
            if not isinstance(request_id,str) or not request_id or len(request_id)>128:raise ValueError('A bounded request_id is required')
            action=data.get('action')
            if action not in self.actions:raise ValueError('Action is unavailable in this session')
            payload=validate_payload(action,data.get('payload',{}))
            canonical=json.dumps(data,sort_keys=True,allow_nan=False)
        except (ValueError,TypeError) as exc:return rejected(str(exc))
        with self._lock:
            if request_id in self._requests:
                saved,ack=self._requests[request_id]
                return dict(ack) if saved==canonical else rejected('request_id already used for a different command')
            if data.get('map_revision')!=self.map_revision:return rejected('Map revision changed; refresh before editing')
            if type(data.get('expected_revision')) is not int or data['expected_revision']!=self.revision:return rejected('Control revision conflict')
            if self.pending.full():return rejected('Command queue is full')
            self.revision+=1;command=Command(request_id,self.map_revision,action,payload,self.revision)
            self.pending.put_nowait(command)
            ack={'request_id':request_id,'status':'accepted','revision':self.revision,'reason':'Queued for processing boundary'}
            self._requests[request_id]=(canonical,ack);self._request_order.append(request_id)
            if len(self._request_order)>256:self._requests.pop(self._request_order.popleft(),None)
            return dict(ack)

    def drain(self,apply):
        while True:
            try:command=self.pending.get_nowait()
            except queue.Empty:break
            try:
                if command.map_revision!=self.map_revision:raise ValueError('Map changed before command application')
                apply(command)
                status='applied';reason=''
            except Exception as exc:status='failed';reason=str(exc)
            with self._lock:
                self.applied_revision=command.revision
                self.history.append({'request_id':command.request_id,'revision':command.revision,'action':command.action,
                                     'status':status,'reason':reason,'at_unix':time.time()})

    def status(self):
        with self._lock:return {'revision':self.revision,'applied_revision':self.applied_revision,
                               'pending':self.pending.qsize(),'history':list(self.history)}
