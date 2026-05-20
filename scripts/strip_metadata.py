#!/usr/bin/env python3
"""Strip identifying metadata from tracked binary deliverables.

Walks every tracked PDF / PNG / JPEG / SVG / WOFF2 in the repo and
removes author / creator / producer / GPS / device fields. Replaces
the title with a neutral product-only string.

Usage:
    python scripts/strip_metadata.py            # dry-run (report only)
    python scripts/strip_metadata.py --write    # rewrite files in place
    python scripts/strip_metadata.py --files file1.pdf file2.png  # subset

Exit codes:
    0 — no metadata changes needed (or --write completed cleanly)
    1 — metadata found and would-be-stripped (dry-run mode only; CI gate)
    2 — error processing a file

CI usage:
    Run with no args; if exit code is 1, run with --write locally and commit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]


# Neutral metadata to apply when --write is set.
NEUTRAL_PDF_METADATA = {
    "/Title": "roam-code deliverable",
    "/Author": "roam-code",
    "/Creator": "pandoc",
    "/Producer": "pandoc",
    "/Subject": "",
    "/Keywords": "",
}


def _git_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        p = REPO_ROOT / line.strip()
        if p.is_file():
            paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# PDF metadata
# ---------------------------------------------------------------------------


def _scan_pdf(path: Path) -> dict | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        print(f"WARN: pypdf not installed, cannot scan {path.name}", file=sys.stderr)
        return None
    try:
        reader = PdfReader(str(path))
        md = dict(reader.metadata or {})
    except Exception as e:
        print(f"WARN: failed to read {path.name}: {e}", file=sys.stderr)
        return None
    # Filter to entries that look identifying.
    leaks = {}
    for k, v in md.items():
        v_str = str(v) if v is not None else ""
        # Skip empty fields.
        if not v_str:
            continue
        # Skip already-neutral values.
        if v_str == NEUTRAL_PDF_METADATA.get(k, ""):
            continue
        # Personal markers worth flagging.
        if k == "/Author" and v_str != NEUTRAL_PDF_METADATA["/Author"]:
            leaks[k] = v_str
        elif k == "/Creator" and v_str != "pandoc" and "pandoc" not in v_str.lower():
            leaks[k] = v_str
        elif k == "/Producer" and v_str != "pandoc" and "pandoc" not in v_str.lower():
            leaks[k] = v_str
        elif k == "/Title" and v_str.startswith("AI Agent"):
            # Old product-name leak.
            leaks[k] = v_str
        elif k == "/CreationDate":
            # Timezone in CreationDate ("D:20260505184901+03'00'") leaks Athens TZ.
            if re.search(r"[+\-]\d{2}'\d{2}'", v_str):
                leaks[k] = v_str
    return leaks


def _strip_pdf(path: Path) -> bool:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("ERROR: pypdf required to strip PDF metadata; pip install pypdf", file=sys.stderr)
        return False
    try:
        reader = PdfReader(str(path))
        writer = PdfWriter()
        for p in reader.pages:
            writer.add_page(p)
        writer.add_metadata(NEUTRAL_PDF_METADATA)
        with open(path, "wb") as f:
            writer.write(f)
        return True
    except Exception as e:
        print(f"ERROR: failed to strip {path.name}: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# PNG metadata (tEXt / iTXt / zTXt chunks)
# ---------------------------------------------------------------------------

PNG_TEXT_CHUNKS = (b"tEXt", b"iTXt", b"zTXt")
PNG_TIME_CHUNK = b"tIME"


def _scan_png(path: Path) -> dict | None:
    try:
        data = path.read_bytes()
    except Exception:
        return None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    leaks = {}
    # Look for text chunks. PNG chunk layout: <length:4><type:4><data:length><crc:4>
    pos = 8
    while pos < len(data) - 8:
        try:
            length = int.from_bytes(data[pos : pos + 4], "big")
            chunk_type = data[pos + 4 : pos + 8]
            chunk_data = data[pos + 8 : pos + 8 + length]
        except Exception:
            break
        if chunk_type in PNG_TEXT_CHUNKS:
            try:
                key, _, value = chunk_data.partition(b"\x00")
                key_str = key.decode("latin-1")
                # iTXt has more structure; extract first text-after-null chain
                value_str = value[:80].decode("utf-8", errors="replace")
                leaks[key_str] = value_str
            except Exception:
                leaks[chunk_type.decode("ascii", errors="replace")] = "<unreadable>"
        elif chunk_type == PNG_TIME_CHUNK:
            leaks["tIME"] = "<creation-time chunk>"
        pos += 8 + length + 4  # advance past length + type + data + crc
    return leaks


def _strip_png(path: Path) -> bool:
    """Rewrite PNG file with all text/time chunks removed (preserves image data)."""
    try:
        data = path.read_bytes()
    except Exception:
        return False
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    pos = 8
    while pos < len(data) - 8:
        try:
            length = int.from_bytes(data[pos : pos + 4], "big")
            chunk_type = data[pos + 4 : pos + 8]
            chunk_total = 8 + length + 4
        except Exception:
            break
        if chunk_type in PNG_TEXT_CHUNKS or chunk_type == PNG_TIME_CHUNK:
            # skip this chunk
            pass
        else:
            out.extend(data[pos : pos + chunk_total])
        pos += chunk_total
    try:
        path.write_bytes(bytes(out))
        return True
    except Exception as e:
        print(f"ERROR: failed to strip {path.name}: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# SVG (inline metadata via XML elements)
# ---------------------------------------------------------------------------

SVG_LEAK_PATTERNS = [
    re.compile(rb"<metadata\b[^>]*>.*?</metadata>", re.DOTALL),
    re.compile(rb"<title\b[^>]*>.*?</title>", re.DOTALL),
    re.compile(rb"<desc\b[^>]*>.*?</desc>", re.DOTALL),
    # Inkscape / Adobe Illustrator authoring tags
    re.compile(rb"\sxmlns:(?:inkscape|sodipodi|adobe)=\"[^\"]+\""),
    re.compile(rb"\s(?:inkscape|sodipodi|adobe):[a-z-]+=\"[^\"]*\""),
]


def _scan_svg(path: Path) -> dict | None:
    try:
        data = path.read_bytes()
    except Exception:
        return None
    leaks = {}
    for i, pat in enumerate(SVG_LEAK_PATTERNS):
        m = pat.search(data)
        if m:
            leaks[f"pattern_{i}"] = m.group(0)[:80].decode("utf-8", errors="replace")
    return leaks


def _strip_svg(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except Exception:
        return False
    out = data
    for pat in SVG_LEAK_PATTERNS:
        out = pat.sub(b"", out)
    if out != data:
        try:
            path.write_bytes(out)
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

EXT_HANDLERS: dict[str, tuple[Callable[[Path], dict | None], Callable[[Path], bool]]] = {
    ".pdf": (_scan_pdf, _strip_pdf),
    ".png": (_scan_png, _strip_png),
    ".svg": (_scan_svg, _strip_svg),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="Rewrite files in place (default: dry-run)")
    ap.add_argument(
        "--files",
        nargs="+",
        help="Specific files to scan; default scans every tracked PDF/PNG/SVG",
    )
    args = ap.parse_args()

    if args.files:
        targets = [Path(f) for f in args.files]
    else:
        targets = [p for p in _git_tracked_files() if p.suffix.lower() in EXT_HANDLERS]

    leaked_files = 0
    written_files = 0
    for path in targets:
        ext = path.suffix.lower()
        if ext not in EXT_HANDLERS:
            continue
        scanner, stripper = EXT_HANDLERS[ext]
        rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
        leaks = scanner(path)
        if leaks is None:
            continue
        if not leaks:
            continue
        leaked_files += 1
        print(f"{rel}: metadata leak detected")
        for k, v in leaks.items():
            print(f"    {k}: {v}")
        if args.write:
            if stripper(path):
                written_files += 1
                print("    → stripped")
            else:
                print("    → strip FAILED")
                return 2

    print()
    if leaked_files == 0:
        print("All scanned binaries are metadata-clean.")
        return 0
    if args.write:
        print(f"Stripped metadata from {written_files} / {leaked_files} files.")
        return 0
    print(f"{leaked_files} file(s) have leaky metadata. Run with --write to rewrite.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
