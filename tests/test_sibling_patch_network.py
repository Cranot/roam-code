from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

from roam.cli import cli
from roam.knowledge.knowledge_claim import (
    KnowledgeClaim,
    PatchFusionError,
    RepairTransferError,
    validate_repair_transfer,
)
from roam.sibling_patch import repair_scorer as rs
from roam.sibling_patch.replay_gate import retarget_patch, run_replay_gate
from tests.conftest import index_in_process, invoke_cli

# --- fixtures / helpers -----------------------------------------------------
_PATCH_USER = (
    "--- a/users.py\n"
    "+++ b/users.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def get_user(cfg):\n"
    '-    return cfg["user"]\n'
    '+    return cfg.get("user")\n'
)

_BASE_CLAIM = dict(
    claim="null-safe dict access transfers across the cfg[...] idiom",
    scope="roam/dict-keyerror",
    provenance={"source": "systemic_finding", "ref": "spn-v1-test"},
    evidence_type="measured",
    confidence=0.8,
    observed_at="2026-07-05T00:00:00Z",
    last_verified_at="2026-07-05T00:00:00Z",
    valid_until="2027-07-05T00:00:00Z",
    trust_decay_class="slow",
    validation_command="pytest -q",
)


def _repair_transfer(candidate_patch=_PATCH_USER, kind="replacement", candidate_gen="lexical_top_n", attestation=None):
    return {
        "repair_intent": {"kind": kind},
        "anchor": {"file": "users.py", "symbol": "get_user", "kind": "function"},
        "candidate_gen": candidate_gen,
        "sibling_detector": "repair_intent_rerank",
        "candidate_patch": candidate_patch,
        "replay_predicate": "pytest -q",
        "fusion_attestation": attestation if attestation is not None else {"status": "green"},
    }


def _git_init(path):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=path, capture_output=True, check=True, env=env)


@pytest.fixture
def transfer_project(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    # Two files sharing the cfg[...] KeyError idiom; the producer fixed get_user.
    (project / "users.py").write_text('def get_user(cfg):\n    return cfg["user"]\n', encoding="utf-8")
    (project / "roles.py").write_text('def get_role(cfg):\n    return cfg["role"]\n', encoding="utf-8")
    _git_init(project)
    output, code = index_in_process(project)
    assert code == 0, output
    return project


# --- 1. schema: patch-fusion invariant -------------------------------------
def test_patch_fusion_rejects_detector_without_patch():
    with pytest.raises(PatchFusionError):
        KnowledgeClaim.create(**_BASE_CLAIM, repair_transfer=_repair_transfer(candidate_patch=""))


def test_patch_fusion_rejects_non_green_attestation():
    with pytest.raises(PatchFusionError):
        KnowledgeClaim.create(**_BASE_CLAIM, repair_transfer=_repair_transfer(attestation={"status": "red"}))


def test_patch_fusion_rejects_missing_attestation():
    with pytest.raises(PatchFusionError):
        KnowledgeClaim.create(**_BASE_CLAIM, repair_transfer=_repair_transfer(attestation={}))


def test_repair_transfer_rejects_graph_candidate_gen():
    with pytest.raises(RepairTransferError):
        validate_repair_transfer(_repair_transfer(candidate_gen="graph"))


def test_repair_transfer_rejects_out_of_scope_kind():
    with pytest.raises(RepairTransferError):
        validate_repair_transfer(_repair_transfer(kind="addition"))


def test_green_repair_transfer_claim_roundtrips():
    claim = KnowledgeClaim.create(**_BASE_CLAIM, repair_transfer=_repair_transfer())
    restored = KnowledgeClaim.from_dict(claim.to_dict())
    assert restored.is_repair_transfer()
    assert restored.repair_transfer["candidate_gen"] == "lexical_top_n"


def test_normal_claim_unaffected_by_extension():
    claim = KnowledgeClaim.create(**_BASE_CLAIM)
    assert not claim.is_repair_transfer()
    assert "repair_transfer" not in claim.to_dict()


# --- 2. scorer: intent, scope, rerank, deleted-signature gate ---------------
def test_intent_is_replacement_and_in_scope():
    intent = rs.derive_repair_intent(rs.parse_patch_changes(_PATCH_USER))
    assert intent.kind == "replacement"
    assert rs.is_defect_intent(intent)


def test_pure_addition_out_of_scope():
    intent = rs.derive_repair_intent(["+    log.debug('added telemetry')"])
    assert not rs.is_defect_intent(intent)


def test_rerank_gates_on_deleted_signature_and_is_deterministic():
    intent = rs.derive_repair_intent(rs.parse_patch_changes(_PATCH_USER))
    anchor_body = 'def get_user(cfg):\n    return cfg["user"]\n'
    sibling = rs.ScorerCandidate.from_body(
        {"id": 2, "file": "roles.py", "symbol": "get_role", "kind": "function", "line_start": 1, "line_end": 2},
        'def get_role(cfg):\n    return cfg["role"]\n',
    )
    unrelated = rs.ScorerCandidate.from_body(
        {"id": 3, "file": "math.py", "symbol": "square", "kind": "function", "line_start": 1, "line_end": 2},
        "def square(x):\n    return x * x\n",
    )
    ranked = rs.rerank(anchor_body, [sibling, unrelated], intent, min_lexical=0.0)
    names = [r.meta["symbol"] for r in ranked]
    assert "get_role" in names  # shares the deleted cfg[STR] signature
    assert "square" not in names  # lacks the deleted-buggy signature -> ineligible
    assert names == [r.meta["symbol"] for r in rs.rerank(anchor_body, [sibling, unrelated], intent, min_lexical=0.0)]


# --- 3. replay-gate: real throwaway-worktree certification ------------------
def test_replay_gate_green_and_propose_only(tmp_path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "mod.py").write_text("def get(d, k):\n    return d[k]\n", encoding="utf-8")
    (repo / "check.py").write_text("import mod\nassert mod.get({}, 'x') is None\n", encoding="utf-8")
    _git_init(repo)
    fix = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def get(d, k):\n-    return d[k]\n+    return d.get(k)\n"
    validation = f'"{sys.executable}" check.py'

    att = run_replay_gate(repo, fix, validation, timeout=120)
    assert att.status == "green"
    assert att.pre_patch_fired and att.post_patch_cleared
    # propose-only: the real tree is untouched, no worktrees leak
    assert "return d[k]" in (repo / "mod.py").read_text(encoding="utf-8")
    wl = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True)
    assert len(wl.stdout.strip().splitlines()) == 1


