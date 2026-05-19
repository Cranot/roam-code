"""W662 drift-guard — detector / catalog / security / world_model / output
modules must NOT use bare ``except Exception:`` (or ``except:``) whose body is
just ``continue`` / ``pass``.

Why this drift-guard exists
---------------------------

W653 fixed a real bug in :func:`roam.catalog.smells.run_all_detectors`:

.. code-block:: python

    for _smell_id, detect_fn in ALL_DETECTORS:
        try:
            hits = detect_fn(conn)
        except Exception:        # <-- bare swallow
            continue              # <-- programmer errors lost

A previous sprint (W601 / W602) had dropped a ``Counter`` import that one of
the detectors referenced. The resulting ``NameError`` was silently swallowed
by the ``except Exception: continue`` handler for months — the detector
quietly produced zero findings and the issue only surfaced when the W653
audit walked the loop by hand.

Per W531 fail-loud discipline + CLAUDE.md "Pattern-2 always-emit" guard,
programmer-class errors (``NameError`` / ``ImportError`` / ``AttributeError``
/ ``TypeError`` — bug-class exceptions that mean the code itself is wrong)
MUST propagate. They must never be lumped into a generic ``except Exception``
that drops them on the floor.

The W653 fix narrows the ``smells.run_all_detectors`` handler to
``except sqlite3.Error`` for the "one bad query shouldn't kill the run" case
and re-raises ``NameError`` / ``ImportError`` / ``AttributeError`` /
``TypeError`` as a ``RuntimeError``. This drift-guard prevents new
``except Exception: continue`` / ``except Exception: pass`` sites from
landing in the detector ecosystem and re-introducing the same bug class.

What counts as "bare swallow"
-----------------------------

An ``ast.ExceptHandler`` is "bare swallow" iff BOTH:

1. It catches the universal-bug-class — i.e. the ``type`` is ``None``
   (literal ``except:``) OR a ``Name`` whose ``id`` is one of
   ``{"Exception", "BaseException"}``. Narrow classes
   (``sqlite3.Error``, ``ImportError``, ``(KeyError, TypeError)``) are
   intentionally allowed: they document what the author expected.

2. Its body consists entirely of ``ast.Pass`` and/or ``ast.Continue``
   statements. A body that logs, re-raises, returns a sentinel, or
   appends to a ``failed_detectors`` list is NOT bare-swallow — it
   handles the error visibly.

This is deliberately narrow. We do not flag ``except Exception: return []``
(returns are a visible signal — caller sees the empty list) or
``except Exception: log.warning(...); continue`` (the log line surfaces the
problem). Only the truly silent shape — catch wide, do nothing — is the
bug class W653 fixed.

Guarded directories
-------------------

* ``src/roam/catalog/`` — the detector ecosystem where W653 lived. HIGH
  priority: this directory contains the original offender + 20+ smell
  detectors + the algorithm-catalog detectors that ``run_all_detectors``
  composes.
* ``src/roam/world_model/`` — R28 classifiers (side_effects, idempotency,
  causal_graph, tx_boundaries). Same dispatch-loop pattern; same risk.
* ``src/roam/security/`` — taint engine + vulnerability ingest. Programmer
  errors here are CRITICAL because they silently disable security checks.
* ``src/roam/output/`` — emitter pipeline. A swallowed ``ImportError`` here
  means a degraded envelope shape that downstream agents cannot detect.

Mirrors the existing AST drift-guards
-------------------------------------

Same shape as :mod:`tests.test_w588_fragile_path_drift` (AST walk +
allowlist with rationale) and :mod:`tests.test_w512_edge_kinds_drift`
(per-file allowlist with deliberate-design carve-outs).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"


# Directories whose detector / pipeline loops are subject to the W653
# fail-loud discipline. Add new directories here as the substrate grows.
_GUARDED_DIRS: tuple[str, ...] = (
    "catalog",
    "world_model",
    "security",
    "output",
    # W746 (W740 drive-by): substrate dirs on the persistence /
    # evidence path inherit the same fail-loud discipline. Each new
    # entry has been audited; obvious sites narrowed inline, the
    # remaining 3 cryptographic-substrate isolation perimeters are
    # documented in ``_PRE_W662_PENDING`` below.
    "constitution",
    "critique",  # W666: critique substrate (aggregator + checks). Programmer
    # errors in the critique pipeline silently degrade the verdict envelope
    # downstream agents consume — same fail-loud discipline as the rest of
    # the evidence path.
    "db",
    "evidence",
    "leases",
    "modes",
    "plugins",
    "pr_bundle",  # future-proof: directory does not exist yet
    "runs",
)


# Pre-W662 inventory: bare-swallow sites that exist on main as of W662
# ship. Each entry is keyed by ``"<rel-from-src/roam>:<lineno>"`` and
# carries a one-line rationale. The drift-guard is fail-loud: new sites
# blocked, existing sites grandfathered with explicit documentation so
# the audit trail is preserved.
#
# Format: ``"<dir>/<file>:<line>": "<rationale>"``.
#
# These are NOT silently acceptable — each represents work either to
# narrow the handler (preferred) or to keep with documented intent.
# Re-audit during periodic dogfood passes; the
# ``test_pre_w662_pending_entries_still_have_pattern`` assertion below
# will catch entries that have already been migrated.
_PRE_W662_PENDING: dict[str, str] = {
    # W1300 — entries refreshed after session edits to runs/ledger.py + the
    # W746 plugin-isolation migration. Two changes:
    #   1. ``catalog/detectors.py:2044`` + ``:2048`` were the plugin-loop
    #      perimeters — those sites were migrated to ``except Exception as
    #      err: log.warning(...)`` (no longer bare-swallow). Dropped.
    #   2. ``runs/ledger.py`` lines shifted +18/+18/+18 after W1255
    #      config-hash stamping was inserted around line 250; the same
    #      cryptographic-substrate isolation rationale applies. Refresh
    #      :315 -> :333 and :386 -> :404. :238 is unshifted.
    # ------------------------------------------------------------------
    # W746-original rationale carried over: the surrounding comments in
    # runs/ledger.py explicitly mandate that ANY failure of the signing
    # subsystem must not block ledger writes — narrowing would contradict
    # the intent. Re-audit when ``verify_chain`` grows a structured
    # "unsigned event" diagnostic (then we could narrow these to the
    # specific raises of ``ensure_ledger_key`` and
    # ``compute_event_signature``).
    "runs/ledger.py:240": (
        "cryptographic-substrate isolation perimeter: ensure_ledger_key "
        "failure on start_run must never block run creation — the "
        "verifier surfaces the absence (W746; consider narrowing to "
        "(OSError, ImportError) once the key-substrate failure modes "
        "are documented as a closed set). Lines shift on every edit to "
        "the surrounding signing-substrate block; re-pin via grep when "
        "this drift-guard reports a 2-3 line offset."
    ),
    "runs/ledger.py:335": (
        "cryptographic-substrate isolation perimeter: HMAC signing of a "
        "new event must NEVER prevent the event from being recorded; "
        "verify_chain flags unsigned events (W746/W1300; same narrowing "
        "path as line 240)"
    ),
    "runs/ledger.py:406": (
        "cryptographic-substrate isolation perimeter: stamping final "
        "signature into meta.json on end_run must not crash the close; "
        "an unsigned/legacy chain legitimately yields None here "
        "(W746/W1300; same narrowing path as line 240)"
    ),
    "catalog/detectors.py:257": (
        "plugin-isolation perimeter: the plugin-loader iteration that "
        "appends third-party detector entries must NEVER let a single "
        "broken plugin block the whole detector roster. Mirrors the "
        "W746 cryptographic-substrate isolation rationale: a wholly-"
        "out-of-tree failure must not poison core machinery."
    ),
}


# ---------------------------------------------------------------------------
# AST detection
# ---------------------------------------------------------------------------


_UNIVERSAL_NAMES = frozenset({"Exception", "BaseException"})


def _catches_universal(handler: ast.ExceptHandler) -> bool:
    """True iff *handler* catches the universal-bug-class.

    Covers two AST shapes:

    * ``except:`` — ``handler.type`` is ``None``.
    * ``except Exception:`` / ``except BaseException:`` —
      ``handler.type`` is ``ast.Name`` with id in ``_UNIVERSAL_NAMES``.

    Tuples like ``except (Exception, OSError):`` are NOT flagged here
    — a tuple is an ``ast.Tuple`` node, not an ``ast.Name``. That's
    intentional: when the author lists multiple classes they have
    thought about the set, and ``Exception`` mixed with siblings is no
    different than ``Exception`` alone for our bug-class concern, so
    we err on the side of permissive. If a tuple-shaped bare-swallow
    proves to be a real regression source, add a tuple branch here.
    """
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name) and handler.type.id in _UNIVERSAL_NAMES:
        return True
    return False


def _body_is_silent(body: list[ast.stmt]) -> bool:
    """True iff *body* is composed only of ``Pass`` / ``Continue`` statements.

    This is the "does nothing visible" check. A single ``return``,
    ``raise``, ``log.warning(...)`` call, list-append, or any other
    statement makes the handler non-silent — the caller is doing
    something with the error.
    """
    if not body:
        return False  # empty bodies are syntactically impossible
    return all(isinstance(stmt, (ast.Pass, ast.Continue)) for stmt in body)


def _is_bare_swallow(handler: ast.ExceptHandler) -> bool:
    """True iff *handler* matches the W653 bug pattern: catch wide, do nothing."""
    return _catches_universal(handler) and _body_is_silent(handler.body)


def _find_bare_swallow_sites(path: Path) -> list[str]:
    """Return ``"<rel>:<lineno>"`` for each bare-swallow handler in *path*."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node):
            hits.append(f"{rel}:{node.lineno}")
    return hits


