# Network boundary

Roam's analysis engine is local: source parsing, indexing, graph queries,
findings, run ledgers, and evidence generation operate on the local working tree
and `.roam/` state. Roam does not automatically upload source code, telemetry,
or analysis results, and it does not run an automatic update check.

Some explicitly selected features do use the network. This page is the
authoritative inventory for the built-in CLI surface.

## External network paths

| Trigger | Direction and destination | Data crossing the boundary | Default |
|---|---|---|---|
| First grammar load with an empty `tree-sitter-language-pack` cache | Inbound from the dependency's GitHub release | Platform/package metadata and a checksum-verified parser bundle; no repository content | Automatic only when a requested parser is absent locally |
| `roam version --check` | Request/response with PyPI | Installed package identity and normal HTTP metadata; latest-version response | Off unless `--check` is passed |
| `roam metrics-push` without `--dry-run` | Outbound to `--endpoint` (Roam Cloud by default) | The allow-listed metrics payload, which can include numerical metrics, repository-relative paths or path hashes, and identifier names; never source bodies | Explicit command; inspect with `--dry-run` |
| `roam guard-pr --post-check` | Outbound to the GitHub Check Runs API | Commit/repository identifiers and the generated check-run verdict, summary, annotations, paths, and finding metadata | Off unless `--post-check` is passed |
| `roam pr-analyze --diff-from-pr URL` | Inbound through `gh pr diff` | Repository/PR identifiers in the request; PR diff returned to the local process | Off unless `--diff-from-pr` is passed |
| `roam pr-replay --github-reviews-gh OWNER/REPO#PR` | Inbound through `gh api` | Repository/PR identifiers in the request; review metadata returned locally. Review bodies are not retained in the evidence packet | Off unless `--github-reviews-gh` is passed |
| `roam stale-refs --check-external` or `check_external = true` in `.roam/stale-refs.toml` | Outbound HEAD/GET requests to URLs found in scanned files | Referenced URLs, normal HTTP metadata, and any operator-supplied `--external-auth-header`; no file bodies | Off unless enabled by flag or repository config |
| `roam cga emit --sign --keyless`, `roam index-export --sign --keyless`, or `roam pr-bundle emit --slsa-l3 --sign --keyless` | Outbound through Cosign to OIDC/Fulcio/Rekor services | OIDC identity flow plus artifact digests, signatures, certificates, and transparency-log evidence; not source bodies | Off unless keyless signing is selected |
| MCP tool summarization with `ROAM_AI_ENABLED=1` | MCP sampling request to the client-selected model/provider | Up to 60,000 characters of the structured Roam report plus a summarization instruction. Report fields can include repository-relative paths, symbols, findings, and source snippets produced by the selected tool | Off unless the environment opt-in is set. Supported compound tools summarize by default when `summarize` is omitted; pass `summarize=false` to keep a call local. Unsupported clients remain local |

Offline-key Cosign signing (`--sign --key PATH`) does not require the keyless
OIDC/Fulcio/Rekor path. Verification with a local public key and bundle is the
air-gap-friendly form; exact verification behavior also depends on the
installed Cosign version.

## Listeners and loopback transports

Roam opens no listener during ordinary CLI analysis. These explicit modes do:

| Trigger | Bind behavior | Purpose |
|---|---|---|
| `roam mcp --transport sse` or `streamable-http` | `--host` / `--port`; defaults to `127.0.0.1:8000` | Serve MCP over HTTP instead of stdio |
| `roam watch --webhook-port PORT` | `--webhook-host`; defaults to `127.0.0.1` | Accept re-index triggers; configure `--webhook-secret` when exposed |
| `roam compile-daemon start` and installed compile hooks | Loopback only (`127.0.0.1`) with a per-repository token | Reuse a warm local process for compile requests |

Binding `mcp` or `watch` to a non-loopback address is an operator decision and
changes the exposure boundary. Apply host firewalling, authentication, and an
appropriate reverse proxy before doing so.

## Air-gapped operation

1. Install the wheel and its dependencies from an approved local mirror or
   wheelhouse.
2. While egress is still available, run `roam index --force` once for every
   platform that will execute Roam. The retained parser bundle is reused after
   the cache is warm.
   Hosted operators can instead point `ROAM_TREE_SITTER_CACHE_DIR` at an
   attested root-owned grammar-pack `libs` directory and set
   `ROAM_TREE_SITTER_CACHE_SEALED=1`. Sealed mode rejects a missing grammar
   before the package's acquisition path; it never tries to repair the
   generation over the network.
3. Avoid the external triggers above and leave `ROAM_AI_ENABLED` unset. Use file/fixture inputs for PR diffs,
   reviews, vulnerability reports, and runtime traces; use offline-key Cosign
   signing where signatures are required.
4. Remember that `roam verify` and repository hooks may execute the project's
   own test, lint, or build commands. Roam cannot constrain network behavior of
   those child tools; enforce the air gap at the host/container boundary.

Package installation, dependency mirrors, generated CI workflows, and tools
that an operator launches separately (for example `pip-audit`, `npm audit`, or
artifact-upload actions) have their own network behavior and are outside the
built-in CLI inventory above.
