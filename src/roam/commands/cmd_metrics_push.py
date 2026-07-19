"""``roam metrics-push`` — Cloud Lite metrics-only push.

Sends a *summary-only* payload from ``roam audit --json`` to a Roam
Cloud Lite endpoint. The payload contains numerical metrics, file paths
(or path hashes when ``--anonymize``), and identifier names — **no
source-code bodies are transmitted**.

This is the CLI engine behind Roam Cloud Lite — the v2 metrics-history
SaaS. The receiving API is hosted at ``api.roam.cloud`` (or wherever
the user configures); the dashboard at ``roam.cloud`` reads from the
same store. ``--dry-run`` prints the payload without posting so users
and CI pipelines can inspect what would leave their machine before
opting in.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because metrics-push is an external-service report transmitter
— it ships an aggregate-metrics payload over HTTP to a Cloud Lite
endpoint with no per-location violations to surface to a SARIF
consumer. The transport semantics (POST status, anonymization mode,
dry-run preview) are environment-scoped, not source-coordinate data.
See ``cmd_doctor`` for the parallel environment-scoped disclosure
pattern (W1085 / W1144) + W1221-audit memo.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json as _json
import urllib.error
import urllib.request
from pathlib import Path

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.git_helpers import detect_roam_version, git_metadata, utc_timestamp
from roam.commands.resolve import ensure_index
from roam.output.formatter import WarningsOut, json_envelope, to_json

DEFAULT_ENDPOINT = "https://api.roam.cloud/v1/metrics"
USER_AGENT = "roam-code-metrics-push"
HTTP_TIMEOUT = 15
DEFAULT_LAST_PR_PATH = Path(".roam") / "last-pr-analysis.json"

# Backward-compatible alias for any external test harness still importing the
# private name.
_git_metadata = git_metadata


# ---------------------------------------------------------- audit + git helpers ---


def _capture_audit() -> dict:
    """Invoke ``roam audit`` in-process and return the JSON envelope.

    On failure, returns an envelope shape carrying ``error`` +
    ``exit_code``. Callers MUST check ``audit_envelope.get("error")``
    before extracting metrics — otherwise the silent-defaults in
    :func:`_extract_metrics` (None for scalars, 0 for counters)
    masquerade as real measurements and get pushed to Cloud Lite.
    See the "audit_ok" gate in :func:`metrics_push`.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "audit"])
    try:
        return _json.loads(result.output)
    except Exception as exc:  # noqa: BLE001 — pr-prep-style defensive
        return {"error": f"roam audit failed: {exc}", "exit_code": result.exit_code}


def _infer_repo_id(git_meta: dict, repo_override: str | None) -> str:
    """Derive a stable repo identifier from --repo or the git origin URL."""
    if repo_override:
        return repo_override
    origin = git_meta.get("git_origin", "")
    if not origin:
        return "<unknown>"
    # Normalise common origin shapes — git@github.com:org/repo.git, https://github.com/org/repo.git
    cleaned = origin
    if cleaned.startswith("git@"):
        # git@github.com:org/repo.git -> github.com/org/repo
        cleaned = cleaned.replace(":", "/").replace("git@", "")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if cleaned.startswith("https://"):
        cleaned = cleaned[len("https://") :]
    if cleaned.startswith("http://"):
        cleaned = cleaned[len("http://") :]
    return cleaned


def _path_hash(path: str) -> str:
    """SHA-256 prefix of a file path, for anonymized payloads."""
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


# ----------------------------------------------------------- payload assembly ---


