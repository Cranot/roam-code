"""Tests for Visual FoxPro language support."""

from __future__ import annotations

import os
import tempfile
import shutil

import pytest

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
        """DO CASE, DO WHILE, DO FORM should NOT produce call refs."""
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
            subprocess.run(["git", "config", "user.email", "test@test.com"],
                           cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"],
                           cwd=tmpdir, capture_output=True)

            # caller.prg uses uppercase: DO BACKUP
            with open(os.path.join(tmpdir, "caller.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION Main\n  DO BACKUP\nENDFUNC\n")

            # target.prg defines lowercase: FUNCTION backup
            with open(os.path.join(tmpdir, "target.prg"), "w", encoding="utf-8") as f:
                f.write("FUNCTION backup\n  RETURN .T.\nENDFUNC\n")

            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"],
                           cwd=tmpdir, capture_output=True)

            from roam.index.indexer import Indexer
            from pathlib import Path
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
        from roam.index.parser import parse_file, REGEX_ONLY_LANGUAGES
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

            from roam.index.symbols import extract_symbols, extract_references
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
            subprocess.run(["git", "config", "user.email", "test@test.com"],
                           cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"],
                           cwd=tmpdir, capture_output=True)

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
            subprocess.run(["git", "commit", "-m", "init"],
                           cwd=tmpdir, capture_output=True)

            # Run indexer
            from roam.index.indexer import Indexer
            from pathlib import Path
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
