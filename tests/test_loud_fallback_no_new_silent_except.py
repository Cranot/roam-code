"""Loud-fallback campaign drift guard — ratchet on silent ``except: pass``.

The 2026-05-21 "make fallback chains loud" campaign (CLAUDE.md
"Make fallback chains loud") swept four zones — the agent-OS substrate
(``agents_md`` / ``world_model`` / ``runs``), ``mcp_server.py``, the
analysis core (``index`` / ``refactor`` / ``search``), and the shared
command helpers (``resolve`` / ``context_helpers`` / ``changed_files`` /
``gate_presets``) — replacing silent ``except: pass`` / swallowed-
exception sites with ``roam.observability.log_swallowed`` lineage
emission.

The campaign did NOT zero every silent handler in ``src/roam`` — it
fixed the highest-value sites on evidence-emitting paths and left
genuine optional-dependency / expected-signal guards in place. This
test is the structural guard that CLOSES the campaign (per the
"drift-guard with campaign" discipline): it RATCHETS the count of
silent ``except: pass`` handlers so new ones cannot be introduced
unnoticed.

Baseline: 107 silent handlers across ``src/roam``, measured 2026-05-22
after the sixth batch swept the remaining ``cmd_*.py`` modules (down
from 158 — the AST helper here reports the true post-fifth-batch count,
which the prior docstring overstated as 183). The sixth batch removed
15 dead ``try: auto_log(...) except Exception: pass`` wrappers
(``auto_log`` is documented + verified to never raise), converted one
``auto_log`` wrapper that guarded real envelope-derivation logic to
``log_swallowed``, and converted 35 silent handlers on the ``roam
next`` router, the findings-registry detector-persistence paths, the
PR-analysis git/diff acquisition paths, and user-config loaders to
``log_swallowed`` lineage emission. Genuine optional-import /
expected-signal / idempotent-normalize guards (token parsers,
``nx.NetworkXNoPath``, temp-file cleanup, ``conn.close()``) were
annotated in place.

The ratchet moves DOWN only. If this test fails because the count went
UP, do NOT bump the baseline to paper over it — make the new handler
loud (emit ``roam.observability.log_swallowed`` before the fallback,
or re-raise a typed exception). If you genuinely need a new silent
guard (a true optional-import / expected-signal catch), annotate it
with a comment AND deliberately lower-or-update ``SILENT_EXCEPT_BASELINE``
with that justification — the same discipline as ``USER_VERSION``.
When cleanup reduces the count, ratchet the baseline DOWN to lock the
gain in.
"""

from __future__ import annotations

import ast

from tests._helpers.repo_root import repo_root

# Measured 2026-05-22 after the loud-fallback campaign's sixth batch.
# Ratchet DOWN as cleanup continues; never UP without justification.
SILENT_EXCEPT_BASELINE = 107


def _silent_except_sites() -> list[str]:
    """Return ``path:lineno`` for every silent ``except ...: pass`` handler.

    A handler counts as silent when, after dropping a leading string
    expression (a stray docstring), its body is exactly one ``pass``
    statement — i.e. the exception is swallowed with no logging, no
    warning, and no typed re-raise.
    """
    root = repo_root()
    src = root / "src" / "roam"
    sites: list[str] = []
    for path in sorted(src.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            real = [
                stmt for stmt in node.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
            ]
            if len(real) == 1 and isinstance(real[0], ast.Pass):
                rel = path.relative_to(root).as_posix()
                sites.append(f"{rel}:{node.lineno}")
    return sites


def test_no_new_silent_except_pass() -> None:
    """No new silent ``except: pass`` handler may enter ``src/roam``."""
    sites = _silent_except_sites()
    assert len(sites) <= SILENT_EXCEPT_BASELINE, (
        f"silent `except: pass` count rose to {len(sites)} "
        f"(baseline {SILENT_EXCEPT_BASELINE}). A new silent handler was "
        f"added — make it loud via roam.observability.log_swallowed, or "
        f"re-raise a typed exception. Do NOT bump the baseline to paper "
        f"over a real regression.\nsites:\n" + "\n".join(sites)
    )
