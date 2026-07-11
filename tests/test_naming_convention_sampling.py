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


# ---------------------------------------------------------------------------
# React-hook carve-out (#221): a JS-family function named ``^use[A-Z]`` follows
# the Rules of Hooks and must NOT be flagged as a naming violation — even in a
# ``.tsx`` file whose dominant function style is PascalCase (its components).
# The carve-out is surgical: it exempts ``^use[A-Z]`` on js functions ONLY, so a
# genuinely mis-named helper (snake_case, or a Python ``useX``) still flags.
# ---------------------------------------------------------------------------


def test_react_hook_name_exempted_for_js_families():
    from roam.commands.cmd_conventions import is_react_hook_name

    for lang in ("typescript", "tsx", "javascript", "vue", "jsx", "svelte"):
        assert is_react_hook_name("useSwarm", lang), lang
        assert is_react_hook_name("useProjects", lang), lang
        assert is_react_hook_name("useDrainPreview", lang), lang
        assert is_react_hook_name("useSwarmActions", lang), lang
        # Library hooks share the same convention.
        assert is_react_hook_name("useState", lang), lang


def test_plain_camelcase_helpers_are_not_hooks():
    # Precision guard #1: ordinary camelCase JS helpers are NOT exempted, so
    # they still flow to the normal violation check.
    from roam.commands.cmd_conventions import is_react_hook_name

    for name in ("handleClick", "getUser", "parseEmail", "resolveBackoff"):
        assert not is_react_hook_name(name, "tsx"), name


def test_non_js_use_names_are_never_hooks():
    # Precision guard #2 (the core language gate): a Python helper is never
    # treated as a React hook, so a genuinely mis-named Python helper still
    # flags. Covers both snake and camel spellings and an untracked language.
    from roam.commands.cmd_conventions import is_react_hook_name

    for lang in ("python", "ruby", "go", "rust", "java", "csharp", None):
        assert not is_react_hook_name("useThing", lang), lang
        assert not is_react_hook_name("use_thing", lang), lang


def test_use_prefix_requires_uppercase():
    # Precision guard #3: ``^use[A-Z]`` requires the capital, so ``username`` /
    # ``useful`` / ``user_id`` are ordinary identifiers, not hooks.
    from roam.commands.cmd_conventions import is_react_hook_name

    for name in ("username", "useful", "user_id", "used", "usering"):
        assert not is_react_hook_name(name, "tsx"), name


# A ``.tsx`` module whose dominant function style is PascalCase (React
# components), plus idiomatic ``useX`` hooks (camelCase, correct) and ONE
# genuinely mis-named plain helper (``get_user_name``, snake_case — a real
# violation that MUST still fire). >= 10 function symbols so the
# ``_NAMING_MIN_LANG_SAMPLES`` gate is satisfied for (js, functions).
TSX_COMPONENTS = """\
export function App() { return null; }
export function Button() { return null; }
export function Header() { return null; }
export function Footer() { return null; }
export function Sidebar() { return null; }
export function Modal() { return null; }
export function Card() { return null; }
export function Table() { return null; }
export function Navbar() { return null; }
export function Spinner() { return null; }
export function useSwarm() { return null; }
export function useProjects() { return null; }
export function useThemes() { return null; }
function get_user_name() { return null; }
"""


def _build_tsx_repo(tmp_path: Path) -> Path:
    proj = tmp_path / "tsx_repo"
    (proj / "src").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "App.tsx").write_text(TSX_COMPONENTS)
    git_init(proj)
    index_in_process(proj)
    return proj


def test_verify_naming_does_not_flag_react_hooks(tmp_path, monkeypatch):
    proj = _build_tsx_repo(tmp_path)
    monkeypatch.chdir(proj)
    from roam.commands.cmd_verify import _check_naming

    with open_db(readonly=True) as conn:
        rows = conn.execute("SELECT id, path FROM files WHERE path LIKE '%App.tsx%'").fetchall()
        all_ids = [r["id"] for r in rows]
        result = _check_naming(conn, all_ids)

    flagged = {v["symbol"] for v in result["violations"]}
    # POSITIVE (fix works): the ``useX`` hooks must NOT be flagged even though
    # the dominant function style in this file is PascalCase.
    for hook in ("useSwarm", "useProjects", "useThemes"):
        assert hook not in flagged, result["violations"]
    # NEGATIVE (precision preserved — THE proof): a genuinely mis-named plain
    # helper (snake_case, NOT a hook) MUST still flag. If this fails the
    # carve-out is over-suppressing.
    assert "get_user_name" in flagged, result["violations"]


def test_conventions_outliers_do_not_flag_react_hooks(tmp_path, monkeypatch):
    proj = _build_tsx_repo(tmp_path)
    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        conv = compute_conventions(conn)

    flagged = {o["name"] for o in conv["outliers"]}
    # Pin the conventions-path wiring: hooks excluded from outliers, mis-named
    # snake helper still an outlier against the PascalCase-dominant style.
    for hook in ("useSwarm", "useProjects", "useThemes"):
        assert hook not in flagged, conv["outliers"]
    assert "get_user_name" in flagged, conv["outliers"]
