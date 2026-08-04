"""Every text reading a byte body admits, for pattern-based leak gates.

A UTF-16 file decoded as UTF-8 is read WRONG rather than failing: every NUL
padding byte is itself valid UTF-8, so ``errors="replace"`` drops nothing and
an ASCII payload arrives as ``g\\x00h\\x00p\\x00_\\x00...``, which matches no
credential pattern. The gate then reports clean over content it decoded
incorrectly — which is not the same as content it proved safe, and nothing in
the output distinguishes the two.

The vector is concrete rather than theoretical. Windows PowerShell's ``>``
redirect and ``Out-File`` emit UTF-16LE by default and roam-code is developed
on Windows, so ``gh auth token > token.txt`` followed by an accidental
``git add`` produces exactly a tracked file holding a live credential the gate
cannot see.

This is the package-side twin of ``scripts/internal_language_patterns.py``'s
``decode_views``. It is duplicated rather than imported because ``scripts/``
is not part of the installed wheel, and these gates ship: ``roam secrets
--fail-on-found`` is a blocking CI gate and ``verify``'s secret check rides
the Claude Stop hook.

The two implementations HAVE now drifted, and no test pins them: this one
grew the byte-transparent view described in ``decode_views`` and the
``scripts/`` twin has not. The twin's own wide-BOM miss is real but is a
prose-catalogue gate rather than a credential gate, so it is recorded here
rather than silently assumed equivalent.
"""

from __future__ import annotations

__all__ = ["decode_text", "decode_views"]

# A byte-order mark that routes the decode to a FIXED-WIDTH codec. These are
# the only branches below that can consume a byte < 0x80: utf-16 pairs bytes
# and utf-32 quads them, so an intact run of ASCII sitting in such a file is
# re-grouped into unrelated code points and disappears. The utf-8 branches
# cannot do this — see ``decode_views``.
_WIDE_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")


def decode_text(data: bytes) -> str:
    """Decode a text body, honouring a UTF-32/UTF-16/UTF-8 byte-order mark.

    ``errors="replace"`` throughout: an undecodable byte must not discard the
    rest of the file, because ASCII leaks after a bad byte still publish.
    """
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return data.decode("utf-32", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    return data.decode("utf-8", errors="replace")


def decode_views(data: bytes) -> list[str]:
    """Every reading of *data* a credential scan must be applied to.

    Three readings, because the failure shapes are different:

    * The BOM-directed decode handles the common case exactly.
    * The NUL-stripped reading is the only thing that catches BOM-LESS UTF-16,
      which the BOM decode cannot see by construction. It costs a second pass
      only on content that already holds embedded NULs, which no legitimate
      scannable text file here does.
    * The byte-transparent (latin-1) reading is the only thing that catches an
      intact ASCII credential in a file a WIDE BOM mis-routes. It costs a pass
      only on files carrying a UTF-16/UTF-32 BOM.

    On that third reading: a leading ``\\xff\\xfe`` sends the whole body to the
    UTF-16 decoder, which consumes bytes in PAIRS. Where the body is not in
    fact UTF-16 — a file PowerShell 5.1's ``>`` wrote as UTF-16LE and cmd.exe's
    ``>>`` then appended single-byte ANSI to, a common Windows mixed-encoding
    append — a contiguous ASCII token is re-grouped into unrelated CJK code
    points and vanishes, with the token still sitting intact and greppable on
    disk. Measured: ``"# creds\\n".encode("utf-16") + b"tok=ghp_<36>"`` scanned
    as 0 findings, 0 read errors. ``latin-1`` maps all 256 byte values 1:1, so
    every byte on disk appears verbatim and every ASCII run survives.

    The U+FFFD count is deliberately NOT the trigger, because it is not the
    signal. It is wrong in both directions, measured on this module:
    the even-length mixed-append above loses the credential with ZERO
    replacements, while latin-1 prose produces 80 replacements and loses
    nothing. What matters is whether the codec can consume an ASCII byte, not
    how many characters it replaced.

    The UTF-8 branches need no such reading, which is why the cost is not paid
    on the common path. ``errors="replace"`` there can only INSERT U+FFFD where
    a byte >= 0x80 already sat: a continuation byte must be 0x80-0xBF, so an
    ASCII byte is never absorbed into an error's maximal subpart and every
    ASCII run decodes to itself. Credential charsets (base64, hex, alnum,
    ``-``, ``_``) are ASCII-only, so a replacement character cannot land inside
    an intact credential — to put one there you must put a non-ASCII byte
    inside the token, which means the token on disk is not the credential.
    Verified exhaustively over every 1- and 2-byte non-ASCII prefix and by
    4,000 random-byte-soup trials: zero dropped ASCII runs.

    Line numbers are preserved within each view, so a caller that enumerates
    lines per view reports a usable location for whichever view matched. The
    latin-1 view is byte-transparent, so its line numbers are the file's
    physical byte lines.
    """
    primary = decode_text(data)
    views = [primary]
    if "\x00" in primary:
        views.append(primary.replace("\x00", ""))
    if data.startswith(_WIDE_BOMS):
        # Byte-transparent, so it cannot itself lose anything. Duplicate
        # findings across views are the accepted cost: a credential gate
        # reporting one leak twice is recoverable, reporting it zero times
        # is the entire failure mode.
        views.append(data.decode("latin-1"))
    return views
