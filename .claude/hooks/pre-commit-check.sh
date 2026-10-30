#!/usr/bin/env bash
# PreToolUse(Bash): before `git commit`, verify the source manifest and, when web
# sources are staged, the TypeScript build. Fast (<5 s); exit 2 blocks the commit.
input=$(cat)
command=$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)
# Only standalone commits: in a compound command the check would run before earlier steps.
case "$command" in "git commit"*) ;; *) exit 0 ;; esac
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0
if ! out=$(python3 tools/repository_manifest.py --check 2>&1); then
  echo "SOURCE_MANIFEST.json is stale. Run: python3 tools/repository_manifest.py && git add SOURCE_MANIFEST.json" >&2
  echo "$out" | tail -c 600 >&2
  exit 2
fi
if git diff --cached --name-only | grep -qE '^web/(src|tests|e2e)/|^web/.*\.ts$' && [ -d web/node_modules ]; then
  if ! out=$(cd web && npx --no-install tsc --noEmit 2>&1); then
    echo "TypeScript errors in staged web sources:" >&2
    echo "$out" | head -20 >&2
    exit 2
  fi
fi
exit 0
