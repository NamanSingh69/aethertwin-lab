"""Disk-cache correctness, bounded residency and streamed commissioning tests."""
from pathlib import Path
import json,subprocess,sys,copy
import numpy as np
import pytest
from ingestion.recorder_replayer import FrameRecorder,ReplaySource,FrameReplaySequence,frame_nbytes
from core.contracts import MultiModalSensorFrame,IMUSample
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
from slam_and_twin.tiled_map import DiskTileStore,DiskBackedVoxelMap,MapBudgetExceeded


def test_disk_tiles_evict_reload_and_keep_collision_and_negative_coordinates(tmp_path):
    store=DiskTileStore(tmp_path/'tiles',tile_size=2,max_resident_tiles=1,max_resident_bytes=12000)
    keys=[(0,0,0),(19349663,73856093,0),(-100,-2,-1),(2,4,6)]
    for i,key in enumerate(keys):store[key]=[float(i),.2,float(i)]
    for i,key in reversed(list(enumerate(keys))):assert store[key]==[float(i),.2,float(i)]
    assert store.evictions>0 and len(store.cache)<=1 and store.resident_bytes<=12000
    assert len(store)==4;del store[keys[-1]];store.close()
    loaded=DiskTileStore(tmp_path/'tiles',tile_size=2,max_resident_tiles=1,max_resident_bytes=12000,readonly=True)
    assert len(loaded)==3 and loaded[keys[1]][0]==1
    assert {tuple(key) for _,coords,_ in loaded.iter_tiles() for key in coords}==set(keys[:-1])
    loaded.close()


def test_disk_map_matches_exact_multi_return_evidence_and_stream_exports_legacy(tmp_path):
    memory=SparseMultiReturnVoxelMap(.25,decay_rate=.98)
    disk=DiskBackedVoxelMap(tmp_path/'map',.25,decay_rate=.98,tile_size=2,max_resident_tiles=1,max_resident_bytes=16000)
    points=np.array([[.625,.125,.125,.4,1,2,0],[1.125,.125,.125,.7,2,2,0],[-1.125,-.375,.625,.8,1,1,.1]])
    origins=np.array([[.125,.125,.125],[.125,.125,.125],[-.125,-.125,.625]])
    for t in [0,.1,.4]:
        for m in [memory,disk]:m.voxelize_and_update(points,origin=origins,beam_ids=[0,0,1],return_metadata_valid=True,timestamp=t)
    probes=np.r_[points[:,:3],origins,[[9,9,9]]]
    np.testing.assert_array_equal(disk.query(probes,True)[0],memory.query(probes,True)[0])
    np.testing.assert_array_equal(disk.is_known(probes),memory.is_known(probes))
    assert disk.observed_bounds[0]<0 and disk.observed_bounds[1]>=1.25
    assert not disk.is_known([[9,9,9]])[0]
    disk.save(tmp_path/'legacy.npz');disk.flush();disk.close()
    legacy=SparseMultiReturnVoxelMap.load(tmp_path/'legacy.npz')
    np.testing.assert_array_equal(legacy.query(probes),memory.query(probes))
    loaded=DiskBackedVoxelMap.load(tmp_path/'map');np.testing.assert_array_equal(loaded.query(probes),memory.query(probes))
    with pytest.raises(TypeError):loaded.voxelize_and_update(points)
    loaded.close()


def test_tile_store_hard_disk_budget_fails_instead_of_losing_history(tmp_path):
    store=DiskTileStore(tmp_path/'limited',tile_size=1,max_resident_tiles=1,max_disk_bytes=65536)
    with pytest.raises(MapBudgetExceeded):
        for index in range(5000):store[(index,2*index,-index)]=[.85,0.,float(index)]
    try:store.close()
    except MapBudgetExceeded:pass


