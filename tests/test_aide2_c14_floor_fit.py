"""AIDE2 C14: an upright L2 sees only above its horizon (real wifi_stationary_60s: elevations 0..90 deg), so the floor is
never in the LiDAR cloud; the floor fit chose a plane 1.95 m ABOVE the sensor or crashed after a 16-minute build."""
import numpy as np
import pytest
from slam_and_twin.static_twin_builder import fit_floor_transform


def room(rng,floor=True):
    parts=[rng.uniform([-4,-3,-.4],[-4,3,2.2],(400,3)),rng.uniform([-4,3,-.4],[4,3,2.2],(400,3)),rng.uniform([4,-3,-.4],[4,3,2.2],(400,3)),
           rng.uniform([-4,-3,1.95],[4,3,1.95],(900,3)),rng.uniform([-1,-1,.6],[0,0,.6],(200,3))]  # walls, ceiling, a bench top
    if floor:parts.append(rng.uniform([-4,-3,-.4],[4,3,-.4],(900,3)))
    return np.concatenate(parts)


def test_without_floor_returns_no_plane_above_the_sensor_is_accepted_as_floor():
    rng=np.random.default_rng(0);points=room(rng,floor=False);sensors=np.zeros((5,3))
    with pytest.raises(ValueError,match='lidar_height_m'):fit_floor_transform(points,sensor_positions=sensors)
    T,report=fit_floor_transform(points,sensor_positions=sensors,declared_lidar_height=.4)
    assert report['method']=='declared_lidar_height' and report['measured'] is False
    assert abs(report['height_before_alignment']+.4)<1e-9 and abs(T[2,3]-.4)<1e-9


def test_a_measured_floor_below_the_sensor_wins_and_is_checked_against_the_tape():
    rng=np.random.default_rng(1);points=room(rng);sensors=np.zeros((5,3))
    T,report=fit_floor_transform(points,sensor_positions=sensors,declared_lidar_height=.43)
    assert report['method']=='measured_floor' and abs(report['height_before_alignment']+.4)<.02
    assert abs(report['declared_minus_measured_m']-.03)<.02


def test_declared_floor_follows_measured_gravity_not_the_sensor_axes():
    rng=np.random.default_rng(2);points=room(rng,floor=False);up=np.array([np.sin(np.radians(3)),0,np.cos(np.radians(3))])
    T,report=fit_floor_transform(points,sensor_positions=np.zeros((1,3)),declared_lidar_height=.5,gravity_up=up)
    np.testing.assert_allclose(T[:3,:3]@up,[0,0,1],atol=1e-9)
    floor_point=-.5*up;assert abs((T[:3,:3]@floor_point+T[:3,3])[2])<1e-9


def test_a_build_without_floor_returns_uses_the_declared_lidar_height(tmp_path):
    import yaml
    from pathlib import Path
    from core.contracts import IMUSample,MultiModalSensorFrame,SensorCapabilities
    from core.se3_math import so3_exp
    from core.pipeline import build_commissioned_assets
    root=Path(__file__).resolve().parents[1];rng=np.random.default_rng(27)
    # Walls and a ceiling only, like an upright L2 at 0.45 m: nothing below the sensor's horizon.
    world=np.r_[rng.uniform([-2,-2,0],[-2,2,2.5],(300,3)),rng.uniform([-2,2,0],[2,2,2.5],(300,3)),rng.uniform([2,-2,0],[2,2,2.5],(300,3)),
                rng.uniform([-2,-2,2.5],[2,2,2.5],(400,3))];world=world[world[:,2]>=.45]
    offsets=np.linspace(0,.1,len(world));frames=[]
    for index in range(6):
        start=index*.1;xyz=np.array([(p-[0,0,.45])@so3_exp([0,0,.6*t]) for p,t in zip(world,start+offsets)])
        imu=tuple(IMUSample(t,[0,0,.6],[0,0,9.80665]) for t in np.linspace(start-.002,start+.1,22))
        frames.append(MultiModalSensorFrame(index,start,np.c_[xyz,np.ones((len(world),3)),offsets],imu,capabilities=SensorCapabilities('unitree-l2'),
            diagnostics={'synthetic':True,'pose_prior_valid':False}))
    config=yaml.safe_load((root/'config/system_config.yaml').read_text())
    config['mapping'].update(commissioning_mode='mobile',twin_voxel_size=.14,lidar_height_m=.45,
        commissioning_budgets={'frame_buffer_bytes':128000,'max_resident_tiles':8,'max_resident_bytes':1000000,'tile_size':4,'tsdf_tile_resolution':16})
    config['planning']['max_local_grid_cells']=10000
    twin,mapping,grid=build_commissioned_assets(frames,tmp_path/'twin',config,progress=lambda *_:None)
    try:
        fit=twin.report['floor_fit'];assert fit['method']=='declared_lidar_height' and fit['measured'] is False
        assert abs(twin.floor_transform[2,3]-.45)<1e-3 and twin.points[:,2].min()>.3  # everything sits above the floor
    finally:twin.close();mapping.close()
