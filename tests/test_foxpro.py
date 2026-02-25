"""Tests for Visual FoxPro language support."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile

from roam.languages.foxpro_lang import FoxProExtractor


def _extract_foxpro(source_text: str, file_path: str = "test.prg"):
    """Helper: run extractor with tree=None (regex-only)."""
    ext = FoxProExtractor()
    source = source_text.encode("utf-8")
    symbols = ext.extract_symbols(None, source, file_path)
    refs = ext.extract_references(None, source, file_path)
    return symbols, refs


# ── Symbol extraction ─────────────────────────────────────────────────


class TestFoxProSymbols:
    def test_function(self):
        src = """\
FUNCTION MyFunc
  LOCAL x
  x = 1
  RETURN x
ENDFUNC
"""
        syms, _ = _extract_foxpro(src)
        funcs = [s for s in syms if s["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "MyFunc"

    def test_procedure(self):
        src = """\
PROCEDURE DoWork
  ? "hello"
ENDPROC
"""
        syms, _ = _extract_foxpro(src)
        funcs = [s for s in syms if s["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "DoWork"

    def test_class_with_methods(self):
        src = """\
DEFINE CLASS MyClass AS Custom
  Name = "test"
  nCount = 0

  FUNCTION Init
    THIS.nCount = 1
  ENDFUNC

  PROCEDURE Destroy
    THIS.nCount = 0
  ENDPROC
ENDDEFINE
"""
        syms, _ = _extract_foxpro(src)
        classes = [s for s in syms if s["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "MyClass"

        methods = [s for s in syms if s["kind"] == "method"]
        assert len(methods) == 2
        names = {m["name"] for m in methods}
        assert names == {"Init", "Destroy"}
        # Methods should have parent_name
        for m in methods:
            assert m["parent_name"] == "MyClass"
            assert m["qualified_name"].startswith("MyClass.")

    def test_class_properties(self):
        src = """\
DEFINE CLASS Config AS Custom
  cServer = "localhost"
  nPort = 3050
  lActive = .T.
ENDDEFINE
"""
        syms, _ = _extract_foxpro(src)
        props = [s for s in syms if s["kind"] == "property"]
        assert len(props) == 3
        names = {p["name"] for p in props}
        assert names == {"cServer", "nPort", "lActive"}
        for p in props:
            assert p["parent_name"] == "Config"

    def test_define_constant(self):
        src = """\
#DEFINE MAX_ITEMS 100
#DEFINE APP_NAME "MyApp"
"""
        syms, _ = _extract_foxpro(src)
        consts = [s for s in syms if s["kind"] == "constant"]
        assert len(consts) == 2
        names = {c["name"] for c in consts}
        assert names == {"MAX_ITEMS", "APP_NAME"}

    def test_implicit_file_function(self):
        """A .prg with no FUNCTION/PROCEDURE is an implicit file function."""
        src = """\
LOCAL cName
cName = "test"
? cName
"""
        syms, _ = _extract_foxpro(src, file_path="myprog.prg")
        assert len(syms) == 1
        assert syms[0]["name"] == "myprog"
        assert syms[0]["kind"] == "function"
        assert syms[0]["line_start"] == 1

    def test_case_insensitivity(self):
        """VFP keywords are case-insensitive."""
        src = """\
function lowerfunc
  return 1
endfunc

FUNCTION UPPERFUNC
  RETURN 2
ENDFUNC

Function MixedFunc
  Return 3
EndFunc
"""
        syms, _ = _extract_foxpro(src)
        funcs = [s for s in syms if s["kind"] == "function"]
        assert len(funcs) == 3
        names = {f["name"] for f in funcs}
        assert names == {"lowerfunc", "UPPERFUNC", "MixedFunc"}

    def test_line_continuation(self):
        """Lines ending with ; are joined."""
        src = """\
FUNCTION ;
  LongName
  RETURN 1
ENDFUNC
"""
        syms, _ = _extract_foxpro(src)
        funcs = [s for s in syms if s["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "LongName"

    def test_comments_stripped(self):
        """Comments should not produce symbols."""
        src = """\