def test_recorder_byte_budget_and_lazy_frame_sequence(tmp_path):
    points=np.zeros((20,7));imu=(IMUSample(0,[0,0,0],[0,0,9.80665]),IMUSample(.1,[0,0,0],[0,0,9.80665]))
    frame=MultiModalSensorFrame(0,0,points,imu,rgb=np.zeros((20,30,3),np.uint8),depth=np.ones((20,30)))
    limit=frame_nbytes(frame)+100
    with FrameRecorder(tmp_path/'frames',chunk_size=16,max_buffer_bytes=limit) as writer:
        for _ in range(5):writer.write(frame)
    assert writer.peak_buffer_bytes<=limit and len(writer.chunks)==5
    sequence=FrameReplaySequence(tmp_path/'frames');assert len(sequence)==5
    np.testing.assert_array_equal(sequence[-1].depth,frame.depth)
    assert len(list(sequence))==len(list(sequence))==5
    with pytest.raises(ValueError,match='One sensor frame'):
        FrameRecorder(tmp_path/'small',max_buffer_bytes=limit//2).write(frame)


def test_direct_builder_has_an_explicit_observation_budget():
    from slam_and_twin.static_twin_builder import StaticTwinBuilder
    frame=MultiModalSensorFrame(0,0,np.zeros((30,7)),diagnostics={'commissioning_stationary':True})
    with pytest.raises(ValueError,match='provide storage_path'):
        StaticTwinBuilder(budgets={'max_in_memory_frames':2}).build(iter([frame]*3))


def test_actual_cli_build_twin_streams_moving_recording_and_publishes_tiled_authority(tmp_path):
    import yaml
    from core.contracts import CameraCalibration,SensorCapabilities
    from core.se3_math import so3_exp
    from core.pipeline import load_commissioned_assets
    rng=np.random.default_rng(27)
    floor=rng.uniform([-2,-2,0],[2,2,0],(220,3));wall=rng.uniform([-2,-2,0],[-2,2,2.5],(180,3));rear=rng.uniform([-2,2,0],[2,2,2.5],(180,3))
    world=np.r_[floor,wall,rear];offsets=np.linspace(0,.1,len(world));frames=[]
    for index in range(2):
        start=index*.1;xyz=np.array([(p-[t,0,1.4])@so3_exp([0,0,.6*t]) for p,t in zip(world,start+offsets)])
        pose=np.eye(4);pose[2,3]=1.4
        imu=tuple(IMUSample(t,[0,0,.6],[0,0,9.80665]) for t in np.linspace(start,start+.1,21))
        calibration=CameraCalibration(np.array([[10.,0,8.],[0,10.,6.],[0,0,1.]]),np.eye(4),calibration_id='independently-measured-fixture',measured=True)
        frames.append(MultiModalSensorFrame(index,start,np.c_[xyz,np.ones((len(world),3)),offsets],imu,sensor_pose_world=pose,
            calibration=calibration,camera_timestamp=start+.1,capabilities=SensorCapabilities('unitree-l2'),
            diagnostics={'synthetic':True,'pose_prior_valid':False}))
    recording=tmp_path/'moving'
    with FrameRecorder(recording,chunk_size=1) as writer:
        for frame in frames:writer.write(frame)
    root=Path(__file__).resolve().parents[1];config=yaml.safe_load((root/'config/system_config.yaml').read_text())
    config['mapping']['commissioning_mode']='mobile';config['mapping']['commissioning_initial_velocity']=[1.,0,0]
    config['mapping']['twin_voxel_size']=.14
    config['mapping']['commissioning_budgets']={'frame_buffer_bytes':128000,'max_resident_tiles':8,'max_resident_bytes':1000000,'tile_size':4,'tsdf_tile_resolution':16}
    config['planning']['max_local_grid_cells']=10000
    config_file=tmp_path/'config.yaml';config_file.write_text(yaml.safe_dump(config));output=tmp_path/'bundle'
    completed=subprocess.run([sys.executable,str(root/'main.py'),'--mode','build-twin','--headless','--config',str(config_file),
        '--input',str(recording),'--output',str(output)],cwd=root,capture_output=True,text=True,timeout=180)
    assert completed.returncode==0,completed.stdout+'\n'+completed.stderr
    manifest=json.loads((output/'bundle_manifest.json').read_text());assert manifest['schema_version']==2
    assert manifest['occupancy_tiles']=='occupancy_tiles'
    original=next(iter(ReplaySource(output/'commissioning')));prepared=FrameReplaySequence(output/'prepared_frames')[0]
    np.testing.assert_array_equal(original.lidar_points,frames[0].lidar_points)
    assert original.lidar_origins is None and prepared.lidar_origins is not None
    assert not np.array_equal(prepared.lidar_points,original.lidar_points)
    twin,mapping,grid=load_commissioned_assets(output)
    try:
        assert len(twin.prepared_frames)==2 and isinstance(twin.prepared_frames,FrameReplaySequence)
        assert len(mapping.cells)>100 and mapping.observed_bounds is not None
        assert grid.occupied.size<=10000 and not mapping.is_known([[100,100,100]])[0]
        assert mapping.cells.resident_bytes<=1000000 and len(mapping.cells.cache)<=8
        assert sum(len(chunk) for chunk in twin.iter_measured_points())>100
    finally:twin.close();mapping.close()
    from core.pipeline import read_commissioning_provenance
    provenance=read_commissioning_provenance(output)
    assert provenance['integrity_verified'] is True
    assert provenance['any_synthetic'] is True and provenance['live_reference_eligible'] is False
    assert provenance['all_camera_calibrations_measured'] is True
    chunk=output/'commissioning'/json.loads((output/'commissioning/manifest.json').read_text())['chunks'][0]['file']
    with chunk.open('r+b') as stream:
        stream.seek(-1,2);value=stream.read(1);stream.seek(-1,2);stream.write(bytes([value[0]^1]))
    assert read_commissioning_provenance(output)['integrity_verified'] is False


def test_observed_extents_local_grid_is_bounded_without_dropping_far_tiles(tmp_path):
    from core.pipeline import observed_planning_window
    mapping=DiskBackedVoxelMap(tmp_path/'wide',1.,tile_size=2,max_resident_tiles=1,max_resident_bytes=16000)
    mapping.cells[(-100,0,0)]=[.85,0.,0.];mapping.cells[(100,0,0)]=[-.4,0.,0.]
    grid,report=observed_planning_window(mapping,{'resolution':1.,'max_local_grid_cells':32})
    assert grid.occupied.size<=32 and report['full_extent_in_local_grid'] is False
    assert report['observed_bounds']==[-100.,101.,0.,1.,0.,1.]
    assert mapping.is_known([[-99.5,.5,.5],[100.5,.5,.5]]).all()
    assert not mapping.is_known([[0.5,.5,.5]])[0]
    mapping.close()


def test_runtime_queries_and_expiry_do_not_flush_writeback_cache(tmp_path):
    mapping=DiskBackedVoxelMap(tmp_path/'dynamic',1.,max_age=.1,expiry_tiles_per_update=0,expiry_interval_updates=1)
    mapping.cells[(0,0,0)]=[.85,0.,0.];mapping.timestamp=1.
    assert not mapping.is_known([[.5,.5,.5]])[0]
    assert not len(mapping.occupied_points())
    assert mapping.cells.conn.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]==0
    mapping.voxelize_and_update(np.empty((0,7)),timestamp=1.)
    assert mapping.cells.conn.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]==0
    assert len(mapping.cells)==1
    mapping.prune_expired(1);assert len(mapping.cells)==0
    mapping.close()