def _load_last_pr_analysis(
    path: Path | None = None,
    *,
    warnings_out: WarningsOut = None,
) -> dict | None:
    """Load `.roam/last-pr-analysis.json` if it exists; return None on miss / read failure.

    The presence of a recent pr-analyze envelope means a Cloud Lite dashboard
    can show "last PR verdict" alongside the trend metrics — without needing
    a separate API call.

    W602: mirrors the W598 ``_load_cache`` plumb — when *warnings_out* is
    supplied, every silent-error site appends one structured closed-enum
    marker so callers can tell "envelope file not on disk" (legitimate
    no-pr-analyze-yet sentinel — does NOT warn, mirrors W597's
    ``daemon_running`` missing-pidfile and W598's ``_load_cache``
    cold-cache discipline) from "file on disk but unreadable" from "JSON
    parsed but top-level not a dict". The ``None`` return on every drop
    path is PRESERVED — None is the caller contract (it means "no last-PR
    block, skip enrichment"). ``warnings_out=None`` (default) preserves
    the pre-W602 silent-drop behaviour.

    Intentional-absence decision (W978 + "Make fallback chains loud"):
    a missing ``.roam/last-pr-analysis.json`` is the common, expected
    path before ``roam pr-analyze`` has ever been run on the repo.
    Warning on every cold call would train operators to ignore real
    warnings — mirrors W598's ``_load_cache`` cold-cache discipline.

    Emitted kinds (closed enum):

      * ``metrics_push_last_pr_read_failed:<path>:<exc_class>:<detail>``
        — ``Path.read_text`` raised ``OSError`` (typically
        ``PermissionError`` / ``IsADirectoryError`` / generic
        ``OSError``). The envelope file is on disk but unreadable.
      * ``metrics_push_last_pr_corrupt:<path>:JSONDecodeError`` — the
        bytes parsed as something other than JSON.
      * ``metrics_push_last_pr_corrupt:<path>:NotAJsonObject`` — JSON
        parsed cleanly but the top-level value was not a dict
        (downstream ``_build_last_pr_block`` indexes ``.get("summary")``
        / ``.get("_meta")`` / ``.get("audit_trail")`` — a non-dict
        payload would crash there).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    p = path or DEFAULT_LAST_PR_PATH
    if not p.exists():
        # Legitimate no-pr-analyze-yet sentinel — do NOT warn (mirrors
        # W598 ``_load_cache`` cold-cache discipline).
        return None
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        _emit(f"metrics_push_last_pr_read_failed:{p}:{type(exc).__name__}:{exc}")
        return None
    except _json.JSONDecodeError:
        _emit(f"metrics_push_last_pr_corrupt:{p}:JSONDecodeError")
        return None
    if not isinstance(raw, dict):
        _emit(f"metrics_push_last_pr_corrupt:{p}:NotAJsonObject")
        return None
    return raw


def _extract_metrics(audit_envelope: dict) -> dict:
    """Pull the allow-listed numeric metrics out of an audit envelope.

    Source-code bodies are NEVER included — only numbers, bucket counts,
    aggregate scores. The receiving API rejects any payload containing
    keys outside this allow-listed schema.
    """
    summary = audit_envelope.get("summary") or {}
    sections = audit_envelope.get("sections") or {}
    health_summary = (sections.get("health") or {}).get("summary") or {}
    debt_summary = (sections.get("debt") or {}).get("summary") or {}
    dead_summary = (sections.get("dead") or {}).get("summary") or {}
    pyramid_summary = (sections.get("test_pyramid") or {}).get("summary") or {}
    danger_summary = (sections.get("hotspots_danger") or {}).get("summary") or {}

    return {
        "health_score": health_summary.get("health_score") or summary.get("health_score"),
        "debt_total_minutes": debt_summary.get("total_remediation_minutes") or debt_summary.get("total_minutes"),
        "debt_total_hours": debt_summary.get("total_remediation_hours"),
        "dead_safe": dead_summary.get("safe", 0),
        "dead_review": dead_summary.get("review", 0),
        "dead_intentional": dead_summary.get("intentional", 0),
        "dead_test_only": dead_summary.get("test_only", 0),
        "dead_total_loc": dead_summary.get("total_dead_loc", 0),
        "danger_zone_count": danger_summary.get("count", 0),
        "test_pyramid": {
            "total": pyramid_summary.get("total", 0),
            "unit": pyramid_summary.get("unit", 0),
            "integration": pyramid_summary.get("integration", 0),
            "e2e": pyramid_summary.get("e2e", 0),
            "smoke": pyramid_summary.get("smoke", 0),
            "unknown": pyramid_summary.get("unknown", 0),
        },
        "imported_coverage_pct": health_summary.get("imported_coverage_pct"),
        "api_surface": summary.get("api_surface") or audit_envelope.get("api_count"),
        "file_total": summary.get("file_total"),
        "symbol_total": summary.get("symbol_total"),
        "actionable_cycles": health_summary.get("actionable_cycles"),
        "tangle_ratio": health_summary.get("tangle_ratio"),
    }


def _extract_hotspots(audit_envelope: dict, *, anonymize: bool, limit: int = 10) -> list[dict]:
    """Pull the top-N danger-zone rows. Path is hashed under anonymize."""
    danger_section = (audit_envelope.get("sections") or {}).get("hotspots_danger") or {}
    danger_zone = danger_section.get("danger_zone") or []
    out: list[dict] = []
    for row in danger_zone[:limit]:
        path = row.get("path", "")
        entry = {
            "danger_score": row.get("danger_score"),
            "churn": row.get("churn"),
            "complexity": row.get("complexity"),
            "max_fan_in": row.get("max_fan_in"),
        }
        if anonymize:
            entry["path_hash"] = _path_hash(path) if path else None
        else:
            entry["path"] = path
        out.append(entry)
    return out


def _build_last_pr_block(
    last_pr_envelope: dict,
    *,
    warnings_out: WarningsOut = None,
) -> dict:
    """Compose the last_pr_analysis block from a saved pr-analyze envelope.

    Folds in only summary numerics + verdict + primary language + timestamp.
    Computes ``age_days`` + ``stale`` (>7 days) so dashboards can grey
    stale entries without needing to compute age client-side.

    A7 (C.1.ddd): if the saved envelope contains an ``audit_trail.conformance``
    block (auto-attached when pr-analyze ran with --audit-trail), surface
    the score so Cloud Lite Growth-tier dashboards can show compliance
    posture alongside trends without a separate API call.

    W602 bonus: the ``ts`` parse silent-skip (line ~214 pre-W602) used to
    drop ``age_days`` / ``stale`` enrichment on a malformed timestamp
    without disclosure. When ``warnings_out`` is supplied, this path
    emits ``metrics_push_last_pr_timestamp_parse_failed:<ts>:<exc>``.
    The block STILL renders without the age fields (caller contract
    preserved); only the warning surfaces the silent enrichment-drop.

    Emitted kinds (closed enum, bonus):

      * ``metrics_push_last_pr_timestamp_parse_failed:<ts>:<exc_class>:<detail>``
        — ``datetime.fromisoformat`` raised on a malformed timestamp.
        The age_days / stale fields are absent from the block.
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    pr_summary = last_pr_envelope.get("summary") or {}
    ai_section = last_pr_envelope.get("ai_likelihood") or {}
    ts = (last_pr_envelope.get("_meta") or {}).get("timestamp")
    block = {
        "verdict": pr_summary.get("verdict"),
        "blast_radius": pr_summary.get("blast_radius"),
        "ai_likelihood": pr_summary.get("ai_likelihood"),
        "rule_violations": pr_summary.get("rule_violations"),
        "high_severity_critique": pr_summary.get("high_severity_critique"),
        "primary_language": ai_section.get("primary_language"),
        "timestamp": ts,
    }
    if ts:
        try:
            pr_dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_days = (_dt.datetime.now(_dt.timezone.utc) - pr_dt).days
            block["age_days"] = age_days
            block["stale"] = age_days > 7
        except (TypeError, ValueError) as exc:
            _emit(f"metrics_push_last_pr_timestamp_parse_failed:{ts}:{type(exc).__name__}:{exc}")
    # A7 — fold in the auto-attached Article 12 conformance score when present.
    conf = (last_pr_envelope.get("audit_trail") or {}).get("conformance")
    if conf:
        block["conformance_score"] = conf.get("score")
        block["conformance_checks_passed"] = conf.get("checks_passed")
        block["conformance_checks_total"] = conf.get("checks_total")
    return block