def _iter_guarded_files() -> list[Path]:
    files: list[Path] = []
    for guarded in _GUARDED_DIRS:
        root = SRC_ROOT / guarded
        if not root.exists():
            continue
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_new_bare_swallow_in_guarded_dirs() -> None:
    """No NEW ``except Exception: continue`` / ``except Exception: pass`` site
    may land in the guarded detector / pipeline directories.

    Per W531 fail-loud + W653 incident: programmer-class errors
    (``NameError`` / ``ImportError`` / ``AttributeError`` / ``TypeError``)
    must propagate. Use either:

    1. Narrow the handler: ``except sqlite3.Error:`` / ``except (KeyError,
       TypeError):`` — document what you actually expect.
    2. Make the swallow visible: ``except Exception as err: log.warning(
       'detector %s failed: %s', name, err); continue`` — at least the
       error reaches the operator.
    3. Re-raise as a typed ``RuntimeError`` (the W653 pattern) — convert
       the bug into a loud crash with context.
    """
    pending = set(_PRE_W662_PENDING)
    violations: list[str] = []
    for path in _iter_guarded_files():
        for hit in _find_bare_swallow_sites(path):
            if hit in pending:
                continue
            violations.append(hit)
    assert not violations, (
        "W662: bare `except Exception: continue|pass` detected in guarded "
        "detector / pipeline directories — programmer-class errors must "
        "propagate per W531 fail-loud discipline + W653 incident. Either "
        "narrow the handler, log+continue, or re-raise as RuntimeError. "
        "Offenders:\n  " + "\n  ".join(violations)
    )


