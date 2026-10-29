"""Write a hash-locked, platform-specific profile from downloaded wheels only."""

import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = []
    for wheel in args.wheels.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            metadata = next(
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            )
            fields = BytesParser().parsebytes(archive.read(metadata))
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        rows.append(
            (fields["Name"].lower().replace("_", "-"), fields["Version"], digest)
        )
    if not rows:
        raise ValueError("No wheels found")
    if len({name for name, _, _ in rows}) != len(rows):
        raise ValueError("Ambiguous wheel versions in profile")
    lines = [
        "# CPython 3.12, platform-specific binary wheels; no ML/sensor dependencies."
    ]
    lines.extend(
        f"{name}=={version} --hash=sha256:{digest}"
        for name, version, digest in sorted(rows)
    )
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
