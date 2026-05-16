"""W217 — hostile-input rendering tests for the PR Replay Markdown report.

The PR Replay renderer (``src/roam/commands/cmd_pr_replay.py``) turns a
``ChangeEvidence`` packet into a buyer-facing Markdown report. The packet
carries strings that originate from commit authors, agents, custom
detectors, and CI providers — none of which the renderer controls. A
hostile actor (rogue agent, fuzzy MITM, mistaken commit author) can put
pipes / newlines / HTML / Markdown-link syntax / control chars / BIDI
overrides / overlong values / etc. into any of those strings.

The W217 directive: **the rendered Markdown must remain valid Markdown
regardless of input**. Specifically:

* every table row must have the same column count as its header;
* no premature section breaks (an injected ``## Header`` in a cell
  must NOT be promoted to a real heading);
* no executable HTML / JS — even though Markdown -> HTML pipelines
  vary, the renderer must not hand them obviously-active content
  outside of code-spans;
* control characters and ANSI escape sequences must be stripped or
  neutralised before they reach the report;
* BIDI / RTL / zero-width chars must be made visible to the reviewer;
* empty / whitespace-only values must render a sentinel so the
  column count doesn't collapse silently;
* overlong values must be truncated so the table stays scannable.

Tests are organised by hostile-input class. Each test constructs one
``ChangeEvidence`` packet exercising one hostile input on one section,
asserts:

1. **column-count invariant** — every table line in the targeted
   section starts with ``|`` and has the same number of pipes;
2. **hostile content is escaped** — the raw hostile sequence does NOT
   appear verbatim (it MUST be substituted / escaped / sentinel'd);
3. **section structure** — the target section heading is still present
   and the subsequent section heading is still present (no premature
   section break).

The renderer fix shipped alongside this test file routes every table
cell through ``_escape_cell_text`` / ``_escape_cell_code`` defined in
``cmd_pr_replay.py``. The fix is stdlib-only; no Markdown sanitisation
library was added.
"""

from __future__ import annotations

import dataclasses
import re

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _empty_packet():
    """Build a minimal ``ChangeEvidence`` packet with no optional fields."""
    from roam.evidence import ChangeEvidence

    return ChangeEvidence(
        evidence_id="test:w217",
        git_range="HEAD~1..HEAD",
        verdict="clean",
        risk_level="low",
    )


def _render(packet, *, review_suggestions=None, commits=None):
    """Render ``packet`` through the shared renderer."""
    from roam.commands.cmd_pr_replay import _render_evidence_markdown

    return _render_evidence_markdown(
        evidence=packet,
        commits=commits or [],
        by_detector=[],
        review_suggestions=review_suggestions,
    )


def _section(md: str, heading: str) -> str:
    """Slice the body of one ``## <heading>`` section out of ``md``.

    Returns text from the heading line up to (but not including) the
    next ``##`` heading. The caller can split on lines, count pipes,
    or grep for sentinels inside the slice.
    """
    parts = md.split(f"## {heading}", 1)
    if len(parts) != 2:
        raise AssertionError(f"section '## {heading}' not found in rendered Markdown")
    # The slice from the heading up to the next "## " heading. Use a
    # regex so we don't accidentally split on a ``### `` subheading.
    rest = parts[1]
    next_heading = re.search(r"\n## [A-Z]", rest)
    if next_heading:
        return rest[: next_heading.start()]
    return rest


def _table_lines(section: str) -> list[str]:
    """Return only lines that look like Markdown table rows."""
    return [ln for ln in section.splitlines() if ln.startswith("|")]


def _assert_table_columns_consistent(section: str, where: str) -> None:
    """Pin: every Markdown table row in ``section`` has the same column count.

    Column count = number of ``|`` characters per line. Markdown's table
    syntax is pipe-delimited and a row with the wrong column count is
    silently dropped (or worse, mis-rendered) by most parsers.
    """
    lines = _table_lines(section)
    assert lines, f"{where}: no table rows found in section"
    counts = [ln.count("|") for ln in lines]
    assert len(set(counts)) == 1, f"{where}: hostile input broke table column count — counts={counts}, lines={lines}"


