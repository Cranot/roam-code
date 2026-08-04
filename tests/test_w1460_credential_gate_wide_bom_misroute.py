"""A credential intact on disk must not vanish into a mis-routed wide decode.

W1333 established that the credential gates must READ a file faithfully, not
decode it wrong and report clean. This is the residual hole in that fix.

``decode_text`` dispatches on the byte-order mark. A leading ``\\xff\\xfe``
sends the ENTIRE body to the UTF-16 decoder, which consumes bytes in PAIRS.
Where the body is not in fact UTF-16, a contiguous ASCII credential is
re-grouped into unrelated CJK code points and disappears -- while the token
sits intact and greppable on disk. The vector is a Windows mixed-encoding
append, which is how files on this repo's own platform get written:
PowerShell 5.1's ``>`` emits UTF-16LE with a BOM, cmd.exe's ``>>`` appends
single-byte ANSI, and the result is a UTF-16 BOM in front of a raw ASCII tail.

Measured before the fix, credential intact on disk in every row::

    plain utf-8                                    2 findings
    utf-16 BOM header + ANSI append                0 findings   <- invisible
    blob starting FF FE, ascii token later         0 findings   <- invisible

REFUTED, and pinned here so it is not re-litigated: the neighbouring theory
that ``errors="replace"`` splits a credential with U+FFFD. On the UTF-8 path a
replacement can only be INSERTED where a byte >= 0x80 already sat -- a
continuation byte must be 0x80-0xBF, so an ASCII byte is never absorbed into
an error's maximal subpart. Credential charsets are ASCII-only, so putting a
replacement inside a token requires putting a non-ASCII byte inside it, which
means the bytes on disk are not the credential. ``test_utf8_replacement_never_``
``splits_an_intact_credential`` holds that line.

The U+FFFD count is also pinned here as the WRONG disclosure signal: it is
wrong in both directions, and a fix keyed to it would be theatre.

Every positive case carries a negative control in the same file, so a change
that merely makes everything loud cannot pass: clean content must still report
zero findings and must not gain a view.

The credential is assembled at runtime -- this repo's pre-push gate correctly
blocks a literal token in a fixture, and bypassing it is not an option.
"""

from __future__ import annotations

import pathlib
import random
import re

import pytest

from roam.commands.cmd_secrets import scan_file
from roam.security.text_views import decode_text, decode_views

# Split so no line of this file is itself a credential-shaped literal.
_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8S9t0"
_LEAK_LINE = f"token = {_TOKEN}\n"
_CLEAN_LINE = "value = not a credential at all\n"

# A UTF-16LE BOM + UTF-16 body, exactly what PowerShell 5.1 `>` writes.
_PS_HEADER = "# creds\n".encode("utf-16")


def _mixed_append(tail: str) -> bytes:
    """PowerShell-written UTF-16 header with a cmd.exe ANSI tail appended."""
    return _PS_HEADER + tail.encode("ascii")


# Each is bytes holding the token as CONTIGUOUS ASCII -- the precondition that
# makes a clean verdict a miss rather than an honest read of broken bytes.
_MISROUTES = {
    "ps_utf16_header_ansi_append_odd": _mixed_append(_LEAK_LINE),
    "ps_utf16_header_ansi_append_even": _mixed_append(_LEAK_LINE + "\n"),
    "blob_leading_ff_fe": b"\xff\xfe\x01\x02\x03\x04\n" + _LEAK_LINE.encode(),
    "blob_leading_fe_ff": b"\xfe\xff\x01\x02\x03\x04\n" + _LEAK_LINE.encode(),
    "utf32_bom_then_ascii": b"\xff\xfe\x00\x00" + _LEAK_LINE.encode(),
}

_CLEAN_MISROUTES = {
    "ps_utf16_header_ansi_append_odd": _PS_HEADER + _CLEAN_LINE.encode("ascii"),
    "blob_leading_ff_fe": b"\xff\xfe\x01\x02\x03\x04\n" + _CLEAN_LINE.encode(),
    "utf32_bom_then_ascii": b"\xff\xfe\x00\x00" + _CLEAN_LINE.encode(),
}


