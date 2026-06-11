"""Naming-convention sampling regressions (dogfood, external Vue/PHP repo).

Three sampling bugs made the naming rule 100%-FP on a camelCase codebase:

1. Test files outvoted production code — PHPUnit ``test_creates_invoice``
   method names made the detected convention "snake_case 62.8%" on a
   PSR-12 camelCase repo, then every production method was flagged.
2. Single lowercase words (``props``, ``run``, ``delay``) were counted as
   snake_case votes AND flagged against camelCase — they carry no case
   signal at all.
3. Framework lifecycle overrides (``setUp``) were told to rename.

These tests pin the fixes at both consumers: the canonical
``conventions_helper.compute_conventions`` and verify's ``_check_naming``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.commands.conventions_helper import compute_conventions  # noqa: E402
from roam.db.connection import open_db  # noqa: E402

PROD_JS = """\
function getUserName(userRecord) { return userRecord.fullName; }
function parseEmail(rawText) { return rawText.trim(); }
function resolveBackoff(retryCount) { return retryCount * 2; }
function syncFromCursor(cursorValue) { return cursorValue + 1; }
function formatAmount(amountValue) { return amountValue.toFixed(2); }
"""

# More test functions than production ones — the poisoned-majority shape.
TEST_JS = """\
function test_creates_user_record() { return true; }
function test_parses_email_lower() { return true; }
function test_parses_email_upper() { return true; }
function test_backoff_doubles() { return true; }
function test_backoff_caps_at_budget() { return true; }
function test_sync_advances_cursor() { return true; }
function test_sync_skips_dupes() { return true; }
function test_format_two_decimals() { return true; }
function test_format_rounds_half_up() { return true; }
function test_format_negative_amounts() { return true; }
"""


def _build_repo(tmp_path: Path) -> Path:
    proj = tmp_path / "camel_repo"
    (proj / "src").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "services.js").write_text(PROD_JS)
    (proj / "tests" / "services.test.js").write_text(TEST_JS)
    git_init(proj)
    index_in_process(proj)
    return proj


def test_test_files_do_not_vote_in_convention(tmp_path, monkeypatch):
    proj = _build_repo(tmp_path)
    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        conv = compute_conventions(conn)

    fn_conv = conv["by_kind"].get("function")
    assert fn_conv is not None, "production functions must be sampled"
    # 10 snake_case test functions vs 5 camelCase production functions:
    # pre-fix the majority came out snake_case. Test-role files must not vote.
    assert fn_conv["style"] == "camelCase", conv["by_kind"]
    assert fn_conv["breakdown"].get("snake_case", 0) == 0


def test_production_camelcase_not_flagged_as_outlier(tmp_path, monkeypatch):
    proj = _build_repo(tmp_path)
    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        conv = compute_conventions(conn)

    flagged = {o["name"] for o in conv["outliers"]}
    for prod_name in ("getUserName", "parseEmail", "resolveBackoff", "syncFromCursor"):
        assert prod_name not in flagged, conv["outliers"]


def test_verify_naming_does_not_flag_production_camelcase(tmp_path, monkeypatch):
    proj = _build_repo(tmp_path)
    monkeypatch.chdir(proj)
    from roam.commands.cmd_verify import _check_naming

    with open_db(readonly=True) as conn:
        rows = conn.execute("SELECT id, path FROM files WHERE path LIKE '%services%'").fetchall()
        all_ids = [r["id"] for r in rows]
        result = _check_naming(conn, all_ids)

    flagged = {v["symbol"] for v in result["violations"]}
    assert not flagged, result["violations"]


def test_framework_lifecycle_names_never_flagged():
    from roam.commands.cmd_conventions import classify_case

    for name in ("setUp", "tearDown", "setUpBeforeClass", "beforeEach", "componentDidMount", "ngOnInit"):
        assert classify_case(name) is None, name


def test_single_lowercase_words_are_neutral_everywhere():
    from roam.commands.cmd_conventions import classify_case

    for name in ("props", "text", "surface", "run", "delay", "session", "emit"):
        assert classify_case(name) is None, name
