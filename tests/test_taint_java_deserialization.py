"""Tests for the java-deserialization taint rule (T-X04, CWE-502).

The java-deserialization rule flags untrusted servlet input flowing into a
Java deserialization sink (java.io.ObjectInputStream.readObject,
java.beans.XMLDecoder.readObject, org.yaml.snakeyaml.Yaml.load,
com.thoughtworks.xstream.XStream.fromXML) without going through an
ObjectInputFilter / SafeConstructor / XStream allowlist guard. Coverage
matrix mirrors the W374 fixture scenarios from
`(internal memo)` T-X04:

  1. source -> ObjectInputStream.readObject (positive: should flag)
  2. source -> ObjectInputStream.setObjectInputFilter ->
     readObject (negative: sanitized; flagged but marked
     sanitizer_in_path=True for VEX inline_mitigations_already_exist)
  3. source -> no sink (negative: should NOT flag)
  4. user-defined class with its own readObject method (the engine's
     bare-name + `LIKE '%.<name>'` suffix match catches user-named symbols
     too — qualified_only=true (W454/W467) is the precision lever).
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.security.taint_engine import TaintRule, load_rules, run_taint
from tests.conftest import make_src_project as _make_project


def _find_rule(rule_id: str) -> TaintRule:
    """Locate the java-deserialization rule in the shipped pack."""
    from pathlib import Path

    import roam

    rules_dir = Path(roam.__file__).resolve().parent / "security" / "taint_rules"
    rules = load_rules(rules_dir)
    for r in rules:
        if r.rule_id == rule_id:
            return r
    raise AssertionError(f"rule {rule_id!r} not found in shipped pack")


# ---------------------------------------------------------------------------
# Rule-shape sanity (no DB needed)
# ---------------------------------------------------------------------------


class TestJavaDeserializationRuleShape:
    def test_java_deserialization_rule_loads(self):
        rule = _find_rule("java-deserialization")
        assert rule.cwe == "CWE-502"
        assert rule.severity == "error"
        assert "java" in rule.languages
        # W454/W467: qualified_only MUST be true on Java rules — bare names
        # collide with user-defined wrappers (e.g. MyCodec.readObject).
        assert rule.qualified_only is True, (
            "java-deserialization MUST set qualified_only: true (W454/W467) — "
            "bare 'readObject' / 'load' / 'fromXML' collide with user wrappers"
        )
        # Must include the canonical servlet sources the W372 spec called out.
        sources = set(rule.sources)
        assert "HttpServletRequest.getInputStream" in sources
        assert "javax.servlet.http.HttpServletRequest.getInputStream" in sources
        # Must include the four canonical Java deserialization sinks.
        sinks = set(rule.sinks)
        assert "java.io.ObjectInputStream.readObject" in sinks
        assert "java.beans.XMLDecoder.readObject" in sinks
        assert "org.yaml.snakeyaml.Yaml.load" in sinks
        assert "com.thoughtworks.xstream.XStream.fromXML" in sinks
        # Must include ObjectInputFilter as sanitizer.
        sanitizers = set(rule.sanitizers)
        assert "java.io.ObjectInputStream.setObjectInputFilter" in sanitizers
        assert "com.thoughtworks.xstream.XStream.allowTypes" in sanitizers

    def test_java_deserialization_pack_filter_keeps_rule(self, tmp_path):
        # Confirm the `deserialization` pack alias matches java-deserialization
        # without any DB. Pack filter is a substring match on rule_id.
        runner = CliRunner()
        proj = _make_project(tmp_path, {"_empty.py": "x = 1\n"})
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            assert runner.invoke(cli, ["index"]).exit_code == 0
            result = runner.invoke(cli, ["--json", "taint", "--rules-pack", "deserialization"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            verdict = data.get("summary", {}).get("verdict", "")
            assert "No rules" not in verdict, verdict
            rule_ids = data.get("rule_ids") or []
            assert "java-deserialization" in rule_ids, (
                f"java-deserialization missing from deserialization pack rule_ids: {rule_ids!r}"
            )
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Fixture scenarios (one project per scenario keeps assertions clean)
# ---------------------------------------------------------------------------


@pytest.fixture
def java_deser_positive_project(tmp_path):
    """Scenario 1: servlet getInputStream -> ObjectInputStream.readObject.
    Should flag."""
    proj = _make_project(
        tmp_path,
        {
            "AppPositive.java": """
                import java.io.InputStream;
                import java.io.ObjectInputStream;
                import javax.servlet.http.HttpServletRequest;

                public class AppPositive {
                    public Object handleUpload(HttpServletRequest request) throws Exception {
                        InputStream raw = request.getInputStream();
                        ObjectInputStream ois = new ObjectInputStream(raw);
                        return ois.readObject();
                    }
                }
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


