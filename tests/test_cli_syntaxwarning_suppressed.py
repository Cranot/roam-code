"""The structured (`--json`/SARIF/agent) warning handler must NOT leak
compile-time ``SyntaxWarning``s into a customer-facing deliverable.

Regression: ``service-report --type reachability-triage`` (a paid deliverable)
emitted raw interpreter ``invalid escape sequence '\\d'`` / ``'\\g'`` / ``'\\ '``
warnings on stderr, sourced from a dependency's ``compile()`` codegen
(``filename=<unknown>``), not from roam's own source (which is
SyntaxWarning-clean under the compileall gate). Those are never actionable
roam findings for a user and must be dropped from the structured stream;
real roam-origin warnings must still be surfaced.
"""

from __future__ import annotations

import sys

from roam.cli import _stderr_showwarning


def _capture(monkeypatch, category, message="x") -> str:
    import io

    buf = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", buf)
    _stderr_showwarning(message, category, "<unknown>", 16)
    return buf.getvalue()


def test_syntaxwarning_is_dropped_from_structured_stream(monkeypatch):
    # compile-time interpreter noise (dependency codegen) — a buyer must not
    # see it in the JSON/PDF deliverable.
    assert _capture(monkeypatch, SyntaxWarning, "invalid escape sequence '\\d'") == ""


def test_syntaxwarning_subclass_also_dropped(monkeypatch):
    class MySyntaxWarning(SyntaxWarning):
        pass

    assert _capture(monkeypatch, MySyntaxWarning) == ""


def test_real_user_warning_is_still_surfaced(monkeypatch):
    out = _capture(monkeypatch, UserWarning, "genuine roam signal")
    assert "genuine roam signal" in out
    assert '"category": "UserWarning"' in out


def test_deprecation_warning_still_surfaced(monkeypatch):
    # Only SyntaxWarning (compile-time) is filtered; other categories that a
    # user could act on are preserved.
    out = _capture(monkeypatch, DeprecationWarning, "old flag")
    assert "old flag" in out
