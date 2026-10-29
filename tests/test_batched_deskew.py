import numpy as np
import pytest
from core.se3_math import so3_exp,so3_exp_batch
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer


def test_pointwise_batch_rotations_match_scalar_reference_at_zero_pi_and_general_rotations():
    rng=np.random.default_rng(102)
    vectors=np.r_[np.zeros((1,3)),[[1e-15,-2e-15,3e-15]],[[np.pi-1e-12,0,0]],rng.normal(size=(300,3))]
    actual=so3_exp_batch(vectors);expected=np.array([so3_exp(w) for w in vectors])
    np.testing.assert_allclose(actual,expected,atol=2e-15,rtol=2e-15)
    np.testing.assert_allclose(actual@actual.transpose(0,2,1),np.broadcast_to(np.eye(3),actual.shape),atol=3e-15)
    assert so3_exp_batch(np.empty((0,3))).shape==(0,3,3)
    with pytest.raises(ValueError):so3_exp_batch([1,2,3])


def test_batched_pose_interpolation_preserves_individual_times_and_quadratic_translation():
    times=np.array([0,.03,.11]);rotvec=np.array([[.01,.02,-.03],[-.04,.02,.07]])
    rotation=np.array([np.eye(3),so3_exp(rotvec[0]),so3_exp(rotvec[0])@so3_exp(rotvec[1])])
    acceleration=np.array([[.5,-.2,.1],[.1,.3,-.1]])
    velocity=np.array([[1,.1,0],[1.015,.094,.003],[1.023,.118,-.005]])
    position=np.array([[0,0,0],[.030225,.00291,.000045],[.111745,.01139,-.000035]])
    trajectory={'times':times,'R':rotation,'rotvec':rotvec,'p':position,'v':velocity,'a':acceleration}
    queries=np.linspace(0,.11,2400);R,p,v=ContinuousTimeIMUDeskewer._sample(trajectory,queries)
    indices=np.clip(np.searchsorted(times,queries,side='right')-1,0,1);delta=queries-times[indices]
    expected=np.array([rotation[j]@so3_exp((t-times[j])/(times[j+1]-times[j])*rotvec[j]) for j,t in zip(indices,queries)])
    np.testing.assert_allclose(R,expected,atol=2e-15)
    np.testing.assert_allclose(p,position[indices]+velocity[indices]*delta[:,None]+.5*acceleration[indices]*delta[:,None]**2,atol=1e-15)
    np.testing.assert_allclose(v,velocity[indices]+acceleration[indices]*delta[:,None],atol=1e-15)
