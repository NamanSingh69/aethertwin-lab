"""Read-only Jetson identity and runtime checks, independent of perception."""
from __future__ import annotations
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8").replace("\0", " ").strip()
    except (OSError, UnicodeError):
        return None


def classify_module(model, compatible=""):
    """A carrier's name is not the module identity. P3767-0000 is NX 16GB."""
    text = f"{model or ''} {compatible or ''}".lower()
    if re.search(r"p3767-0000(?:\D|$)", text):
        return "orin-nx-16gb"
    if re.search(r"p3767-0001(?:\D|$)", text):
        return "orin-nx-8gb"
    if re.search(r"p3767-000[35](?:\D|$)", text):
        return "orin-nano-8gb"
    if re.search(r"p3767-0004(?:\D|$)", text):
        return "orin-nano-4gb"
    if "orin nx" in text:
        if re.search(r"\b16\s*g(?:b|ib)?\b", text):
            return "orin-nx-16gb"
        if re.search(r"\b8\s*g(?:b|ib)?\b", text):
            return "orin-nx-8gb"
        return "orin-nx-unknown-memory"
    if "orin nano" in text:
        return "orin-nano"
    return "unrecognized"


def identity_checks(model, compatible, memory_bytes, expected="orin-nano-8gb"):
    module = classify_module(model, compatible)
    # Linux reserves RAM for firmware. Reject 8GB without requiring MemTotal to
    # equal marketed capacity. This is not a free-RAM availability check.
    if expected == "orin-nano-8gb":
        family = module in ("orin-nano", "orin-nano-8gb")
        lower, upper = 6, 9
    elif expected == "orin-nx-16gb":
        family = module in ("orin-nx-16gb", "orin-nx-unknown-memory")
        lower, upper = 12, 17
    else:
        raise ValueError("Unsupported expected Jetson module")
    memory_ok = isinstance(memory_bytes, int) and lower * 1024**3 <= memory_bytes <= upper * 1024**3
    return module, {"expected_family": family, "expected_memory_class": memory_ok,
                    "expected_module": family and memory_ok}


def probe(software_only=False, expected="orin-nano-8gb"):
    model = _read("/proc/device-tree/model")
    compatible = _read("/proc/device-tree/compatible")
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB", _read("/proc/meminfo") or "", re.M)
    memory = int(match.group(1)) * 1024 if match else None
    module, checks = identity_checks(model, compatible, memory, expected)
    checks.update(python_312=sys.version_info[:2] == (3, 12), linux=platform.system() == "Linux",
                  arm64=platform.machine().lower() in ("aarch64", "arm64"))
    report = {"schema_version": 2, "collected_at_unix": time.time(),
              "expected_module": expected, "detected_module": module,
              "platform": platform.system(), "machine": platform.machine(),
              "python": platform.python_version(), "software_only": bool(software_only),
              "hardware_validation": "not_performed", "sensor_validation": "not_performed",
              "model": model, "device_tree_compatible": compatible, "memory_total_bytes": memory,
              "os_release": _read("/etc/os-release"), "l4t_release": _read("/etc/nv_tegra_release"),
              "jetpack_package": None, "packages": {}, "checks": checks,
              "notes": ["Identity and CUDA smoke checks do not qualify physical acquisition, accuracy or thermals.",
                        "Use the installed board/carrier-supported JetPack; do not force the desktop Ubuntu version onto the Jetson.",
                        "Keep the NVIDIA image digest and Python/CUDA/PyTorch compatibility record."]}
    if platform.system() == "Linux":
        try:
            result = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "nvidia-jetpack"],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                report["jetpack_package"] = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    for package in ("torch", "numpy", "scipy", "open3d", "opencv-python", "pyorbbecsdk2", "tensorrt"):
        try:
            report["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][package] = None
    report["gpu"] = {"status": "not_tested" if software_only else "unavailable"}
    try:
        import torch
        report["gpu"].update(torch_version=str(torch.__version__), torch_cuda_build=torch.version.cuda)
        checks["cuda_enabled_torch_build"] = torch.version.cuda is not None
        if not software_only:
            checks["cuda_available"] = torch.cuda.is_available()
            if checks["cuda_available"]:
                capability = torch.cuda.get_device_capability(0)
                values = torch.arange(16, dtype=torch.float32).reshape(4, 4) / 16
                device = values.to("cuda")
                error = float(((values @ values.T) - (device @ device.T).cpu()).abs().max())
                checks.update(orin_sm87=tuple(capability) == (8, 7), cuda_math=error <= 1e-5)
                report["gpu"].update(status="passed" if checks["cuda_math"] else "failed",
                                     name=torch.cuda.get_device_name(0), compute_capability=list(capability),
                                     fp32_max_abs_error=error,
                                     total_memory_bytes=torch.cuda.get_device_properties(0).total_memory)
    except Exception as exc:
        report["gpu"]["error"] = f"{type(exc).__name__}: {exc}"
        checks["cuda_enabled_torch_build"] = False
    required = ["python_312", "linux", "arm64", "cuda_enabled_torch_build"]
    if not software_only:
        required += ["expected_module", "cuda_available", "orin_sm87", "cuda_math"]
    report["required_checks"] = required
    report["missing_requirements"] = [key for key in required if not checks.get(key, False)]
    report["runtime_checks_passed"] = not report["missing_requirements"]
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--software-only", action="store_true")
    parser.add_argument("--require-compatible", action="store_true")
    parser.add_argument("--expected", choices=("orin-nano-8gb", "orin-nx-16gb"), default="orin-nano-8gb")
    args = parser.parse_args(argv)
    result = probe(args.software_only, args.expected)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 2 if args.require_compatible and not result["runtime_checks_passed"] else 0
