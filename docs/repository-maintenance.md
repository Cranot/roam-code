# Repository maintenance

This guide is for a roam-code source checkout. Use the
[getting-started guide](../templates/distribution/landing-page/docs/getting-started.html)
when installing Roam to analyze another project. Run the commands below from
the repository root.

## Establish the checkout state

```bash
git status --short --branch
git branch -vv
git worktree list
git stash list
git remote -v
git fetch origin --prune
git rev-list --left-right --count HEAD...origin/main
```

The final pair is local-only commits followed by remote-only commits; `0 0`
means the two histories agree. Fetch updates remote-tracking refs without
changing working files. Compare branch tips before merging, and inspect each
worktree and stash before retiring it. Untracked files and ignored project
state are not protected by a normal Git commit.

For a clean checkout that is only behind its upstream, `git pull --ff-only`
advances without creating a merge commit. Divergent histories require review
of the actual changes. Preserve unique work with a commit or backup before
cleanup. A local branch being merged says nothing about uncommitted content
in another worktree.

## Use the locked environment

```bash
uv sync --locked --no-default-groups --extra dev --group ci --python 3.12
uv lock --check
uv run --no-sync python --version
uv run --no-sync python -m pip check
uv run --no-sync roam --version
```

This installs the checkout in editable mode with development, MCP, and CI
documentation dependencies. CI tests the supported Python matrix; Python 3.12
is the standard local choice. The setup-uv steps in
[roam-ci.yml](../.github/workflows/roam-ci.yml) pin the CI uv version.

Use `uv run --no-sync` for commands after syncing. A global `python`, `pytest`,
or `roam` may belong to another interpreter or installed release. On Windows,
`Get-Command python, roam` shows PATH resolution; on a POSIX shell, use
`command -v python roam`.

If package metadata reports duplicate versions, or a native parser fails after
an update, stop the MCP server or watcher using this checkout before repairing
the package. Windows keeps loaded native libraries open. Reinstall through the
same locked dependency selection:

```bash
uv sync --locked --no-default-groups --extra dev --group ci --python 3.12 --reinstall-package tree-sitter-language-pack
uv run --no-sync python -m pip check
uv run --no-sync roam doctor
```

If the environment remains inconsistent, recreate the virtual environment from
the lockfile after preserving anything locally installed that you need. Avoid
mixing manual package-file deletion with an active process using that install.

## Refresh the index

```bash
uv run --no-sync roam index
uv run --no-sync roam doctor
uv run --no-sync roam health --explain
```

`roam init` is first-run bootstrap: it creates configuration and a starter
ignore file as well as the index. `roam index` refreshes an existing checkout
without requesting new CI configuration.

Initialization requires a valid Git marker at the resolved project root, not
merely a file or directory named `.git`. Running from a nested directory uses
the validated repository root; linked Git worktrees are supported. An invalid
root is refused before bootstrap writes. In JSON mode, that refusal exits 2
with `error_code: "FILE_NOT_FOUND"`, `summary.state: "not_initialized"`, and an
empty `created` list. Check the intended checkout before running `git init`;
an empty `.git` directory does not establish a repository.

Normal indexing reuses unchanged source data, reprocesses changed files and
affected neighbors, and refreshes Git metadata. A commit can change `HEAD`
without changing any file contents. The full index path still checks Git
history and records a manifest in that case. Skipping Git collection requires
both an unchanged manifest HEAD and that exact commit in the indexed history.

Use `uv run --no-sync roam index --force` when a full rebuild is needed. It
rebuilds derived index data and forces Git-stat collection within the configured
history window. It does not erase the configured history limit or bypass a
live writer lock. Internal light indexing can omit expensive Git analysis;
a full `roam index` is the appropriate baseline for history-sensitive work.

Manifest configuration identity records persistent settings. Invocation options
such as a forced rebuild are recorded separately, so running with `--force`
does not itself imply configuration drift. Differences between successive
manifests may still reflect real edits or environment changes.

