r"""W805-LLLLL -- ``cmd_attest`` verifier-side identity-skip DISCONFIRMATION pin.

Hundred-and-sixteenth-in-batch W805 sweep. PROBE was: is ``cmd_attest`` the
FIFTH member of the verifier-side identity-skip slice of the lineage-
disclosure family, alongside:

- W805-PPPP cmd_cga                  (predicate.subject[0].name never checked)
- W805-UUUU cmd_audit_trail_verify   (actor/repo/git_sha never cross-checked)
- W805-ZZZZ cmd_evidence_diff        (two-packet identity never cross-checked)
- W805-BBBBB cmd_pr_bundle validate  (bundle commit_sha never cross-checked)

The hypothesis: ``cmd_attest`` emits an attestation predicate (legacy 7-axis
``evidence`` dict, content-hashed via ``--sign``; see W805-GGGGG signing-
surface pin). If cmd_attest had a ``--verify`` subcommand or sibling
``verify_attest`` path that re-loads + re-checks the attestation against
live state, it would be the 5th verifier-side family member.

**RESULT: DISCONFIRMED.** ``cmd_attest`` is structurally INELIGIBLE for the
verifier-side identity-skip family. The pin captures the POSITIVE
architectural invariant (no verifier-side re-load path exists on cmd_attest;
attestation verification is delegated to the sibling ``cga`` / ``audit-
trail-verify`` commands per the docstring at cmd_attest.py:754) so a future
refactor that introduces a ``--verify`` flag, ``verify_attestation`` helper,
or persisted ``.roam/attest/`` reader on cmd_attest is caught at the
architectural gate, not after it ships with the same bug class as
cmd_cga / cmd_audit_trail_verify.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Full-source read of ``cmd_attest.py``** (1167 lines). The command:
   - Click-decorated ``@click.command("attest")`` with options
     ``--staged`` / ``--format`` / ``--sign`` / ``--output``. NO
     ``--verify`` flag, NO ``--load`` flag, NO subcommand group.
   - Optional ``--sign`` flag computes ``_content_hash(evidence)``
     (cmd_attest.py:907-908, sha256 over canonical JSON dump).
     This is the PRODUCER-SIDE signing surface (pinned at W805-GGGGG
     for producer-coverage flattening). The signed hash is EMITTED
     INTO the output envelope; it is NEVER re-loaded + re-checked
     by cmd_attest itself.
   - Output sinks: stdout (text / markdown / json) OR ``--output
     <file>`` via ``atomic_write_text``. There is NO ``.roam/attest/``
     persistence directory; the user controls the output path.
   - Docstring (cmd_attest.py:753-755) explicitly delegates
     verification to siblings: "See also ``cga`` (CodeGraph attestation),
     ``pr-risk`` (single composite risk score), and ``audit-trail-
     verify`` (verify a previously-signed artifact)."

2. **Cross-file grep for attest verification helpers**:

       grep -rn 'def load_attest|def read_attest|def verify_attest|
                 verify_attestation|load_attestation|read_attestation'
                 src/roam/

   Result: ZERO hits. No verification reader exists. Attestation
   verification lives in cmd_cga (``roam cga verify <path>``, see
   cmd_cga.py:76 + src/roam/attest/cga.py:383-458) and in
   ``audit-trail-verify``. The verifier-side responsibility is
   architecturally SEPARATED from cmd_attest, by design.

3. **Cross-file grep for an attest persistence directory**:

       grep -rn '\.roam/attest|\.roam.attest' src/roam/

   Result: hits on ``.roam/attestations/`` (the CGA in-toto write
   path, owned by cmd_cga + emit_vsa.py + cmd_article_12_check.py),
   ZERO hits on ``.roam/attest/`` (no cmd_attest-owned directory).
   The cmd_attest output path is USER-controlled via ``--output``;
   the command itself does not maintain a persistence convention.

4. **Axis distinction from prior cmd_attest probes.**
   cmd_attest has been pinned twice already on distinct axes:

     * W805-OOOO -- shared ``get_changed_files`` silent-SAFE on
       bogus-ref input. THIRD strict consumer of the helper.
     * W805-GGGGG -- ``--sign`` signing-surface producer-coverage
       flattening (legacy 7-axis evidence dict, no W174
       ChangeEvidence link, no producer-coverage Q1-Q8 state).

   W805-LLLLL probes a THIRD axis: the verifier-side re-load +
   identity-check path. The three axes are independent:
     - OOOO covers SHARED-HELPER ingest (boundary).
     - GGGGG covers PRODUCER signing surface (output mint).
     - LLLLL covers VERIFIER re-load (input read-back).
   A command can confirm any combination; cmd_attest confirms
   OOOO + GGGGG and DISCONFIRMS LLLLL. All three pins coexist.

5. **Structural eligibility check against the 4-STRONG family shape.**
   All four sister verifier-side members share TWO load-bearing
   structural features:

     (a) A PERSISTED ARTEFACT on disk that the command itself owns:
         ``.roam/attestations/<sha>.intoto.jsonl`` (PPPP read-back
         via ``verify_cga_statement``),
         ``.roam/runs/<id>/events.jsonl`` (UUUU read-back via
         ``audit_trail_verify``),
         ``.roam/evidence/<scope>.json`` (ZZZZ read-back via
         ``evidence_diff``),
         ``.roam/pr-bundles/<branch>.json`` (BBBBB read-back via
         ``pr_bundle validate``).

     (b) A RE-LOAD + RE-CHECK PATH inside the same command (or
         sibling sub-verb): ``cga verify``, ``audit-trail-verify``,
         ``evidence-diff``, ``pr-bundle validate``.

   cmd_attest has NEITHER. (a) No cmd_attest-owned persistence
   directory -- the ``--output`` sink is user-controlled and
   transient. (b) No re-load helper in cmd_attest.py; verification
   is delegated to ``cga verify`` / ``audit-trail-verify``. The
   verifier-side identity-skip family is therefore structurally
   inapplicable to cmd_attest: there is no command-owned identity
   to re-load, ergo no identity to silently skip cross-checking.

6. **W907 verify-the-cycle check.** Scanned cmd_attest.py for
   defensive "to avoid circular import" / "to avoid cycle" /
   "lazy import" hedges. ZERO hits. The function-local imports
   inside the ``_collect_*`` helpers (e.g.
   ``from roam.graph.builder import build_symbol_graph`` inside
   ``_collect_blast_radius``) are deferred for HEAVY-IMPORT
   reasons (networkx ~500ms cold-import cost on a no-changes
   fast path), not to dodge a fake cycle. W907 invariant holds;
   no cargo-cult hedges.

W805 sweep tally update
=======================

- Through W805-LLLLL: ~55/55 commands probed across the sweep.
- This entry: DISCONFIRMATION (architectural invariant pinned, no bug).
- Verifier-side family STAYS at 4-STRONG (PPPP / UUUU / ZZZZ / BBBBB).
  cmd_attest is NOT eligible; family did not grow.
- Lineage-disclosure family STAYS at 9-STRONG; cmd_attest does not
  become a 10th member via this probe (it is already a 2-axis member
  via W805-OOOO + W805-GGGGG, which are NOT verifier-side identity-
  skip family entries).

W805-MMMMM candidate axes (recommendations for next batch)
==========================================================

- ``cmd_for_*`` recipe commands (for_security_review / for_bug_fix /
  for_refactor / diagnose_issue) -- compound-recipe orchestrators
  that chain ``preflight`` / ``impact`` / ``critique`` / ``vulns``.
  Axis: COMPOUND-RECIPE identity-skip on a verifier-side replay
  (the resolved-symbol passed between subcommands re-resolves from
  a string at each link -- LAW 9 ``coupling lives in what steps SAY``).
- ``cmd_triage`` -- forward-looking findings-prioritizer, possibly
  paired with a persisted-priority artefact.
- ``cmd_findings show`` -- reads a persisted finding row; could it
  re-load without cross-checking the detector_version that minted
  the row?
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_attest.py"


# ---------------------------------------------------------------------------
# Architectural invariants -- HOLD TODAY (no xfail)
# ---------------------------------------------------------------------------


def test_cmd_attest_exists():
    """``cmd_attest.py`` must exist (W805 sweep precondition)."""
    if not _CMD_PATH.exists():
        pytest.skip(f"cmd_attest not present at {_CMD_PATH}")
    assert _CMD_PATH.is_file()


def test_cmd_attest_has_no_verify_mode():
    """W978 verification: cmd_attest does NOT expose a ``--verify`` path.

    The verifier-side identity-skip family (cga/audit_trail/evidence_diff/
    pr-bundle validate) is structurally defined by a re-load + re-check
    shape inside the same command (or a sibling sub-verb of the same
    command group). cmd_attest is a click.command (not a click.group)
    with NO ``--verify`` flag, NO ``--load`` flag, NO ``verify`` or
    ``validate`` subcommand.

    If a future refactor introduces a verification path on cmd_attest,
    THIS test fails -- alerting the next agent that the verifier-side
    identity-skip family probe (W805-LLLLL) must be re-opened.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_attest not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    # No --verify / --load option on the click decorator.
    for forbidden in ('"--verify"', "'--verify'", '"--load"', "'--load'"):
        assert forbidden not in src, (
            f"cmd_attest now declares a `{forbidden}` option -- the W805-LLLLL "
            "disconfirmation pin assumed no verifier-side flag existed; that "
            "assumption is now invalid. The verifier-side identity-skip "
            "family probe MUST be re-opened on cmd_attest."
        )
    # No verify_attest / load_attest / read_attest helper functions.
    for forbidden in (
        "def verify_attestation",
        "def load_attestation",
        "def read_attestation",
        "def verify_attest",
        "def load_attest",
        "def read_attest",
        "def _verify_attestation",
        "def _load_attestation",
    ):
        assert forbidden not in src, (
            f"cmd_attest now defines `{forbidden}` -- a verifier-side re-load "
            "path now exists. W805-LLLLL disconfirmation pin MUST be "
            "re-opened; the command is now eligible for the verifier-side "
            "identity-skip family (PPPP/UUUU/ZZZZ/BBBBB)."
        )