def test_pre_w662_pending_entries_actually_exist() -> None:
    """Every ``_PRE_W662_PENDING`` entry must point at a real file.

    Stale entries (the file was deleted or renamed without updating
    this dict) silently widen the allowlist and let real regressions
    through.
    """
    missing: list[str] = []
    for entry in _PRE_W662_PENDING:
        rel, _, _line = entry.partition(":")
        if not (SRC_ROOT / rel).exists():
            missing.append(entry)
    assert not missing, f"W662: _PRE_W662_PENDING references missing files: {missing}"


def test_pre_w662_pending_entries_still_have_pattern() -> None:
    """Every ``_PRE_W662_PENDING`` entry must still contain a bare-swallow
    handler.

    Once a site has been narrowed / migrated to fail-loud, its entry
    must drop from ``_PRE_W662_PENDING`` — otherwise the allowlist
    keeps shielding a file that no longer needs shielding, and a
    future regression in the same file would slip through silently.
    """
    stale: list[str] = []
    for entry in _PRE_W662_PENDING:
        rel, _, _line = entry.partition(":")
        path = SRC_ROOT / rel
        if not path.exists():
            continue  # caught by the previous test
        hits = set(_find_bare_swallow_sites(path))
        if entry not in hits:
            stale.append(entry)
    assert not stale, (
        "W662: _PRE_W662_PENDING entries no longer contain a bare-swallow "
        "handler (the site was migrated) — drop these entries:\n  " + "\n  ".join(stale)
    )


