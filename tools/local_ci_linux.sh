#!/usr/bin/env bash
# Linux stage of tools/local_ci.py: the Ubuntu Python job of .github/workflows/ci.yml, run in WSL Ubuntu on the laptop
# instead of on a GitHub runner. Tests the COMMITTED tree (git archive HEAD), like CI.
#
#   wsl.exe -d Ubuntu -e bash /mnt/c/<repo>/tools/local_ci_linux.sh /mnt/c/<repo>
#
# The venv lives in $AETHERTWIN_CI_HOME (default ~/aethertwin-ci) and is rebuilt only when requirements-ubuntu.lock
# changes. It is created without pip (Ubuntu's python3.12-venv package, which needs sudo, is not required) and
# filled by another pip through `pip --python` ($AETHERTWIN_CI_PIP, default Miniconda's).
set -euo pipefail
REPO="${1:?usage: local_ci_linux.sh <repository path in WSL>}"
BASE="${AETHERTWIN_CI_HOME:-$HOME/aethertwin-ci}"
VENV="$BASE/venv"
SRC="$BASE/src"
LOCK="$REPO/requirements-ubuntu.lock"
PIP="${AETHERTWIN_CI_PIP:-$HOME/miniconda3/bin/python -m pip}"
mkdir -p "$BASE"
want="$(sha256sum "$LOCK" | cut -d' ' -f1)"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$VENV/.lock-sha256" 2>/dev/null || true)" != "$want" ]; then
  echo "local_ci_linux: building $VENV from requirements-ubuntu.lock (one time per lock change)"
  rm -rf "$VENV"
  python3.12 -m venv --without-pip "$VENV"
  $PIP --python "$VENV/bin/python" install -q --require-hashes -r "$LOCK"
  echo "$want" > "$VENV/.lock-sha256"
fi
$PIP --python "$VENV/bin/python" check
rm -rf "$SRC"
mkdir -p "$SRC"
git -c safe.directory="$REPO" -C "$REPO" archive HEAD | tar -x -C "$SRC"
cd "$SRC"
# repository_manifest.py lists files with `git ls-files`: index the exported tree in a throwaway repository.
git init -q && git -c core.autocrlf=false add -A -f
"$VENV/bin/python" tools/repository_manifest.py --check
OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 RUN_OBSERVED_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 \
  "$VENV/bin/python" tools/fast_check.py --full
