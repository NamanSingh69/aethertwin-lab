import numpy as np
import pytest
import native.voxel_rays as native
from slam_and_twin.log_odds_octomap import traverse_voxels,traverse_beams


def assert_sequence_equal(origins,targets,size):
    result=native.traverse_rays(origins,targets,size,backend='native')
    common=np.asarray(origins).shape==(3,)
    for index,target in enumerate(targets):
        origin=origins if common else origins[index]
        expected=np.asarray(list(traverse_voxels(origin,target,size)),np.int64)
        selection=slice(result.offsets[index],result.offsets[index+1])
        np.testing.assert_array_equal(result.keys[selection],expected)
        np.testing.assert_array_equal(result.owners[selection],index)
    np.testing.assert_array_equal(result.unique_keys(),traverse_beams(origins,targets,size))
    assert result.backend=='native' and not result.keys.flags.writeable


@pytest.mark.skipif(not native.backend_status()['available'],reason='Optional native kernel unavailable; fallback covered separately')
def test_native_exact_sequences_random_per_return_origins_and_boundary_ties():
    rng=np.random.default_rng(672)
    origins=rng.uniform(-3,3,(200,3));targets=rng.uniform(-6,6,(200,3))
    for size in (.18,.25,1.):assert_sequence_equal(origins,targets,size)
    cases=np.array([[0,0,0],[1,1,1],[-1,-1,-1],[.25,0,0],[1,0,-1],[0,-.25,0],
        [1.,1.+1e-13,0],[1.,1.+1e-10,0],[-2.,.75,-.25]])
    assert_sequence_equal(np.zeros(3),cases,.25)
    assert_sequence_equal(np.array([.25,-.25,.5]),cases,.25)


@pytest.mark.skipif(not native.backend_status()['available'],reason='Optional native kernel unavailable')
def test_native_multi_return_limits_protect_hits_and_unknown_behind_final_return():
    # A moving acquisition origin belongs to the whole beam, and intermediate
    # echoes remain hits even when a final ray traverses their cells.
    origins=np.array([[-1.,0,.5],[-1.,0,.5],[.1,-.2,.5]])
    returns=np.array([[0.,0,.5],[1.,0,.5],[.1,1.,.5]])
    hit_keys=set(map(tuple,np.floor(returns/.25).astype(np.int64)))
    finals=np.array([1,2])
    result=native.traverse_rays(origins[finals],returns[finals],.25,backend='native')
    free=set(map(tuple,result.unique_keys()))-hit_keys
    assert (0,0,2) not in free and (4,0,2) not in free
    assert (5,0,2) not in free and (-4,0,2) in free
    np.testing.assert_array_equal(result.unique_keys(),traverse_beams(origins[finals],returns[finals],.25))


def test_reference_fallback_is_explicit_and_native_request_does_not_silently_substitute(monkeypatch):
    monkeypatch.setattr(native,'_load',lambda:None)
    monkeypatch.setattr(native,'_STATUS',{'available':False,'backend':'python_reference','reason':'Deliberately absent test kernel'})
    target=np.array([[1.,1.,1.],[0,0,0]])
    result=native.traverse_rays(np.zeros(3),target,.25)
    assert result.backend=='python_reference'
    np.testing.assert_array_equal(result.unique_keys(),traverse_beams(np.zeros(3),target,.25))
    with pytest.raises(native.NativeUnavailable,match='Deliberately absent'):native.traverse_rays(np.zeros(3),target,.25,backend='native')


@pytest.mark.parametrize('backend',['reference','auto'])
def test_ray_empty_capacity_and_invalid_contracts(backend):
    empty=native.traverse_rays([0,0,0],np.empty((0,3)),.25,backend=backend)
    assert empty.keys.shape==(0,3) and empty.offsets.tolist()==[0]
    diagonal=native.traverse_rays([0,0,0],np.array([[10.,10.,10.]]),1.,backend=backend,max_cells=11)
    assert len(diagonal.keys)==11
    with pytest.raises(ValueError,match='capacity|status'):native.traverse_rays([0,0,0],np.array([[10.,0,0]]),1.,backend=backend,max_cells=10)
    for origins,targets,size in [([0,0,0],np.array([[np.nan,0,0]]),.25),
                                ([0,0],np.array([[1,0,0]]),.25),([0,0,0],np.array([[1,0,0]]),0)]:
        with pytest.raises(ValueError):native.traverse_rays(origins,targets,size,backend=backend)
