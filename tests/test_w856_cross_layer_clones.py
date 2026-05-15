"""Tests for the W856 cross-layer clone detector.

Detector lives in ``src/roam/catalog/clones_cross_layer.py``. New module
(NOT added to ``smells.py`` directly) so it can mature behind a stable
surface — mirrors the W855 (rename-invariant clones) and W857 (parallel
hierarchies) layout.

What we exercise
----------------
- Layer classification (``_classify_layer``) over the path-fragment
  heuristics — both positive (controller / service / repository / view)
  and unmatched (random utility module).
- Detector end-to-end on a synthetic in-memory DB seeded with hand-crafted
  ``files`` / ``symbols`` / ``edges`` rows. We never invoke
  ``roam init`` here — every fixture is a stand-alone SQLite schema
  matching the subset of columns the detector reads.
- The four hard contract bits: severity is ``warning``, kind is
  ``cross_layer_clone``, confidence is ``structural``, detector_version
  is stamped (W81 discipline).
- LAW-4 anchor terminal: the description ends on ``callees`` per the
  ``concrete_plural_terminals`` set.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roam.catalog.clones_cross_layer import (
    CROSS_LAYER_CLONE_DETECTOR_VERSION,
    _classify_layer,
    _jaccard,
    detect_cross_layer_clones,
)


# ---------------------------------------------------------------------------
# Tiny in-memory schema — subset matching what the detector reads.
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            line_start INTEGER, line_end INTEGER, parent_id INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        """
    )
    conn.commit()
    return conn


_NEXT_ID = {"v": 1}


def _next_id() -> int:
    i = _NEXT_ID["v"]
    _NEXT_ID["v"] = i + 1
    return i


def _add_file(conn: sqlite3.Connection, path: str) -> int:
    fid = _next_id()
    conn.execute(
        "INSERT INTO files (id, path, language) VALUES (?, ?, 'python')",
        (fid, path),
    )
    return fid


def _add_function(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    name: str,
    line: int = 10,
    kind: str = "function",
) -> int:
    sid = _next_id()
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, file_id, name, kind, line, line + 20),
    )
    return sid


def _add_call(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'call')",
        (source_id, target_id),
    )


def _seed_callees(
    conn: sqlite3.Connection, *, host_file_id: int, names: list[str]
) -> list[int]:
    """Helper: create a list of stub callee symbols and return their ids."""
    out: list[int] = []
    for n in names:
        out.append(_add_function(conn, file_id=host_file_id, name=n, line=1))
    return out


# ---------------------------------------------------------------------------
# Unit tests: layer classification
# ---------------------------------------------------------------------------


class TestLayerClassification:
    def test_controller_path(self) -> None:
        assert _classify_layer("app/http/controllers/OrderController.py") == "controller"

    def test_service_path(self) -> None:
        assert _classify_layer("src/services/order_service.py") == "service"

    def test_repository_path(self) -> None:
        assert _classify_layer("app/repositories/order_repo.py") == "repository"

    def test_view_path(self) -> None:
        # /templates/ matches view; controller doesn't share that fragment.
        assert _classify_layer("app/templates/order.html") == "view"

    def test_unmatched_path(self) -> None:
        assert _classify_layer("lib/utils/helpers.py") is None

    def test_empty_path(self) -> None:
        assert _classify_layer("") is None

    def test_windows_style_path_normalised(self) -> None:
        assert (
            _classify_layer("app\\http\\controllers\\OrderController.py")
            == "controller"
        )


# ---------------------------------------------------------------------------
# Unit tests: jaccard primitive sanity (small smoke test on the helper).
# ---------------------------------------------------------------------------


class TestJaccard:
    def test_identical(self) -> None:
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_disjoint(self) -> None:
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial(self) -> None:
        # {a,b,c,d} ∩ {a,b,c} = 3, union = 4 -> 0.75
        assert _jaccard({"a", "b", "c", "d"}, {"a", "b", "c"}) == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Integration tests: detector on synthetic DB.
# ---------------------------------------------------------------------------


