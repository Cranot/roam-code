#!/usr/bin/env bash
# test-exec-bits.sh — every tracked script with a shebang MUST carry the git exec bit.
#
# git tracks the executable bit as part of a file's mode (100755 vs 100644),
# independent of the filesystem permissions on any given checkout. It rots
# silently two ways:
#   - a script is added on a checkout where core.fileMode is off (the default
#     on Windows) and lands in the index as 100644 even though it has a
#     shebang and is meant to run directly.
#   - a later `git checkout --` / clean self-heal restores files from the
#     index, silently re-stripping any bit that was only ever set on disk.
#
# Either way the failure stays invisible until something actually execs the
# file directly (a CI step, a git hook, `./script.sh`) and gets a bare
# "Permission denied" instead of a diff-time signal. Catch it here instead.
set -u
cd "$(dirname "$0")/.." || exit 2

bad=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  head -c2 "$f" 2>/dev/null | grep -q '#!' || continue      # only scripts with a shebang
  mode=$(git ls-files -s -- "$f" | awk '{print $1}')
  if [ "$mode" != "100755" ]; then
    echo "  MISSING GIT EXEC BIT  $f  (git mode $mode)"
    bad=$((bad + 1))
  fi
done < <(git ls-files -- '*.sh')

if [ "$bad" -gt 0 ]; then
  echo
  echo "$bad tracked script(s) with a shebang lack the git exec bit."
  echo "A 'git checkout' will re-strip any bit set only on disk. Fix with:"
  echo "  git update-index --chmod=+x <file>"
  exit 1
fi
echo "OK: every tracked shebang script carries the git exec bit"
exit 0