def test_cmd_attest_is_emitter_only_not_a_click_group():
    """cmd_attest is a leaf command, NOT a sub-verb group.

    Pins the architectural invariant that drives the disconfirmation:
    cmd_attest is declared via ``@click.command("attest")`` (single leaf),
    NOT via ``@click.group("attest")`` (which would admit ``attest verify``
    / ``attest validate`` sub-verbs). If the decorator drifts to a group,
    a verifier sub-verb becomes structurally possible and W805-LLLLL must
    be reopened.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_attest not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    assert '@click.command("attest")' in src, (
        "cmd_attest is no longer declared via @click.command('attest') -- "
        "if it switched to @click.group, verifier sub-verbs are now "
        "structurally admissible. Re-open W805-LLLLL."
    )
    # And explicitly: no group decorator on the attest entry point.
    assert '@click.group("attest")' not in src
    assert "@click.group('attest')" not in src


def test_cmd_attest_delegates_verification_to_siblings():
    """Docstring delegates verification to ``cga`` / ``audit-trail-verify``.

    Pins the load-bearing architectural choice: cmd_attest is the EMITTER
    layer; verification is a SIBLING command's responsibility. If the
    "See also ``audit-trail-verify`` (verify a previously-signed artifact)"
    line is removed from the docstring, the architectural delegation has
    drifted and W805-LLLLL must be re-probed.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_attest not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    # The docstring at cmd_attest.py:754 names the verify sibling.
    assert "audit-trail-verify" in src, (
        "cmd_attest docstring no longer references `audit-trail-verify` -- "
        "the architectural delegation that grounds the W805-LLLLL "
        "disconfirmation is missing. Re-probe the verifier-side axis."
    )


