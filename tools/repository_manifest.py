"""Write/check a byte-exact SHA256 inventory of files selected for Git."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "SOURCE_MANIFEST.json"


def inventory():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    entries = {}
    for name in sorted(filter(None, names)):
        if name == MANIFEST.name:
            continue
        path = ROOT / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries[name] = {"bytes": path.stat().st_size, "sha256": digest}
    return {"schema_version": 1, "project": "lidar_lab_digital_twin", "files": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = inventory()
    if args.check:
        expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if current != expected:
            a, b = current["files"], expected["files"]
            changed = sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))
            raise SystemExit("Repository inventory differs: " + ", ".join(changed))
        print(f"Verified {len(current['files'])} tracked files.")
    else:
        MANIFEST.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote inventory for {len(current['files'])} tracked files.")


if __name__ == "__main__":
    main()
