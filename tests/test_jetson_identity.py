import pytest
from core.configuration import load_config
from maintenance.hardware import identity_checks
from core.resources import resource_root


@pytest.mark.parametrize("model,compatible,gib,expected,ok", [
    ("NVIDIA Jetson Orin Nano Developer Kit", "nvidia,p3767-0005", 7, "orin-nano-8gb", True),
    ("NVIDIA Jetson Orin Nano", "nvidia,p3767-0003", 7, "orin-nano-8gb", True),
    ("NVIDIA Jetson Orin Nano", "nvidia,p3767-0004", 4, "orin-nano-8gb", False),
    ("NVIDIA Jetson Orin Nano Developer Kit", "nvidia,p3768-0000+p3767-0000", 15, "orin-nano-8gb", False),
    ("NVIDIA Jetson Orin Nano Developer Kit", "nvidia,p3768-0000+p3767-0000", 15, "orin-nx-16gb", True),
    ("Jetson Orin NX 8GB", "nvidia,p3767-0001", 7, "orin-nx-16gb", False),
    ("Jetson Xavier NX 16GB", "", 15, "orin-nx-16gb", False),
    (None, None, 8, "orin-nano-8gb", False),
])
def test_module_identity(model, compatible, gib, expected, ok):
    _, checks = identity_checks(model, compatible, gib * 1024**3, expected)
    assert checks["expected_module"] is ok


def test_legacy_nano_profile_keeps_identity_and_navigation_unset():
    cfg = load_config(resource_root() / "config/profiles/jetson_edge.yaml")
    assert cfg["hardware"]["edge_module"] == "Jetson Orin Nano 8GB"
    assert cfg["hardware"]["memory_gb"] == 8
    assert not cfg["hardware"]["raspberry_pi"] and not cfg["hardware"]["lidar_2d"]
    assert cfg["planning"]["start"] is None and cfg["planning"]["goal"] is None
    assert cfg["live"]["camera_width"] == 1280