Doctor's index-manifest-history advisory compares successive runs. A `git_head`
difference can simply record an expected commit advance; inspect the named
fields and the current freshness checks before treating history drift as an
indexing failure. Re-indexing solely to erase that comparison is not a repair.

## Recover from a writer or interrupted index

`ROAM_DB_DIR` and the `db_dir` project setting keep the database and its
`index.lock` / `index.state` control files together. They do not relocate project
configuration, agent ledgers, or response evidence. Stop older indexers before
upgrading or moving an existing store; mixed-version writers are not supported.
For an explicit build policy, set `ROAM_NO_AUTO_INDEX=1`: analysis refuses a
missing or incomplete index with exit 3, while `roam index` and `roam init`
remain intentional build commands. JSON refusals distinguish those states
without opening the index. `init` still creates project configuration.

An ownership error means another writer is active, or Roam cannot prove that
the recorded owner is stale. Wait for the writer, inspect the process that
owns this checkout, and stop it through its normal shutdown path if needed.
Preserve the lock and lifecycle marker while investigating. The indexer can
recover a proven abandoned generation and require a full rebuild; removing a
lock by hand defeats the ownership check.

For SQLite `database is locked`, close competing writers and check whether a
file-sync client is touching the database. Keep the repository or database
directory on a local, writable filesystem outside cloud-sync roots. Retry
`roam index` once the writer has released the database.

`.roam/` also stores rules, annotations, memory, signed ledgers, keys, and proof
bundles. Preserve that project state before any directory-level reset. A
rebuildable database does not make the whole directory disposable.

## Read health results correctly

| Check | What its result establishes |
| --- | --- |
| `roam doctor` | Installation, environment, index, and cache checks; advisory-only results exit 0, blocking failures exit 2 |
| `roam doctor --strict` | The same checks, with advisory failures promoted to exit 2 |
| `roam db-check --ci` | Index consistency checks; failed checks or high-severity findings exit 5, while medium findings remain review advisories |
| `roam syntax-check src/roam/db/connection.py` | Tree-sitter syntax checking of the named file; this is not compiler validation or proof of complete symbol extraction |
| `roam health --explain` | An architectural score and its component contributions; it does not run the test suite |
| `roam health --gate` | The configured architectural quality gate; a failed gate exits 5 |
| Relevant pytest suites | Executable behavior covered by those tests |
| `scripts/prepush_check.py --full` | The FAST structural checks plus additional documentation checks |
| `scripts/prepush_check.py --release` | The FULL tier plus the non-slow suite and release checks; read its printed coverage limits |

`--workers N` bounds both structural tests and the complete release suite to
1-4 workers. Structural bundles use `loadfile`; the release suite uses
`loadgroup` to preserve grouped-test isolation. Explicit arguments keep the
budget stable whether or not `CI` or `ROAM_XDIST_WORKERS` is set. A single
worker still runs through xdist; use `pytest -n 0` for direct serial debugging.

The reference workflow [.github/workflows/roam.yml](../.github/workflows/roam.yml)
is manually triggered and deliberately differs from an active generated
workflow. Doctor can flag that as template drift. Review that advisory against
the file's stated purpose before regenerating it. Actual repository CI is
[roam-ci.yml](../.github/workflows/roam-ci.yml).

Architectural findings are candidates for investigation. Check the named symbol,
edge evidence, metric definition, and scope before refactoring. A low score, a
parser warning, and a failing test describe different conditions.

A zero-symbol-file advisory does not by itself establish a parser failure.
Package markers, import-only entry points, markup, and styles can legitimately
contribute no named symbols. Inspect the reported files before classifying the
index as broken. For syntax checks, pass actual file paths or `--changed`;
a result with zero checked files establishes no source validation.

## Documentation checks

