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
the Claude Stop hook. A test pins the two implementations to the same
behaviour so they cannot drift.
"""

from __future__ import annotations

__all__ = ["decode_text", "decode_views"]


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

    Two readings, because the two failure shapes are different:

    * The BOM-directed decode handles the common case exactly.
    * The NUL-stripped reading is the only thing that catches BOM-LESS UTF-16,
      which the BOM decode cannot see by construction. It costs a second pass
      only on content that already holds embedded NULs, which no legitimate
      scannable text file here does.

    Line numbers are preserved within each view, so a caller that enumerates
    lines per view reports a usable location for whichever view matched.
    """
    primary = decode_text(data)
    if "\x00" not in primary:
        return [primary]
    return [primary, primary.replace("\x00", "")]
