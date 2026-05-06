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

Pass — Priority C.2.a per internal backlog.
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

from roam.commands.git_helpers import detect_roam_version, git_metadata, utc_timestamp
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json

DEFAULT_ENDPOINT = "https://api.roam.cloud/v1/metrics"
USER_AGENT = "roam-code-metrics-push"
HTTP_TIMEOUT = 15
DEFAULT_LAST_PR_PATH = Path(".roam") / "last-pr-analysis.json"

# Backward-compatible alias for any external test harness still importing the
# private name.
_git_metadata = git_metadata


# ---------------------------------------------------------- audit + git helpers ---


def _capture_audit() -> dict:
    """Invoke ``roam audit`` in-process and return the JSON envelope."""
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


def _load_last_pr_analysis(path: Path | None = None) -> dict | None:
    """Load `.roam/last-pr-analysis.json` if it exists; return None on miss / read failure.

    The presence of a recent pr-analyze envelope means a Cloud Lite dashboard
    can show "last PR verdict" alongside the trend metrics — without needing
    a separate API call.
    """
    p = path or DEFAULT_LAST_PR_PATH
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return None


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


def _build_last_pr_block(last_pr_envelope: dict) -> dict:
    """Compose the last_pr_analysis block from a saved pr-analyze envelope.

    Folds in only summary numerics + verdict + primary language + timestamp.
    Computes ``age_days`` + ``stale`` (>7 days) so dashboards can grey
    stale entries without needing to compute age client-side.
    """
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
        except (TypeError, ValueError):
            pass
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

    audit_envelope = _capture_audit()
    git_meta = git_metadata()
    repo_id = _infer_repo_id(git_meta, repo_override)
    last_pr = _load_last_pr_analysis() if include_pr_analysis else None
    payload = _build_payload(
        audit_envelope,
        repo_id=repo_id,
        git_meta=git_meta,
        anonymize=anonymize,
        include_hotspots=include_hotspots,
        last_pr_envelope=last_pr,
    )

    if dry_run:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "metrics-push",
                        summary={
                            "verdict": "dry-run — payload not POSTed",
                            "repo": repo_id,
                            "git_sha": payload.get("git_sha"),
                            "anonymized": anonymize,
                            "endpoint": endpoint,
                        },
                        payload=payload,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: dry-run — would POST {len(_json.dumps(payload))} bytes to {endpoint}")
            click.echo()
            click.echo(_json.dumps(payload, indent=2))
        return

    if not token:
        ctx.fail("--token required (or set ROAM_CLOUD_TOKEN env var); use --dry-run to inspect without posting.")

    ok, status, response_text = _post_metrics(endpoint, token, payload, timeout=timeout)

    summary = {
        "verdict": "metrics pushed" if ok else f"push failed ({status})",
        "ok": ok,
        "status_code": status,
        "endpoint": endpoint,
        "repo": repo_id,
        "git_sha": payload.get("git_sha"),
        "anonymized": anonymize,
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "metrics-push",
                    summary=summary,
                    payload=payload,
                    response_excerpt=response_text,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {summary['verdict']}")
        click.echo(f"  endpoint:  {endpoint}")
        click.echo(f"  status:    {status}")
        click.echo(f"  repo:      {repo_id}")
        click.echo(f"  anonymize: {anonymize}")
        if not ok and response_text:
            click.echo()
            click.echo("Response excerpt:")
            click.echo(response_text[:200])