def test_local_iteration_includes_dirty_new_cells_without_forcing_disk_write(tmp_path):
    mapping=DiskBackedVoxelMap(tmp_path/'dirty',1.,tile_size=16)
    mapping.cells[(0,0,0)]=[.85,0.,0.];mapping.flush()
    mapping.cells[(15,0,0)]=[.85,0.,0.]
    points=list(mapping.iter_occupied_points(bounds=[15,16,0,1,0,1]))
    np.testing.assert_array_equal(np.concatenate(points),[[15.5,.5,.5]])
    assert mapping.cells.conn.execute('SELECT SUM(n) FROM tiles').fetchone()[0]==1
    assert mapping.observed_bounds[1]==16
    mapping.close()


def test_disk_reconstruction_does_not_retain_source_rgbd_and_exports_all_tsdf_tiles(tmp_path):
    import weakref,gc
    from core.contracts import CameraCalibration
    from slam_and_twin.static_twin_builder import StaticTwinBuilder
    rng=np.random.default_rng(8)
    floor=rng.uniform([-2,-2,0],[2,2,0],(180,3));wall=rng.uniform([-2,-2,0],[-2,2,2.5],(140,3));rear=rng.uniform([-2,2,0],[2,2,2.5],(140,3))
    world=np.r_[floor,wall,rear];points=np.c_[world-[0,0,1.4],np.ones((len(world),3)),np.zeros(len(world))]
    calibration=CameraCalibration(np.array([[30.,0,15.5],[0,30.,11.5],[0,0,1.]]),np.eye(4),np.array([.1,-.02,0,0,0]))
    references=[]
    def stream():
        for index in range(5):
            pose=np.eye(4);pose[2,3]=1.4;t=index*.1
            imu=(IMUSample(t,[0,0,0],[0,0,9.80665]),IMUSample(t+.1,[0,0,0],[0,0,9.80665]))
            frame=MultiModalSensorFrame(index,t,points,imu,sensor_pose_world=pose,rgb=np.zeros((24,32,3),np.uint8),depth=np.full((24,32),1.2,np.float32),
                calibration=calibration,camera_timestamp=t+.1,diagnostics={'commissioning_stationary':True,'synthetic':True})
            references.append(weakref.ref(frame));yield frame
    twin=StaticTwinBuilder(.14,5,storage_path=tmp_path/'observations',budgets={'max_in_memory_frames':1,'frame_buffer_bytes':65536,
        'max_keyframes':3,'reconstruction_points':200,'tsdf_tile_resolution':16,'mesh_preview_vertices':10000,'mesh_preview_triangles':10000}).build(stream())
    try:
        gc.collect();assert all(ref() is None for ref in references)
        assert isinstance(twin.prepared_frames,FrameReplaySequence) and len(twin.prepared_frames)==5
        assert len(twin.points)<=200 and twin.report['total_measured_surface_voxels']>200
        report=twin.report['storage']['tsdf_tiles'];assert report['all_observations_processed'] and report['frames']==5
        meshes=list(twin.iter_tsdf_meshes());assert meshes and sum(len(mesh.triangles) for mesh in meshes)==report['total_triangles']
        assert report['resident_tile_voxels']==16**3
        assert twin.report['storage']['retired_keyframes']==2
        assert twin.report['storage']['frame_buffer_peak_bytes']<=65536
    finally:twin.close()


