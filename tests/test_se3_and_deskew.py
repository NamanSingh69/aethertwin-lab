import numpy as np
import pytest
from scipy.linalg import expm
from core.se3_math import se3_exp,se3_log,so3_exp,so3_log,hat,inverse,adjoint
from core.contracts import MultiModalSensorFrame

def test_se3_matches_independent_matrix_exponential():
    rng=np.random.default_rng(17)
    for angle in [1e-10,.2,2.0,np.pi-1e-9]:
        axis=rng.normal(size=3);axis*=angle/np.linalg.norm(axis)
        xi=np.r_[axis,rng.normal(size=3)]
        matrix=np.zeros((4,4));matrix[:3,:3]=hat(xi[:3]);matrix[:3,3]=xi[3:]
        T=se3_exp(xi)
        assert np.allclose(T,expm(matrix),atol=1e-10)
        assert np.allclose(se3_exp(se3_log(T)),T,atol=1e-9)
        assert np.allclose(inverse(T)@T,np.eye(4),atol=1e-10)

def test_adjoint_matches_conjugation():
    T=se3_exp(np.array([.2,-.3,.1,1.,2.,3.]));xi=np.array([.03,.01,-.02,.04,-.02,.01])
    assert np.allclose(T@se3_exp(xi)@inverse(T),se3_exp(adjoint(T)@xi))

def test_sensor_boundary_owns_immutable_arrays():
    raw=np.zeros((2,7),np.float32);frame=MultiModalSensorFrame(0,0.,raw)
    raw[:]=2
    assert not frame.lidar_points.any()
    with pytest.raises(ValueError):frame.lidar_points[0,0]=3
    with pytest.raises(ValueError):MultiModalSensorFrame(0,0.,np.zeros((2,6)))
