"""F10 regression — tx-boundaries domain gate.

Cross-library validation: on express, tx-boundaries classified ``res.json`` = "mutation outside
transaction scope" and flagged 13 unsafe_mutation symbols on a stateless HTTP
framework with NO transaction layer. Like n1 (no models) / missing-index (no
migrations), the honest response is N/A — not a confident false positive.
"""

from __future__ import annotations

from collections import Counter

from roam.commands.cmd_tx_boundaries import _tx_domain_is_na


def test_na_when_no_transaction_context_but_unsafe_mutations() -> None:
    # express-shaped: only non_transactional + unsafe_mutation, no BEGIN/commit.
    bc = Counter({"non_transactional": 125, "unsafe_mutation": 13})
    assert _tx_domain_is_na(bc, classification=None) is True


def test_not_na_when_real_transactions_exist() -> None:
    bc = Counter({"transactional": 4, "unsafe_mutation": 2, "non_transactional": 50})
    assert _tx_domain_is_na(bc, classification=None) is False


def test_not_na_when_unmatched_markers_exist() -> None:
    # An unmatched BEGIN is real transaction context (a leak) — do not gate.
    bc = Counter({"unmatched_begin": 1, "unsafe_mutation": 3})
    assert _tx_domain_is_na(bc, classification=None) is False


def test_explicit_classification_opts_back_in() -> None:
    bc = Counter({"non_transactional": 125, "unsafe_mutation": 13})
    assert _tx_domain_is_na(bc, classification="unsafe_mutation") is False


def test_not_na_when_no_unsafe_mutations() -> None:
    bc = Counter({"non_transactional": 100})
    assert _tx_domain_is_na(bc, classification=None) is False
