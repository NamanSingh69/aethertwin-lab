"""Include the calibrated demonstration resources in ordinary wheel installs."""
from pathlib import Path
from setuptools import setup

root = Path(__file__).parent
folders = ("config", "assets/checkpoints", "assets/twin_seed7", "assets/calibration", "assets/ground_truth_example",
           "assets/sample_recording", "assets/supervised_example", "assets/rendering/mesa", "web/dist", "hardware_halo/firmware", "docs", "deployment")
groups = {}
for folder in folders:
    for path in sorted((root / folder).rglob("*")):
        if (path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
            and (not path.name.startswith('.env') or path.name in ('.env.example','.env.template'))):
            destination = Path("share/lidar-lab-digital-twin") / path.parent.relative_to(root)
            groups.setdefault(destination.as_posix(), []).append(path.relative_to(root).as_posix())
groups["share/lidar-lab-digital-twin"] = ["SPEC.md", "README.md"]
setup(data_files=sorted(groups.items()))
