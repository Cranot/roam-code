#!/usr/bin/env sh
#
# Git ``post-merge`` hook — runs ``roam stale-refs --diff`` after every
# ``git pull`` / ``git merge`` so the developer learns about doc
# breakage their teammate's PR introduced *before* it ambushes them
# in a half-written commit.
#
# Wire-in:
#   cp templates/examples/post-merge-stale-refs.sh .git/hooks/post-merge
#   chmod +x .git/hooks/post-merge
#
# Or add it to a husky / lefthook setup pointing at this script.
#
# Behaviour:
# * Runs ``roam stale-refs --diff HEAD@{1}`` to get refs that NEWLY
#   broke between the previous HEAD and the merged HEAD.
# * Prints a one-line verdict; never blocks, never fails the merge.
# * Exits 0 unconditionally — diagnostic-only.
# * Skips silently if ``roam`` isn't on PATH (so cloned repos without
#   the dev tooling don't error out for new contributors).

if ! command -v roam >/dev/null 2>&1; then
  exit 0
fi

# Compare the merged HEAD against the pre-merge HEAD. ``HEAD@{1}`` is
# the previous tip; ``--diff`` filters to refs new in this merge.
echo "==> roam stale-refs (post-merge) — checking for new dangling refs"
roam stale-refs --diff HEAD@{1} || true

exit 0
