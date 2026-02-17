"""Visual FoxPro language extractor (regex-only, no tree-sitter)."""

from __future__ import annotations

import logging
import os
import re
import struct
from .base import LanguageExtractor

log = logging.getLogger(__name__)


# ── Encoding detection ────────────────────────────────────────────────

# Windows codepages commonly used with VFP, ordered by global prevalence.
# VFP is a Windows-era tool; .prg files almost always use a Windows codepage
# matching the developer's locale (cp1252 Western, cp1251 Cyrillic, cp1253
# Greek, cp1254 Turkish, cp1250 Central European, cp932 Japanese, cp936
# Simplified Chinese, cp949 Korean, cp950 Traditional Chinese, etc.).
# Latin-1 (iso-8859-1) is the final fallback — it maps every byte 0x00-0xFF
# to a codepoint, so it never raises UnicodeDecodeError.
_FALLBACK_CODEPAGES = ("cp1252", "cp1251", "cp1250", "cp1253", "cp1254",
                       "cp1255", "cp1256", "cp932", "cp936", "cp949",
                       "cp950", "latin-1")


def _decode_source(source: bytes) -> str:
    """Decode VFP source bytes to str with smart encoding detection.

    Strategy (zero external dependencies):
    1. BOM detection (UTF-8-BOM, UTF-16 LE/BE)
    2. Strict UTF-8 (modern editors may have re-saved legacy files)
    3. Heuristic: try common Windows codepages — pick the first that
       decodes cleanly AND produces the most printable characters
    4. Latin-1 fallback (always succeeds)
    """
    if not source:
        return ""

    # 1. BOM detection
    if source[:3] == b"\xef\xbb\xbf":
        return source[3:].decode("utf-8", errors="replace")
    if source[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return source.decode("utf-16")

    # 2. Strict UTF-8
    try:
        return source.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 3. Heuristic codepage selection — try each and score by printability
    best_text = None
    best_score = -1
    # Only sample up to 8 KB for speed on large files
    sample = source[:8192]
    for cp in _FALLBACK_CODEPAGES:
        try:
            text = sample.decode(cp)
        except (UnicodeDecodeError, LookupError):
            continue
        # Score: count of printable chars (letters, digits, whitespace, common punct)
        score = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
        if score > best_score:
            best_score = score
            best_text = cp

    if best_text and best_text != "latin-1":
        try:
            return source.decode(best_text)
        except UnicodeDecodeError:
            pass

    # 4. Latin-1 always works (every byte maps to a codepoint)
    return source.decode("latin-1")


# ── Preprocessing helpers ─────────────────────────────────────────────

def _preprocess(source: bytes) -> tuple[list[str], dict[int, int]]:
    """Join continuation lines, strip comments, build line map.

    Returns:
        processed_lines: list of cleaned source lines (0-indexed)
        line_map: dict mapping processed-line-index -> original 1-based line number
    """
    text = _decode_source(source)
    raw_lines = text.split("\n")

    # Phase 1: join continuation lines (`;` at end of line in VFP)
    joined: list[tuple[str, int]] = []  # (line_text, original_1based_line)
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        orig_line = i + 1
        # VFP continuation: line ends with `;` (possibly followed by whitespace)
        while line.rstrip().endswith(";") and i + 1 < len(raw_lines):
            line = line.rstrip()[:-1] + " " + raw_lines[i + 1].lstrip()
            i += 1
        joined.append((line, orig_line))
        i += 1

    # Phase 2: strip comments, build line_map
    processed: list[str] = []
    line_map: dict[int, int] = {}
    in_block_comment = False

    for idx, (line, orig) in enumerate(joined):
        stripped = line.lstrip()

        # Block comment: *!* ... *!* (VFP disabled code blocks)
        # We treat lines starting with *!* as comment toggles
        if stripped.startswith("*!*"):
            in_block_comment = not in_block_comment
            line_map[len(processed)] = orig
            processed.append("")
            continue

        if in_block_comment:
            line_map[len(processed)] = orig
            processed.append("")
            continue

        # Full-line comments: lines starting with * or NOTE
        if stripped.startswith("*") or stripped.upper().startswith("NOTE "):
            line_map[len(processed)] = orig
            processed.append("")
            continue

        # Inline comments: && to end of line (but not inside strings)
        clean = _strip_inline_comment(line)
        line_map[len(processed)] = orig
        processed.append(clean)

    return processed, line_map


def _strip_inline_comment(line: str) -> str:
    """Remove && inline comment, respecting quoted strings."""
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '&' and not in_single and not in_double:
            if i + 1 < len(line) and line[i + 1] == '&':
                return line[:i].rstrip()
        i += 1
    return line


# ── Regex patterns ────────────────────────────────────────────────────

_RE_FUNC = re.compile(
    r"^\s*(FUNCTION|PROCEDURE)\s+(\w+)",
    re.IGNORECASE,
)
_RE_ENDFUNC = re.compile(
    r"^\s*(ENDFUNC|ENDPROC)\b",
    re.IGNORECASE,
)
_RE_CLASS = re.compile(
    r"^\s*DEFINE\s+CLASS\s+(\w+)\s+AS\s+(\w+)(?:\s+OF\s+(\S+))?",
    re.IGNORECASE,
)
_RE_ENDDEFINE = re.compile(
    r"^\s*ENDDEFINE\b",
    re.IGNORECASE,
)
_RE_DEFINE_CONST = re.compile(
    r"^\s*#DEFINE\s+(\w+)\s+(.*)",
    re.IGNORECASE,
)
_RE_PROPERTY = re.compile(
    r"^\s*(\w+)\s*=\s*(.+)",
)

# Reference patterns
_RE_DO_FILE = re.compile(
    r"^\s*DO\s+(\w+)(?:\s+WITH\b)?",
    re.IGNORECASE,
)
_RE_DO_IN = re.compile(
    r"^\s*DO\s+(\w+)\s+IN\s+(\S+)",
    re.IGNORECASE,
)
_RE_SET_PROC = re.compile(
    r"^\s*SET\s+PROCEDURE\s+TO\s+(\S+)",
    re.IGNORECASE,
)
_RE_SET_CLASSLIB = re.compile(
    r"^\s*SET\s+CLASSLIB\s+TO\s+(\S+)",
    re.IGNORECASE,
)
_RE_INCLUDE = re.compile(
    r'^\s*#INCLUDE\s+["\']?([^"\']+)',
    re.IGNORECASE,
)
_RE_CREATEOBJ = re.compile(
    r'\bCREATEOBJECT\s*\(\s*["\'](\w+)["\']',
    re.IGNORECASE,
)
_RE_NEWOBJ = re.compile(
    r'\bNEWOBJECT\s*\(\s*["\'](\w+)["\']\s*,\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_DECLARE = re.compile(
    r"^\s*DECLARE\s+\w+\s+(\w+)\s+IN\s+(\S+)",
    re.IGNORECASE,
)

# =funcname(args) — VFP expression call (discard return value)
_RE_EXPR_CALL = re.compile(
    r"^\s*=(\w+)\s*\(",
    re.IGNORECASE,
)

# DO FORM formname — opens a form (links .prg → .scx)
_RE_DO_FORM = re.compile(
    r"^\s*DO\s+FORM\s+(\S+)",
    re.IGNORECASE,
)

# obj.method(args) — method call on an object (mid-line search)
# Matches THIS.method(), THISFORM.method(), var.method()
_RE_METHOD_CALL = re.compile(
    r"\b(\w+)\.(\w+)\s*\(",
)

# VFP built-in functions — comprehensive list from VFP9 language reference.
# Used to exclude built-in calls from reference extraction (reduces noise).
# Source: hackfox.github.io/section4/, vfphelp.com VFP9 docs
_VFP_BUILTINS = frozenset({
    # ── Math ──
    "ABS", "ACOS", "ASIN", "ATN2", "CEILING", "COS", "EXP", "FLOOR",
    "FV", "INT", "LOG", "LOG10", "MAX", "MIN", "MOD", "PI", "PV",
    "RAND", "ROUND", "SIGN", "SIN", "SQRT", "TAN", "VAL",
    # ── String ──
    "ALLTRIM", "ASC", "AT", "ATC", "ATCC", "ATCLINE", "ATLINE", "AT_C",
    "CHR", "CHRTRAN", "CHRTRANC", "CMONTH", "CDOW", "DIFFERENCE",
    "GETWORDCOUNT", "GETWORDNUM", "LEFT", "LEFTC", "LEN", "LENC",
    "LIKE", "LIKEC", "LOWER", "LTRIM", "MLINE", "MEMLINES", "OCCURS",
    "PADL", "PADC", "PADR", "PROPER", "RAT", "RATC", "RATLINE",
    "REPLICATE", "RIGHT", "RIGHTC", "RTRIM", "SOUNDEX", "SPACE",
    "STR", "STRCONV", "STREXTRACT", "STRTRAN", "STUFF", "STUFFC",
    "SUBSTR", "SUBSTRC", "TEXTMERGE", "TRANSFORM", "TRIM", "UPPER",
    # ── Date / Time ──
    "CDOW", "CMONTH", "CTOD", "CTOT", "DATE", "DATETIME", "DAY",
    "DMY", "DOW", "DTOC", "DTOR", "DTOS", "DTOT", "GOMONTH",
    "HOUR", "MDY", "MINUTE", "MONTH", "QUARTER", "SEC", "SECONDS",
    "TIME", "TTOC", "TTOD", "WEEK", "YEAR",
    # ── Type / Conversion ──
    "BINTOC", "CAST", "CTOBIN", "CPCONVERT", "CPCURRENT",
    "EMPTY", "EVALUATE", "EVL", "IIF", "ICASE",
    "ISALPHA", "ISBLANK", "ISDIGIT", "ISLEADBYTE", "ISLOWER",
    "ISNULL", "ISUPPER", "NVL", "TYPE", "VARTYPE",
    # ── File I/O ──
    "ADDBS", "CURDIR", "DEFAULTEXT", "DIRECTORY", "DRIVETYPE",
    "FCHSIZE", "FCLOSE", "FCOUNT", "FCREATE", "FDATE", "FEOF",
    "FERROR", "FFLUSH", "FGETS", "FILE", "FILETOSTR", "FLDCOUNT",
    "FLOCK", "FOPEN", "FORCEEXT", "FORCEPATH", "FPUTS", "FREAD",
    "FSEEK", "FSIZE", "FTIME", "FULLPATH", "FWRITE",
    "GETDIR", "GETFILE", "HOME", "JUSTDRIVE", "JUSTEXT",
    "JUSTFNAME", "JUSTPATH", "JUSTSTEM", "LOCFILE", "PUTFILE",
    "STRTOFILE",
    # ── Cursor / Table ──
    "ALIAS", "BOF", "CANDIDATE", "CDX", "CPDBF", "CURSORGETPROP",
    "CURSORSETPROP", "CURSORTOXML", "CURVAL", "DBF", "DBGETPROP",
    "DBSETPROP", "DBUSED", "DBC", "EOF", "FIELD", "FILTER", "FOUND",
    "GETFLDSTATE", "GETNEXTMODIFIED", "HEADER", "IDXCOLLATE", "INDBC",
    "INDEXSEEK", "ISEXCLUSIVE", "ISFLOCKED", "ISMARKED", "ISREADONLY",
    "ISRLOCKED", "KEY", "KEYMATCH", "LOCK", "LOOKUP", "LUPDATE",
    "MTON", "NTOM", "OLDVAL", "ORDER", "RECCOUNT", "RECNO", "RECSIZE",
    "RELATION", "REQUERY", "RLOCK", "SEEK", "SELECT", "SETFLDSTATE",
    "TABLEUPDATE", "TABLEREVERT", "TAG", "TAGCOUNT", "TAGNO", "TARGET",
    "TXNLEVEL", "USED", "XMLTOCURSOR", "XMLUPDATEGRAM",
    # ── Array ──
    "ACOPY", "ADATABASES", "ADBOBJECTS", "ADEL", "ADIR", "AELEMENT",
    "AERROR", "AFIELDS", "AFONT", "AGETCLASS", "AGETFILEVERSION",
    "AINS", "AINSTANCE", "ALANGUAGE", "ALEN", "ALINES", "AMEMBERS",
    "ANETRESOURCES", "APRINTERS", "APROCINFO", "ASCAN", "ASELOBJ",
    "ASESSIONS", "ASORT", "ASTACKINFO", "ASUBSCRIPT", "AUSED",
    "AVCXCLASSES",
    # ── Object / Class ──
    "ACLASS", "COMPOBJ", "COMPROP", "CREATEOBJECT", "DODEFAULT",
    "GETINTERFACE", "GETOBJECT", "GETPEM", "NEWOBJECT",
    "PEMSTATUS",
    # ── UI / Display ──
    "BAR", "BARCOUNT", "BARPROMPT", "CAPSLOCK", "CNTBAR", "CNTPAD",
    "FONTMETRIC", "GETCOLOR", "GETCP", "GETEXPR", "GETFONT",
    "GETPICT", "GETPRINTER", "INKEY", "INPUTBOX", "INSMODE",
    "LASTKEY", "MCOL", "MDOWN", "MESSAGEBOX", "MROW", "MWINDOW",
    "NUMLOCK", "OBJNUM", "OBJTOCLIENT", "OBJVAR", "PAD", "PRMBAR",
    "PRMPAD", "PROMPT", "PRTINFO", "RGB", "RGBSCHEME", "ROW",
    "SCHEME", "SCOLS", "SKPBAR", "SKPPAD", "SROWS", "SYSMETRIC",
    "TXTWIDTH", "WBORDER", "WCHILD", "WCOLS", "WDOCKABLE", "WEXIST",
    "WFONT", "WLAST", "WLCOL", "WLROW", "WMAXIMUM", "WMINIMUM",
    "WONTOP", "WOUTPUT", "WPARENT", "WREAD", "WROWS", "WTITLE",
    "WVISIBLE",
    # ── System / Environment ──
    "DISKSPACE", "EXECSCRIPT", "GETENV", "GETHOST", "LINENO",
    "MEMORY", "MESSAGE", "ON", "OS", "PARAMETERS", "PCOUNT",
    "PRINTSTATUS", "PROGRAM", "RDLEVEL", "SET", "SYS", "VERSION",
    # ── Bitwise ──
    "BITAND", "BITCLEAR", "BITLSHIFT", "BITNOT", "BITOR",
    "BITRSHIFT", "BITSET", "BITTEST", "BITXOR",
    # ── Event binding ──
    "BINDEVENT", "RAISEEVENT", "UNBINDEVENTS",
    # ── Miscellaneous ──
    "ANSITOOEM", "BETWEEN", "CHRSAW", "INLIST", "OEMTOANSI",
    "TEXTWIDTH", "VARREAD",
})

# Keywords that look like DO but aren't file calls
# Note: FORM is NOT here — DO FORM is handled separately by _RE_DO_FORM
_DO_KEYWORDS = frozenset({"CASE", "WHILE"})


# ── SCX/SCT binary parsing ───────────────────────────────────────────
# .SCX = DBF (dBASE) format with field descriptors for form elements
# .SCT = FPT (memo) format storing actual text/binary content
# Packed together by parse_file as: 4-byte-big-endian-scx-length + scx + sct

def _unpack_scx_sct(source: bytes) -> tuple[bytes, bytes]:
    """Split packed bytes back into (scx_bytes, sct_bytes)."""
    if len(source) < 4:
        return source, b""
    scx_len = struct.unpack(">I", source[:4])[0]
    scx_bytes = source[4:4 + scx_len]
    sct_bytes = source[4 + scx_len:]
    return scx_bytes, sct_bytes


def _get_fpt_block_size(sct_bytes: bytes) -> int:
    """Read FPT header block size (bytes 6-7, big-endian). Default 64."""
    if len(sct_bytes) < 8:
        return 64
    bs = struct.unpack(">H", sct_bytes[6:8])[0]
    return bs if bs > 0 else 64


def _read_fpt_memo(sct_bytes: bytes, block_num: int, block_size: int) -> bytes | None:
    """Read raw memo data from an FPT block reference."""
    if block_num == 0:
        return None
    offset = block_num * block_size
    if offset + 8 > len(sct_bytes):
        return None
    header = sct_bytes[offset:offset + 8]
    data_len = struct.unpack(">I", header[4:8])[0]
    if data_len == 0 or data_len > 10_000_000:
        return None
    end = offset + 8 + data_len
    if end > len(sct_bytes):
        return None
    return sct_bytes[offset + 8:end]


def _read_fpt_memo_text(sct_bytes: bytes, block_num: int, block_size: int) -> str:
    """Read memo as decoded text using _decode_source."""
    data = _read_fpt_memo(sct_bytes, block_num, block_size)
    if data is None:
        return ""
    return _decode_source(data)


_MAX_SCX_RECORDS = 50_000  # Safety cap for corrupted headers


def _scx_get_char(rec_data, fields, name):
    """Read a character field from a DBF record."""
    if name in fields:
        fi = fields[name]
        start = fi["offset"]
        end = start + fi["size"]
        if end <= len(rec_data):
            return rec_data[start:end].decode("ascii", errors="replace").strip()
    return ""


def _scx_get_memo_ref(rec_data, fields, name):
    """Read a memo block reference from a DBF record."""
    if name in fields and fields[name]["type"] == "M":
        fi = fields[name]
        start = fi["offset"]
        if start + 4 <= len(rec_data):
            try:
                return struct.unpack("<I", rec_data[start:start + 4])[0]
            except struct.error:
                return 0
    return 0


def _scx_get_memo_text(rec_data, fields, name, sct_bytes, block_size, has_sct):
    """Read memo text from an FPT file for a given field."""
    if not has_sct:
        return ""
    ref = _scx_get_memo_ref(rec_data, fields, name)
    return _read_fpt_memo_text(sct_bytes, ref, block_size)


def _parse_single_scx_record(rec_num, rec_data, fields, sct_bytes,
                              block_size, has_sct):
    """Parse a single SCX record from raw bytes."""
    deleted = rec_data[0] == ord("*")
    platform = _scx_get_char(rec_data, fields, "PLATFORM")

    def memo(name):
        return _scx_get_memo_text(rec_data, fields, name, sct_bytes, block_size, has_sct)

    return {
        "record_num": rec_num,
        "deleted": deleted,
        "platform": platform,
        "objname": memo("OBJNAME"),
        "parent": memo("PARENT"),
        "class_name": memo("CLASS"),
        "classloc": memo("CLASSLOC"),
        "baseclass": memo("BASECLASS"),
        "methods": memo("METHODS"),
    }


def _parse_scx_records(scx_bytes: bytes, sct_bytes: bytes) -> list[dict]:
    """Parse DBF structure from .scx bytes, resolve memo fields from .sct.

    Returns list of record dicts with keys:
        record_num, deleted, platform, objname, parent, class_name,
        classloc, baseclass, methods
    """
    if len(scx_bytes) < 32:
        return []

    # DBF header
    version = scx_bytes[0]
    valid_versions = {0x02, 0x03, 0x04, 0x05, 0x30, 0x31, 0x32,
                      0x43, 0x63, 0x83, 0x8B, 0xCB, 0xF5, 0xFB}
    if version not in valid_versions:
        return []

    try:
        num_records = struct.unpack("<I", scx_bytes[4:8])[0]
        header_size = struct.unpack("<H", scx_bytes[8:10])[0]
        record_size = struct.unpack("<H", scx_bytes[10:12])[0]
    except struct.error:
        return []

    if record_size == 0 or num_records == 0:
        return []
    if header_size < 32:
        return []
    if num_records > _MAX_SCX_RECORDS:
        log.warning("SCX claims %d records (capped at %d)", num_records, _MAX_SCX_RECORDS)
        num_records = _MAX_SCX_RECORDS

    # Parse field descriptors (starting at byte 32)
    fields: dict[str, dict] = {}
    pos = 32
    while pos + 32 <= min(len(scx_bytes), header_size):
        if scx_bytes[pos] == 0x0D:
            break
        try:
            name = scx_bytes[pos:pos + 11].split(b"\x00")[0].decode("ascii", errors="replace")
            ftype = chr(scx_bytes[pos + 11])
            displacement = struct.unpack("<I", scx_bytes[pos + 12:pos + 16])[0]
            size = scx_bytes[pos + 16]
        except (struct.error, IndexError):
            break
        if displacement < record_size:
            fields[name] = {"type": ftype, "offset": displacement, "size": size}
        pos += 32

    block_size = _get_fpt_block_size(sct_bytes) if sct_bytes else 64
    has_sct = len(sct_bytes) >= 512

    records = []
    for rec_num in range(num_records):
        rec_offset = header_size + rec_num * record_size
        if rec_offset + record_size > len(scx_bytes):
            break
        rec_data = scx_bytes[rec_offset:rec_offset + record_size]
        try:
            rec = _parse_single_scx_record(rec_num, rec_data, fields, sct_bytes,
                                           block_size, has_sct)
            records.append(rec)
        except Exception as exc:
            log.debug("Skipping SCX record %d: %s", rec_num, exc)
            continue

    return records


def _extract_procedures(methods_text: str) -> list[dict]:
    """Split PROCEDURE/FUNCTION blocks from a Methods field.

    Returns list of dicts with keys: type, name, code.
    """
    if not methods_text:
        return []
    procedures = []
    current_name = None
    current_type = None
    current_lines: list[str] = []

    for line in methods_text.split("\n"):
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("PROCEDURE ") or upper.startswith("FUNCTION "):
            if current_name is not None:
                procedures.append({
                    "type": current_type,
                    "name": current_name,
                    "code": "\n".join(current_lines),
                })
            parts = stripped.split(None, 1)
            current_type = parts[0].upper()
            current_name = parts[1] if len(parts) > 1 else ""
            current_lines = [line]
        elif upper.startswith("ENDPROC") or upper.startswith("ENDFUNC"):
            if current_name is not None:
                current_lines.append(line)
                procedures.append({
                    "type": current_type,
                    "name": current_name,
                    "code": "\n".join(current_lines),
                })
                current_name = None
                current_type = None
                current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    # Handle last procedure (may lack ENDPROC)
    if current_name is not None:
        procedures.append({
            "type": current_type,
            "name": current_name,
            "code": "\n".join(current_lines),
        })
    return procedures


class FoxProExtractor(LanguageExtractor):
    """Regex-only extractor for Visual FoxPro .prg and .scx form files."""

    @property
    def language_name(self) -> str:
        return "foxpro"

    @property
    def file_extensions(self) -> list[str]:
        return [".prg", ".scx"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        if file_path.lower().endswith(".scx"):
            return self._extract_scx_symbols(source, file_path)
        lines, line_map = _preprocess(source)
        symbols: list[dict] = []
        state = "TOP_LEVEL"
        current_class: str | None = None
        current_class_start: int | None = None
        current_class_base: str | None = None
        current_func: str | None = None
        current_func_start: int | None = None
        current_func_kind: str | None = None
        has_top_level_routine = False

        def _close_func_at(end_line: int):
            nonlocal current_func, current_func_start, current_func_kind
            if current_func and current_func_start is not None:
                # Map to roam standard kinds: "function" or "method"
                kind = "method" if (current_class is not None) else "function"
                qn = current_func
                parent = None
                if current_class:
                    qn = f"{current_class}.{current_func}"
                    parent = current_class
                sig_keyword = (current_func_kind or "FUNCTION").upper()
                symbols.append(self._make_symbol(
                    name=current_func,
                    kind=kind,
                    line_start=current_func_start,
                    line_end=end_line,
                    qualified_name=qn,
                    signature=f"{sig_keyword} {current_func}",
                    visibility="public",
                    is_exported=True,
                    parent_name=parent,
                ))
            current_func = None
            current_func_start = None
            current_func_kind = None

        for idx, line in enumerate(lines):
            orig = line_map.get(idx, idx + 1)
            stripped = line.strip()
            if not stripped:
                continue

            upper = stripped.upper()

            # ENDDEFINE
            if _RE_ENDDEFINE.match(stripped):
                _close_func_at(orig)
                if current_class and current_class_start is not None:
                    symbols.append(self._make_symbol(
                        name=current_class,
                        kind="class",
                        line_start=current_class_start,
                        line_end=orig,
                        signature=f"DEFINE CLASS {current_class} AS {current_class_base or 'Custom'}",
                        visibility="public",
                        is_exported=True,
                    ))
                current_class = None
                current_class_start = None
                current_class_base = None
                state = "TOP_LEVEL"
                continue

            # ENDFUNC / ENDPROC
            if _RE_ENDFUNC.match(stripped):
                _close_func_at(orig)
                if current_class:
                    state = "IN_CLASS"
                else:
                    state = "TOP_LEVEL"
                continue

            # DEFINE CLASS
            m = _RE_CLASS.match(stripped)
            if m:
                _close_func_at(orig - 1)
                current_class = m.group(1)
                current_class_base = m.group(2)
                current_class_start = orig
                state = "IN_CLASS"
                has_top_level_routine = True
                continue

            # FUNCTION / PROCEDURE
            m = _RE_FUNC.match(stripped)
            if m:
                # Close any open function (VFP allows implicit end)
                _close_func_at(orig - 1)
                current_func = m.group(2)
                current_func_start = orig
                current_func_kind = m.group(1).upper()
                has_top_level_routine = True
                if current_class:
                    state = "IN_METHOD"
                continue

            # #DEFINE constant
            m = _RE_DEFINE_CONST.match(stripped)
            if m:
                symbols.append(self._make_symbol(
                    name=m.group(1),
                    kind="constant",
                    line_start=orig,
                    line_end=orig,
                    signature=f"#DEFINE {m.group(1)} {m.group(2).strip()[:60]}",
                    visibility="public",
                    is_exported=True,
                ))
                continue

            # Property assignment inside class body (not inside a method)
            if state == "IN_CLASS" and current_func is None:
                m = _RE_PROPERTY.match(stripped)
                if m:
                    prop_name = m.group(1)
                    # Skip keywords that look like assignments
                    if prop_name.upper() not in (
                        "IF", "DO", "FOR", "SET", "LOCAL", "PRIVATE",
                        "PUBLIC", "STORE", "RETURN", "ENDFOR", "ENDIF",
                        "ENDDO", "ELSE", "OTHERWISE", "CASE",
                    ):
                        symbols.append(self._make_symbol(
                            name=prop_name,
                            kind="property",
                            line_start=orig,
                            line_end=orig,
                            qualified_name=f"{current_class}.{prop_name}" if current_class else prop_name,
                            signature=f"{prop_name} = {m.group(2).strip()[:40]}",
                            visibility="public",
                            is_exported=True,
                            parent_name=current_class,
                        ))

        # Close any still-open function
        if current_func:
            _close_func_at(line_map.get(len(lines) - 1, len(lines)))

        # Close any still-open class (missing ENDDEFINE)
        if current_class and current_class_start is not None:
            symbols.append(self._make_symbol(
                name=current_class,
                kind="class",
                line_start=current_class_start,
                line_end=line_map.get(len(lines) - 1, len(lines)),
                signature=f"DEFINE CLASS {current_class} AS {current_class_base or 'Custom'}",
                visibility="public",
                is_exported=True,
            ))

        # Implicit file function: if .prg has no top-level routines,
        # treat the entire file as a single function named after the file
        if not has_top_level_routine and lines:
            stem = os.path.splitext(os.path.basename(file_path))[0]
            last_line = line_map.get(len(lines) - 1, len(lines))
            symbols.append(self._make_symbol(
                name=stem,
                kind="function",
                line_start=1,
                line_end=last_line,
                signature=f"DO {stem}",
                visibility="public",
                is_exported=True,
            ))

        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        if file_path.lower().endswith(".scx"):
            return self._extract_scx_references(source, file_path)
        lines, line_map = _preprocess(source)
        refs: list[dict] = []

        # Track current scope for source_name
        current_func: str | None = None
        current_class: str | None = None

        for idx, line in enumerate(lines):
            orig = line_map.get(idx, idx + 1)
            stripped = line.strip()
            if not stripped:
                continue

            upper = stripped.upper()

            # Track scope
            m = _RE_CLASS.match(stripped)
            if m:
                current_class = m.group(1)
                base = m.group(2)
                # Inheritance reference (skip generic bases)
                if base.upper() not in ("CUSTOM", "SESSION", "FORM",
                                         "COMMANDBUTTON", "TEXTBOX",
                                         "LABEL", "CONTAINER", "PAGE",
                                         "PAGEFRAME", "GRID", "COLUMN",
                                         "HEADER", "COMBOBOX", "LISTBOX",
                                         "EDITBOX", "SPINNER", "TIMER",
                                         "IMAGE", "SHAPE", "LINE",
                                         "COMMANDGROUP", "OPTIONGROUP",
                                         "CHECKBOX", "OPTIONBUTTON"):
                    refs.append(self._make_reference(
                        target_name=base,
                        kind="inherits",
                        line=orig,
                        source_name=m.group(1),
                    ))
                continue

            m = _RE_FUNC.match(stripped)
            if m:
                current_func = m.group(2)
                continue

            if _RE_ENDFUNC.match(stripped):
                current_func = None
                continue

            if _RE_ENDDEFINE.match(stripped):
                current_class = None
                current_func = None
                continue

            scope = current_func
            if current_class and current_func:
                scope = f"{current_class}.{current_func}"
            elif current_class:
                scope = current_class

            # DO proc IN file
            m = _RE_DO_IN.match(stripped)
            if m:
                proc = m.group(1)
                lib = m.group(2).strip("'\"")
                refs.append(self._make_reference(
                    target_name=proc,
                    kind="call",
                    line=orig,
                    source_name=scope,
                    import_path=lib,
                ))
                continue

            # DO FORM formname — links to .scx form file
            m = _RE_DO_FORM.match(stripped)
            if m:
                target = m.group(1).strip("'\"")
                target = os.path.splitext(target)[0]  # strip .scx if present
                refs.append(self._make_reference(
                    target_name=target,
                    kind="call",
                    line=orig,
                    source_name=scope,
                ))
                continue

            # DO filename (not DO CASE / DO WHILE)
            m = _RE_DO_FILE.match(stripped)
            if m:
                target = m.group(1)
                if target.upper() not in _DO_KEYWORDS:
                    refs.append(self._make_reference(
                        target_name=target,
                        kind="call",
                        line=orig,
                        source_name=scope,
                    ))
                    continue

            # SET PROCEDURE TO
            m = _RE_SET_PROC.match(stripped)
            if m:
                path = m.group(1).strip("'\"")
                refs.append(self._make_reference(
                    target_name=os.path.splitext(os.path.basename(path))[0],
                    kind="import",
                    line=orig,
                    source_name=scope,
                    import_path=path,
                ))
                continue

            # SET CLASSLIB TO
            m = _RE_SET_CLASSLIB.match(stripped)
            if m:
                path = m.group(1).strip("'\"")
                refs.append(self._make_reference(
                    target_name=os.path.splitext(os.path.basename(path))[0],
                    kind="import",
                    line=orig,
                    source_name=scope,
                    import_path=path,
                ))
                continue

            # #INCLUDE
            m = _RE_INCLUDE.match(stripped)
            if m:
                path = m.group(1).strip()
                refs.append(self._make_reference(
                    target_name=os.path.splitext(os.path.basename(path))[0],
                    kind="import",
                    line=orig,
                    source_name=scope,
                    import_path=path,
                ))
                continue

            # CREATEOBJECT("class") — mid-line search
            for cm in _RE_CREATEOBJ.finditer(stripped):
                refs.append(self._make_reference(
                    target_name=cm.group(1),
                    kind="call",
                    line=orig,
                    source_name=scope,
                ))

            # NEWOBJECT("class", "lib") — mid-line search
            for nm in _RE_NEWOBJ.finditer(stripped):
                refs.append(self._make_reference(
                    target_name=nm.group(1),
                    kind="call",
                    line=orig,
                    source_name=scope,
                    import_path=nm.group(2),
                ))

            # DECLARE func IN dll
            m = _RE_DECLARE.match(stripped)
            if m:
                refs.append(self._make_reference(
                    target_name=m.group(1),
                    kind="call",
                    line=orig,
                    source_name=scope,
                    import_path=m.group(2).strip("'\""),
                ))
                continue

            # =funcname(args) — expression-style function call
            m = _RE_EXPR_CALL.match(stripped)
            if m:
                fname = m.group(1)
                if fname.upper() not in _VFP_BUILTINS:
                    refs.append(self._make_reference(
                        target_name=fname,
                        kind="call",
                        line=orig,
                        source_name=scope,
                    ))

            # obj.method(args) — method calls (THIS.x(), THISFORM.x(), var.x())
            for mc in _RE_METHOD_CALL.finditer(stripped):
                obj_name = mc.group(1)
                method_name = mc.group(2)
                # Skip VFP built-in objects/namespaces as targets
                if method_name.upper() in _VFP_BUILTINS:
                    continue
                # Skip known noise patterns
                if obj_name.upper() in ("M", "THIS", "THISFORM", "THISFORMSET"):
                    # THIS.method() — target is the method name
                    refs.append(self._make_reference(
                        target_name=method_name,
                        kind="call",
                        line=orig,
                        source_name=scope,
                    ))
                else:
                    # variable.method() — target is the method name
                    refs.append(self._make_reference(
                        target_name=method_name,
                        kind="call",
                        line=orig,
                        source_name=scope,
                    ))

        return refs

    # ── SCX form support ──────────────────────────────────────────────

    def _extract_scx_symbols(self, source: bytes, file_path: str) -> list[dict]:
        """Extract symbols from a packed .scx/.sct binary.

        Creates:
        - A form-level class symbol (name = filename stem)
        - Method symbols for each PROCEDURE/FUNCTION in each control's Methods
        Qualified names: FormName.ControlName.MethodName
        Synthetic line numbers: record_num * 1000 + proc_idx (stable, ordered)
        """
        try:
            scx_bytes, sct_bytes = _unpack_scx_sct(source)
            records = _parse_scx_records(scx_bytes, sct_bytes)
        except Exception as exc:
            log.debug("SCX parse error for %s: %s", file_path, exc)
            return []

        form_name = os.path.splitext(os.path.basename(file_path))[0]
        symbols: list[dict] = []

        # Form-level class symbol
        symbols.append(self._make_symbol(
            name=form_name,
            kind="class",
            line_start=1,
            line_end=max(len(records) * 1000, 1),
            signature=f"FORM {form_name}",
            visibility="public",
            is_exported=True,
        ))

        for rec in records:
            if rec["deleted"] or rec["platform"].strip() == "COMMENT":
                continue
            methods_text = rec["methods"]
            if not methods_text:
                continue

            objname = rec["objname"]
            parent = rec["parent"]
            record_num = rec["record_num"]

            # Build control path for qualified names
            if parent:
                control_path = f"{parent}.{objname}"
            else:
                control_path = objname

            procs = _extract_procedures(methods_text)
            for proc_idx, proc in enumerate(procs):
                proc_name = proc["name"]
                if not proc_name:
                    continue
                syn_line = record_num * 1000 + proc_idx
                qn = f"{form_name}.{control_path}.{proc_name}" if control_path else f"{form_name}.{proc_name}"
                parent_name = f"{form_name}.{control_path}" if control_path else form_name

                symbols.append(self._make_symbol(
                    name=proc_name,
                    kind="method",
                    line_start=syn_line,
                    line_end=syn_line + len(proc["code"].split("\n")),
                    qualified_name=qn,
                    signature=f"{proc['type']} {proc_name}",
                    visibility="public",
                    is_exported=True,
                    parent_name=parent_name,
                ))

        return symbols

    def _code_line_refs(self, stripped, line_num, scope, refs):
        """Extract references from a single VFP code line."""
        # DO FORM
        m = _RE_DO_FORM.match(stripped)
        if m:
            target = m.group(1).strip("'\"")
            target = os.path.splitext(target)[0]
            refs.append(self._make_reference(
                target_name=target, kind="call",
                line=line_num, source_name=scope,
            ))
            return

        # DO proc IN file
        m = _RE_DO_IN.match(stripped)
        if m:
            refs.append(self._make_reference(
                target_name=m.group(1), kind="call",
                line=line_num, source_name=scope,
                import_path=m.group(2).strip("'\""),
            ))
            return

        # DO filename
        m = _RE_DO_FILE.match(stripped)
        if m:
            target = m.group(1)
            if target.upper() not in _DO_KEYWORDS:
                refs.append(self._make_reference(
                    target_name=target, kind="call",
                    line=line_num, source_name=scope,
                ))
                return

        # SET PROCEDURE TO
        m = _RE_SET_PROC.match(stripped)
        if m:
            path = m.group(1).strip("'\"")
            refs.append(self._make_reference(
                target_name=os.path.splitext(os.path.basename(path))[0],
                kind="import", line=line_num, source_name=scope,
                import_path=path,
            ))
            return

        # CREATEOBJECT
        for cm in _RE_CREATEOBJ.finditer(stripped):
            refs.append(self._make_reference(
                target_name=cm.group(1), kind="call",
                line=line_num, source_name=scope,
            ))

        # =funcname()
        m = _RE_EXPR_CALL.match(stripped)
        if m:
            fname = m.group(1)
            if fname.upper() not in _VFP_BUILTINS:
                refs.append(self._make_reference(
                    target_name=fname, kind="call",
                    line=line_num, source_name=scope,
                ))

        # obj.method()
        for mc in _RE_METHOD_CALL.finditer(stripped):
            method_name = mc.group(2)
            if method_name.upper() not in _VFP_BUILTINS:
                refs.append(self._make_reference(
                    target_name=method_name, kind="call",
                    line=line_num, source_name=scope,
                ))

    def _extract_scx_references(self, source: bytes, file_path: str) -> list[dict]:
        """Extract references from VFP code inside .scx form controls."""
        try:
            scx_bytes, sct_bytes = _unpack_scx_sct(source)
            records = _parse_scx_records(scx_bytes, sct_bytes)
        except Exception as exc:
            log.debug("SCX parse error for %s: %s", file_path, exc)
            return []

        form_name = os.path.splitext(os.path.basename(file_path))[0]
        refs: list[dict] = []

        for rec in records:
            if rec["deleted"] or rec["platform"].strip() == "COMMENT":
                continue

            objname = rec["objname"]
            parent = rec["parent"]
            record_num = rec["record_num"]

            # Build control path
            control_path = f"{parent}.{objname}" if parent else objname

            # Extract classloc references (class library imports) for all controls
            classloc = rec["classloc"]
            if classloc:
                lib_name = os.path.splitext(os.path.basename(classloc))[0]
                refs.append(self._make_reference(
                    target_name=lib_name, kind="import",
                    line=record_num * 1000, source_name=form_name,
                    import_path=classloc,
                ))

            # Extract references from procedures in this control's methods
            methods_text = rec["methods"]
            if not methods_text:
                continue

            procs = _extract_procedures(methods_text)
            for proc_idx, proc in enumerate(procs):
                proc_name = proc["name"]
                if not proc_name:
                    continue
                scope = f"{form_name}.{control_path}.{proc_name}" if control_path else f"{form_name}.{proc_name}"
                syn_line = record_num * 1000 + proc_idx

                for line_offset, code_line in enumerate(proc["code"].split("\n")):
                    stripped = code_line.strip()
                    if not stripped:
                        continue
                    self._code_line_refs(stripped, syn_line + line_offset, scope, refs)

        return refs
