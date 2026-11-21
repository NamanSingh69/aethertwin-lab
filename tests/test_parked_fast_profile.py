from pathlib import Path
from core.configuration import load_config
ROOT=Path(__file__).resolve().parents[1]


def test_parked_fast_profile_only_switches_the_dynamic_map_to_memory():
    base=load_config(ROOT/'config/profiles/hotspot.yaml');fast=load_config(ROOT/'config/profiles/hotspot_parked_fast.yaml')
    assert base['mapping']['runtime_tiled'] is True and fast['mapping']['runtime_tiled'] is False
    assert fast['planning']==base['planning'] and 'idle_grid_interval' not in base['planning']
    for section in ('live','acquisition','perception','capture_node'):assert fast.get(section)==base.get(section)
    assert {k:v for k,v in fast['mapping'].items() if k!='runtime_tiled'}=={k:v for k,v in base['mapping'].items() if k!='runtime_tiled'}