```bash
uv run --no-sync python scripts/sync_surface_counts.py
uv run --no-sync python dev/build_readme_counts.py --check
uv run --no-sync python scripts/build_commands_doc.py --check
uv run --no-sync python scripts/build_changelog_html.py
uv run --no-sync python scripts/check_install_targets.py
uv run --no-sync python scripts/linkcheck.py --strict
uv run --no-sync pytest tests/test_doc_consistency.py tests/test_commands_doc_synced.py -n 0
```

Use the [documentation map](README.md#documentation-authorities) for each
generator's write mode. The HTML link checker validates local pages and anchors;
external destination availability is a separate check. Verify new examples
against `roam <command> --help`, and distinguish generated references from
handwritten tutorials.

`roam --json doc-drift` is an additional Markdown audit. It checks recognized
path, count, and project-version claims against mechanical authorities. Paths
use repository-root precedence, with an existing document-relative sibling as
a fallback; verified path findings include the resolved repository path. Inspect
`status`, `reason`, and `metric_definition` for each result. Source-language
counts measure languages present in the index, not supported parser languages;
examples and scoped populations cannot be verified by whole-index totals.
Explicitly scoped examples, inline code counts, and unsupported modifiers are
disclosed as unverifiable. Prose scope recognition remains heuristic, and bare
historical or contextual counts can still need manual review.

`doc-drift --ci` fails on objective drift and refuses a run that scanned no
Markdown documents. An exit 0 can still include unverifiable claims and
`partial_success: true`; it does not certify all prose as current. Use the
registry-derived count gates above for the public product surface.

## Finish a change

For a field report, reproduce each claim you intend to fix before editing.
Use small positive and negative fixtures: the false positive must disappear
while a genuine instance remains detectable. Then invoke the affected CLI
commands on this repository as a larger integration check. Save exact arguments,
stdout/stderr, exit status, timing, source revision, and index state under
`internal/dogfood/`. Keep third-party source and identifying report details
private; public regressions should use generic fixtures.

Record timeouts, missing prerequisites, unsupported scopes, and heuristic
disagreements separately. A help invocation proves syntax availability, not a
successful analysis. Do not bulk-apply detector recommendations or invoke write,
publish, export, or network commands merely to increase command coverage.
Read [detector evidence](concepts/detector-evidence.md) for cross-command metric
differences and the remaining static-analysis limits.

Run focused tests and the gate appropriate to the change, review `git diff`,
and update the Unreleased changelog for user-visible behavior. A full local
non-slow run can take hours, particularly on Windows. Use a persistent
terminal or a detached process with saved output and an explicit completion
status for long runs.

At handoff, separate the source revision and worktree changes from index state,
test evidence, remote synchronization, and deployment state. Keep a dated private
note linking the detailed audit and exact result files; record pending checks
and unresolved findings without promoting them to completed work. Regenerate
the private catalogue with `uv run --no-sync python dev/build_internal_index.py --write`
after adding or changing notes. Preserve historical measurements as historical;
do not replace earlier results with current counts or sum overlapping test runs.

Before a release, wait for all required CI jobs on the exact pushed commit.
A successful FAST or FULL gate is not evidence that the entire test matrix
passed. Record which checks completed and which remain pending. Follow
[CONTRIBUTING.md](../CONTRIBUTING.md#deploys) for tagging and website publishing.

The [container release path](containers.md) is held by default and runs only
with explicit `ROAM_CONTAINER_PUBLISH=true`, after package/evidence verification.
Keep that opt-in disabled pending image-wide security review; package and site
releases can proceed independently. A skipped container job means unpublished.
Check its exact digest, signature and anonymous pull as separate
release evidence; a green PyPI upload does not prove a public image exists.
For issue triage, reproduce protocol claims against the installed advertised
revision ([MCP compatibility](mcp-protocol-compatibility.md)) and CLI examples
against current help ([agent CLI guide](agent-cli.md)). Reply with what was
measured, the release containing a fix, and any remaining scope; leave broader
requests open when only part is addressed.
