import time
from pathlib import Path
from command_center.runtime import BrowserRuntime
from command_center.service import SceneHub
from command_center.controls import ControlPlane
from core.configuration import load_config
from ingestion.recorder_replayer import ReplaySource
import numpy as np
import pytest
from types import SimpleNamespace


def test_browser_records_raw_frames_and_closes_its_workers(tmp_path):
    import torch
    torch.set_num_threads(2)
    root=Path(__file__).resolve().parents[1]
    runtime=BrowserRuntime(load_config(root/'config/system_config.yaml'),root=root,
        max_frames=2,output=tmp_path/'viewer',record=tmp_path/'raw')
    hub=SceneHub().start()
    try:
        runtime.start(hub,ControlPlane())
        deadline=time.monotonic()+30
        while not runtime.completed and runtime.error is None and time.monotonic()<deadline:time.sleep(.03)
        assert runtime.error is None and runtime.completed
        state=runtime.pipeline.last_state
        assert tuple(state.diagnostics['module_shapes']['perception_tracking']['scene_flow'])==state.voxel_flow_vectors.shape
    finally:runtime.close();hub.close()
    segments=list((tmp_path/'raw').glob('*/*/manifest.json'))
    assert len(segments)==1
    replay=ReplaySource(segments[0].parent)
    try:
        assert len(replay)==2
        frame=next(iter(replay))
        assert frame.painted_features is None
        assert len(frame.lidar_points)>2000
        assert replay.manifest['metadata']['source_mode']=='sim'
    finally:replay.close()
    assert runtime.recorder.status()['complete']
    assert not runtime._thread.is_alive() and not runtime.halo._thread.is_alive()


def test_initialization_failure_closes_tiled_database_before_temporary_directory(monkeypatch,tmp_path):
    import core.pipeline as module
    from tracking_and_navigation.safe_corridor_planner import PlannerGrid
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    root=Path(__file__).resolve().parents[1]
    cfg=load_config(root/'config/profiles/laptop.yaml')
    cfg['mapping']['runtime_cache_root']=str(tmp_path)
    monkeypatch.setattr(module,'InferenceEngine',lambda *args,**kwargs:SimpleNamespace())
    twin=SimpleNamespace(floor_transform=np.eye(4),points=np.zeros((1,3)))
    grid=PlannerGrid(np.zeros(3),.25,np.zeros((4,4,4),bool),np.ones((4,4,4),bool))
    pipeline=module.LabPipeline.__new__(module.LabPipeline)
    with pytest.raises(ValueError,match='at least six'):
        pipeline.__init__(cfg,None,twin,SparseMultiReturnVoxelMap(.25),grid,mobile=True)
    assert pipeline._closed and pipeline.occupancy.cells.closed
    assert not list(tmp_path.iterdir())