* This is a comment
*!* This is disabled code
*!* FUNCTION ShouldNotExist
*!* ENDFUNC
*!*
FUNCTION RealFunc  && this is an inline comment
  RETURN 1
ENDFUNC
"""
        syms, _ = _extract_foxpro(src)
        funcs = [s for s in syms if s["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "RealFunc"

    def test_multiple_functions_implicit_end(self):
        """When a new FUNCTION starts, close the previous one implicitly."""
        src = """\
FUNCTION First
  RETURN 1

FUNCTION Second
  RETURN 2
"""
        syms, _ = _extract_foxpro(src)
        funcs = [s for s in syms if s["kind"] == "function"]
        assert len(funcs) == 2
        assert funcs[0]["name"] == "First"
        assert funcs[1]["name"] == "Second"
        # First should end before Second starts
        assert funcs[0]["line_end"] < funcs[1]["line_start"]


# ── Reference extraction ──────────────────────────────────────────────


class TestFoxProReferences:
    def test_do_filename(self):
        src = """\
FUNCTION Main
  DO backup
  DO cleanup WITH "test"
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "backup" in targets
        assert "cleanup" in targets

    def test_do_excluded_keywords(self):
        """DO CASE, DO WHILE should NOT produce call refs."""
        src = """\
FUNCTION Test
  DO CASE
  CASE x = 1
  ENDCASE
  DO WHILE .T.
  ENDDO
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "CASE" not in targets
        assert "WHILE" not in targets

    def test_do_in(self):
        src = """\
FUNCTION Main
  DO MyProc IN mylib.prg
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        assert len(calls) == 1
        assert calls[0]["target_name"] == "MyProc"
        assert calls[0]["import_path"] == "mylib.prg"

    def test_set_procedure(self):
        src = """\
SET PROCEDURE TO utils.prg
"""
        _, refs = _extract_foxpro(src)
        imports = [r for r in refs if r["kind"] == "import"]
        assert len(imports) == 1
        assert imports[0]["target_name"] == "utils"
        assert imports[0]["import_path"] == "utils.prg"

    def test_set_classlib(self):
        src = """\
SET CLASSLIB TO mylibs.vcx
"""
        _, refs = _extract_foxpro(src)
        imports = [r for r in refs if r["kind"] == "import"]
        assert len(imports) == 1
        assert imports[0]["target_name"] == "mylibs"

    def test_include(self):
        src = """\
#INCLUDE "foxpro.h"
"""
        _, refs = _extract_foxpro(src)
        imports = [r for r in refs if r["kind"] == "import"]
        assert len(imports) == 1
        assert imports[0]["target_name"] == "foxpro"
        assert imports[0]["import_path"] == "foxpro.h"

    def test_createobject(self):
        src = """\
FUNCTION Test
  LOCAL oObj
  oObj = CREATEOBJECT("MyClass")
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "MyClass" in targets

    def test_newobject(self):
        src = """\
FUNCTION Test
  LOCAL oObj
  oObj = NEWOBJECT("MyClass", "mylib.vcx")
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        assert any(r["target_name"] == "MyClass" and r["import_path"] == "mylib.vcx" for r in calls)

    def test_inheritance(self):
        src = """\
DEFINE CLASS Child AS ParentClass
ENDDEFINE
"""
        _, refs = _extract_foxpro(src)
        inherits = [r for r in refs if r["kind"] == "inherits"]
        assert len(inherits) == 1
        assert inherits[0]["target_name"] == "ParentClass"
        assert inherits[0]["source_name"] == "Child"

    def test_inheritance_skip_builtins(self):
        """Inheriting from Custom/Session/Form should NOT produce inherits ref."""
        src = """\
DEFINE CLASS Foo AS Custom
ENDDEFINE
DEFINE CLASS Bar AS Session
ENDDEFINE
"""
        _, refs = _extract_foxpro(src)
        inherits = [r for r in refs if r["kind"] == "inherits"]
        assert len(inherits) == 0

    def test_declare_in(self):
        src = """\
DECLARE INTEGER GetTickCount IN kernel32
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        assert len(calls) == 1
        assert calls[0]["target_name"] == "GetTickCount"
        assert calls[0]["import_path"] == "kernel32"

    def test_scope_tracking(self):
        """References should track their enclosing scope."""
        src = """\
