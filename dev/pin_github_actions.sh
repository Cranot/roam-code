#!/usr/bin/env bash
# Pin every GitHub Action reference in repository YAML files to a commit SHA.
#
# Why: floating major tags (@v4, @v5, @v3) let an action publisher
# silently move HEAD — exactly the supply-chain takeover risk that
# audit R14 (security review) called out. Pinning to a commit SHA
# means the action runs the bytes you reviewed, even if the upstream
# tag is moved.
#
# Output format: `uses: org/repo@<40-char-sha>  # v4` so Dependabot
# (which is already configured for github-actions in dependabot.yml)
# picks up the version comment and proposes SHA bumps weekly.
#
# Requires: gh CLI authenticated.
#
# Run: bash dev/pin_github_actions.sh [--check]

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--check" ]]; then
  check_only=true
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
else
  check_only=false
fi

# Any YAML file can be used as a workflow template, and composite actions are
# named action.yml/action.yaml anywhere in the tree. Enumerate all YAML files
# so new workflows and templates are covered without updating this script.
#
# Enumerated from git's index rather than a filesystem walk, and the difference
# is load-bearing: `find` also descends into gitignored trees. This repo checks
# out third-party repositories under bench-repos/ as benchmark fixtures, and
# those carry hundreds of deliberately-unpinned workflows that are not part of
# this repo's supply chain at all. Walking the filesystem reported every one of
# them and would have left the gate permanently red. Tracked files are also the
# correct scope on the merits: only what this repo ships can be pushed. Staged
# additions are included, since `git ls-files` reads the index.
mapfile -t files < <(
  if git rev-parse --git-dir >/dev/null 2>&1; then
    git ls-files -- '*.yml' '*.yaml' | sed 's|^|./|'
  else
    find . -path './.git' -prune -o -type f \( -name '*.yml' -o -name '*.yaml' \) -print
  fi | sort
)

# Sentinel ref emitted on the SAME tab-separated stream for a file the scan
# could not read. It cannot collide with a real ref: a real ref always
# contains '@' and never a space-free literal of this shape.
UNREADABLE_REF='@@UNREADABLE@@'

scan_refs() {
  local file relative line line_number ref
  local uses_re='^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*([^[:space:]#]+)'

  for file in "${files[@]}"; do
    relative="${file#./}"
    # A file tracked in git's index but absent from (or unreadable on) disk
    # used to abort the whole scan. `done < "$file"` returns non-zero, `set -e`
    # kills the process-substitution SUBSHELL, and every LATER file is skipped
    # silently -- the consumer just sees EOF, `unpinned` stays 0, and the gate
    # exits 0. Measured: with two tracked workflows both carrying floating
    # refs, deleting the alphabetically-FIRST one made the gate report nothing
    # and exit 0; deleting the LAST one reported the first and exited 1. The
    # right answer was reachable only by luck of ordering.
    if [[ ! -r "$file" ]]; then
      printf '%s\t%s\t%s\n' "$relative" "0" "$UNREADABLE_REF"
      continue
    fi
    line_number=0
    # Belt and braces: even a readable-at-stat file can fail to open (race,
    # permissions, a directory). The redirect must not be able to kill the
    # subshell and take every later file with it.
    {
      while IFS= read -r line || [[ -n "$line" ]]; do
        line_number=$((line_number + 1))
        if [[ "$line" =~ $uses_re ]]; then
          ref="${BASH_REMATCH[2]}"
          [[ "$ref" == *@* ]] || continue
          printf '%s\t%s\t%s\n' "$relative" "$line_number" "$ref"
        fi
      done < "$file"
    } || printf '%s\t%s\t%s\n' "$relative" "0" "$UNREADABLE_REF"
  done
}

