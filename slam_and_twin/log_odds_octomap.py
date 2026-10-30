"""Collision-resolved sparse voxel evidence with exact 3D DDA beam traversal."""
from pathlib import Path
import numpy as np
from core.contracts import VoxelGridState


def traverse_voxels(origin,target,size):
    """Amanatides-Woo; includes origin cell and endpoint cell, zero axes handled."""
    o=np.asarray(origin,float)/size;t=np.asarray(target,float)/size;cell=np.floor(o).astype(np.int64);end=np.floor(t).astype(np.int64)
    direction=t-o;step=np.sign(direction).astype(np.int64)
    delta=np.full(3,np.inf);moving=direction!=0;delta[moving]=1/np.abs(direction[moving])
    boundary=cell+(step>0);maximum=np.full(3,np.inf);maximum[moving]=(boundary[moving]-o[moving])/direction[moving]
    maximum[cell==end]=np.inf
    yield tuple(cell)
    limit=int(np.abs(end-cell).sum())+3
    for _ in range(limit):
        if np.array_equal(cell,end):break
        axes=np.flatnonzero(np.isclose(maximum,maximum.min(),atol=1e-12,rtol=0))
        cell[axes]+=step[axes];maximum[axes]+=delta[axes];maximum[cell==end]=np.inf;yield tuple(cell)


def traverse_beams(origin,targets,size,max_cells=None):
    """Batched exact DDA, identical tie/boundary rules to traverse_voxels.

    Iteration is over traversal depth, not over individual rays. Temporary
    arrays are bounded by beam_count * maximum traversal depth, then uniqued.
    """
    targets=np.asarray(targets,dtype=np.float64).reshape(-1,3)
    if max_cells is not None and (isinstance(max_cells,bool) or not isinstance(max_cells,(int,np.integer)) or max_cells<1):raise ValueError('Reference ray capacity must be a positive integer')
    if not len(targets):return np.empty((0,3),np.int64)
    if max_cells is not None and len(targets)>max_cells:raise ValueError(f'Reference ray capacity {max_cells} exceeded before allocation')
    origins=np.asarray(origin,dtype=np.float64)
    if origins.shape==(3,):origins=np.broadcast_to(origins,targets.shape)
    if origins.shape!=targets.shape or not np.isfinite(origins).all() or not np.isfinite(targets).all():
        raise ValueError('DDA requires one finite common origin or an aligned origin for every ray')
    o=origins/size;t=targets/size
    cell=np.floor(o).astype(np.int64);end=np.floor(t).astype(np.int64)
    direction=t-o;step=np.sign(direction).astype(np.int64);moving=direction!=0
    delta=np.full(direction.shape,np.inf);np.divide(1.,np.abs(direction),out=delta,where=moving)
    maximum=np.full(direction.shape,np.inf)
    np.divide(cell+(step>0)-o,direction,out=maximum,where=moving)
    maximum[cell==end]=np.inf
    chunks=[cell.copy()];active=np.any(cell!=end,axis=1);total=len(cell)
    limit=int(np.max(np.abs(end-cell).sum(1)))+3
    for _ in range(limit):
        if not active.any():break
        count=int(np.count_nonzero(active))
        if max_cells is not None and total+count>max_cells:raise ValueError(f'Reference ray capacity {max_cells} exceeded before traversal allocation')
        minimum=maximum.min(1)
        axes=np.isclose(maximum,minimum[:,None],atol=1e-12,rtol=0)&active[:,None]
        cell[axes]+=step[axes];maximum[axes]+=delta[axes]
        maximum[cell==end]=np.inf
        chunks.append(cell[active].copy());total+=count;active=np.any(cell!=end,axis=1)
    if active.any():raise RuntimeError('DDA traversal failed to reach measured endpoints')
    return np.unique(np.concatenate(chunks),axis=0)