def _build_payload(
    audit_envelope: dict,
    *,
    repo_id: str,
    git_meta: dict,
    anonymize: bool,
    include_hotspots: bool,
    last_pr_envelope: dict | None = None,
) -> dict:
    """Compose the metrics-only payload from a ``roam audit`` envelope.

    Refactor (P23): metrics, hotspots, and last-pr-analysis blocks are
    extracted into helpers above. This function is now a flat coordinator.
    """
    payload: dict = {
        "schema": "roam-metrics-v1",
        "schema_version": "1.0.0",
        "repo": repo_id,
        "git_sha": git_meta.get("git_sha"),
        "git_branch": git_meta.get("git_branch"),
        "timestamp": utc_timestamp(),
        "tool_version": detect_roam_version(),
        "anonymized": bool(anonymize),
        "metrics": _extract_metrics(audit_envelope),
    }
    if include_hotspots:
        payload["hotspots"] = _extract_hotspots(audit_envelope, anonymize=anonymize)
    if last_pr_envelope:
        payload["last_pr_analysis"] = _build_last_pr_block(last_pr_envelope)
    return payload


# Backward-compatible alias.
_detect_tool_version = detect_roam_version


# ---------------------------------------------------------------- HTTP push ---


def _post_metrics(endpoint: str, token: str, payload: dict, timeout: int = HTTP_TIMEOUT) -> tuple[bool, int, str]:
    """POST the payload as JSON. Returns ``(success, status_code, response_text)``.

    Uses stdlib :mod:`urllib.request` to avoid adding ``httpx`` /
    ``requests`` as a dependency. Honors the supplied timeout (default
    15s, overridable via ``--timeout`` CLI flag for slow networks).
    """
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Roam-Schema": payload.get("schema", "roam-metrics-v1"),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — explicit endpoint
            text = resp.read().decode("utf-8", errors="replace")[:1024]
            return (200 <= resp.status < 300), resp.status, text
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")[:1024] if e.fp else ""
        return False, e.code, text
    except (urllib.error.URLError, OSError) as e:
        return False, 0, str(e)[:512]


