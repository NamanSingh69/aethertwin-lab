import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import fast_check


def test_groups_cover_every_file_once_and_balance_by_size():
    files=['tests/test_sweep_merge.py','tests/test_imu_bias.py','tests/test_sensing_mapping.py','tests/test_fast_check.py']
    split=fast_check.groups(files,2)
    assert len(split)==2 and sorted(sum(split,[]))==sorted(files)
    assert fast_check.groups(files[:1],8)==[files[:1]]


def test_summary_counts_executed_tests_and_exit_code(capsys):
    assert fast_check.main(['tests/test_sweep_merge.py','--workers','1'])==0
    out=capsys.readouterr().out
    assert 'passed=2 failed=0 skipped=0 errors=0 total=2 files=1' in out


def test_timing_sensitive_files_run_serially_after_the_parallel_groups():
    # C45: the c22 speed-ratio test failed twice under 8-worker load on 2026-11-05 and passed alone; assertions stay as they are.
    files=['tests/test_sweep_merge.py','tests/test_aide2_c22_tracker_geometry.py','tests/test_imu_bias.py','tests/test_hardware_halo.py']
    parallel,serial=fast_check.split_serial(files)
    assert serial==['tests/test_aide2_c22_tracker_geometry.py','tests/test_hardware_halo.py']
    assert parallel==['tests/test_sweep_merge.py','tests/test_imu_bias.py']
    assert set(fast_check.SERIAL)>={'tests/test_aide2_c17_tile_groups.py','tests/test_aide2_c22_tracker_geometry.py','tests/test_aide2_c23_voxel_features.py','tests/test_hardware_halo.py'}