def _write(tmp_path: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# The precondition every positive case rests on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_MISROUTES))
def test_the_credential_really_is_intact_on_disk(label):
    """Without this the whole suite proves nothing: a scanner is entitled to
    miss a token that is not actually there."""
    assert _TOKEN.encode("ascii") in _MISROUTES[label], label


# ---------------------------------------------------------------------------
# decode_views — the shared primitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_MISROUTES))
def test_some_view_surfaces_a_credential_a_wide_bom_misroutes(label):
    assert any(_TOKEN in view for view in decode_views(_MISROUTES[label])), label


@pytest.mark.parametrize("label", sorted(_MISROUTES))
def test_the_primary_view_alone_cannot_see_it(label):
    """The extra view is load-bearing, not decorative -- if the primary decode
    ever starts finding these, this suite has stopped testing anything."""
    assert _TOKEN not in decode_text(_MISROUTES[label]), label


def test_the_extra_view_is_byte_transparent():
    """latin-1 maps all 256 byte values 1:1, so the added view cannot itself
    lose anything -- the property the fix depends on."""
    data = bytes(range(256))
    assert data.decode("latin-1").encode("latin-1") == data


# ---------------------------------------------------------------------------
# Negative controls — a "make everything loud" change dies here
# ---------------------------------------------------------------------------


def test_plain_utf8_still_costs_exactly_one_view():
    """The common path must not pay for this. W1333 pinned this too; it stays
    pinned, because a fix that doubles every scan is not free."""
    assert len(decode_views(_LEAK_LINE.encode("utf-8"))) == 1
    assert len(decode_views(_CLEAN_LINE.encode("utf-8"))) == 1


def test_bomless_utf16_still_costs_exactly_two_views():
    views = decode_views(_LEAK_LINE.encode("utf-16-le"))
    assert len(views) == 2
    assert _TOKEN in views[1]


def test_utf8_bom_does_not_trigger_the_extra_view():
    """utf-8-sig is ASCII-preserving, so it needs no byte-transparent reading."""
    assert len(decode_views(_LEAK_LINE.encode("utf-8-sig"))) == 1


@pytest.mark.parametrize("label", sorted(_CLEAN_MISROUTES))
def test_clean_content_in_a_misrouted_file_reports_nothing(tmp_path, label):
    path = _write(tmp_path, f"clean_{label}.txt", _CLEAN_MISROUTES[label])
    assert scan_file(str(path), min_severity="all") == [], label


def test_well_formed_utf16_stays_clean_and_stays_readable(tmp_path):
    """A genuine UTF-16 file must not become noisy just because it has a BOM."""
    clean = _write(tmp_path, "clean_utf16.txt", _CLEAN_LINE.encode("utf-16"))
    assert scan_file(str(clean), min_severity="all") == []
    leak = _write(tmp_path, "leak_utf16.txt", _LEAK_LINE.encode("utf-16"))
    assert scan_file(str(leak), min_severity="all"), "W1333 regression"


def test_the_extra_view_does_not_double_report(tmp_path):
    """Adding a third reading must not turn one leak into two findings."""
    utf8 = _write(tmp_path, "one_utf8.txt", _LEAK_LINE.encode("utf-8"))
    utf16 = _write(tmp_path, "one_utf16.txt", _LEAK_LINE.encode("utf-16"))
    assert len(scan_file(str(utf16), min_severity="all")) == len(scan_file(str(utf8), min_severity="all"))


# ---------------------------------------------------------------------------
# roam secrets — the blocking CI gate (--fail-on-found)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_MISROUTES))
def test_secrets_scan_file_reports_the_misrouted_credential(tmp_path, label):
    path = _write(tmp_path, f"leak_{label}.txt", _MISROUTES[label])
    read_errors: list[dict] = []
    findings = scan_file(str(path), min_severity="all", read_errors=read_errors)
    assert findings, f"{label}: credential intact on disk reported CLEAN"
    assert read_errors == [], f"{label}: must be a finding, not a read failure"


# ---------------------------------------------------------------------------
# verify --auto secret check — the Claude Stop-hook gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_MISROUTES))
def test_verify_secret_scan_views_surface_the_credential(tmp_path, label):
    from roam.commands.cmd_verify import _read_secret_scan_views

    path = _write(tmp_path, f"verify_{label}.txt", _MISROUTES[label])
    views = _read_secret_scan_views(path)
    assert views is not None, label
    assert any(_TOKEN in view for view in views), label


# ---------------------------------------------------------------------------
# The refutation, pinned
# ---------------------------------------------------------------------------


def test_utf8_replacement_never_splits_an_intact_credential():
    """No non-ASCII prefix can make the UTF-8 replace decoder eat ASCII."""
    payload = _TOKEN.encode("ascii")
    for lead in range(0x80, 0x100):
        for second in [None, *range(0x00, 0x100)]:
            prefix = bytes([lead]) if second is None else bytes([lead, second])
            decoded = (prefix + payload).decode("utf-8", errors="replace")
            assert _TOKEN in decoded, f"prefix {prefix!r} destroyed an intact token"


def test_utf8_path_never_drops_an_intact_ascii_run():
    """Random byte soup: every contiguous printable-ASCII run survives."""
    run_re = re.compile(rb"[\x20-\x7e]{8,}")
    rng = random.Random(1460)
    for _ in range(1500):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(1, 120)))
        if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
            continue  # wide/utf-8-sig BOM branches are covered above
        decoded = decode_text(data)
        for match in run_re.finditer(data):
            assert match.group().decode("ascii") in decoded, data


def test_replacement_count_is_the_wrong_disclosure_signal():
    """Pins WHY the fix is not keyed to U+FFFD: the count is wrong in both
    directions, so a gate built on it would warn where nothing was lost and
    stay silent where a credential was."""
    lost_silently = _MISROUTES["ps_utf16_header_ansi_append_even"]
    views = decode_views(lost_silently)
    assert sum(view.count("�") for view in views) == 0
    assert _TOKEN not in decode_text(lost_silently), "credential lost with 0 replacements"

    noisy_but_lossless = ("caf\xe9 na\xefve r\xe9sum\xe9 " * 20).encode("latin-1")
    decoded = decode_text(noisy_but_lossless)
    assert decoded.count("�") > 50, "many replacements"
    for run in re.findall(rb"[\x20-\x7e]{8,}", noisy_but_lossless):
        assert run.decode("ascii") in decoded, "yet nothing scannable was lost"
