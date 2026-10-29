"""Small evidence helpers shared by preparation commands."""
import hashlib
import json
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_fingerprint(root):
    root = Path(root)
    files = {}
    directories = ("core", "ingestion", "slam_and_twin", "perception_ml", "tracking_and_navigation",
                   "command_center", "visualization", "diagnostics", "hardware_halo", "maintenance",
                   "config", "tools", "deployment", "tests", "web/src", "web/tests", "web/dist")
    candidates = [root / name for name in ("main.py", "pyproject.toml", "setup.py", "requirements-core.txt",
                                         "requirements-windows.lock", "requirements-ubuntu.lock", "web/package-lock.json")]
    for folder in directories:
        candidates.extend(p for p in (root / folder).rglob("*") if p.is_file()
                          and "__pycache__" not in p.parts and p.suffix in (".py", ".yaml", ".json", ".ts", ".tsx", ".js", ".css", ".html", ".c", ".cpp", ".h"))
    for path in sorted(set(candidates)):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = digest(path)
    return {"sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), "files": files}
