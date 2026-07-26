#!/usr/bin/env bash
# Pin every GitHub Action in .github/workflows/ and action.yml to a commit SHA.
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
# Run: bash dev/pin_github_actions.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# Resolve `org/repo@TAG` to its commit SHA via the GitHub API.
sha_for() {
  local repo="$1"
  local ref="$2"
  gh api "repos/${repo}/commits/${ref}" --jq '.sha'
}

# Include both workflow syntax (``- uses:``) and composite-action syntax
# (``uses:``). Extract the reference itself rather than a whitespace column:
# the old ``awk '{print $2}'`` read workflow lines as the literal ``uses:``.
shopt -s nullglob
files=(.github/workflows/*.yml .github/workflows/*.yaml action.yml)
mapfile -t refs < <(
  grep -hE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[a-zA-Z0-9_./-]+@[a-zA-Z0-9._/-]+' "${files[@]}" |
    sed -E 's|^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*([^[:space:]#]+).*|\2|' |
    sort -u
)

for ref in "${refs[@]}"; do
  # Skip already-pinned (40-char hex) and local action references.
  if [[ "$ref" =~ @[0-9a-f]{40}$ ]]; then continue; fi
  if [[ "$ref" == ./* ]]; then continue; fi

  repo="${ref%@*}"
  tag="${ref#*@}"
  sha=$(sha_for "$repo" "$tag")
  if [[ -z "$sha" ]]; then
    echo "warn: could not resolve $ref" >&2
    continue
  fi

  # Replace this complete owner/repo reference only. Replacing a bare ``@v4``
  # globally can pin unrelated actions to the wrong repository's commit.
  pinned="${repo}@${sha}  # ${tag}"
  escaped_ref=$(printf '%s\n' "${ref}" | sed 's/[][\.^$*]/\\&/g')
  echo "pin: $ref -> $pinned"
  for f in "${files[@]}"; do
    # macOS sed needs `-i ''`; GNU sed accepts `-i` alone. Detect.
    if sed --version >/dev/null 2>&1; then
      sed -i "s|${escaped_ref}|${pinned}|g" "$f"
    else
      sed -i '' "s|${escaped_ref}|${pinned}|g" "$f"
    fi
  done
done

echo
echo "Done. Review the diff and commit. Dependabot will propose SHA"
echo "bumps weekly via the existing github-actions schedule in"
echo ".github/dependabot.yml — version comments are preserved across"
echo "those bumps so the audit trail stays readable."
