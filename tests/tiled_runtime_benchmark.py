"""Offline four-frame full-pipeline legacy/grouped tiled parity and timing."""
from pathlib import Path
import argparse,hashlib,json,sys,time
import numpy as np
import torch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.configuration import load_config
import core.pipeline as runtime
from ingestion.recorder_replayer import ReplaySource
from slam_and_twin.tiled_map import DiskBackedVoxelMap,DiskTileStore
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap


class LegacyDiskMap(DiskBackedVoxelMap):
    """Exact pre-batching access pattern, retaining identical bounded storage."""
    _apply_evidence=SparseMultiReturnVoxelMap._apply_evidence
    _observed_values=SparseMultiReturnVoxelMap._observed_values
    def query(self,points,return_known=False):
        coordinates=np.floor(np.asarray(points)[:,:3]/self.voxel_size).astype(np.int64)
        values=np.zeros(len(coordinates),np.float32);known=np.zeros(len(coordinates),bool)
        for i,key in enumerate(coordinates):
            value=self.cells.get(tuple(key))
            if value is not None and self.timestamp-value[2]<=self.max_age:
                known[i]=True;values[i]=value[0]*self.decay_rate**(max(0.,self.timestamp-value[2])/.1)
        return (values,known) if return_known else values


def legacy_iter_tiles(self,bounds=None):
    """Original cache-populating iterator, for an honest pre-fix control."""
    sql='SELECT x,y,z FROM tiles';parameters=()
    if bounds is not None:
        bounds=np.asarray(bounds,dtype=np.int64).reshape(2,3);lower=bounds[0]//self.tile_size;upper=bounds[1]//self.tile_size
        sql+=' WHERE x>=? AND y>=? AND z>=? AND x<=? AND y<=? AND z<=?';parameters=(*map(int,lower),*map(int,upper))
    with self.lock:extras=[tile for tile in self.cache if self.conn.execute('SELECT 1 FROM tiles WHERE x=? AND y=? AND z=?',tile).fetchone() is None]
    def keys():
        for tile in self.conn.execute(sql+' ORDER BY x,y,z',parameters):
            if tuple(tile) not in extras:yield tuple(tile)
        for tile in extras:
            if bounds is None or np.all((np.asarray(tile)>=lower)&(np.asarray(tile)<=upper)):yield tile
    for tile in keys():
        with self.lock:
            data=self._page(tile)['data'];coords=np.array(list(data),np.int64).reshape(-1,3);values=np.array(list(data.values()),float).reshape(-1,self.value_width)
        if bounds is not None:
            selected=np.all((coords>=bounds[0])&(coords<=bounds[1]),axis=1);coords=coords[selected];values=values[selected]
        if len(coords):yield tuple(tile),coords,values


def execute(kind,frames,output):
    config=load_config(ROOT/'config/profiles/laptop.yaml');config['input_mode']='replay'
    config['mapping']['runtime_cache_root']=str(output/'.cache');original=runtime.DiskBackedVoxelMap;original_iterator=DiskTileStore.iter_tiles
    runtime.DiskBackedVoxelMap=LegacyDiskMap if kind=='legacy' else DiskBackedVoxelMap
    if kind=='legacy':DiskTileStore.iter_tiles=legacy_iter_tiles
    twin,static,grid=runtime.load_commissioned_assets(ROOT/'assets/twin_seed7')
    pipeline=None;states=[];start=time.perf_counter()
    try:
        pipeline=runtime.LabPipeline(config,ROOT/'assets/checkpoints/synthetic.pt',twin,static,grid)
        for frame in frames:states.append(pipeline.process(frame))
        report=pipeline.report();report['storage']=pipeline.occupancy.cells.statistics()
        report['frame_module_ms']=[dict(state.diagnostics['module_ms']) for state in states]
        report['frame_storage']=[dict(state.diagnostics['map_storage']) for state in states]
        rows=sorted(pipeline.occupancy.cells.items());keys=np.asarray([key for key,_ in rows],np.int64);values=np.asarray([value for _,value in rows],np.float64)
        report['state_sha256']=hashlib.sha256(keys.tobytes()+values.tobytes()).hexdigest()
        report['offline_total_seconds']=time.perf_counter()-start
        return report,states,keys,values
    finally:
        runtime.DiskBackedVoxelMap=original
        DiskTileStore.iter_tiles=original_iterator
        if pipeline is not None:pipeline.close()
        else:
            if hasattr(twin,'close'):twin.close()
            if hasattr(static,'close'):static.close()


def benchmark(output,frame_count=4):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);source=ReplaySource(ROOT/'assets/sample_recording')
    frames=[]
    try:
        for frame in source:
            frames.append(frame)
            if len(frames)>=frame_count:break
    finally:source.close()
    before,old_states,old_keys,old_values=execute('legacy',frames,output)
    after,new_states,new_keys,new_values=execute('grouped',frames,output)
    np.testing.assert_array_equal(old_keys,new_keys);np.testing.assert_array_equal(old_values,new_values)
    maximum=0.
    for old,new in zip(old_states,new_states):
        for field in ('voxel_centers','voxel_colors','voxel_flow_vectors','floor_risk_values','traffic_heatmap_2d'):
            a=getattr(old,field);b=getattr(new,field);np.testing.assert_array_equal(a,b)
            maximum=max(maximum,float(np.max(np.abs(a-b))) if np.size(a) else 0.)
        assert old.trajectory_status==new.trajectory_status
        if old.planned_safe_path is not None:np.testing.assert_array_equal(old.planned_safe_path,new.planned_safe_path)
    result={'schema_version':1,'source':'actual_sample_recording_full_laptop_pipeline','frames':len(frames),
        'baseline':'Legacy per-cell access methods against unchanged bounded DiskTileStore',
        'all_algorithms_enabled':True,'before':before,'after':after,'exact_map_and_snapshot_parity':True,'maximum_snapshot_error':maximum,
        'limits':'Offline processing; no8080 process, source or settings were stopped/changed. Both use identical32-tile/64MiB budgets.'}
    (output/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'before_occupancy_ms':[x['occupancy'] for x in before['frame_module_ms']],
        'after_occupancy_ms':[x['occupancy'] for x in after['frame_module_ms']],
        'before_evictions':before['storage']['evictions'],'after_evictions':after['storage']['evictions'],
        'parity':True,'before_p50_ms':before['processing_latency_ms']['p50'],'after_p50_ms':after['processing_latency_ms']['p50']},indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'artifacts/tiled_runtime_batch')
    parser.add_argument('--frames',type=int,default=4);args=parser.parse_args();torch.set_num_threads(2)
    benchmark(args.output,args.frames)
