"""AIDE2 C28: if the monitor calibration fails on lab day, live mode had no way to run at all (it requires a twin built
with a measured camera calibration). A real LiDAR-only twin may now drive an explicitly uncalibrated live run."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_aide2_c1_real_data_build import moving_frames,build_config


def real(frames):
    from dataclasses import replace
    return [replace(f,diagnostics={**f.diagnostics,'synthetic':False,'source_mode':'live'}) for f in frames]


def test_a_real_uncalibrated_build_is_lidar_only_eligible_but_not_live_eligible(tmp_path):
    from core.pipeline import build_commissioned_assets,read_commissioning_provenance
    twin,mapping,grid=build_commissioned_assets(real(moving_frames()),tmp_path/'twin',build_config(),progress=lambda *_:None)
    twin.close();mapping.close()
    provenance=read_commissioning_provenance(tmp_path/'twin')
    assert provenance['live_reference_eligible'] is False and provenance['lidar_only_reference_eligible'] is True


def test_live_accepts_a_lidar_only_twin_only_for_an_explicitly_uncalibrated_run():
    from command_center.runtime import live_twin_acceptable
    calibrated={'live_reference_eligible':True,'lidar_only_reference_eligible':False}
    lidar_only={'live_reference_eligible':False,'lidar_only_reference_eligible':True}
    synthetic={'live_reference_eligible':False,'lidar_only_reference_eligible':False}
    assert live_twin_acceptable(calibrated,{'live':{}}) and live_twin_acceptable(calibrated,{'live':{'capture_bringup':True}})
    assert not live_twin_acceptable(lidar_only,{'live':{}}) and live_twin_acceptable(lidar_only,{'live':{'capture_bringup':True}})
    assert not live_twin_acceptable(synthetic,{'live':{'capture_bringup':True}})
