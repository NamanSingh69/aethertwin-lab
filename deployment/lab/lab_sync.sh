#!/usr/bin/env bash
# Operator-laptop helpers (Git Bash or Linux). The repository is private, so devices never get GitHub
# credentials: committed source is streamed over SSH with `git archive`, and evidence comes back by scp.
#   deployment/lab/lab_sync.sh push-pi                 # committed HEAD -> Pi:~/aethertwin/src
#   deployment/lab/lab_sync.sh push-desktop            # committed HEAD -> desktop C:/aethertwin/src
#   deployment/lab/lab_sync.sh push-secret FILE        # e.g. config/capture_node.key or the measured calibration
#   deployment/lab/lab_sync.sh pi "command"            # run a command on the Pi
#   deployment/lab/lab_sync.sh desktop "command"       # run a command on the desktop (cmd.exe syntax)
#   deployment/lab/lab_sync.sh pull-pi-evidence        # Pi:~/aethertwin/evidence -> reports/hardware/pi/
# Environment: AETHERTWIN_SSH_KEY (default ~/.ssh/aethertwin_ed25519), PI (default aether@192.168.1.2),
# DESKTOP (user@192.168.1.10, required for desktop commands), DESKTOP_DIR (default C:/aethertwin/src).
set -euo pipefail
KEY=${AETHERTWIN_SSH_KEY:-$HOME/.ssh/aethertwin_ed25519}
PI=${PI:-aether@192.168.1.2}; DESKTOP=${DESKTOP:-}; DESKTOP_DIR=${DESKTOP_DIR:-C:/aethertwin/src}
ROOT=$(git rev-parse --show-toplevel)
remote(){ ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$@"; }
copy(){ scp -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$@"; }
commit(){
  local head; head=$(git -C "$ROOT" rev-parse HEAD)
  if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then echo "Note: uncommitted changes are NOT sent; shipping $head" >&2; fi
  echo "$head"
}
need_desktop(){ [ -n "$DESKTOP" ] || { echo "Set DESKTOP=user@192.168.1.10" >&2; exit 2; }; }

case "${1:-}" in
  push-pi)
    head=$(commit)
    git -C "$ROOT" archive --format=tar HEAD | remote "$PI" "mkdir -p ~/aethertwin/src && tar -x -C ~/aethertwin/src && echo $head > ~/aethertwin/src/SOURCE_COMMIT"
    echo "Pi source at $head";;
  push-desktop)
    need_desktop;head=$(commit)
    remote "$DESKTOP" "if not exist \"${DESKTOP_DIR//\//\\}\" mkdir \"${DESKTOP_DIR//\//\\}\""
    git -C "$ROOT" archive --format=tar HEAD | remote "$DESKTOP" "tar -x -f - -C $DESKTOP_DIR && echo $head> $DESKTOP_DIR/SOURCE_COMMIT"
    echo "Desktop source at $head";;
  push-secret)
    file=${2:?file}; name=$(basename "$file")
    copy "$file" "$PI:aethertwin/src/config/$name" && remote "$PI" "chmod 600 ~/aethertwin/src/config/$name"
    if [ -n "$DESKTOP" ]; then copy "$file" "$DESKTOP:$DESKTOP_DIR/config/$name"; fi
    echo "Copied $name to the Pi${DESKTOP:+ and the desktop} (never into git)";;
  pi) shift;remote "$PI" "$@";;
  desktop) need_desktop;shift;remote "$DESKTOP" "$@";;
  pull-pi-evidence)
    mkdir -p "$ROOT/reports/hardware/pi";copy -r "$PI:aethertwin/evidence/." "$ROOT/reports/hardware/pi/";ls "$ROOT/reports/hardware/pi";;
  *) sed -n '2,12p' "$0";exit 2;;
esac
