"""``roam version`` — print the installed roam-code version.

with ``--check``, also queries PyPI (with a tight timeout) to
report whether a newer version is available. Offline-friendly: when
PyPI is unreachable, just prints the local version. No nag.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam version`` prints invocation-scoped tool-
identity metadata (installed roam-code version string + optional
PyPI-latest comparison) — not per-location code violations in any
indexed workspace. SARIF is reserved for scanning results. See
``cmd_surface`` / ``cmd_capabilities`` for the parallel meta-
registry disclosure pattern + action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH propagation plan + W1224-audit memo.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import click

try:
    from importlib.metadata import version as _version

    _HAVE_METADATA = True
except ImportError:
    _HAVE_METADATA = False

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_PYPI_URL = "https://pypi.org/pypi/roam-code/json"
_PYPI_TIMEOUT = 2.0


def _local_version() -> str:
    if _HAVE_METADATA:
        try:
            return _version("roam-code")
        except Exception:
            # Genuine guard: the "unknown" return value below IS the
            # explicit loud signal that version resolution failed.
            pass
    return "unknown"


def _pypi_latest() -> str | None:
    """Return the latest version on PyPI or None on any failure."""
    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=_PYPI_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("info") or {}).get("version")
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


@roam_capability(
    name="version",
    category="getting-started",
    summary="Print the installed roam-code version (and check PyPI with --check)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command()
@click.option("--check", "do_check", is_flag=True, help="also query PyPI for the latest version.")
@click.pass_context
def version(ctx, do_check) -> None:
    """Print the installed roam-code version (and check PyPI with --check)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    local = _local_version()
    latest = _pypi_latest() if do_check else None
    upgrade = bool(latest and latest != local and local != "unknown")
    verdict = (
        f"installed: {local}, latest on PyPI: {latest}" + (" — upgrade available" if upgrade else "")
        if do_check
        else f"installed: {local}"
    )
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "version",
                    summary={
                        "verdict": verdict,
                        "local": local,
                        "latest": latest,
                        "upgrade_available": upgrade,
                    },
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
    if upgrade:
        click.echo()
        click.echo(f"  pip install --upgrade roam-code  # → {latest}")