def test_replay_gate_not_applicable_when_defect_absent(tmp_path):
    repo = tmp_path / "clean"
    repo.mkdir()
    (repo / "mod.py").write_text("def get(d, k):\n    return d.get(k)\n", encoding="utf-8")
    (repo / "check.py").write_text("import mod\nassert mod.get({}, 'x') is None\n", encoding="utf-8")
    _git_init(repo)
    fix = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def get(d, k):\n-    return d[k]\n+    return d.get(k)\n"
    att = run_replay_gate(repo, fix, f'"{sys.executable}" check.py', timeout=120)
    assert att.status == "not_applicable"


def test_replay_gate_skipped_without_validation():
    att = run_replay_gate(tempfile.gettempdir(), "patch", None)
    assert att.status == "skipped"


def test_retarget_single_file_patch():
    out = retarget_patch(_PATCH_USER, "roles.py")
    assert out is not None
    assert "a/roles.py" in out and "b/roles.py" in out


# --- 4. CLI: default-off no-op ---------------------------------------------
def test_sibling_patch_default_off(cli_runner, monkeypatch):
    monkeypatch.delenv("ROAM_EXPERIMENTAL_REPAIR_SIBLINGS", raising=False)
    result = cli_runner.invoke(cli, ["sibling-patch", "--help"])
    assert result.exit_code != 0
    assert "No such command" in result.output


# --- 5. CLI: end-to-end propose + replay-certify ---------------------------
def test_sibling_patch_apply_end_to_end(cli_runner, transfer_project, tmp_path, monkeypatch):
    monkeypatch.setenv("ROAM_EXPERIMENTAL_REPAIR_SIBLINGS", "1")
    claim = KnowledgeClaim.create(**_BASE_CLAIM, repair_transfer=_repair_transfer())
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(json.dumps(claim.to_dict()), encoding="utf-8")
    validation = f'"{sys.executable}" -c "import users; assert users.get_user({{}}) is None"'

    result = invoke_cli(
        cli_runner,
        ["sibling-patch", "apply", str(claim_path), "--validation-command", validation, "--max-replays", "3"],
        cwd=transfer_project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["propose_only"] is True
    assert payload["summary"]["in_scope"] is True
    assert payload["summary"]["candidate_count"] >= 1
    assert payload["repair_intent"]["kind"] == "replacement"
    # the shared-idiom sibling is proposed
    symbols = {c["symbol"] for c in payload["candidates"]}
    assert "get_role" in symbols or "get_user" in symbols
    # replay ran and certified at least one site green
    assert payload["summary"]["replay_ran"] is True
    assert payload["summary"]["certified_green"] >= 1
    # propose-only: the consumer tree is untouched
    assert (transfer_project / "users.py").read_text(encoding="utf-8") == 'def get_user(cfg):\n    return cfg["user"]\n'


def test_sibling_patch_apply_out_of_scope_addition(cli_runner, transfer_project, tmp_path, monkeypatch):
    monkeypatch.setenv("ROAM_EXPERIMENTAL_REPAIR_SIBLINGS", "1")
    # An addition-only patch: the schema still needs a green attestation, but the
    # command's scope-gate reports it out of scope and proposes nothing.
    add_patch = (
        '--- a/users.py\n+++ b/users.py\n@@ -1,2 +1,3 @@\n def get_user(cfg):\n     return cfg["user"]\n+    # audit\n'
    )
    rt = _repair_transfer(candidate_patch=add_patch, kind="replacement")  # schema kind stays defect-shaped
    claim = KnowledgeClaim.create(**_BASE_CLAIM, repair_transfer=rt)
    claim_path = tmp_path / "claim_add.json"
    claim_path.write_text(json.dumps(claim.to_dict()), encoding="utf-8")

    result = invoke_cli(
        cli_runner,
        ["sibling-patch", "apply", str(claim_path)],
        cwd=transfer_project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # derived intent from an addition-only diff is out of scope -> no proposals
    assert payload["summary"]["in_scope"] is False
    assert payload["summary"]["candidate_count"] == 0