if [[ "$check_only" == true ]]; then
  unpinned=0
  scan_incomplete=0
  scanned=0
  unreadable_files=()
  while IFS=$'\t' read -r file line ref; do
    if [[ "$ref" == "$UNREADABLE_REF" ]]; then
      scan_incomplete=1
      unreadable_files+=("$file")
      continue
    fi
    [[ "$ref" =~ @[0-9a-fA-F]{40}$ ]] && continue
    [[ "$ref" == ./* ]] && continue
    printf '%s:%s: uses: %s\n' "$file" "$line" "$ref"
    unpinned=1
  done < <(scan_refs)

  # Publish the denominator. A gate that reports on some of its input and
  # exits 0 is indistinguishable from one that read all of it and found
  # nothing -- and only CLEAN authorizes (see exit_codes.gate_should_fail:
  # CLEAN / VIOLATION / UNANALYZABLE).
  scanned=$(( ${#files[@]} - ${#unreadable_files[@]} ))
  printf 'scanned %s/%s tracked YAML files\n' "$scanned" "${#files[@]}"
  if (( scan_incomplete )); then
    printf 'UNANALYZABLE: %s tracked file(s) could not be read; their refs were NOT checked:\n' \
      "${#unreadable_files[@]}" >&2
    for f in "${unreadable_files[@]}"; do
      printf '  - %s\n' "$f" >&2
    done
  fi
  if (( unpinned || scan_incomplete )); then
    exit 1
  fi
  exit 0
fi

# Resolve `org/repo@TAG` to its commit SHA via the GitHub API.
sha_for() {
  local repo="$1"
  local ref="$2"
  gh api "repos/${repo}/commits/${ref}" --jq '.sha'
}

mapfile -t refs < <(scan_refs | cut -f3 | awk -v u="$UNREADABLE_REF" '$0 != u' | sort -u)

unresolved=0
for ref in "${refs[@]}"; do
  # Skip already-pinned (40-char hex) and local action references.
  if [[ "$ref" =~ @[0-9a-fA-F]{40}$ ]]; then continue; fi
  if [[ "$ref" == ./* ]]; then continue; fi

  repo="${ref%@*}"
  tag="${ref#*@}"
  # `|| true` so the intended warn-and-continue branch below is REACHABLE.
  # Under `set -e` a failing `sha_for` killed the script outright, which made
  # the `if [[ -z "$sha" ]]` branch dead code that had never once run.
  sha=$(sha_for "$repo" "$tag" || true)
  if [[ -z "$sha" ]]; then
    echo "warn: could not resolve $ref" >&2
    unresolved=$((unresolved + 1))
    continue
  fi

  # Replace this complete owner/repo reference only. Replacing a bare ``@v4``
  # globally can pin unrelated actions to the wrong repository's commit.
  pinned="${repo}@${sha}  # ${tag}"
  escaped_ref=$(printf '%s\n' "${ref}" | sed 's/[][\.^$*]/\\&/g')
  echo "pin: $ref -> $pinned"
  for f in "${files[@]}"; do
    # A tracked-but-absent file is a NORMAL state in the write path: mid
    # `git mv`, a sparse checkout, a staged-but-uncommitted `git rm`. Skip it
    # and keep pinning. Only --check (the gate) refuses.
    [[ -w "$f" ]] || continue
    # macOS sed needs `-i ''`; GNU sed accepts `-i` alone. Detect.
    if sed --version >/dev/null 2>&1; then
      sed -i "s|${escaped_ref}|${pinned}|g" "$f"
    else
      sed -i '' "s|${escaped_ref}|${pinned}|g" "$f"
    fi
  done
done

echo
if (( unresolved > 0 )); then
  # Never print "Done." over a ref that stayed floating.
  echo "INCOMPLETE: ${unresolved} ref(s) could not be resolved and are STILL"
  echo "floating. Re-run after fixing gh auth / network, then re-check with"
  echo "  bash dev/pin_github_actions.sh --check"
  exit 1
fi
echo "Done. Review the diff and commit. Dependabot will propose SHA"
echo "bumps weekly via the existing github-actions schedule in"
echo ".github/dependabot.yml — version comments are preserved across"
echo "those bumps so the audit trail stays readable."