def test_cmd_attest_has_no_persisted_attest_directory():
    """cmd_attest does NOT own a ``.roam/attest/`` persistence directory.

    The four sister verifier-side family members each own a command-
    specific persisted-artefact directory (.roam/attestations/, .roam/
    runs/, .roam/evidence/, .roam/pr-bundles/). cmd_attest's ``--output``
    sink is USER-controlled (any path the user supplies) and there is
    no command-owned convention. If a ``.roam/attest/`` directory
    convention is introduced, the command gains a persisted identity
    that a verifier could silently skip-check -- re-open W805-LLLLL.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_attest not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    # No .roam/attest/ or .roam/attestations/ write convention inside
    # cmd_attest itself. (The .roam/attestations/ directory belongs to
    # cmd_cga / emit_vsa.py / article_12_check.py -- NOT cmd_attest.)
    assert ".roam/attest/" not in src, (
        "cmd_attest now writes to .roam/attest/ -- a command-owned "
        "persistence directory now exists. W805-LLLLL disconfirmation "
        "pin MUST be re-opened."
    )
    assert ".roam/attestations" not in src, (
        "cmd_attest now writes to .roam/attestations/ -- it has annexed "
        "the cmd_cga persistence directory. Verifier-side re-load is "
        "now structurally possible; re-open W805-LLLLL."
    )


def test_w805_oooo_sister_axis_preserved():
    """W805-OOOO (shared-helper get_changed_files axis) sister test exists.

    The LLLLL disconfirmation does NOT supersede the OOOO confirmation --
    the two probe DISTINCT axes on the same command (boundary ingest vs.
    verifier re-load). Both pins coexist.
    """
    sister = _REPO_ROOT / "tests" / "test_w805_oooo_cmd_attest_disclosure.py"
    assert sister.exists(), (
        "W805-OOOO sister test missing -- the LLLLL disconfirmation pin "
        "assumes OOOO's shared-helper axis stays orthogonal and pinned."
    )


def test_w805_ggggg_sister_axis_preserved():
    """W805-GGGGG (signing-surface producer-coverage axis) sister test exists.

    LLLLL probes the VERIFIER re-load axis; GGGGG pinned the PRODUCER
    signing-surface axis. Both are cmd_attest probes on distinct axes;
    both pins coexist.
    """
    sister = _REPO_ROOT / "tests" / "test_w805_ggggg_cmd_attest_signing_surface_producer_coverage.py"
    assert sister.exists(), (
        "W805-GGGGG sister test missing -- the LLLLL disconfirmation pin "
        "assumes GGGGG's signing-surface axis stays orthogonal and pinned."
    )


def test_w805_pppp_sister_verifier_family_preserved():
    """Sister verifier-side family head (W805-PPPP cmd_cga) is intact.

    The verifier-side family is 4-STRONG (PPPP/UUUU/ZZZZ/BBBBB) and
    cmd_attest is NOT a fifth member. Pin the sister tests' presence
    so a future renaming/deletion is caught -- if the family head goes
    missing, the disconfirmation rationale becomes stale.
    """
    for name in (
        "test_w805_pppp_cmd_cga_attestation_lineage.py",
        "test_w805_uuuu_cmd_audit_trail_verify_identity_skip.py",
        "test_w805_zzzz_cmd_evidence_diff_cross_repo_identity.py",
        "test_w805_bbbbb_cmd_pr_bundle_validate_identity_skip.py",
    ):
        sister = _REPO_ROOT / "tests" / name
        assert sister.exists(), (
            f"Sister verifier-side family member {name} missing -- "
            "the LLLLL disconfirmation pin's 4-STRONG family-shape "
            "rationale assumes all four sisters stay green."
        )