def test_lazy_full_mesh_export_keeps_remote_submeshes_and_unique_tile_ids(tmp_path):
    from types import SimpleNamespace
    import open3d as o3d
    from command_center.assets import export_twin_assets
    output=tmp_path/'display';calls=[]
    def geometry():
        for index,translation in enumerate(([0,0,0],[0,0,5],[1000,0,0])):
            if index:assert list(output.glob('*.glb')), 'Exporter eagerly retained all source geometry'
            mesh=o3d.geometry.TriangleMesh.create_box();mesh.translate(translation);calls.append(index);yield mesh
    preview=o3d.geometry.TriangleMesh.create_box()
    twin=SimpleNamespace(mesh=preview,tsdf_archive=object(),iter_tsdf_meshes=geometry)
    manifest=export_twin_assets(twin,output,'full-map-revision',max_tile_triangles=5)
    assert calls==[0,1,2] and manifest['source_meshes']==3
    assert manifest['bounds'][3]==1001 and manifest['bounds'][5]==6
    assert manifest['geometry_source']=='all_persisted_tsdf_tiles'
    assert len({tile['id'] for tile in manifest['tiles']})==len(manifest['tiles'])
    assert sum(tile['lods'][0]['triangles'] for tile in manifest['tiles'])==36
    assert all(lod['triangles']<=5 for tile in manifest['tiles'] for lod in tile['lods'])
    cached=export_twin_assets(twin,output,'full-map-revision',max_tile_triangles=5)
    assert cached==manifest and calls==[0,1,2]