DEFINE CLASS Worker AS Custom
  FUNCTION DoWork
    DO helper
  ENDFUNC
ENDDEFINE
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        assert len(calls) == 1
        assert calls[0]["source_name"] == "Worker.DoWork"

    def test_expression_call(self):
        """=funcname() should produce a call reference."""
        src = """\
FUNCTION Main
  =MyCustomFunc("test")
  =_ms("message", 0, "title")
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "MyCustomFunc" in targets
        assert "_ms" in targets

    def test_expression_call_skips_builtins(self):
        """VFP built-in functions should not produce call refs."""
        src = """\
FUNCTION Test
  =MESSAGEBOX("hello", 0, "title")
  =FCLOSE(handle)
  =STRTOFILE(data, "file.txt")
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "MESSAGEBOX" not in targets
        assert "FCLOSE" not in targets
        assert "STRTOFILE" not in targets

    def test_this_method_call(self):
        """THIS.method() should produce a call reference."""
        src = """\
DEFINE CLASS Worker AS Custom
  FUNCTION Init
    THIS.Setup()
    THIS.LoadData()
  ENDFUNC
ENDDEFINE
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "Setup" in targets
        assert "LoadData" in targets

    def test_object_method_call(self):
        """obj.method() should produce a call reference."""
        src = """\
FUNCTION Test
  LOCAL oObj
  oObj = CREATEOBJECT("MyClass")
  oObj.Execute()
  oObj.Process("data")
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "MyClass" in targets  # from CREATEOBJECT
        assert "Execute" in targets
        assert "Process" in targets


# ── Encoding detection ────────────────────────────────────────────────


class TestFoxProEncoding:
    def test_utf8(self):
        """UTF-8 source should decode correctly."""
        src = "FUNCTION Hello\n  RETURN 1\nENDFUNC\n"
        syms, _ = _extract_foxpro(src)
        assert len(syms) == 1
        assert syms[0]["name"] == "Hello"

    def test_latin1_bytes(self):
        """Latin-1 encoded source should decode correctly."""
        # German umlauts in Latin-1 (not valid UTF-8)
        src_bytes = "FUNCTION Über\n  RETURN 1\nENDFUNC\n".encode("latin-1")
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, src_bytes, "test.prg")
        assert len(syms) == 1

    def test_cp1253_greek(self):
        """Windows-1253 (Greek) encoded source should decode correctly."""
        # Greek text in Windows-1253
        src_bytes = "FUNCTION main\n  cName = 'Ελληνικά'\n  RETURN 1\nENDFUNC\n".encode("cp1253")
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, src_bytes, "test.prg")
        assert len(syms) == 1
        assert syms[0]["name"] == "main"

    def test_utf8_bom(self):
        """UTF-8 with BOM should work."""
        src_bytes = b"\xef\xbb\xbf" + "FUNCTION Hello\n  RETURN 1\nENDFUNC\n".encode("utf-8")
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, src_bytes, "test.prg")
        assert len(syms) == 1
        assert syms[0]["name"] == "Hello"

    def test_empty_source(self):
        """Empty source should not crash — produces implicit file function."""
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, b"", "test.prg")
        # Empty .prg still becomes an implicit file function (VFP behavior)
        assert len(syms) <= 1


# ── Case-insensitive resolution ──────────────────────────────────────