class SparseMultiReturnVoxelMap:
    PRIME_X=73856093;PRIME_Y=19349663;PRIME_Z=83492791
    def __init__(self,voxel_size=.18,decay_rate=.96,min_log_odds=-4,max_log_odds=4,max_age=120.,ray_backend='auto',max_traversal_cells=5_000_000):
        if voxel_size<=0 or not 0<decay_rate<=1:raise ValueError("Invalid voxel map configuration")
        self.voxel_size=float(voxel_size);self.decay_rate=float(decay_rate);self.minimum=min_log_odds;self.maximum=max_log_odds
        self.max_age=float(max_age);self.cells={};self.timestamp=0.;self._updates=0
        if ray_backend not in ('auto','native','reference') or max_traversal_cells<1:raise ValueError('Invalid ray backend/budget')
        self.ray_backend=ray_backend;self.max_traversal_cells=int(max_traversal_cells);self.last_ray_backend='not_used'

    @classmethod
    def compute_spatial_hash(cls,coords):
        # Hash is a bucket identifier, NEVER voxel identity. Tuples resolve collisions.
        a=np.asarray(coords,dtype=np.int64).astype(np.uint64)
        with np.errstate(over='ignore'):
            h=(a[:,0]*np.uint64(cls.PRIME_X))^(a[:,1]*np.uint64(cls.PRIME_Y))^(a[:,2]*np.uint64(cls.PRIME_Z))
        return (h&np.uint64((1<<63)-1)).astype(np.int64)

    def _value(self,key,t):
        val=self.cells.get(key)
        if val is None:return 0.
        return val[0]*self.decay_rate**(max(0.,t-val[2])/.1)

    # Cell values are (log_odds, permeability, timestamp) tuples: float-only tuples are untracked by the
    # cyclic GC, so full collections no longer walk every cell (they reached ~100 ms per pass at 70k cells).
    # Hot paths below inline _value with identical scalar arithmetic and use Python-int key tuples
    # (equal to and hashing like numpy-int tuples, but far cheaper to build and look up).
    def query(self,points,return_known=False):
        keys=list(map(tuple,np.floor(np.asarray(points)[:,:3]/self.voxel_size).astype(np.int64).tolist()))
        cells=self.cells;t=self.timestamp;rate=self.decay_rate;found=[cells.get(k) for k in keys]
        values=np.array([0. if v is None else v[0]*rate**(max(0.,t-v[2])/.1) for v in found],np.float32)
        known=np.array([v is not None for v in found],bool)
        return (values,known) if return_known else values

    def is_known(self,points):return self.query(points,True)[1]

    def _apply_evidence(self,free,hits,penetrations):
        """Per-cell evidence hook; storage backends may group independent keys."""
        cells=self.cells;t=self.timestamp;rate=self.decay_rate;minimum=self.minimum;maximum=self.maximum
        for key in free:
            v=cells.get(key);previous=0. if v is None else v[0]*rate**(max(0.,t-v[2])/.1)
            cells[key]=(max(minimum,previous-.4),0.,t)
        for key,increment in hits.items():
            old=cells.get(key);perm=penetrations[key] if old is None else .7*old[1]+.3*penetrations[key]
            previous=0. if old is None else old[0]*rate**(max(0.,t-old[2])/.1)
            cells[key]=(min(maximum,previous+increment),perm,t)

    def _observed_values(self,unique):
        cells=self.cells;t=self.timestamp;rate=self.decay_rate;stored=[cells[k] for k in map(tuple,np.asarray(unique).tolist())]
        return (np.array([v[0]*rate**(max(0.,t-v[2])/.1) for v in stored],np.float32),
                np.array([v[1] for v in stored],np.float32))

    def voxelize_and_update(self,points,rgb=None,timestamp=None,origin=None,beam_ids=None,return_metadata_valid=False,integrate_rays=True):
        points=np.asarray(points)
        n=len(points);xyz=points[:,:3];origin=np.zeros(3) if origin is None else np.asarray(origin,float)
        if origin.shape==(3,):origins=np.broadcast_to(origin,(n,3))
        elif origin.shape==(n,3):origins=origin
        else:raise ValueError('Map origin must have shape (3,) or (number_of_returns,3)')
        if not np.isfinite(origins).all():raise ValueError('Map ray origins must be finite')
        if beam_ids is not None:
            beam_ids=np.asarray(beam_ids)
            if beam_ids.shape!=(n,):raise ValueError('Beam identities must align with returns')
            if n:
                order=np.argsort(beam_ids,kind='stable')
                same=np.diff(beam_ids[order])==0
                if np.any(np.linalg.norm(np.diff(origins[order],axis=0)[same],axis=1)>1e-6):
                    raise ValueError('Returns of one beam must share the same acquisition origin')
        self.timestamp=float(self.timestamp+.1 if timestamp is None else timestamp)
        coords=np.floor(xyz/self.voxel_size).astype(np.int64)
        unique,inv,counts=np.unique(coords,axis=0,return_inverse=True,return_counts=True)
        free=set();hits={};penetrations={}
        if return_metadata_valid and beam_ids is None:raise ValueError("Multi-return integration requires beam identity")
        if integrate_rays and n:
            if beam_ids is None:finals=np.arange(n)
            else:
                distance=np.linalg.norm(xyz-origins,axis=1)
                order=np.lexsort((distance,np.asarray(beam_ids)))
                boundaries=np.r_[0,np.flatnonzero(np.diff(np.asarray(beam_ids)[order]))+1,n]
                finals=order[boundaries[1:]-1] if return_metadata_valid else order[boundaries[:-1]]
            # Endpoint/intermediate-hit evidence is excluded globally below.
            from native.voxel_rays import backend_status,traverse_beams as native_beams
            use_native=self.ray_backend=='native' or (self.ray_backend=='auto' and backend_status()['available'])
            if use_native:
                traversed=native_beams(origins[finals],xyz[finals],self.voxel_size,backend='native',max_cells=self.max_traversal_cells)
                self.last_ray_backend='native'
            else:
                traversed=traverse_beams(origins[finals],xyz[finals],self.voxel_size,max_cells=self.max_traversal_cells)
                self.last_ray_backend='python_batched_reference'
            free=set(map(tuple,np.asarray(traversed).tolist()))
        if n:
            # Aggregate each return once. Repeatedly scanning inv for every
            # voxel was O(points * voxels), particularly costly at camera scale.
            if return_metadata_valid:
                score=(points[:,5]-points[:,4])/points[:,5]
                solid=np.bincount(inv,weights=(score<=0),minlength=len(unique))>0
                permeability=np.bincount(inv,weights=score,minlength=len(unique))/counts
            else:
                solid=np.ones(len(unique),bool);permeability=np.zeros(len(unique))
            unique_keys=list(map(tuple,unique.tolist()))
            hits={key:(.85 if is_solid else .25) for key,is_solid in zip(unique_keys,solid)}
            penetrations={key:float(value) for key,value in zip(unique_keys,permeability)}
        self._apply_evidence(free.difference(hits),hits,penetrations)
        self._updates+=1
        if self._updates%50==0 and np.isfinite(self.max_age) and getattr(self,'eager_pruning',True):
            stale=[k for k,v in self.cells.items() if self.timestamp-v[2]>self.max_age]
            for key in stale:del self.cells[key]
        V=len(unique);features=np.zeros((V,12),np.float32)
        lo,perm=self._observed_values(unique)
        if n:
            mean=np.zeros((V,3));np.add.at(mean,inv,xyz);mean/=counts[:,None]
            intensity=np.bincount(inv,weights=points[:,3],minlength=V)/counts
            minz=np.full(V,np.inf);maxz=np.full(V,-np.inf);np.minimum.at(minz,inv,xyz[:,2]);np.maximum.at(maxz,inv,xyz[:,2])
            colors=np.full((V,3),.5)
            if rgb is not None:
                colors=np.zeros((V,3));np.add.at(colors,inv,np.asarray(rgb)[:,:3]);colors/=counts[:,None]
            features[:]=np.c_[mean,intensity,maxz-minz,np.log1p(counts),perm,lo,colors,maxz]
        else:mean=np.empty((0,3))
        return VoxelGridState(self.voxel_size,unique.astype(np.int32),mean.astype(np.float32),features,lo,perm,inv)

    def _thresholded(self,above,threshold):
        if not isinstance(self.cells,dict):
            return [k for k in self.cells if (self._value(k,self.timestamp)>threshold if above else self._value(k,self.timestamp)<threshold)]
        t=self.timestamp;rate=self.decay_rate
        # Decay never moves a value past its stored v0 (0<rate<=1; a rounded v0*p with p<=1 stays within v0), so
        # cells whose stored value is already on the wrong side of a same-signed threshold skip the power exactly.
        if above and threshold>=0:
            return [k for k,v in self.cells.items() if v[0]>threshold and v[0]*rate**(max(0.,t-v[2])/.1)>threshold]
        if not above and threshold<0:
            return [k for k,v in self.cells.items() if v[0]<threshold and v[0]*rate**(max(0.,t-v[2])/.1)<threshold]
        values=((k,v[0]*rate**(max(0.,t-v[2])/.1)) for k,v in self.cells.items())
        return [k for k,value in values if (value>threshold if above else value<threshold)]

    def occupied_points(self,threshold=.4):
        keys=self._thresholded(True,threshold)
        return (np.array(keys,dtype=float).reshape(-1,3)+.5)*self.voxel_size

    def free_points(self,threshold=-.2):
        keys=self._thresholded(False,threshold)
        return (np.array(keys,dtype=float).reshape(-1,3)+.5)*self.voxel_size

    def save(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path,coords=np.array(list(self.cells),np.int64).reshape(-1,3),values=np.array(list(self.cells.values())).reshape(-1,3),
            voxel_size=self.voxel_size,decay_rate=self.decay_rate,timestamp=self.timestamp,
            schema_version=1,min_log_odds=self.minimum,max_log_odds=self.maximum,max_age=self.max_age,updates=self._updates)

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as z:
            if 'schema_version' in z and int(z['schema_version'])!=1:raise ValueError('Unsupported occupancy map version')
            m=cls(float(z['voxel_size']),float(z['decay_rate']),
                  float(z['min_log_odds']) if 'min_log_odds' in z else -4,
                  float(z['max_log_odds']) if 'max_log_odds' in z else 4,
                  float(z['max_age']) if 'max_age' in z else 120.)
            m.timestamp=float(z['timestamp']);m.cells={tuple(k):tuple(v.tolist()) for k,v in zip(z['coords'],z['values'])}
            m._updates=int(z['updates']) if 'updates' in z else 0
        return m
