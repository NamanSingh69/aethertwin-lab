"""Write/check a byte-exact SHA256 inventory of files selected for Git."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "SOURCE_MANIFEST.json"


def inventory(root=ROOT, index=False):
    """Hash the working tree (default, what --check verifies) or, with index=True, the staged blobs.

    index=True makes a commit's manifest match exactly what is committed even while other agents have
    unstaged edits in the same checkout (no stash needed).
    """
    root = Path(root)
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    staged = {}
    if index:
        for record in subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=root).decode("utf-8").split("\0"):
            if record:
                meta, name = record.split("\t", 1)
                staged[name] = meta.split()[1]
    entries = {}
    for name in sorted(filter(None, names)):
        if name == MANIFEST.name:
            continue
        if index:
            data = subprocess.check_output(["git", "cat-file", "blob", staged[name]], cwd=root)
            entries[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            continue
        path = root / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries[name] = {"bytes": path.stat().st_size, "sha256": digest}
    return {"schema_version": 1, "project": "lidar_lab_digital_twin", "files": entries}


def check(root=ROOT, index=False):
    """Paths whose inventory differs from the manifest. index=True compares the STAGED manifest with staged
    content (what a commit records); otherwise the working-tree manifest with the working tree (CI)."""
    root = Path(root)
    current = inventory(root, index=index)
    if index:
        text = subprocess.check_output(["git", "show", f":{MANIFEST.name}"], cwd=root).decode("utf-8")
    else:
        text = (root / MANIFEST.name).read_text(encoding="utf-8")
    a, b = current["files"], json.loads(text)["files"]
    return sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--index", action="store_true", help="hash staged content instead of the working tree")
    args = parser.parse_args()
    if args.check:
        changed = check(index=args.index)
        if changed:
            raise SystemExit("Repository inventory differs: " + ", ".join(changed))
        print("Verified tracked files" + (" (staged content)." if args.index else "."))
        return
    current = inventory(index=args.index)
    MANIFEST.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote inventory for {len(current['files'])} tracked files.")


if __name__ == "__main__":
    main()
