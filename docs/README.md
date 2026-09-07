# Documentation map

Start with the [project README](../README.md) for installation and the product
overview. The user guides are maintained in this repository under
`templates/distribution/landing-page/docs/` and published to
[roam-code.com/docs](https://roam-code.com/docs/). Editing that directory updates
the next website deployment; a Git push alone does not publish the site.

## Choose a guide

| Need | Maintained source |
| --- | --- |
| Understand what Roam is, how to speak about it, and how an agent should trust its output | [Understanding roam-code](understanding-roam.md) |
| Install and run the first analysis | [Getting started](../templates/distribution/landing-page/docs/getting-started.html) |
| Run Roam in Docker | [Containers, mount permissions and release evidence](containers.md) |
| Find a command or workflow | [User command reference](../templates/distribution/landing-page/docs/command-reference.html), [complete command index](COMMANDS.md) |
| Connect an MCP client | [MCP usage](../templates/distribution/landing-page/docs/mcp-usage.html), [tool inventory](mcp-tools.md) |
| Check MCP protocol support | [Protocol compatibility and handshake tests](mcp-protocol-compatibility.md) |
| Make bounded agent CLI calls | [Agent CLI guide](agent-cli.md) |
| Understand the index and evidence pipeline | [Architecture](../templates/distribution/landing-page/docs/architecture.html), [agent contract](../templates/distribution/landing-page/docs/agent-contract.html) |
| Diagnose an installation, index, or lock | [Troubleshooting](../templates/distribution/landing-page/docs/troubleshooting.html) |
| Maintain this checkout and validate a change | [Repository maintenance](repository-maintenance.md), [contributing](../CONTRIBUTING.md) |
| Edit and publish the homepage | [Website maintenance](website-maintenance.md) |
| Prepare and publish a package release | [Release guide](releases.md) |
| Integrate CI and SARIF | [CI integration](ci-integration.md) |
| Understand data leaving the machine | [Network boundary](network-boundary.md) |
| Interpret caller counts | [Caller metrics](concepts/caller-metrics.md) |
| Evaluate findings and avoid overclaiming | [Detector evidence and limitations](concepts/detector-evidence.md) |
| Verify a repair, proof bundle, or benchmark claim | [Verification evidence and accounting](concepts/verification-evidence.md) |
| Read a historical installation example | [Fresh-install smoke transcript](fresh-install-smoke.md) |
| Review changes by release | [Changelog](../CHANGELOG.md) |

Examples, benchmark results, and smoke transcripts describe their stated
fixture, version, and measurement date. They are not assertions about today's
repository size or performance. Read the current generated inventory for the
shipped command surface and use `roam <command> --help` for exact flags.

## Documentation authorities

| Content | Authority | Regenerate |
| --- | --- | --- |
| Package version | `pyproject.toml` | Change only for a release, then follow [the release guide](releases.md) |
| Surface counts and release pins | CLI/MCP registries; package identity from `pyproject.toml`, install pins from the highest published `v*` tag | `uv run --no-sync python scripts/sync_surface_counts.py --write` |
| Count blocks, MCP reference, and server cards | Source tool registrations and docstrings | `uv run --no-sync python dev/build_readme_counts.py --apply` |
| Complete command index | `roam surface --json` | `uv run --no-sync python scripts/build_commands_doc.py` |
| Website changelog | `CHANGELOG.md` | `uv run --no-sync python scripts/build_changelog_html.py --write` |
| User guides and troubleshooting | Implemented behavior and executable CLI help | Edit the source guide and verify examples and links |

Keep generated regions generated. Correct a tool's description in its source
docstring before rebuilding the MCP reference. Historical release notes and
measured transcripts retain their original values. Private planning, audit
output, and session notes belong under the ignored `internal/` directory.

See the [documentation checks](repository-maintenance.md#documentation-checks)
for the checks to run before submitting updates.

## Research, experiments, and older material

These documents answer different questions from the current user guides:

| Document | How to use it |
| --- | --- |
| [Algorithm research](algo-polish-research.md) | Historical design ideas, with a current routing note. Recheck each proposed task against implementation before scheduling it. |
| [Sibling patch experiment](sibling-patch-network-v1.md) | Default-off experimental behavior and its trust limits; not a general repair guarantee or an enabled integration. |
| [Legacy audit template](audit_report_template.md) | Superseded example material, not a current command/output contract. |
| [Fresh-install transcript](fresh-install-smoke.md) | The stated historical fixture and version, not a live installation or performance claim. |

## Keeping the docs useful

Give each procedure one maintained home and link to it from other entry points.
When merging guides, preserve existing links and anchors or leave a short pointer.
Keep unique evidence in a dated record; mark what supersedes it instead of
silently rewriting old results. A current handoff should state what is done,
what remains uncertain, and the next bounded task with an acceptance check.

Use public GitHub issues for the community queue. Keep local priorities, raw
reports, release receipts, and private project details under `internal/`; that
folder's catalogue is navigation, not proof that every old proposal is active.