class TestFoxProCaseInsensitiveResolution:
    def test_case_insensitive_cross_file_edge(self):
        """DO BACKUP (uppercase) should resolve to FUNCTION backup (lowercase)."""
        tmpdir = tempfile.mkdtemp()
        try:
            import subprocess

            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

            # caller.prg uses uppercase: DO BACKUP
            with open(os.path.join(tmpdir, "caller.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION Main\n  DO BACKUP\nENDFUNC\n")

            # target.prg defines lowercase: FUNCTION backup
            with open(os.path.join(tmpdir, "target.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION backup\n  RETURN .T.\nENDFUNC\n")

            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmpdir, capture_output=True)

            from pathlib import Path

            from roam.index.indexer import Indexer

            indexer = Indexer(project_root=Path(tmpdir))
            indexer.run(force=True)

            from roam.db.connection import open_db

            with open_db(project_root=Path(tmpdir), readonly=True) as conn:
                edges = conn.execute("SELECT * FROM edges WHERE kind = 'call'").fetchall()
                # The case-insensitive fallback should resolve DO BACKUP -> backup
                assert len(edges) >= 1, "Case-insensitive DO BACKUP should resolve to FUNCTION backup"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Integration test ──────────────────────────────────────────────────


class TestFoxProIntegration:
    def test_pipeline_integration(self):
        """Test that FoxPro files pass through the indexing pipeline."""
        from roam.index.parser import REGEX_ONLY_LANGUAGES, parse_file
        from roam.languages.registry import get_extractor

        assert "foxpro" in REGEX_ONLY_LANGUAGES

        # Create a temp .prg file
        tmpdir = tempfile.mkdtemp()
        try:
            prg = os.path.join(tmpdir, "test.prg")
            with open(prg, "w", encoding="utf-8") as f:
                f.write("FUNCTION Hello\n  RETURN 1\nENDFUNC\n")

            from pathlib import Path

            tree, source, lang = parse_file(Path(prg), "foxpro")
            assert tree is None  # No tree-sitter tree
            assert source is not None  # Source bytes available
            assert lang == "foxpro"

            extractor = get_extractor("foxpro")
            assert extractor is not None
            assert extractor.language_name == "foxpro"

            from roam.index.symbols import extract_references, extract_symbols

            syms = extract_symbols(tree, source, "test.prg", extractor)
            assert len(syms) == 1
            assert syms[0]["name"] == "Hello"

            refs = extract_references(tree, source, "test.prg", extractor)
            assert isinstance(refs, list)
        finally:
            shutil.rmtree(tmpdir)

    def test_full_index_with_prg_files(self):
        """Integration test: create a temp project with .prg files, run index."""
        tmpdir = tempfile.mkdtemp()
        try:
            # Create git repo
            import subprocess

            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

            # Create .prg files
            with open(os.path.join(tmpdir, "main.prg"), "w", encoding="utf-8") as f:
                f.write("SET PROCEDURE TO utils.prg\n")
                f.write("DO backup\n")

            with open(os.path.join(tmpdir, "utils.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION backup\n")
                f.write("  RETURN .T.\n")
                f.write("ENDFUNC\n")

            # Add files to git
            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmpdir, capture_output=True)

            # Run indexer
            from pathlib import Path

            from roam.index.indexer import Indexer

            indexer = Indexer(project_root=Path(tmpdir))
            indexer.run(force=True)

            # Verify results
            from roam.db.connection import open_db

            with open_db(project_root=Path(tmpdir), readonly=True) as conn:
                files = conn.execute("SELECT * FROM files WHERE language = 'foxpro'").fetchall()
                assert len(files) == 2

                syms = conn.execute("SELECT * FROM symbols").fetchall()
                sym_names = {s["name"] for s in syms}
                assert "backup" in sym_names
                # main.prg has no FUNCTION so it should be implicit
                assert "main" in sym_names

                # Check edges: main -> utils (via SET PROCEDURE or DO backup)
                edges = conn.execute("SELECT * FROM edges").fetchall()
                assert len(edges) >= 1  # At least one cross-file edge
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── SCX/SCT synthetic binary builder ─────────────────────────────────


class _FPTBuilder:
    """Builds a minimal FPT (memo) file for testing."""

    def __init__(self, block_size: int = 64):
        self.block_size = block_size
        self.header_blocks = 512 // block_size
        self.next_block = self.header_blocks
        self.data = bytearray(512)
        # Write block_size to header
        struct.pack_into(">H", self.data, 6, block_size)

    def add_memo(self, text: str) -> int:
        """Add a text memo and return its block number. Returns 0 for empty."""
        if not text:
            return 0
        encoded = text.encode("cp1253", errors="replace")
        block_num = self.next_block
        # Memo block: type (4B) + length (4B) + data
        memo_header = struct.pack(">II", 1, len(encoded))
        block_data = memo_header + encoded
        # Pad to block boundary
        padded_len = ((len(block_data) + self.block_size - 1) // self.block_size) * self.block_size
        block_data = block_data.ljust(padded_len, b"\x00")
        self.data.extend(block_data)
        self.next_block += padded_len // self.block_size
        return block_num

    def build(self) -> bytes:
        struct.pack_into(">I", self.data, 0, self.next_block)
        return bytes(self.data)


def _build_synthetic_scx_sct(controls: list[dict]) -> tuple[bytes, bytes]:
    """Build minimal binary .scx (DBF) + .sct (FPT) from control dicts.

    Each control dict can have:
        platform, uniqueid, objname, parent, class_name, classloc,
        baseclass, methods, properties, protected, deleted (bool)
    """
    fpt = _FPTBuilder()

    # Field definitions: (name, type, size)
    field_defs = [
        ("PLATFORM", "C", 8),
        ("UNIQUEID", "C", 10),
        ("CLASS", "M", 4),
        ("CLASSLOC", "M", 4),
        ("BASECLASS", "M", 4),
        ("OBJNAME", "M", 4),
        ("PARENT", "M", 4),
        ("PROPERTIES", "M", 4),
        ("PROTECTED", "M", 4),
        ("METHODS", "M", 4),
        ("OBJCODE", "M", 4),
    ]

    # Compute field displacements (offset 1 = after deletion flag byte)
    displacement = 1
    field_info = []
    for name, ftype, size in field_defs:
        field_info.append((name, ftype, displacement, size))
        displacement += size
    record_size = displacement

    # Build field descriptors (32 bytes each)
    field_descriptors = bytearray()
    for name, ftype, disp, size in field_info:
        fd = bytearray(32)
        fd[0:11] = name.encode("ascii")[:11].ljust(11, b"\x00")
        fd[11] = ord(ftype)
        struct.pack_into("<I", fd, 12, disp)
        fd[16] = size
        field_descriptors.extend(fd)
    field_descriptors.append(0x0D)  # Terminator

    header_size = 32 + len(field_descriptors)

    # DBF header (32 bytes)
    dbf_header = bytearray(32)
    dbf_header[0] = 0x30  # VFP version
    struct.pack_into("<I", dbf_header, 4, len(controls))
    struct.pack_into("<H", dbf_header, 8, header_size)
    struct.pack_into("<H", dbf_header, 10, record_size)

    # Build records
    record_bytes = bytearray()
    # Map from control dict key -> (field_name_for_char, displacement_for_char)
    char_fields = {"platform": 1, "uniqueid": 9}  # displacement for PLATFORM, UNIQUEID
    # Map from control dict key -> displacement for memo fields
    memo_map = {
        "class_name": 19,
        "classloc": 23,
        "baseclass": 27,
        "objname": 31,
        "parent": 35,
        "properties": 39,
        "protected": 43,
        "methods": 47,
    }

    for ctrl in controls:
        rec = bytearray(record_size)
        rec[0] = ord("*") if ctrl.get("deleted", False) else ord(" ")

        # Character fields
        platform = ctrl.get("platform", "WINDOWS").encode("ascii")[:8].ljust(8, b" ")
        uniqueid = ctrl.get("uniqueid", "").encode("ascii")[:10].ljust(10, b" ")
        rec[1:9] = platform
        rec[9:19] = uniqueid

        # Memo fields
        for key, offset in memo_map.items():
            text = ctrl.get(key, "")
            block_num = fpt.add_memo(text) if text else 0
            struct.pack_into("<I", rec, offset, block_num)

        # OBJCODE is always 0 (no compiled p-code in tests)
        struct.pack_into("<I", rec, 51, 0)
        record_bytes.extend(rec)

    scx_bytes = bytes(dbf_header) + bytes(field_descriptors) + bytes(record_bytes)
    sct_bytes = fpt.build()
    return scx_bytes, sct_bytes


def _pack_for_extractor(scx_bytes: bytes, sct_bytes: bytes) -> bytes:
    """Wrap SCX+SCT in the length-prefixed format expected by the extractor."""
    return struct.pack(">I", len(scx_bytes)) + scx_bytes + sct_bytes


# ── DO FORM reference tests ──────────────────────────────────────────


class TestDoFormReference:
    def test_do_form_produces_call(self):
        """DO FORM myform should produce a call reference to 'myform'."""
        src = """\
FUNCTION Main
  DO FORM myform
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "myform" in targets

    def test_do_form_strips_extension(self):
        """DO FORM myform.scx should strip .scx and target 'myform'."""
        src = """\
FUNCTION Main
  DO FORM myform.scx
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "myform" in targets
        assert "myform.scx" not in targets

    def test_do_form_quoted_path(self):
        """DO FORM 'subdir/myform' should strip path and extension."""
        src = """\
FUNCTION Main
  DO FORM 'subdir/myform.scx'
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "subdir/myform" in targets

    def test_do_case_while_still_excluded(self):
        """DO CASE and DO WHILE should still not produce call references."""
        src = """\
FUNCTION Test
  DO CASE
  CASE x = 1
  ENDCASE
  DO WHILE .T.
  ENDDO
  DO FORM myform
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "CASE" not in targets
        assert "WHILE" not in targets
        assert "myform" in targets  # DO FORM now works

    def test_do_form_case_insensitive(self):
        """do form, Do Form, DO FORM should all work."""
        src = """\
FUNCTION Main
  do form formA
  Do Form formB
  DO FORM formC
ENDFUNC
"""
        _, refs = _extract_foxpro(src)
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "formA" in targets
        assert "formB" in targets
        assert "formC" in targets


# ── SCX parsing tests ────────────────────────────────────────────────


class TestSCXParsing:
    def test_form_symbol(self):
        """A basic SCX form should produce a class symbol named after the file."""
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "myform.scx")
        classes = [s for s in syms if s["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "myform"

    def test_method_extraction(self):
        """Methods in controls should be extracted as method symbols."""
        methods = "PROCEDURE Init\n  THIS.Caption = 'Hello'\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {
                    "platform": "WINDOWS",
                    "objname": "MyForm",
                    "baseclass": "form",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "myform.scx")
        meths = [s for s in syms if s["kind"] == "method"]
        assert len(meths) == 1
        assert meths[0]["name"] == "Init"

    def test_qualified_names(self):
        """Method symbols should have FormName.Control.MethodName qualified names."""
        methods = "PROCEDURE Click\n  RETURN\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "cmdSave",
                    "parent": "MyForm",
                    "baseclass": "commandbutton",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "testform.scx")
        meths = [s for s in syms if s["kind"] == "method"]
        assert len(meths) == 1
        assert meths[0]["qualified_name"] == "testform.MyForm.cmdSave.Click"
        assert meths[0]["parent_name"] == "testform.MyForm.cmdSave"

    def test_comment_record_skipped(self):
        """Records with platform='COMMENT' should be skipped."""
        methods = "PROCEDURE Init\n  RETURN\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "COMMENT", "objname": "Header", "methods": methods},
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "test.scx")
        # Should only have the form class symbol, no methods from COMMENT record
        meths = [s for s in syms if s["kind"] == "method"]
        assert len(meths) == 0

    def test_deleted_record_skipped(self):
        """Deleted records (deletion flag = '*') should be skipped."""
        methods = "PROCEDURE Init\n  RETURN\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {
                    "platform": "WINDOWS",
                    "objname": "OldCtrl",
                    "baseclass": "textbox",
                    "methods": methods,
                    "deleted": True,
                },
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "test.scx")
        meths = [s for s in syms if s["kind"] == "method"]
        assert len(meths) == 0

    def test_missing_sct_graceful_degradation(self):
        """If .sct is missing (empty), form symbol should still be created."""
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
            ]
        )
        # Pack with empty sct to simulate missing companion file
        packed = struct.pack(">I", len(scx)) + scx  # No sct bytes
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "test.scx")
        # Should still have the form class symbol
        classes = [s for s in syms if s["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "test"

    def test_multiple_controls_with_methods(self):
        """Multiple controls each with methods should all be extracted."""
        methods1 = "PROCEDURE Click\n  DO backup\nENDPROC\n"
        methods2 = "PROCEDURE Init\n  THIS.Value = 0\nENDPROC\nPROCEDURE Destroy\n  RETURN\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "cmdSave",
                    "parent": "MyForm",
                    "baseclass": "commandbutton",
                    "methods": methods1,
                },
                {
                    "platform": "WINDOWS",
                    "objname": "txtAmount",
                    "parent": "MyForm",
                    "baseclass": "textbox",
                    "methods": methods2,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "test.scx")
        meths = [s for s in syms if s["kind"] == "method"]
        names = {m["name"] for m in meths}
        assert names == {"Click", "Init", "Destroy"}

    def test_synthetic_line_numbers(self):
        """Synthetic line numbers should be record_num * 1000 + proc_idx."""
        methods = "PROCEDURE Init\n  RETURN\nENDPROC\nPROCEDURE Click\n  RETURN\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "btn",
                    "parent": "MyForm",
                    "baseclass": "commandbutton",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        syms = ext.extract_symbols(None, packed, "test.scx")
        meths = sorted([s for s in syms if s["kind"] == "method"], key=lambda s: s["line_start"])
        # Record 1 (second record, idx 1): methods at line 1000+0, 1000+1
        assert meths[0]["name"] == "Init"
        assert meths[0]["line_start"] == 1000  # record_num=1, proc_idx=0
        assert meths[1]["name"] == "Click"
        assert meths[1]["line_start"] == 1001  # record_num=1, proc_idx=1


# ── SCX reference extraction tests ───────────────────────────────────


class TestSCXReferences:
    def test_do_in_scx_methods(self):
        """DO filename inside SCX methods should produce call references."""
        methods = "PROCEDURE Click\n  DO backup\n  DO cleanup WITH 'test'\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "cmdSave",
                    "parent": "MyForm",
                    "baseclass": "commandbutton",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        refs = ext.extract_references(None, packed, "test.scx")
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "backup" in targets
        assert "cleanup" in targets

    def test_do_form_in_scx_methods(self):
        """DO FORM inside SCX methods should produce call references."""
        methods = "PROCEDURE Click\n  DO FORM otherform\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "cmdOpen",
                    "parent": "MyForm",
                    "baseclass": "commandbutton",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        refs = ext.extract_references(None, packed, "test.scx")
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "otherform" in targets

    def test_createobject_in_scx(self):
        """CREATEOBJECT inside SCX methods should produce call references."""
        methods = 'PROCEDURE Init\n  oHelper = CREATEOBJECT("MyHelper")\nENDPROC\n'
        scx, sct = _build_synthetic_scx_sct(
            [
                {
                    "platform": "WINDOWS",
                    "objname": "MyForm",
                    "baseclass": "form",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        refs = ext.extract_references(None, packed, "test.scx")
        calls = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in calls}
        assert "MyHelper" in targets

    def test_classloc_import(self):
        """Controls with classloc should produce import references."""
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "oGrid",
                    "parent": "MyForm",
                    "baseclass": "grid",
                    "classloc": "mylibs.vcx",
                    "class_name": "CustomGrid",
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        refs = ext.extract_references(None, packed, "test.scx")
        imports = [r for r in refs if r["kind"] == "import"]
        assert any(r["target_name"] == "mylibs" and r["import_path"] == "mylibs.vcx" for r in imports)

    def test_scope_in_scx_references(self):
        """References inside SCX methods should have correct scope."""
        methods = "PROCEDURE Click\n  DO helper\nENDPROC\n"
        scx, sct = _build_synthetic_scx_sct(
            [
                {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                {
                    "platform": "WINDOWS",
                    "objname": "cmdRun",
                    "parent": "MyForm",
                    "baseclass": "commandbutton",
                    "methods": methods,
                },
            ]
        )
        packed = _pack_for_extractor(scx, sct)
        ext = FoxProExtractor()
        refs = ext.extract_references(None, packed, "test.scx")
        calls = [r for r in refs if r["kind"] == "call" and r["target_name"] == "helper"]
        assert len(calls) == 1
        assert calls[0]["source_name"] == "test.MyForm.cmdRun.Click"


# ── SCX integration tests ────────────────────────────────────────────


class TestSCXIntegration:
    def test_full_pipeline_prg_to_scx(self):
        """Integration: .prg -> DO FORM -> .scx -> DO backup -> .prg"""
        tmpdir = tempfile.mkdtemp()
        try:
            import subprocess

            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)

            # main.prg calls DO FORM myform
            with open(os.path.join(tmpdir, "main.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION Main\n  DO FORM myform\nENDFUNC\n")

            # backup.prg has a function
            with open(os.path.join(tmpdir, "backup.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION backup\n  RETURN .T.\nENDFUNC\n")

            # myform.scx/sct — form with a button that calls DO backup
            methods = "PROCEDURE Click\n  DO backup\nENDPROC\n"
            scx, sct = _build_synthetic_scx_sct(
                [
                    {"platform": "WINDOWS", "objname": "MyForm", "baseclass": "form"},
                    {
                        "platform": "WINDOWS",
                        "objname": "cmdSave",
                        "parent": "MyForm",
                        "baseclass": "commandbutton",
                        "methods": methods,
                    },
                ]
            )
            with open(os.path.join(tmpdir, "myform.scx"), "wb") as f:
                f.write(scx)
            with open(os.path.join(tmpdir, "myform.sct"), "wb") as f:
                f.write(sct)

            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmpdir, capture_output=True)

            # Run indexer
            from pathlib import Path

            from roam.index.indexer import Indexer

            indexer = Indexer(project_root=Path(tmpdir))
            indexer.run(force=True)

            # Verify
            from roam.db.connection import open_db

            with open_db(project_root=Path(tmpdir), readonly=True) as conn:
                # Should have 3 foxpro files: main.prg, backup.prg, myform.scx
                files = conn.execute("SELECT * FROM files WHERE language = 'foxpro'").fetchall()
                file_paths = {f["path"] for f in files}
                assert "main.prg" in file_paths
                assert "backup.prg" in file_paths
                assert "myform.scx" in file_paths

                # Symbols should include: Main (from main.prg), backup (from backup.prg),
                # myform (form class), Click (method in SCX)
                syms = conn.execute("SELECT * FROM symbols").fetchall()
                sym_names = {s["name"] for s in syms}
                assert "Main" in sym_names
                assert "backup" in sym_names
                assert "myform" in sym_names
                assert "Click" in sym_names

                # Should have cross-file edges
                edges = conn.execute("SELECT * FROM edges WHERE kind = 'call'").fetchall()
                assert len(edges) >= 1, "Should have at least one cross-file call edge"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_scx_pipeline_parse_file(self):
        """parse_file should pack SCX+SCT into length-prefixed format."""
        from pathlib import Path

        from roam.index.parser import parse_file

        tmpdir = tempfile.mkdtemp()
        try:
            methods = "PROCEDURE Init\n  RETURN\nENDPROC\n"
            scx, sct = _build_synthetic_scx_sct(
                [
                    {
                        "platform": "WINDOWS",
                        "objname": "MyForm",
                        "baseclass": "form",
                        "methods": methods,
                    },
                ]
            )
            with open(os.path.join(tmpdir, "test.scx"), "wb") as f:
                f.write(scx)
            with open(os.path.join(tmpdir, "test.sct"), "wb") as f:
                f.write(sct)

            tree, source, lang = parse_file(Path(os.path.join(tmpdir, "test.scx")))
            assert tree is None
            assert lang == "foxpro"
            assert source is not None
            # Source should be packed: 4-byte length header
            assert len(source) > 4
            scx_len = struct.unpack(">I", source[:4])[0]
            assert scx_len == len(scx)

            # Extractor should work on the packed data
            ext = FoxProExtractor()
            syms = ext.extract_symbols(None, source, "test.scx")
            classes = [s for s in syms if s["kind"] == "class"]
            assert len(classes) == 1
            meths = [s for s in syms if s["kind"] == "method"]
            assert len(meths) == 1
            assert meths[0]["name"] == "Init"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