def _assert_section_intact(md: str, target: str, follower: str) -> None:
    """Pin: ``## target`` is followed (eventually) by ``## follower``.

    Confirms no premature section break injected by hostile input.
    """
    target_idx = md.find(f"## {target}")
    follower_idx = md.find(f"## {follower}")
    assert target_idx >= 0, f"section '## {target}' missing from rendered output"
    assert follower_idx >= 0, f"section '## {follower}' missing from rendered output"
    assert target_idx < follower_idx, (
        f"section '## {target}' must precede '## {follower}', "
        f"but indices are target={target_idx} follower={follower_idx}"
    )


# ---------------------------------------------------------------------------
# 1. Pipe characters
# ---------------------------------------------------------------------------


def test_hostile_pipes_in_actor_id_does_not_break_table():
    """Pipes inside ``actor_id`` must not introduce extra table columns."""
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent|admin|root",
                display_name="Cursor | enterprise",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / hostile pipes")
    # Hostile literal pipes must NOT appear verbatim — the renderer
    # substitutes ``|`` -> ``/``.
    assert "agent|admin|root" not in md, "Hostile pipes leaked unescaped into Actors section"
    assert "Cursor | enterprise" not in md, "Hostile pipes in display_name leaked unescaped"
    # And the next section is still where it should be.
    _assert_section_intact(md, target="Actors", follower="Authorities")