# -------------------------------------------------------------- main command ---


@roam_capability(
    name="metrics-push",
    category="workflow",
    summary="Push metrics-only summary to Roam Cloud Lite",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=False,
    requires_index=True,
)
@click.command(name="metrics-push")
@click.option(
    "--token",
    envvar="ROAM_CLOUD_TOKEN",
    default=None,
    help="Auth token (env: ROAM_CLOUD_TOKEN). Required unless --dry-run.",
)
@click.option(
    "--repo",
    "repo_override",
    default=None,
    help="Override repo identifier (default: derived from git origin URL).",
)
@click.option(
    "--endpoint",
    default=DEFAULT_ENDPOINT,
    show_default=True,
    help="Roam Cloud Lite API endpoint.",
)
@click.option(
    "--anonymize",
    is_flag=True,
    help="Replace file paths with SHA-256 hash prefixes (path never leaves machine).",
)
@click.option(
    "--include-hotspots/--no-hotspots",
    default=True,
    show_default=True,
    help="Include top 10 danger-zone hotspot rows in the payload.",
)
@click.option(
    "--include-pr-analysis/--no-pr-analysis",
    default=True,
    show_default=True,
    help=f"Fold {DEFAULT_LAST_PR_PATH} (verdict + blast + ai) into payload when present.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the payload to stdout instead of POSTing. Token not required.",
)
@click.option(
    "--timeout",
    type=int,
    default=HTTP_TIMEOUT,
    show_default=True,
    help="HTTP request timeout in seconds (raise for slow networks / large payloads).",
)
@click.pass_context
def metrics_push(
    ctx,
    token: str | None,
    repo_override: str | None,
    endpoint: str,
    anonymize: bool,
    include_hotspots: bool,
    include_pr_analysis: bool,
    dry_run: bool,
    timeout: int,
) -> None:
    """Push metrics-only summary to Roam Cloud Lite.

    \b
    Examples:
      roam metrics-push --dry-run                    # inspect payload locally
      roam metrics-push --token $ROAM_CLOUD_TOKEN
      roam metrics-push --anonymize                  # path-hash hotspots
      roam metrics-push --no-hotspots --json         # minimal payload

    No source-code bodies are transmitted — only numerical metrics, file
    paths (or hashes with --anonymize), bucket counts, and aggregate
    scores. Inspect the exact payload with --dry-run before opting in.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # W607-DI -- substrate-boundary plumbing for cmd_metrics_push.
    # ``_run_check_di`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607di_warnings_out`` rather than
    # crashing the metrics-push command outright. cmd_metrics_push is
    # the Cloud Lite metrics-only transmitter -- surfaces the unique
    # ``danger_score`` aggregate metric and folds in
    # ``.roam/last-pr-analysis.json`` enrichment. The command sits at
    # the boundary between local audit + HTTP push, so substrates span
    # audit-envelope ingest, git introspection, last-PR enrichment,
    # payload assembly, HTTP push, verdict composition, and the JSON
    # envelope serialization.
    #
    # Marker family ``metrics_push_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped (10 phases):
    #
    #   * capture_audit               -- in-process ``roam audit`` invoke
    #                                    (the audit_envelope.get("error")
    #                                    silent-default path is preserved;
    #                                    this layer adds disclosure on
    #                                    an outright CliRunner crash)
    #   * git_metadata                -- git_sha / git_branch / git_origin
    #                                    introspection (subprocess.run
    #                                    boundary; on a non-git checkout
    #                                    helpers return empty dict)
    #   * infer_repo_id               -- origin URL normalization
    #   * load_last_pr_analysis       -- .roam/last-pr-analysis.json read
    #                                    (already has W602 warnings_out
    #                                    plumbing internally; W607-DI
    #                                    wraps the call to disclose any
    #                                    raise BEYOND the W602 silent-skip
    #                                    contract on missing/corrupt file)
    #   * build_payload               -- _extract_metrics + _extract_hotspots
    #                                    + _build_last_pr_block coordinator
    #   * serialize_payload           -- _json.dumps(payload) size calc +
    #                                    dry-run printout (the bytes-count
    #                                    in the dry-run VERDICT message
    #                                    depends on this substrate)
    #   * post_metrics                -- HTTP POST (the network boundary;
    #                                    urlopen wrapper catches HTTPError
    #                                    + URLError internally, but a
    #                                    payload-encoding raise OR a
    #                                    monkeypatched substrate failure
    #                                    must NOT crash the command)
    #   * compose_verdict             -- LAW 6 single-line verdict string
    #   * serialize_envelope          -- to_json(json_envelope(...))
    #                                    projection for the JSON path
    #   * emit_text_output            -- text-path formatting (non-JSON
    #                                    branch); a raise here used to
    #                                    obliterate the operator-visible
    #                                    summary, now degrades to a
    #                                    minimal one-line VERDICT echo
    _w607di_warnings_out: list[str] = []

    def _run_check_di(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-DI marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``metrics_push_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607di_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607di_warnings_out.append(f"metrics_push_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DI: ``capture_audit`` substrate -- the in-process audit invoke.
    # ``_capture_audit`` already returns an ``error``-stamped envelope on
    # CliRunner failures, but an outright CliRunner crash (e.g. import
    # error in click stack) used to torpedo the command. The W607-DI
    # wrap degrades to a synthetic error envelope so the rest of the
    # pipeline composes.
    audit_envelope = _run_check_di(
        "capture_audit",
        _capture_audit,
        default={"error": "audit capture substrate raised", "exit_code": -1},
    )
    if audit_envelope is None:
        audit_envelope = {"error": "audit capture substrate raised", "exit_code": -1}
    audit_failed = isinstance(audit_envelope.get("error"), str)

    # W607-DI: ``git_metadata`` substrate. The helper shells out to ``git``
    # via subprocess; on a non-git checkout it returns ``{}`` (legitimate
    # silent-absence path). The W607-DI wrap discloses outright raises.
    git_meta = _run_check_di("git_metadata", git_metadata, default={})
    if git_meta is None:
        git_meta = {}

    # W607-DI: ``infer_repo_id`` substrate.
    repo_id = _run_check_di(
        "infer_repo_id",
        _infer_repo_id,
        git_meta,
        repo_override,
        default="<unknown>",
    )
    if repo_id is None:
        repo_id = "<unknown>"

    # W607-DI: ``load_last_pr_analysis`` substrate. The helper has W602
    # warnings_out plumbing internally for the corrupt-file path; the
    # W607-DI wrap catches any raise that escapes that envelope (e.g.
    # an inline raise in DEFAULT_LAST_PR_PATH resolution).
    last_pr = None
    if include_pr_analysis:
        last_pr = _run_check_di(
            "load_last_pr_analysis",
            _load_last_pr_analysis,
            default=None,
        )

    # W607-DI: ``build_payload`` substrate.
    payload = _run_check_di(
        "build_payload",
        _build_payload,
        audit_envelope,
        repo_id=repo_id,
        git_meta=git_meta,
        anonymize=anonymize,
        include_hotspots=include_hotspots,
        last_pr_envelope=last_pr,
        default={
            "schema": "roam-metrics-v1",
            "schema_version": "1.0.0",
            "repo": repo_id,
            "anonymized": bool(anonymize),
            "metrics": {},
        },
    )
    if payload is None:
        payload = {
            "schema": "roam-metrics-v1",
            "schema_version": "1.0.0",
            "repo": repo_id,
            "anonymized": bool(anonymize),
            "metrics": {},
        }

    # Pattern 2 + silent-metric-drop fix: when `roam audit` failed (returned an
    # error envelope), _extract_metrics() silently defaults everything to
    # None/0. Surface that explicitly on the payload so neither --dry-run
    # consumers nor the Cloud Lite receiver mistake the defaults for real
    # measurements. The receiving API can drop the row; the local audit
    # operator can see WHY it dropped.
    if audit_failed:
        payload["audit_status"] = "failed"
        payload["audit_error"] = audit_envelope.get("error")
        payload["audit_exit_code"] = audit_envelope.get("exit_code")

    if dry_run:
        # W607-DI: ``compose_verdict`` substrate on the dry-run path.
        def _dry_verdict():
            return (
                "dry-run — audit failed; payload would carry no real metrics"
                if audit_failed
                else "dry-run — payload not POSTed"
            )

        dry_verdict = _run_check_di(
            "compose_verdict",
            _dry_verdict,
            default="dry-run — verdict substrate degraded",
        )
        if dry_verdict is None:
            dry_verdict = "dry-run — verdict substrate degraded"

        # W607-DI: ``serialize_payload`` substrate -- _json.dumps for the
        # bytes count. A raise here used to crash the dry-run preview
        # via the f-string ``{len(_json.dumps(payload))}`` evaluation.
        # Bind via a substrate call so the dry-run still emits a
        # well-formed VERDICT line even on a payload that's not
        # JSON-serializable.
        payload_serialized = _run_check_di(
            "serialize_payload",
            _json.dumps,
            payload,
            default=None,
        )

        if json_mode:
            dry_summary: dict = {
                "verdict": dry_verdict,
                "repo": repo_id,
                "git_sha": payload.get("git_sha"),
                "anonymized": anonymize,
                "endpoint": endpoint,
                "audit_status": "failed" if audit_failed else "ok",
                "partial_success": audit_failed or bool(_w607di_warnings_out),
            }
            envelope_kwargs = dict(summary=dry_summary, payload=payload)
            # W607-DI: mirror substrate markers into BOTH top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard -- a degraded substrate
            # path must NOT be mistaken for a clean dry-run preview.
            if _w607di_warnings_out:
                dry_summary["warnings_out"] = list(_w607di_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607di_warnings_out)

            def _emit_dry_envelope():
                click.echo(to_json(json_envelope("metrics-push", **envelope_kwargs)))

            _run_check_di("serialize_envelope", _emit_dry_envelope, default=None)
        else:
            # W607-DI: ``emit_text_output`` substrate -- dry-run text path.
            def _emit_dry_text():
                # Guard the bytes-count formatting: if serialize_payload
                # degraded, surface "?" instead of a TypeError on len(None).
                if payload_serialized is None:
                    bytes_label = "?"
                else:
                    bytes_label = str(len(payload_serialized))
                click.echo(f"VERDICT: {dry_verdict}; would POST {bytes_label} bytes to {endpoint}")
                click.echo()
                if payload_serialized is not None:
                    # Pretty-print only when serialization succeeded.
                    try:
                        click.echo(_json.dumps(payload, indent=2))
                    except (TypeError, ValueError):
                        click.echo("<payload not JSON-serializable>")

            _run_check_di("emit_text_output", _emit_dry_text, default=None)
        return

    if not token:
        ctx.fail("--token required (or set ROAM_CLOUD_TOKEN env var); use --dry-run to inspect without posting.")

    # W607-DI: ``post_metrics`` substrate -- the HTTP push boundary.
    # ``_post_metrics`` catches HTTPError + URLError internally and
    # returns (False, code, text). A raise outside those (e.g. a
    # _json.dumps TypeError on a payload that contains a datetime,
    # or a monkeypatched substrate failure) used to crash the
    # command. The W607-DI wrap degrades to the canonical failed-push
    # tuple so the verdict composer still emits a coherent
    # ``push failed (0)`` line.
    post_result = _run_check_di(
        "post_metrics",
        _post_metrics,
        endpoint,
        token,
        payload,
        timeout=timeout,
        default=(False, 0, "post_metrics substrate degraded"),
    )
    if post_result is None:
        post_result = (False, 0, "post_metrics substrate degraded")
    ok, status, response_text = post_result

    # W607-DI: ``compose_verdict`` substrate -- LAW 6 single-line verdict.
    def _push_verdict():
        if audit_failed:
            return "metrics pushed (audit failed; payload empty)" if ok else f"push failed ({status})"
        return "metrics pushed" if ok else f"push failed ({status})"

    verdict = _run_check_di(
        "compose_verdict",
        _push_verdict,
        default=f"push failed ({status})",
    )
    if verdict is None:
        verdict = f"push failed ({status})"

    summary = {
        "verdict": verdict,
        "ok": ok,
        "status_code": status,
        "endpoint": endpoint,
        "repo": repo_id,
        "git_sha": payload.get("git_sha"),
        "anonymized": anonymize,
        "audit_status": "failed" if audit_failed else "ok",
        "partial_success": audit_failed or not ok or bool(_w607di_warnings_out),
    }

    # W607-DI: mirror substrate markers into BOTH the top-level envelope
    # ``warnings_out`` AND ``summary.warnings_out`` so MCP consumers see
    # disclosure regardless of which surface they read.
    envelope_kwargs = dict(
        summary=summary,
        payload=payload,
        response_excerpt=response_text,
    )
    if _w607di_warnings_out:
        summary["warnings_out"] = list(_w607di_warnings_out)
        envelope_kwargs["warnings_out"] = list(_w607di_warnings_out)

    if json_mode:
        # W607-DI: ``serialize_envelope`` substrate -- to_json + json_envelope
        # projection on the JSON path.
        def _emit_envelope():
            click.echo(to_json(json_envelope("metrics-push", **envelope_kwargs)))

        _run_check_di("serialize_envelope", _emit_envelope, default=None)
    else:
        # W607-DI: ``emit_text_output`` substrate -- text path.
        def _emit_text():
            click.echo(f"VERDICT: {summary['verdict']}")
            click.echo(f"  endpoint:  {endpoint}")
            click.echo(f"  status:    {status}")
            click.echo(f"  repo:      {repo_id}")
            click.echo(f"  anonymize: {anonymize}")
            if audit_failed:
                click.echo(f"  audit:     failed (exit={payload.get('audit_exit_code')!r})")
            if not ok and response_text:
                click.echo()
                click.echo("Response excerpt:")
                click.echo(response_text[:200])

        _run_check_di("emit_text_output", _emit_text, default=None)

    # Exit non-zero so CI consumers + shell pipelines see the failure.
    # The JSON envelope already carried ok:false; the text path used to
    # always exit 0 which masked the failure from non-JSON callers.
    if not ok:
        ctx.exit(1)