def test_detector_catches_synthetic_continue_offender(tmp_path: Path) -> None:
    """The AST detector flags ``except Exception: continue`` — the canonical
    W653 shape.
    """
    src = (
        "def f(items):\n"
        "    for x in items:\n"
        "        try:\n"
        "            do(x)\n"
        "        except Exception:\n"
        "            continue\n"
    )
    offender = tmp_path / "synthetic_continue.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    hits = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node)]
    assert hits == [5], f"W662 detector must flag the bare-swallow handler on line 5; got {hits}"


def test_detector_catches_synthetic_pass_offender(tmp_path: Path) -> None:
    """The AST detector flags ``except Exception: pass`` — same pattern,
    different terminal statement.
    """
    src = "def f():\n    try:\n        do()\n    except Exception:\n        pass\n"
    offender = tmp_path / "synthetic_pass.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    hits = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node)]
    assert hits == [4], f"W662 detector must flag the bare-swallow handler on line 4; got {hits}"


def test_detector_catches_bare_colon_offender(tmp_path: Path) -> None:
    """The AST detector flags the legacy ``except:`` (no class) shape too —
    even wider than ``except Exception:`` since it catches
    ``KeyboardInterrupt`` / ``SystemExit``.
    """
    src = "def f():\n    try:\n        do()\n    except:\n        pass\n"
    offender = tmp_path / "synthetic_bare.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    hits = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node)]
    assert hits == [4], f"W662 detector must flag the bare ``except:`` handler on line 4; got {hits}"


def test_detector_ignores_narrow_handler(tmp_path: Path) -> None:
    """Narrow exception classes are NOT flagged — they document author intent."""
    src = (
        "import sqlite3\n"
        "def f():\n"
        "    try:\n"
        "        do()\n"
        "    except sqlite3.Error:\n"
        "        pass\n"
        "    try:\n"
        "        do()\n"
        "    except (KeyError, IndexError):\n"
        "        pass\n"
        "    try:\n"
        "        do()\n"
        "    except ImportError:\n"
        "        return []\n"
    )
    offender = tmp_path / "synthetic_narrow.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    hits = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node)]
    assert hits == [], f"W662 detector must NOT flag narrow exception classes; got {hits}"


def test_detector_ignores_visible_handler_body(tmp_path: Path) -> None:
    """``except Exception:`` with a visible body (log / raise / return / append)
    is NOT flagged — the error reaches the operator or the caller.
    """
    src = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def f():\n"
        "    try:\n"
        "        do()\n"
        "    except Exception as err:\n"
        "        log.warning('failed: %s', err)\n"
        "        return []\n"
        "    try:\n"
        "        do()\n"
        "    except Exception as err:\n"
        "        raise RuntimeError('wrapped') from err\n"
        "    try:\n"
        "        do()\n"
        "    except Exception:\n"
        "        return None\n"
    )
    offender = tmp_path / "synthetic_visible.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    hits = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node)]
    assert hits == [], f"W662 detector must NOT flag handlers with visible bodies; got {hits}"


def test_detector_ignores_string_literal_mentions(tmp_path: Path) -> None:
    """Docstring / string-literal mentions of the pattern must NOT be flagged
    — the AST walks expression nodes, not string contents.
    """
    src = (
        '"""This module discusses ``except Exception: continue`` patterns."""\n'
        "DOC = 'except Exception:\\n    pass  # this is a bug'\n"
    )
    offender = tmp_path / "synthetic_doc.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    hits = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_bare_swallow(node)]
    assert hits == [], f"W662 detector must ignore string-literal mentions; got {hits}"


def test_w653_canonical_fix_site_is_clean() -> None:
    """The original W653 offender — :func:`roam.catalog.smells.run_all_detectors` —
    must NOT contain a bare-swallow handler.

    Pins the W653 fix in place: if a future refactor re-introduces
    ``except Exception: continue`` in the smells dispatch loop, this
    test fails with a pointer back to the original incident.
    """
    smells_path = SRC_ROOT / "catalog" / "smells.py"
    assert smells_path.exists(), "catalog/smells.py is the W653 fix site"
    sites = _find_bare_swallow_sites(smells_path)
    assert sites == [], (
        "W662: catalog/smells.py contains a bare-swallow handler — the W653 "
        "fix has regressed. The run_all_detectors loop MUST narrow to "
        "sqlite3.Error + re-raise programmer-class errors. "
        f"Offenders: {sites}"
    )
