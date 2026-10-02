# Releasing Roam

This guide owns package release preparation and publication. Use
[CONTRIBUTING.md](../CONTRIBUTING.md) for development and hooks,
[repository maintenance](repository-maintenance.md) for checkout/environment
checks, and [website maintenance](website-maintenance.md#publishing) for the
separate website deployment. A documentation-only change does not require a
package release.

## Version and release cadence

Package identity comes from `pyproject.toml` → `version`. Install instructions
have a separate authority: an available release, not merely the version under
development. The pin generator selects the highest `v*` tag; maintainers must
also verify that its package has actually been published before adopting those
pins. A tag can exist while its publication is waiting for approval or has
failed. The synchronization scripts separate identity from installation pins,
but the tag lookup alone is not a registry-availability check:

| Script                             | Owns                                                                                                                                                              |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/sync_surface_counts.py`   | Release **pins** — `roam-code==X`, `Cranot/roam-code@vX`, `action.yml`'s `version` input default, `server.json` (server + PyPI package pin), `docs/COMMANDS.md`, `docs/ci-integration.md`, the shipped CI templates, `templates/examples/`, landing-page version stamps. Plus all surface counts. |
| `dev/build_readme_counts.py`       | The `mcp-server-card.json` family (bundled + 3 public mirrors) and the marker-protected count blocks in README / CLAUDE / AGENTS / llms-install.                     |

Both run in dry-run/check mode as CI gates. Identity drift is checked against
`pyproject.toml`; install-pin drift is checked against the selected tag.
Repair surface pins with
`scripts/sync_surface_counts.py --write` and generated blocks/cards with
`dev/build_readme_counts.py --apply`. Regenerate the command index with
`scripts/build_commands_doc.py` when its registry changes.

If the highest tag is still awaiting publication, preserve the last verified
installation pins and report the synchronization gate as blocked. Do not make
the check green by advertising a package that users cannot install, changing a
tag, or bypassing a required reviewer. Complete the approved release workflow,
then synchronize and run `scripts/check_install_targets.py --network` before
landing the pin update. This is a temporary release hold, not a gate waiver.

Not every version literal is derived, and a find-replace across the repo is
wrong. Three other classes exist and each is deliberate:

- **Historical** — a record of what was measured or built at a past version:
  `CHANGELOG.md` and its rendered `changelog.html`, `benchmarks/cross-repo-l1/`,
  `templates/audit-report/sample-redacted.md`, and the narrative comments in
  `src/roam/plan/compiler.py`, `src/roam/plan/plan_cache.py`, `src/roam/verdict.py`.
  Rewriting one falsifies a result. Never sync these.
- **Deliberately lagging** — `.github/workflows/roam.yml` consumes this repo's
  *own published* action, so it can only pin a tag/SHA that already exists. It
  moves in a follow-up commit **after** the release tag is pushed.
- **Fixture / illustrative** — arbitrary version values in test fixtures and
  PEP 440 grammar examples. They carry no pin shape and nothing syncs them.

The exemption registry with a reason per entry is
`scripts/sync_surface_counts.py::_VERSION_PIN_EXEMPT`; the gate and both
controls are pinned by `tests/test_w1501_release_version_pins.py`.

**Workflow:**

1. Every PR / direct push lands under `[Unreleased]` in `CHANGELOG.md`.
2. A *release* is a deliberate event:
   1. bump `pyproject.toml`;
   2. rename `[Unreleased]` → `[X.Y.Z] — YYYY-MM-DD`, add a fresh empty
      `[Unreleased]` block;
   3. run `uv lock` so `uv.lock`'s own `roam-code` row follows, then refresh
      the locked editable environment with
      `uv sync --locked --no-default-groups --extra dev --group ci --python 3.12`.
      This must precede generators that read the live `roam surface` version;
      otherwise installed metadata can silently restore the previous identity;
   4. run `python scripts/sync_surface_counts.py --write` and
      `python dev/build_readme_counts.py --apply`, followed by
      `python scripts/build_commands_doc.py`; re-running their check modes
      must then be clean;
   5. run `python scripts/build_changelog_html.py --write` to re-render
      `changelog.html` from the new `CHANGELOG.md` section (authoring the
      section is the one genuinely manual step);
   6. follow the exact-commit checks below before tagging and publishing.
3. **After** the tag and package are published, rerun the surface-sync script
   so install examples follow the newly available release, and check the targets
   with `python scripts/check_install_targets.py --network`. Update the dormant
   `.github/workflows/roam.yml` reference to a reviewed release SHA in a follow-up
   commit when refreshing that example.
4. Aim for **weekly to bi-weekly** releases. Patches (`X.Y.Z`) for
   hotfixes only. Don't bump version per commit.

**SemVer interpretation here:**

| Bump        | Meaning                                                |
| ----------- | ------------------------------------------------------ |
| Major (`X`) | Breaking change to CLI / MCP API surface               |
| Minor (`Y`) | New commands, new MCP tools, new languages, schema     |
| Patch (`Z`) | Bug fixes, doc updates, internal cleanup, CI tweaks    |

## Publish the verified package

Use the locked development environment from the
[setup guide](../CONTRIBUTING.md#installation). The following shell examples
are for Bash with that environment active; on PowerShell, use the same Git
operations and verify the captured values before continuing.

PyPI publishes from a tag (`.github/workflows/publish.yml`). Run the exact
release gate before pushing the version-bump commit:

```bash
set -eu
release_status="$(git status --porcelain=v1 --untracked-files=all)"
test -z "$release_status"
git fsck --connectivity-only --no-dangling
python scripts/prepush_check.py --release
release_sha="$(git rev-parse HEAD)"
git push origin main
```

Keep long-running qualification clones outside directories subject to automatic
age-based cleanup. A local clone can retain old modification times on copied
Git objects; a cleanup service may remove them during the run even when the
checkout was just created. Verify Git connectivity before and after qualification,
and keep the source commit and final clean-tree check with the test receipt.
An interrupted or damaged clone does not qualify a release.

Wait for the commit CI run for `release_sha` to pass. Then prove local `HEAD`
and `origin/main` still name that exact reviewed commit, derive the version from
its committed `pyproject.toml`, and attach the annotated tag to the captured SHA:

```bash
set -eu
git fetch origin main
release_status="$(git status --porcelain=v1 --untracked-files=all)"
test -z "$release_status"
test "$(git rev-parse HEAD)" = "$release_sha"
test "$(git rev-parse origin/main)" = "$release_sha"
version="$(python -c 'import sys; toml = __import__("tomllib" if sys.version_info >= (3, 11) else "tomli"); print(toml.load(open("pyproject.toml", "rb"))["project"]["version"])')"
git tag -a "v${version}" "$release_sha" -m "roam-code ${version}"
git push origin "v${version}"
```

Do not create the tag from a dirty tree or before the commit CI run is green.

After publication:

1. Check the publication workflow on that exact tag and inspect the actual
   wheel and source archive, including package data and public resource loaders.
   Editable/source tests can pass while a required file is missing from a wheel.
2. Verify the published digests and provenance against the reviewed release
   source. Run fresh installed-package smoke checks without importing the checkout.
   Keep a known-bad artifact as a negative control when correcting packaging.
3. Refresh published installation examples, run the network install-target check,
   and qualify that follow-up commit separately. Update each installation and the
   website only when those surfaces are in scope; record their own identities.

Keep immutable releases and pushed history intact. Fix a published defect in a
new release; never move an existing release tag to a corrected tree. Store dated
logs, artifact receipts, source revisions, incomplete checks, and recovery paths
under ignored `internal/`, not in public documentation.

Container publication has its own [hold and opt-in](containers.md).
A skipped container job means not published. A package upload does not establish
image security, a public image, website deployment, or every supported transport.