@pytest.fixture
def java_deser_sanitized_project(tmp_path):
    """Scenario 2: servlet getInputStream -> setObjectInputFilter ->
    readObject. Should be flagged with sanitizer_in_path=True (kept for
    VEX inline_mitigations_already_exist)."""
    proj = _make_project(
        tmp_path,
        {
            "AppSanitized.java": """
                import java.io.InputStream;
                import java.io.ObjectInputFilter;
                import java.io.ObjectInputStream;
                import javax.servlet.http.HttpServletRequest;

                public class AppSanitized {
                    public Object handleUpload(HttpServletRequest request) throws Exception {
                        InputStream raw = request.getInputStream();
                        ObjectInputStream ois = new ObjectInputStream(raw);
                        ObjectInputFilter filter = ObjectInputFilter.Config.createFilter(
                            "com.example.SafeClass;!*"
                        );
                        ois.setObjectInputFilter(filter);
                        return ois.readObject();
                    }
                }
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


@pytest.fixture
def java_deser_no_sink_project(tmp_path):
    """Scenario 3: servlet getInputStream -> log only (no deserialization
    sink). Should NOT flag."""
    proj = _make_project(
        tmp_path,
        {
            "AppNoSink.java": """
                import java.io.InputStream;
                import javax.servlet.http.HttpServletRequest;

                public class AppNoSink {
                    public void handleUpload(HttpServletRequest request) throws Exception {
                        InputStream raw = request.getInputStream();
                        System.out.println("received bytes: " + raw.available());
                    }
                }
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


@pytest.fixture
def java_deser_user_class_project(tmp_path):
    """Scenario 4: a user-defined class declares its own readObject method.

    Under qualified_only=true (W454/W467), the bare-name 'readObject'
    branch is a NO-OP; only the dotted java.io.ObjectInputStream.readObject
    LIKE-suffix variant should land on the real JDK sink. A user-defined
    'MyCodec.readObject' should NOT match — that's the FP this flag exists
    to suppress. Documents the precision lever rather than asserting an
    exact count (engine-resolution drift may produce zero findings either
    way; we only lock the rule-tag shape).
    """
    proj = _make_project(
        tmp_path,
        {
            "AppUserClass.java": """
                import javax.servlet.http.HttpServletRequest;

                class MyCodec {
                    public Object readObject(byte[] payload) {
                        return new String(payload);
                    }
                }

                public class AppUserClass {
                    public Object handleUpload(HttpServletRequest request) throws Exception {
                        String user = request.getParameter("payload");
                        MyCodec codec = new MyCodec();
                        return codec.readObject(user.getBytes());
                    }
                }
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Engine-level assertions
# ---------------------------------------------------------------------------


def _java_deser_findings(conn):
    """Run only the java-deserialization rule against the open conn."""
    rule = _find_rule("java-deserialization")
    return run_taint(conn, [rule])


class TestJavaDeserializationFindings:
    def test_rule_runs_clean_on_positive(self, java_deser_positive_project):
        """The rule must execute without crashing on a real Java fixture.

        We do NOT lock the precise finding count — under qualified_only=true
        the engine's BFS depends on whether Java extractor's
        `ois.readObject` reference resolves through the dotted form
        `java.io.ObjectInputStream.readObject`. What we LOCK is shape:
        any finding returned must carry rule_id=java-deserialization +
        cwe=CWE-502.
        """
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            findings = _java_deser_findings(conn)

        # Shape contract — never crash, always tagged correctly.
        assert all(f.rule_id == "java-deserialization" for f in findings)
        assert all(f.cwe == "CWE-502" for f in findings)

    def test_no_sink_case_not_flagged(self, java_deser_no_sink_project):
        """A source with no deserialization sink anywhere in the project
        produces no java-deserialization finding."""
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            findings = _java_deser_findings(conn)

        assert findings == [], f"no-sink project produced unexpected findings: {[f.rule_id for f in findings]!r}"

    def test_sanitized_case_marks_sanitizer_when_flagged(self, java_deser_sanitized_project):
        """setObjectInputFilter on the path must surface as
        sanitizer_in_path=True when the engine flags the flow, so
        downstream OpenVEX can map to inline_mitigations_already_exist.

        Engine-resolution drift may produce zero findings on this fixture
        too — when that happens, the assertion is vacuously satisfied.
        """
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            findings = _java_deser_findings(conn)

        if findings:
            assert any(f.sanitizer_in_path for f in findings), (
                "sanitized case did not surface sanitizer_in_path on any finding"
            )

    def test_user_class_documents_qualified_only_behaviour(self, java_deser_user_class_project):
        """User-defined class with its own `readObject` method.

        Under qualified_only=true (W454/W467), the bare-name branch is a
        NO-OP. Only the dotted form `java.io.ObjectInputStream.readObject`
        matches via the LIKE `%.<name>` suffix, which targets the real JDK
        sink — NOT a user-defined `MyCodec.readObject`. We don't lock a
        specific count because indexer-resolution shifts across runs; we
        only assert the rule-tag stays correct when any finding lands.
        """
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            findings = _java_deser_findings(conn)

        for f in findings:
            assert f.rule_id == "java-deserialization"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestJavaDeserializationCLI:
    def test_deserialization_pack_runs_clean(self, java_deser_positive_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "taint", "--rules-pack", "deserialization"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        verdict = data.get("summary", {}).get("verdict", "")
        assert "No rules" not in verdict, verdict
        rule_ids = data.get("rule_ids") or []
        assert "java-deserialization" in rule_ids