class TestCrossLayerCloneDetector:
    def setup_method(self) -> None:
        _NEXT_ID["v"] = 1

    # ---- positive case --------------------------------------------------

    def test_controller_service_clone_emitted(self, tmp_path: Path) -> None:
        """Controller + service sharing >=3 callees -> finding emitted."""
        conn = _make_db(tmp_path)

        # Domain helpers live in a regular module — neither controller
        # nor service. We don't care what layer the callees live in;
        # only the layer classification of the CALLERS matters.
        domain = _add_file(conn, "src/domain/order_math.py")
        apply_tax, apply_discount, sum_items, validate_addr = _seed_callees(
            conn,
            host_file_id=domain,
            names=["apply_tax", "apply_discount", "sum_line_items", "validate_address"],
        )

        # Controller side
        ctrl_file = _add_file(conn, "app/http/controllers/OrderController.py")
        ctrl_compute = _add_function(
            conn, file_id=ctrl_file, name="computeTotal", line=42
        )
        _add_call(conn, ctrl_compute, apply_tax)
        _add_call(conn, ctrl_compute, apply_discount)
        _add_call(conn, ctrl_compute, sum_items)
        _add_call(conn, ctrl_compute, validate_addr)

        # Service side -- shares 3 of 4 callees -> jaccard 3/4 = 0.75 (>=0.7).
        svc_file = _add_file(conn, "src/services/order_service.py")
        svc_calc = _add_function(
            conn, file_id=svc_file, name="calculateAmount", line=17
        )
        _add_call(conn, svc_calc, apply_tax)
        _add_call(conn, svc_calc, apply_discount)
        _add_call(conn, svc_calc, sum_items)

        conn.commit()
        findings = detect_cross_layer_clones(conn)
        assert len(findings) == 1
        f0 = findings[0]
        assert f0["smell_id"] == "cross-layer-clone"
        assert f0["severity"] == "warning"
        assert f0["kind"] == "cross_layer_clone"
        assert f0["confidence"] == "structural"
        assert f0["detector_version"] == CROSS_LAYER_CLONE_DETECTOR_VERSION
        # symbol_name uses the layer:name || layer:name pattern.
        assert "controller:computeTotal" in f0["symbol_name"]
        assert "service:calculateAmount" in f0["symbol_name"]
        # location points at the controller side (alphabetical layer_a).
        assert f0["location"].startswith("app/http/controllers/")
        # description ends on the LAW-4 concrete-noun terminal "callees".
        assert f0["description"].rstrip(".").endswith("callees")
        conn.close()

    def test_controller_repository_clone_emitted(self, tmp_path: Path) -> None:
        """Controller + repository sharing 3+ callees also emit (any cross-pair)."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/account.py")
        a, b, c = _seed_callees(
            conn, host_file_id=domain, names=["validate_iban", "lookup_bank", "encrypt"]
        )

        ctrl_file = _add_file(conn, "app/controllers/AccountController.py")
        ctrl = _add_function(conn, file_id=ctrl_file, name="openAccount", line=5)
        _add_call(conn, ctrl, a)
        _add_call(conn, ctrl, b)
        _add_call(conn, ctrl, c)

        repo_file = _add_file(conn, "app/repositories/account_repo.py")
        repo = _add_function(conn, file_id=repo_file, name="createAccount", line=9)
        _add_call(conn, repo, a)
        _add_call(conn, repo, b)
        _add_call(conn, repo, c)

        conn.commit()
        findings = detect_cross_layer_clones(conn)
        assert len(findings) == 1
        # Layer pair is (controller, repository) — alphabetical order on key.
        ev = findings[0]["evidence"]
        assert {ev["layer_a"], ev["layer_b"]} == {"controller", "repository"}
        conn.close()

    # ---- intra-layer is not this detector's job -------------------------

    def test_no_clone_for_same_layer_pair(self, tmp_path: Path) -> None:
        """Two controllers sharing callees are NOT flagged (W95/W855 territory)."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/cart.py")
        a, b, c, d = _seed_callees(
            conn, host_file_id=domain, names=["price", "tax", "discount", "round_currency"]
        )

        c1 = _add_file(conn, "app/controllers/CartController.py")
        c2 = _add_file(conn, "app/controllers/CheckoutController.py")
        sym1 = _add_function(conn, file_id=c1, name="getCart", line=1)
        sym2 = _add_function(conn, file_id=c2, name="getCheckout", line=1)
        for callee in (a, b, c, d):
            _add_call(conn, sym1, callee)
            _add_call(conn, sym2, callee)

        conn.commit()
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    # ---- threshold gates ------------------------------------------------

    def test_below_threshold_not_flagged(self, tmp_path: Path) -> None:
        """Sharing only 2 callees -> NOT flagged even at jaccard >= 0.7."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b = _seed_callees(conn, host_file_id=domain, names=["foo", "bar"])

        ctrl = _add_file(conn, "app/controllers/X.py")
        svc = _add_file(conn, "src/services/Y.py")
        c_sym = _add_function(conn, file_id=ctrl, name="cmethod", line=1)
        s_sym = _add_function(conn, file_id=svc, name="smethod", line=1)
        # Both call exactly the same two callees -> jaccard 1.0 but
        # shared count is 2 (< default min_shared_callees=3).
        _add_call(conn, c_sym, a)
        _add_call(conn, c_sym, b)
        _add_call(conn, s_sym, a)
        _add_call(conn, s_sym, b)

        conn.commit()
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    def test_low_jaccard_not_flagged(self, tmp_path: Path) -> None:
        """Sharing 3 callees but with many extras -> jaccard < 0.7."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b, c, x, y, z, w, q = _seed_callees(
            conn,
            host_file_id=domain,
            names=["a_fn", "b_fn", "c_fn", "x1", "x2", "x3", "x4", "x5"],
        )

        ctrl = _add_file(conn, "app/controllers/C.py")
        svc = _add_file(conn, "src/services/S.py")
        c_sym = _add_function(conn, file_id=ctrl, name="cm", line=1)
        s_sym = _add_function(conn, file_id=svc, name="sm", line=1)
        # Shared: a,b,c (3). Ctrl extras: x,y. Svc extras: z,w,q.
        # Union = 8; jaccard = 3/8 = 0.375 (< 0.7).
        for n in (a, b, c, x, y):
            _add_call(conn, c_sym, n)
        for n in (a, b, c, z, w, q):
            _add_call(conn, s_sym, n)

        conn.commit()
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    # ---- negative cases -------------------------------------------------

    def test_unrelated_pair_not_flagged(self, tmp_path: Path) -> None:
        """Two functions in different layers with 0 shared callees -> no finding."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b, c, d, e, f = _seed_callees(
            conn, host_file_id=domain, names=["a", "b", "c", "d", "e", "f"]
        )

        ctrl = _add_file(conn, "app/controllers/X.py")
        svc = _add_file(conn, "src/services/Y.py")
        c_sym = _add_function(conn, file_id=ctrl, name="cm", line=1)
        s_sym = _add_function(conn, file_id=svc, name="sm", line=1)
        # Disjoint callee sets.
        for callee in (a, b, c):
            _add_call(conn, c_sym, callee)
        for callee in (d, e, f):
            _add_call(conn, s_sym, callee)

        conn.commit()
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    # ---- shape contracts ------------------------------------------------

    def test_jaccard_value_in_metric_value(self, tmp_path: Path) -> None:
        """Finding's metric_value is the rounded Jaccard score."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b, c, d = _seed_callees(
            conn, host_file_id=domain, names=["a", "b", "c", "d"]
        )

        ctrl = _add_file(conn, "app/controllers/X.py")
        svc = _add_file(conn, "src/services/Y.py")
        c_sym = _add_function(conn, file_id=ctrl, name="cm", line=1)
        s_sym = _add_function(conn, file_id=svc, name="sm", line=1)
        # Shared a,b,c. Ctrl also calls d. Union=4, intersection=3 -> 0.75.
        for callee in (a, b, c, d):
            _add_call(conn, c_sym, callee)
        for callee in (a, b, c):
            _add_call(conn, s_sym, callee)

        conn.commit()
        findings = detect_cross_layer_clones(conn)
        assert len(findings) == 1
        assert findings[0]["metric_value"] == pytest.approx(0.75)
        assert findings[0]["threshold"] == 0.7
        # Evidence-side metric duplicates the headline.
        assert findings[0]["evidence"]["jaccard"] == pytest.approx(0.75)
        conn.close()

    def test_finding_carries_evidence(self, tmp_path: Path) -> None:
        """Evidence dict carries shared_callees, layer_a, layer_b, file_a, file_b."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b, c = _seed_callees(
            conn, host_file_id=domain, names=["alpha", "beta", "gamma"]
        )

        ctrl = _add_file(conn, "app/controllers/X.py")
        svc = _add_file(conn, "src/services/Y.py")
        c_sym = _add_function(conn, file_id=ctrl, name="cm", line=1)
        s_sym = _add_function(conn, file_id=svc, name="sm", line=1)
        for callee in (a, b, c):
            _add_call(conn, c_sym, callee)
            _add_call(conn, s_sym, callee)

        conn.commit()
        findings = detect_cross_layer_clones(conn)
        assert len(findings) == 1
        ev = findings[0]["evidence"]
        # All five evidence axes present.
        assert "shared_callees" in ev
        assert "layer_a" in ev
        assert "layer_b" in ev
        assert "file_a" in ev
        assert "file_b" in ev
        # shared_callees is sorted and contains exactly the matched names.
        assert ev["shared_callees"] == ["alpha", "beta", "gamma"]
        # layer_a/layer_b are the canonical (alphabetical) pair.
        assert {ev["layer_a"], ev["layer_b"]} == {"controller", "service"}
        # The two file paths are present (one per layer).
        assert ev["file_a"].startswith("app/controllers/")
        assert ev["file_b"].startswith("src/services/")
        conn.close()

    # ---- robustness -----------------------------------------------------

    def test_empty_corpus_no_findings(self, tmp_path: Path) -> None:
        """[] on an empty connection; no exceptions on the path."""
        conn = _make_db(tmp_path)
        # No files, no symbols, no edges seeded.
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    def test_callable_symbols_present_but_no_edges_no_findings(
        self, tmp_path: Path
    ) -> None:
        """Layered symbols exist but have zero outbound calls -> no findings."""
        conn = _make_db(tmp_path)
        ctrl = _add_file(conn, "app/controllers/X.py")
        svc = _add_file(conn, "src/services/Y.py")
        _add_function(conn, file_id=ctrl, name="cm", line=1)
        _add_function(conn, file_id=svc, name="sm", line=1)
        conn.commit()
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    def test_only_one_layer_populated_no_findings(self, tmp_path: Path) -> None:
        """Only controllers (no other layer) means no cross-layer pair exists."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b, c = _seed_callees(conn, host_file_id=domain, names=["a", "b", "c"])
        c1 = _add_file(conn, "app/controllers/X.py")
        c2 = _add_file(conn, "app/controllers/Z.py")
        s1 = _add_function(conn, file_id=c1, name="m1", line=1)
        s2 = _add_function(conn, file_id=c2, name="m2", line=1)
        for callee in (a, b, c):
            _add_call(conn, s1, callee)
            _add_call(conn, s2, callee)
        conn.commit()
        assert detect_cross_layer_clones(conn) == []
        conn.close()

    def test_pair_deduped(self, tmp_path: Path) -> None:
        """Same (sym_a, sym_b) pair never emits twice even with duplicate edges."""
        conn = _make_db(tmp_path)
        domain = _add_file(conn, "src/domain/m.py")
        a, b, c = _seed_callees(conn, host_file_id=domain, names=["a", "b", "c"])

        ctrl = _add_file(conn, "app/controllers/X.py")
        svc = _add_file(conn, "src/services/Y.py")
        c_sym = _add_function(conn, file_id=ctrl, name="cm", line=1)
        s_sym = _add_function(conn, file_id=svc, name="sm", line=1)
        for callee in (a, b, c):
            _add_call(conn, c_sym, callee)
            # Duplicate the call edge (real codebases occasionally have
            # multiple call sites to the same callee in one function).
            _add_call(conn, c_sym, callee)
            _add_call(conn, s_sym, callee)
        conn.commit()
        findings = detect_cross_layer_clones(conn)
        assert len(findings) == 1
        conn.close()


# ---------------------------------------------------------------------------
# Registry wiring sanity: the W856 detector is registered in ALL_DETECTORS.
# ---------------------------------------------------------------------------


def test_detector_registered_in_smells_module() -> None:
    """W856 is wired into the central ALL_DETECTORS registry."""
    from roam.catalog.smells import ALL_DETECTORS

    names = [sid for (sid, _fn) in ALL_DETECTORS]
    assert "cross-layer-clone" in names
