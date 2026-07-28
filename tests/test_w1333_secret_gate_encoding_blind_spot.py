"""The credential gates must see a UTF-16 file, not decode it wrong and pass.

``errors="replace"`` reads a UTF-16 body WRONG rather than failing: every NUL
padding byte is itself valid UTF-8, so nothing is dropped and an ASCII payload
arrives as ``g\\x00h\\x00p\\x00_\\x00...``, matching no credential pattern. The
gate then reports clean over content it decoded incorrectly — which is not the
same as content it proved safe, and nothing in the output distinguishes them.

Concrete, not theoretical: Windows PowerShell's ``>`` redirect and ``Out-File``
emit UTF-16LE by default and roam-code is developed on Windows, so
``gh auth token > token.txt`` plus an accidental ``git add`` produces exactly a
tracked file holding a live credential. Both affected gates are blocking:
``roam secrets --fail-on-found`` gates CI, and ``verify``'s secret check rides
``verify --auto`` and therefore the Claude Stop hook.

Measured before the fix — same credential, three encodings:

    utf8_leak.py     4 findings
    utf16_bom.py     0 findings      <- BOM, invisible
    utf16_nobom.py   0 findings      <- BOM-less, invisible

Every positive case here carries a negative control in the same test, so a
"make everything loud" change cannot pass: a clean file must still report zero.

The credential is assembled at runtime. The repo's own pre-push gate correctly
blocks a literal token in a fixture, and bypassing it is not an option.
"""

from __future__ import annotations

import pathlib

import pytest

from roam.security.text_views import decode_text, decode_views

# Split so no line of this file is itself a credential-shaped literal.
_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8S9t0"
_LEAK_BODY = f'TOKEN = "{_TOKEN}"\n'
_CLEAN_BODY = 'VALUE = "not a credential at all"\n'

_ENCODINGS = {
    "utf8": lambda text: text.encode("utf-8"),
    "utf8_bom": lambda text: text.encode("utf-8-sig"),
    "utf16_bom": lambda text: text.encode("utf-16"),
    "utf16_le_nobom": lambda text: text.encode("utf-16-le"),
    "utf16_be_nobom": lambda text: text.encode("utf-16-be"),
}


def _write(tmp_path: pathlib.Path, name: str, body: str, encoder) -> pathlib.Path:
    path = tmp_path / name
    path.write_bytes(encoder(body))
    return path


# ---------------------------------------------------------------------------
# decode_views — the shared primitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_decode_views_surfaces_the_payload_for_every_encoding(label):
    """Some view must contain the readable token, whatever the encoding."""
    data = _ENCODINGS[label](_LEAK_BODY)
    assert any(_TOKEN in view for view in decode_views(data)), label


def test_decode_views_costs_nothing_extra_when_there_are_no_nuls():
    """A plain UTF-8 body yields exactly one view — the common case is free."""
    assert len(decode_views(_LEAK_BODY.encode("utf-8"))) == 1


def test_decode_views_adds_the_nul_stripped_reading_only_when_needed():
    """BOM-less UTF-16 is ONLY visible in the second view, by construction."""
    views = decode_views(_LEAK_BODY.encode("utf-16-le"))
    assert len(views) == 2
    assert _TOKEN not in views[0], "BOM-less UTF-16 cannot be read by the primary decode"
    assert _TOKEN in views[1], "the NUL-stripped view is what catches it"


def test_decode_text_honours_a_bom_without_guessing():
    assert _TOKEN in decode_text(_LEAK_BODY.encode("utf-16"))
    assert _TOKEN in decode_text(_LEAK_BODY.encode("utf-8-sig"))


# ---------------------------------------------------------------------------
# roam secrets — the blocking CI gate (--fail-on-found)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_secrets_scan_file_finds_the_credential_in_every_encoding(tmp_path, label):
    from roam.commands.cmd_secrets import scan_file

    leaked = _write(tmp_path, f"leak_{label}.py", _LEAK_BODY, _ENCODINGS[label])
    assert scan_file(str(leaked), min_severity="all"), f"{label}: credential not detected"


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_secrets_scan_file_stays_quiet_on_clean_content(tmp_path, label):
    """Negative control: a fix that merely makes everything loud fails here."""
    from roam.commands.cmd_secrets import scan_file

    clean = _write(tmp_path, f"clean_{label}.py", _CLEAN_BODY, _ENCODINGS[label])
    assert scan_file(str(clean), min_severity="all") == [], f"{label}: false positive"


def test_secrets_scan_file_does_not_double_report_across_views(tmp_path):
    """Two agreeing readings of one line must not become two findings."""
    from roam.commands.cmd_secrets import scan_file

    utf8 = _write(tmp_path, "one_utf8.py", _LEAK_BODY, _ENCODINGS["utf8"])
    utf16 = _write(tmp_path, "one_utf16.py", _LEAK_BODY, _ENCODINGS["utf16_le_nobom"])
    assert len(scan_file(str(utf16), min_severity="all")) == len(scan_file(str(utf8), min_severity="all"))


# ---------------------------------------------------------------------------
# verify --auto secret check — the Claude Stop-hook gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_verify_secret_reader_surfaces_every_encoding(tmp_path, label):
    from roam.commands.cmd_verify import _read_secret_scan_views

    leaked = _write(tmp_path, f"v_{label}.py", _LEAK_BODY, _ENCODINGS[label])
    views = _read_secret_scan_views(leaked)
    assert views is not None, f"{label}: reader returned None"
    assert any(_TOKEN in view for view in views), f"{label}: credential invisible to verify"


def test_verify_secret_reader_returns_none_for_an_unreadable_path(tmp_path):
    """A missing file is still None, not an empty success."""
    from roam.commands.cmd_verify import _read_secret_scan_views

    assert _read_secret_scan_views(tmp_path / "does-not-exist.py") is None


def test_verify_check_secrets_flags_a_utf16_credential(tmp_path):
    """End-to-end through the real check, not just its reader."""
    from roam.commands.cmd_verify import _check_secrets

    _write(tmp_path, "leak.py", _LEAK_BODY, _ENCODINGS["utf16_le_nobom"])
    result = _check_secrets(["leak.py"], tmp_path)
    assert result.get("violations"), "UTF-16 credential did not reach _check_secrets violations"


def test_verify_check_secrets_stays_clean_on_clean_content(tmp_path):
    """Negative control for the end-to-end path."""
    from roam.commands.cmd_verify import _check_secrets

    _write(tmp_path, "fine.py", _CLEAN_BODY, _ENCODINGS["utf16_le_nobom"])
    assert not _check_secrets(["fine.py"], tmp_path).get("violations")
