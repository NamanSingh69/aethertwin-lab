"""ClockAligner: device-to-host time fit that tolerates read jitter and still catches real resets."""
import numpy as np
import pytest
from ingestion.sensor_synchronizer import ClockAligner


def test_startup_jitter_does_not_look_like_drift():
    # Four frames 33 ms apart read with tens of ms of host jitter: the old 4-sample slope check tripped here.
    clock=ClockAligner();rng=np.random.default_rng(0)
    for k in range(120):
        device=k/30.;host=100.+device+abs(rng.normal(0,.02))
        mapped=clock.update(device,host)
        assert abs(mapped-(100.+device))<.08
    assert .99<clock.scale<1.01 and clock.residual<.03


def test_real_drift_and_clock_reset_are_still_rejected():
    fast=ClockAligner()
    with pytest.raises(ValueError,match='drift'):
        for k in range(200):fast.update(k/30.,100.+1.05*k/30.)  # 5 % drift, seen once the span reaches 2 s
    reset=ClockAligner();reset.update(10.,100.);reset.update(10.1,100.1)
    with pytest.raises(ValueError,match='reset'):reset.update(0.,100.2)