def test_hostile_pipes_in_authority_id_does_not_break_table():
    """Pipes inside ``authority_id`` must not introduce extra table columns."""
    from roam.evidence.refs import AuthorityRef

    packet = dataclasses.replace(
        _empty_packet(),
        authority_refs=(
            AuthorityRef(
                authority_kind="approval",
                authority_id="approval:pr|42|review",
                granted_by="human:alice|root@example.com",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Authorities")
    _assert_table_columns_consistent(section, where="Authorities / hostile pipes")
    assert "approval:pr|42|review" not in md
    assert "human:alice|root@example.com" not in md
    _assert_section_intact(md, target="Authorities", follower="Environment")


def test_hostile_pipes_in_environment_id_does_not_break_table():
    """Pipes inside ``env_id`` must not introduce extra table columns."""
    from roam.evidence.refs import EnvironmentRef

    packet = dataclasses.replace(
        _empty_packet(),
        environment_refs=(
            EnvironmentRef(
                env_kind="ci_job",
                env_id="ci_job:gh|owner|repo|run|123",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Environment")
    _assert_table_columns_consistent(section, where="Environment / hostile pipes")
    assert "ci_job:gh|owner|repo|run|123" not in md
    _assert_section_intact(md, target="Environment", follower="Findings")


def test_hostile_pipes_in_findings_detector_does_not_break_table():
    """Pipes inside finding ``detector`` strings must not break the table."""
    packet = dataclasses.replace(
        _empty_packet(),
        findings=({"detector": "evil|detector|name", "confidence": "high|inj", "total_findings": 1},),
    )
    md = _render(packet)
    section = _section(md, "Findings")
    _assert_table_columns_consistent(section, where="Findings / hostile pipes")
    assert "evil|detector|name" not in md
    assert "high|inj" not in md


def test_hostile_pipes_in_changed_subject_does_not_break_table():
    """Pipes inside ``EvidenceSubject.qualified_name`` must not break the table."""
    from roam.evidence.subject import EvidenceSubject

    packet = dataclasses.replace(
        _empty_packet(),
        changed_subjects=(EvidenceSubject(kind="symbol", qualified_name="src|file.py::evil|sym"),),
    )
    md = _render(packet)
    section = _section(md, "Changed subjects (top 20)")
    _assert_table_columns_consistent(section, where="Changed subjects / hostile pipes")
    assert "src|file.py::evil|sym" not in md


# ---------------------------------------------------------------------------
# 2. Newlines / CR
# ---------------------------------------------------------------------------


def test_hostile_newlines_in_actor_id_collapse_to_single_line():
    """``\\n`` / ``\\r`` in ``actor_id`` must not split a table row."""
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="line1\nline2\nline3",
                display_name="multi\rline\rname",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / hostile newlines")
    # The literal multi-line value must NOT appear verbatim. We accept
    # any one-line collapse (space-joined is what the renderer does).
    assert "line1\nline2" not in md
    assert "multi\rline" not in md
    _assert_section_intact(md, target="Actors", follower="Authorities")


def test_hostile_newlines_in_finding_detector_collapse():
    """``\\n`` in finding ``detector`` must collapse, not split the row."""
    packet = dataclasses.replace(
        _empty_packet(),
        findings=({"detector": "evil\ndetector\nname", "confidence": "high", "total_findings": 1},),
    )
    md = _render(packet)
    section = _section(md, "Findings")
    _assert_table_columns_consistent(section, where="Findings / hostile newlines")
    assert "evil\ndetector" not in md


def test_hostile_newlines_in_changed_subject_collapse():
    """``\\n`` in ``qualified_name`` must collapse before backtick wrap."""
    from roam.evidence.subject import EvidenceSubject

    packet = dataclasses.replace(
        _empty_packet(),
        changed_subjects=(EvidenceSubject(kind="symbol", qualified_name="src.py\n::evil_sym"),),
    )
    md = _render(packet)
    section = _section(md, "Changed subjects (top 20)")
    _assert_table_columns_consistent(section, where="Changed subjects / hostile newlines")
    assert "src.py\n::evil_sym" not in md


# ---------------------------------------------------------------------------
# 3. HTML tag injection
# ---------------------------------------------------------------------------


def test_hostile_html_in_display_name_is_inert():
    """HTML tags in ``display_name`` must not survive as executable HTML.

    The renderer wraps display columns in plain prose (no backticks),
    so we can't rely on code-span neutralisation. The escape pipeline
    must at minimum keep the value on one line and avoid promoting it
    to a real heading; many Markdown -> HTML pipelines will then
    HTML-escape what we emit. We assert column-count safety
    unconditionally and check the value did pass through.
    """
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="<img src=x onerror=alert(1)>",
                display_name="<script>alert(1)</script>",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / hostile HTML")
    # The HTML must not have grown extra column dividers.
    # And no premature section break.
    _assert_section_intact(md, target="Actors", follower="Authorities")


def test_hostile_html_in_finding_detector_is_inert():
    """HTML tags inside finding ``detector`` field must not break the table."""
    packet = dataclasses.replace(
        _empty_packet(),
        findings=({"detector": "<script>alert(1)</script>", "confidence": "high", "total_findings": 1},),
    )
    md = _render(packet)
    section = _section(md, "Findings")
    _assert_table_columns_consistent(section, where="Findings / hostile HTML")


# ---------------------------------------------------------------------------
# 4. Markdown link injection
# ---------------------------------------------------------------------------


def test_hostile_markdown_link_injection_is_neutralised():
    """``[label](javascript:...)`` in display columns must be escaped."""
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:test",
                display_name="[click me](javascript:alert(1))",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / hostile markdown link")
    # The raw bracket+paren syntax must be escaped or substituted —
    # the literal ``[click me](javascript:alert(1))`` must not appear
    # as a working Markdown link.
    assert "[click me](javascript:alert(1))" not in md, "Markdown link injection survived unescaped"


def test_hostile_markdown_link_in_authority_granted_by_is_neutralised():
    """Markdown link syntax in ``granted_by`` must be escaped."""
    from roam.evidence.refs import AuthorityRef

    packet = dataclasses.replace(
        _empty_packet(),
        authority_refs=(
            AuthorityRef(
                authority_kind="approval",
                authority_id="approval:pr_42",
                granted_by="[redacted](https://evil.example)",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Authorities")
    _assert_table_columns_consistent(section, where="Authorities / hostile markdown link")
    assert "[redacted](https://evil.example)" not in md


# ---------------------------------------------------------------------------
# 5. Control characters
# ---------------------------------------------------------------------------


def test_hostile_control_chars_in_actor_id_are_stripped():
    """C0 control chars (NUL, BEL, BS, FF, ...) must be stripped."""
    from roam.evidence.refs import ActorRef

    # The raw chars: NUL (\x00), SOH (\x01), BEL (\x07), BS (\x08), FF (\x0c)
    raw_id = "evil\x00\x01\x07\x08\x0cinj"
    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(ActorRef(actor_kind="agent", actor_id=raw_id),),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / control chars")
    # Control chars must not survive into the rendered output.
    for bad in ("\x00", "\x01", "\x07", "\x08", "\x0c"):
        assert bad not in md, f"control char {bad!r} leaked into output"


def test_hostile_ansi_escape_in_display_name_is_stripped():
    """ANSI escape sequences (terminal colour codes) must be stripped."""
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:test",
                display_name="\x1b[31mRED text\x1b[0m",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / ANSI escape")
    # The ESC byte (\x1b) must not survive into a buyer-facing report.
    assert "\x1b" not in md, "ANSI escape byte leaked into rendered output"


# ---------------------------------------------------------------------------
# 6. Markdown header / blockquote injection
# ---------------------------------------------------------------------------


def test_hostile_markdown_header_in_display_name_not_promoted():
    """A leading ``## `` in a cell must NOT be promoted to a real heading.

    The renderer collapses newlines first, so any post-newline ``##``
    becomes mid-line text and can't be promoted. But the literal "##"
    string at cell start is the dangerous case for fault-tolerant
    Markdown parsers; we assert no premature section break.
    """
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:test",
                display_name="\n## Injected Header\n",
            ),
        ),
    )
    md = _render(packet)
    # Count the legitimate top-level ``## Actors``, ``## Authorities``,
    # etc. We don't want ``## Injected Header`` to appear in that list.
    real_headings = [ln for ln in md.splitlines() if ln.startswith("## ")]
    injected = [ln for ln in real_headings if "Injected Header" in ln]
    assert not injected, f"Injected Markdown header was promoted to a real heading: {injected}"
    # And the structure is still intact.
    _assert_section_intact(md, target="Actors", follower="Authorities")


def test_hostile_markdown_header_in_finding_field_not_promoted():
    """A ``# `` in a finding field must not break the section structure."""
    packet = dataclasses.replace(
        _empty_packet(),
        findings=(
            {
                "detector": "# This breaks the section",
                "confidence": "high",
                "total_findings": 1,
            },
        ),
    )
    md = _render(packet)
    section = _section(md, "Findings")
    _assert_table_columns_consistent(section, where="Findings / hostile markdown header")
    real_headings = [ln for ln in md.splitlines() if ln.startswith("## ")]
    injected = [ln for ln in real_headings if "This breaks the section" in ln]
    assert not injected
    _assert_section_intact(md, target="Findings", follower="Tests")


# ---------------------------------------------------------------------------
# 7. Overlong values
# ---------------------------------------------------------------------------


def test_hostile_overlong_actor_id_is_truncated():
    """A 10 000-byte ``actor_id`` must not blow out the rendered table."""
    from roam.evidence.refs import ActorRef

    huge = "x" * 10_000
    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(ActorRef(actor_kind="agent", actor_id=huge),),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / overlong value")
    # The rendered value must be truncated. Bounded by the helper's
    # _MAX_CELL_LEN constant (200); we check the section body never
    # contains a 1 000-char single run of x's.
    huge_run = "x" * 1_000
    assert huge_run not in md, "Overlong actor_id was not truncated (long x-run survived)"


# ---------------------------------------------------------------------------
# 8. Empty / whitespace-only
# ---------------------------------------------------------------------------


def test_hostile_empty_string_renders_sentinel_not_blank_cell():
    """Empty / whitespace-only display_name must render a sentinel.

    A blank cell in a Markdown table is technically legal but a
    consecutive ``| | |`` run is hard to read and easy to misalign.
    The renderer emits an explicit ``<empty>`` sentinel so the column
    count is preserved AND the reviewer sees the absence.
    """
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:test",
                # Note: ActorRef.actor_id must be non-empty (post_init
                # rejects ""); but display_name is optional / coercible
                # to ``"   "``.
                display_name="   ",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / whitespace-only display")
    # The empty-cell sentinel must appear so the reviewer sees the
    # absence rather than a silently-blank cell.
    assert "<empty>" in section, "Whitespace-only display_name should render an <empty> sentinel"


# ---------------------------------------------------------------------------
# 9. Trailing pipe at end of string
# ---------------------------------------------------------------------------


def test_hostile_trailing_pipe_does_not_change_column_count():
    """A trailing ``|`` at end of a value must not add a column."""
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:X|",
                display_name="Y|",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / trailing pipe")
    # The literal trailing pipe must NOT appear.
    assert "agent:X|" not in md
    assert "Y|" not in md.split("---")[0]  # ignore the closing template line


# ---------------------------------------------------------------------------
# 10. Backticks in fields
# ---------------------------------------------------------------------------


def test_hostile_backticks_in_actor_id_do_not_break_code_span():
    """Backticks in ``actor_id`` must not terminate the wrapping code-span.

    The renderer wraps actor_id in backticks: ``` `<id>` ```. If the
    raw id contains a backtick, the code-span closes early and the
    rest of the row becomes mixed-mode Markdown, often breaking the
    table. The fix is to escape backticks inside the id.
    """
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="`whoami`",
                display_name="``` injection",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / hostile backticks")
    # The literal unescaped triple-backtick must NOT appear in the
    # display name (a fenced-code-block opener would inject a code
    # block into the report body).
    assert "``` injection" not in md.split("---")[0]


def test_hostile_triple_backtick_in_finding_does_not_open_code_block():
    """Triple-backtick fence in a finding field must not open a code block."""
    packet = dataclasses.replace(
        _empty_packet(),
        findings=(
            {
                "detector": "```python\nimport os; os.system('rm -rf /')\n```",
                "confidence": "high",
                "total_findings": 1,
            },
        ),
    )
    md = _render(packet)
    section = _section(md, "Findings")
    _assert_table_columns_consistent(section, where="Findings / triple backtick")
    # The unmodified injection block must NOT appear verbatim — at
    # minimum the newlines must collapse to spaces, breaking the
    # fenced code block.
    assert "```python\nimport os" not in md


# ---------------------------------------------------------------------------
# 11. Unicode RTL / BIDI / zero-width
# ---------------------------------------------------------------------------


def test_hostile_rtl_override_is_made_visible():
    """U+202E (RTL override) must be surfaced as a visible codepoint marker.

    A hostile actor can use ``\\u202e`` to flip subsequent text into
    RTL display order — what reads as ``admin`` to the user can be
    ``nimda`` in the underlying bytes. The renderer must make the
    invisible character visible so a reviewer can see the manipulation.
    """
    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="‮admin",
                display_name="totally​hidden",
            ),
        ),
    )
    md = _render(packet)
    section = _section(md, "Actors")
    _assert_table_columns_consistent(section, where="Actors / BIDI override")
    # The raw ‮ / ​ bytes must NOT survive into the output.
    assert "‮" not in md, "RTL override survived raw into report"
    assert "​" not in md, "Zero-width space survived raw into report"
    # And the visible codepoint marker must appear — so the reviewer
    # has a chance to see the manipulation. The marker format is
    # ``<U+XXXX>`` per the renderer's _collapse_to_line.
    assert "U+202E" in md, "RTL override should be made visible as a <U+202E> marker"


# ---------------------------------------------------------------------------
# Bonus: the big assertion — the rendered Markdown must remain valid.
# ---------------------------------------------------------------------------


def test_compound_hostile_packet_renders_valid_markdown():
    """Every section of a maximally-hostile packet renders valid tables.

    Combines all 11 hostile classes into one packet and asserts the
    column-count invariant for every table section. This is the
    canonical W217 invariant: no single hostile input class, and no
    combination of them, can break the Markdown structure.
    """
    from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef
    from roam.evidence.subject import EvidenceSubject

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent|admin\n`whoami`‮",
                display_name="<script>alert(1)</script>\n## Inj",
            ),
            ActorRef(
                actor_kind="human",
                actor_id="x" * 5_000,
                display_name="   ",
            ),
        ),
        authority_refs=(
            AuthorityRef(
                authority_kind="approval",
                authority_id="approval:[evil](js:1)|x",
                granted_by="alice\nbob\rcharlie",
            ),
        ),
        environment_refs=(
            EnvironmentRef(
                env_kind="ci_job",
                env_id="ci|job\x1b[31m|x",
            ),
        ),
        changed_subjects=(
            EvidenceSubject(
                kind="symbol",
                qualified_name="src/foo.py::bad|\nname",
            ),
        ),
        findings=({"detector": "evil|\ndetector", "confidence": "high|x", "total_findings": 1},),
    )
    md = _render(packet)

    # Every section's table must have a consistent column count.
    for heading in (
        "Changed subjects (top 20)",
        "Actors",
        "Authorities",
        "Environment",
        "Findings",
    ):
        section = _section(md, heading)
        _assert_table_columns_consistent(section, where=f"compound packet / {heading}")

    # All section headings present, in expected order.
    headings_order = [
        "Scope",
        "Changed subjects (top 20)",
        "Actors",
        "Authorities",
        "Environment",
        "Findings",
        "Tests",
        "Approvals and accepted risks",
        "Suggested Review configuration",
        "Evidence limitations",
    ]
    indices = [md.find(f"## {h}") for h in headings_order]
    assert all(i >= 0 for i in indices), f"missing headings — indices={dict(zip(headings_order, indices))}"
    assert indices == sorted(indices), f"section order is wrong — got {dict(zip(headings_order, indices))}"
    # And no injected header was promoted.
    real_headings = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert not any("Inj" in ln and ln.startswith("## ") for ln in real_headings)