def test_requested_planning_region_pages_far_static_tiles_and_blocks_unknown(tmp_path):
    from slam_and_twin.planning_region import load_observed_planning_region
    mapping=DiskBackedVoxelMap(tmp_path/'region',1.,tile_size=2,max_resident_tiles=1,max_resident_bytes=12000)
    for x in range(100,106):
        for y in range(3):
            for z in range(2):mapping.cells[(x,y,z)]=[-.4,0.,0.]
    mapping.cells[(103,1,0)]=[.85,0.,0.]
    try:
        grid=load_observed_planning_region(mapping,[100.5,1.5,.5],[105.5,1.5,.5],{'resolution':1.,'region_margin_m':1.,'max_local_grid_cells':128})
        occupied=grid.index([103.5,1.5,.5]);assert grid.occupied[tuple(occupied)]
        unknown=grid.index([99.5,1.5,.5]);assert not grid.known[tuple(unknown)]
        assert grid.contains_index(grid.index([100.5,1.5,.5])) and grid.contains_index(grid.index([105.5,1.5,.5]))
        assert mapping.cells.resident_bytes<=12000
        with pytest.raises(MapBudgetExceeded,match='resource limit'):
            load_observed_planning_region(mapping,[0,0,0],[1000,1000,10],{'resolution':.25,'region_margin_m':2.,'max_local_grid_cells':100})
    finally:mapping.close()


def test_orphan_chunks_and_active_recordings_are_never_overwritten(tmp_path):
    orphan=tmp_path/'orphan';orphan.mkdir();chunk=orphan/'chunk_000000.npz';chunk.write_bytes(b'recoverable-partial-data')
    before=chunk.read_bytes()
    with pytest.raises(FileExistsError,match='preserved for recovery'):FrameRecorder(orphan)
    assert chunk.read_bytes()==before and not (orphan/'manifest.json').exists()
    active=FrameRecorder(tmp_path/'active')
    with pytest.raises(FileExistsError,match='active or interrupted'):FrameRecorder(tmp_path/'active')
    active.close();assert not (tmp_path/'active/.recording.lock').exists()


def test_full_tile_distance_and_local_icp_do_not_use_the_capped_preview(tmp_path):
    from scipy.spatial import cKDTree
    from slam_and_twin.tiled_reconstruction import DiskPointCloud
    from slam_and_twin.static_twin_builder import StaticTwinBuilder
    from core.se3_math import so3_exp,transform_points
    store=DiskPointCloud(tmp_path/'surfaces',.05,tile_size=2,max_resident_tiles=1,max_resident_bytes=16000)
    points=np.array([[0.,0,0],[10000.,1.,0],[10000.,1.2,0]])
    store.add(points);transform=np.eye(4);transform[:3,:3]=so3_exp([0,0,.3]);transform[:3,3]=[2.,-1.,.4];store.transform=transform
    twin=StaticTwinBuilder();twin.point_store=store;twin.points=store.sample(1,seed=0)
    world=transform_points(transform,points);index=int(np.argmax(cKDTree(twin.points).query(world)[0]))
    query=world[[index]]+[0,0,.02]
    try:
        assert cKDTree(twin.points).query(query)[0][0]>1000
        np.testing.assert_allclose(twin.distances(query,radius=.1),[.02],atol=1e-9)
        np.testing.assert_allclose(twin.distances(query,radius=None),[.02],atol=1e-9)
        assert np.isinf(twin.distances(query+[0,0,10],radius=.1)[0])
        local=twin.load_local_points(world[index],radius=.4,max_points=10)
        assert len(local)>=1 and np.all(np.linalg.norm(local-world[index],axis=1)<=.4)
        with pytest.raises(MapBudgetExceeded,match='ICP'):
            twin.load_local_points(world[1],radius=.5,max_points=1)
        assert store.cells.resident_bytes<=16000 and len(store.cells.cache)<=1
    finally:twin.close()
