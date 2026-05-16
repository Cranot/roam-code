"""Tests for the Laravel post-resolver that suppresses dead-detection
FPs caused by Laravel's dynamic-dispatch idioms.

Seven idioms are covered (W36.9 — dogfood finding #9; W36.10 — wave 2):

1. Route::*('path', [Class::class, 'method'])  -> ``laravel_route`` edge
2. Eloquent scope methods (``scope[A-Z]\\w*``) -> ``laravel_scope`` edge
3. Policy auto-discovery                       -> ``laravel_policy`` edge
4. Observer registration (Foo::observe(...))   -> ``laravel_observer`` edge
5. Job dispatch (Bus::dispatch / ::dispatch)   -> ``laravel_job`` edge
6. ShouldQueue interface                       -> ``laravel_queue`` edge
7. Artisan command (extends Command)           -> ``laravel_artisan`` edge

W36.11 adds file-anchor provenance tests — symbol-less files (the
canonical ``routes/web.php`` case) should anchor their synthesized
edges on a per-file synthetic ``module``-kind symbol rather than on
the target class. The anchor's ``is_exported=0`` keeps it out of the
dead-detector while preserving correct ``roam impact`` provenance.

A regression test guards that genuinely unused PHP methods are still
flagged as dead.

The tests use a minimal in-memory SQLite schema mirroring the production
edges/symbols/files tables so the resolver can be unit-tested without
spinning up the full indexer pipeline.
"""

from __future__ import annotations

import sqlite3

from roam.index.laravel_post import (
    _ARTISAN_COMMAND_RE,
    _ELOQUENT_SCOPE_RE,
    _JOB_DISPATCH_RE,
    _OBSERVER_REGISTER_RE,
    _ROUTE_CLASS_STRING_RE,
    _SHOULDQUEUE_CLASS_RE,
    SYNTHETIC_FILE_ANCHOR_NAME,
    _is_laravel_project,
    resolve_laravel_dispatch,
)


def _make_conn() -> sqlite3.Connection:
    """In-memory DB whose schema is a strict subset of the production one.

    Mirrors the columns the post-resolver writes to: ``is_exported`` is
    needed by the W36.11 synthetic-anchor insert.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            language TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            kind TEXT NOT NULL,
            line_start INTEGER,
            line_end INTEGER,
            visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1,
            parent_id INTEGER
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            line INTEGER,
            bridge TEXT,
            confidence REAL,
            -- A6 / W81: stamped by the Laravel post-resolver alongside
            -- ``bridge`` so consumers can detect drift in the dispatch
            -- inference regex set.
            bridge_version TEXT
        );
        """
    )
    return conn


def _add_file(conn, file_id, path, language="php"):
    conn.execute(
        "INSERT INTO files (id, path, language) VALUES (?, ?, ?)",
        (file_id, path, language),
    )


def _add_symbol(conn, sym_id, file_id, name, qualified_name, kind, line_start=1, line_end=None):
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sym_id, file_id, name, qualified_name, kind, line_start, line_end),
    )


def _setup_laravel_root(tmp_path):
    """Mark a directory as a Laravel project (artisan binary present)."""
    (tmp_path / "artisan").write_text("#!/usr/bin/env php\n<?php\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Regex unit tests — fast, deterministic, no DB
# ---------------------------------------------------------------------------


class TestRouteClassStringRegex:
    """Coverage for ``[ClassName::class, 'method']`` syntax variants."""

    def test_simple_form(self):
        m = _ROUTE_CLASS_STRING_RE.search("[FooController::class, 'index']")
        assert m is not None
        assert m.group(1) == "FooController"
        assert m.group(2) == "index"

    def test_fully_qualified_class_name(self):
        m = _ROUTE_CLASS_STRING_RE.search("[App\\Http\\Controllers\\FooController::class, 'show']")
        assert m is not None
        assert m.group(1) == "App\\Http\\Controllers\\FooController"
        assert m.group(2) == "show"

    def test_double_quoted_method_name(self):
        m = _ROUTE_CLASS_STRING_RE.search('[Bar::class, "store"]')
        assert m is not None
        assert m.group(2) == "store"

    def test_internal_whitespace_tolerated(self):
        m = _ROUTE_CLASS_STRING_RE.search("[ Bar::class , 'show' ]")
        assert m is not None
        assert m.group(1) == "Bar"
        assert m.group(2) == "show"

    def test_unrelated_array_not_matched(self):
        """A plain PHP array of strings must not match."""
        assert _ROUTE_CLASS_STRING_RE.search("['foo', 'bar']") is None

    def test_class_alone_without_method_not_matched(self):
        """A bare ``Class::class`` reference is not a callable; skip it."""
        assert _ROUTE_CLASS_STRING_RE.search("[Bar::class]") is None


class TestEloquentScopeRegex:
    def test_camel_case_scope_matches(self):
        assert _ELOQUENT_SCOPE_RE.match("scopeActive") is not None
        assert _ELOQUENT_SCOPE_RE.match("scopeForCompany") is not None

    def test_lowercase_continuation_rejected(self):
        """``scopefoo`` is not Laravel convention (lowercase suffix)."""
        assert _ELOQUENT_SCOPE_RE.match("scopefoo") is None

    def test_bare_scope_rejected(self):
        """``scope`` alone is not an Eloquent scope method."""
        assert _ELOQUENT_SCOPE_RE.match("scope") is None

    def test_method_not_starting_with_scope_rejected(self):
        assert _ELOQUENT_SCOPE_RE.match("unusedScope") is None
        assert _ELOQUENT_SCOPE_RE.match("getScopeName") is None


# ---------------------------------------------------------------------------
# Project gating
# ---------------------------------------------------------------------------


class TestProjectGating:
    def test_artisan_marker_detected(self, tmp_path):
        (tmp_path / "artisan").write_text("#!/usr/bin/env php\n<?php\n")
        assert _is_laravel_project(tmp_path) is True

    def test_composer_with_laravel_framework_detected(self, tmp_path):
        (tmp_path / "composer.json").write_text('{"require": {"laravel/framework": "^10"}}')
        assert _is_laravel_project(tmp_path) is True

    def test_plain_php_project_not_detected(self, tmp_path):
        (tmp_path / "composer.json").write_text('{"require": {"phpunit/phpunit": "^9"}}')
        assert _is_laravel_project(tmp_path) is False

    def test_no_markers_returns_false(self, tmp_path):
        assert _is_laravel_project(tmp_path) is False


# ---------------------------------------------------------------------------
# Full resolver — idiom-by-idiom integration
# ---------------------------------------------------------------------------


class TestRouteClassStringResolver:
    def test_route_class_string_method_gains_edge(self, tmp_path):
        """Route::get('/foo', [FooController::class, 'index']) -> ``index``
        gains an inbound ``laravel_route`` edge.

        W36.11: source_id resolves to a synthetic file anchor whose
        ``file_id`` matches ``routes/web.php`` — NOT the controller
        class (which was the W36.9 fallback).
        """
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [FooController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php", language="php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php", language="php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )
        _add_symbol(
            conn,
            102,
            2,
            "unusedMethod",
            "App\\Http\\Controllers\\FooController\\unusedMethod",
            "method",
        )

        n = resolve_laravel_dispatch(conn, root)
        assert n == 1
        edges = conn.execute("SELECT source_id, target_id, kind, bridge, confidence FROM edges").fetchall()
        assert len(edges) == 1
        e = dict(edges[0])
        # W36.11: source is a synthetic anchor in routes/web.php
        # (file_id=1), NOT the FooController class (id=100).
        assert e["source_id"] != 100, (
            "Edge source must not be the target controller class — that was the W36.9 provenance bug W36.11 fixes."
        )
        source_row = conn.execute(
            "SELECT name, file_id, kind, is_exported FROM symbols WHERE id = ?",
            (e["source_id"],),
        ).fetchone()
        assert source_row["name"] == SYNTHETIC_FILE_ANCHOR_NAME
        assert source_row["file_id"] == 1  # routes/web.php
        assert source_row["kind"] == "module"
        assert source_row["is_exported"] == 0
        assert e["target_id"] == 101
        assert e["kind"] == "laravel_route"
        assert e["bridge"] == "laravel"
        assert e["confidence"] == 0.85

    def test_unmatched_method_name_produces_no_edge(self, tmp_path):
        """``[FooController::class, 'doesNotExist']`` resolves to nothing
        because the method is not in the symbol table."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/x', [FooController::class, 'doesNotExist']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        # No method symbol called "doesNotExist".

        n = resolve_laravel_dispatch(conn, root)
        assert n == 0

    def test_idempotent_re_run_does_not_duplicate_edges(self, tmp_path):
        """Running the resolver twice produces the same edge set, not double."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [FooController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )

        assert resolve_laravel_dispatch(conn, root) == 1
        assert resolve_laravel_dispatch(conn, root) == 1
        rows = conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'laravel_route'").fetchone()
        assert rows[0] == 1


class TestEloquentScopeResolver:
    def test_eloquent_scope_method_gains_edge(self, tmp_path):
        """``scopeActive`` on a model class gains a ``laravel_scope`` edge
        from the model class itself (Laravel's ``__callStatic`` routes
        ``Model::active()`` to ``scopeActive``)."""
        root = _setup_laravel_root(tmp_path)

        conn = _make_conn()
        _add_file(conn, 1, "app/Models/Bar.php")
        _add_symbol(conn, 200, 1, "Bar", "App\\Models\\Bar", "class")
        _add_symbol(
            conn,
            201,
            1,
            "scopeActive",
            "App\\Models\\Bar\\scopeActive",
            "method",
        )
        _add_symbol(
            conn,
            202,
            1,
            "unusedScope",
            "App\\Models\\Bar\\unusedScope",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id, kind FROM edges WHERE kind = 'laravel_scope'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        # scopeActive -> covered; unusedScope -> still has zero inbound edges
        assert (200, 201) in edges
        assert (200, 202) not in edges

    def test_lowercase_scope_not_covered(self, tmp_path):
        """``scopefoo`` (lowercase suffix) is NOT a Laravel scope; no edge."""
        root = _setup_laravel_root(tmp_path)

        conn = _make_conn()
        _add_file(conn, 1, "app/Models/Bar.php")
        _add_symbol(conn, 200, 1, "Bar", "App\\Models\\Bar", "class")
        _add_symbol(
            conn,
            201,
            1,
            "scopefoo",
            "App\\Models\\Bar\\scopefoo",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        assert conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'laravel_scope'").fetchone()[0] == 0


class TestPolicyAutoDiscoveryResolver:
    def test_policy_paired_with_model_gains_edge(self, tmp_path):
        """``App\\Policies\\FooPolicy`` paired with ``App\\Models\\Foo`` -
        Policy methods gain a ``laravel_policy`` edge from the Model class."""
        root = _setup_laravel_root(tmp_path)

        conn = _make_conn()
        _add_file(conn, 1, "app/Models/Foo.php")
        _add_file(conn, 2, "app/Policies/FooPolicy.php")
        _add_symbol(conn, 300, 1, "Foo", "App\\Models\\Foo", "class")
        _add_symbol(conn, 400, 2, "FooPolicy", "App\\Policies\\FooPolicy", "class")
        _add_symbol(
            conn,
            401,
            2,
            "view",
            "App\\Policies\\FooPolicy\\view",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id, kind FROM edges WHERE kind = 'laravel_policy'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        assert (300, 401) in edges

    def test_policy_without_matching_model_produces_no_edge(self, tmp_path):
        """An orphan Policy class (no matching Model) gets no edge."""
        root = _setup_laravel_root(tmp_path)

        conn = _make_conn()
        _add_file(conn, 1, "app/Policies/OrphanPolicy.php")
        _add_symbol(
            conn,
            500,
            1,
            "OrphanPolicy",
            "App\\Policies\\OrphanPolicy",
            "class",
        )
        _add_symbol(
            conn,
            501,
            1,
            "view",
            "App\\Policies\\OrphanPolicy\\view",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        assert conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'laravel_policy'").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Regression: genuinely unused methods stay dead
# ---------------------------------------------------------------------------


class TestDeadIsStillDead:
    def test_unused_method_in_controller_has_no_inbound_edge(self, tmp_path):
        """``unusedMethod`` on a controller with NO Route binding to it
        must NOT pick up an edge from the Laravel resolver — its dead-
        detection FP signal stays."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [FooController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )
        _add_symbol(
            conn,
            102,
            2,
            "unusedMethod",
            "App\\Http\\Controllers\\FooController\\unusedMethod",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        # ``unusedMethod`` (id 102) has zero inbound edges of any kind.
        rows = conn.execute("SELECT COUNT(*) FROM edges WHERE target_id = 102").fetchone()
        assert rows[0] == 0


# ---------------------------------------------------------------------------
# Skip when project is not Laravel
# ---------------------------------------------------------------------------


class TestNonLaravelProjectsSkipped:
    def test_plain_php_project_skipped(self, tmp_path):
        """A PHP project without ``artisan`` or ``laravel/framework`` in
        composer.json must not run the resolver — and zero edges are
        inserted even when the patterns would otherwise match."""
        (tmp_path / "composer.json").write_text('{"require": {"phpunit/phpunit": "^9"}}')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.php").write_text(
            "<?php\n// [Bar::class, 'baz'] looks like Laravel syntax but isn't.\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "src/foo.php")
        _add_symbol(conn, 100, 1, "Bar", "Bar", "class")
        _add_symbol(conn, 101, 1, "baz", "Bar\\baz", "method")

        assert resolve_laravel_dispatch(conn, tmp_path) == 0


# ---------------------------------------------------------------------------
# W36.10 wave 2 — regex unit tests for the four new idioms
# ---------------------------------------------------------------------------


class TestObserverRegistrationRegex:
    def test_simple_form(self):
        m = _OBSERVER_REGISTER_RE.search("Foo::observe(FooObserver::class)")
        assert m is not None
        assert m.group(1) == "Foo"
        assert m.group(2) == "FooObserver"

    def test_fully_qualified_observer(self):
        m = _OBSERVER_REGISTER_RE.search("User::observe(App\\Observers\\UserObserver::class)")
        assert m is not None
        assert m.group(2) == "App\\Observers\\UserObserver"

    def test_internal_whitespace_tolerated(self):
        m = _OBSERVER_REGISTER_RE.search("Foo::observe( Bar::class )")
        assert m is not None
        assert m.group(2) == "Bar"

    def test_unrelated_static_call_not_matched(self):
        assert _OBSERVER_REGISTER_RE.search("Foo::create([])") is None


class TestJobDispatchRegex:
    def test_bus_dispatch_new(self):
        m = _JOB_DISPATCH_RE.search("Bus::dispatch(new SyncJob($payload))")
        assert m is not None
        assert m.group(1) == "SyncJob"
        assert m.group(2) is None

    def test_bus_dispatch_now_variant(self):
        m = _JOB_DISPATCH_RE.search("Bus::dispatchNow(new SyncJob)")
        assert m is not None
        assert m.group(1) == "SyncJob"

    def test_static_dispatch(self):
        m = _JOB_DISPATCH_RE.search("SyncJob::dispatch($payload);")
        assert m is not None
        assert m.group(1) is None
        assert m.group(2) == "SyncJob"

    def test_static_dispatch_if_variant(self):
        m = _JOB_DISPATCH_RE.search("SyncJob::dispatchIf($cond);")
        assert m is not None
        assert m.group(2) == "SyncJob"

    def test_unrelated_static_call_not_matched(self):
        assert _JOB_DISPATCH_RE.search("Foo::create($x);") is None


class TestShouldQueueRegex:
    def test_single_interface(self):
        m = _SHOULDQUEUE_CLASS_RE.search("class SyncJob implements ShouldQueue {")
        assert m is not None
        assert m.group(1) == "SyncJob"

    def test_multiple_interfaces_shouldqueue_at_end(self):
        m = _SHOULDQUEUE_CLASS_RE.search("class SyncJob implements Bar, Baz, ShouldQueue {")
        assert m is not None
        assert m.group(1) == "SyncJob"

    def test_multiple_interfaces_shouldqueue_in_middle(self):
        m = _SHOULDQUEUE_CLASS_RE.search("class SyncJob implements Bar, ShouldQueue, Baz {")
        assert m is not None
        assert m.group(1) == "SyncJob"

    def test_class_with_extends_and_shouldqueue(self):
        m = _SHOULDQUEUE_CLASS_RE.search("class SyncJob extends BaseJob implements ShouldQueue {")
        assert m is not None
        assert m.group(1) == "SyncJob"

    def test_no_shouldqueue_not_matched(self):
        assert _SHOULDQUEUE_CLASS_RE.search("class SyncJob implements Bar {") is None


class TestArtisanCommandRegex:
    def test_bare_command(self):
        m = _ARTISAN_COMMAND_RE.search("class FooCommand extends Command {")
        assert m is not None
        assert m.group(1) == "FooCommand"

    def test_fully_qualified_command(self):
        m = _ARTISAN_COMMAND_RE.search("class FooCommand extends Illuminate\\Console\\Command {")
        assert m is not None
        assert m.group(1) == "FooCommand"

    def test_extends_other_class_not_matched(self):
        assert _ARTISAN_COMMAND_RE.search("class FooCommand extends BaseController {") is None


# ---------------------------------------------------------------------------
# W36.10 wave 2 — full resolver integration tests
# ---------------------------------------------------------------------------


class TestObserverRegistration:
    def test_observed_observer_class_not_dead(self, tmp_path):
        """``Foo::observe(FooObserver::class)`` -> standard Observer methods
        on ``FooObserver`` gain inbound ``laravel_observer`` edges from
        the ``Foo`` model class."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Providers").mkdir()
        (root / "app" / "Providers" / "AppServiceProvider.php").write_text("<?php\nFoo::observe(FooObserver::class);\n")

        conn = _make_conn()
        _add_file(conn, 1, "app/Providers/AppServiceProvider.php")
        _add_file(conn, 2, "app/Models/Foo.php")
        _add_file(conn, 3, "app/Observers/FooObserver.php")
        _add_symbol(conn, 100, 2, "Foo", "App\\Models\\Foo", "class")
        _add_symbol(conn, 200, 3, "FooObserver", "App\\Observers\\FooObserver", "class")
        _add_symbol(conn, 201, 3, "created", "App\\Observers\\FooObserver\\created", "method")
        _add_symbol(conn, 202, 3, "updated", "App\\Observers\\FooObserver\\updated", "method")
        _add_symbol(conn, 203, 3, "deleted", "App\\Observers\\FooObserver\\deleted", "method")

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'laravel_observer'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        # Foo class -> each of created/updated/deleted on FooObserver.
        assert (100, 201) in edges
        assert (100, 202) in edges
        assert (100, 203) in edges

    def test_observer_with_unused_helper_method_still_flagged(self, tmp_path):
        """A non-standard helper method on the Observer class (e.g.
        ``sendAlert``) is NOT in ``_OBSERVER_METHODS`` and therefore picks
        up no ``laravel_observer`` edge — it stays dead."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Providers").mkdir()
        (root / "app" / "Providers" / "AppServiceProvider.php").write_text("<?php\nFoo::observe(FooObserver::class);\n")

        conn = _make_conn()
        _add_file(conn, 1, "app/Providers/AppServiceProvider.php")
        _add_file(conn, 2, "app/Models/Foo.php")
        _add_file(conn, 3, "app/Observers/FooObserver.php")
        _add_symbol(conn, 100, 2, "Foo", "App\\Models\\Foo", "class")
        _add_symbol(conn, 200, 3, "FooObserver", "App\\Observers\\FooObserver", "class")
        _add_symbol(conn, 201, 3, "created", "App\\Observers\\FooObserver\\created", "method")
        _add_symbol(conn, 299, 3, "sendAlert", "App\\Observers\\FooObserver\\sendAlert", "method")

        resolve_laravel_dispatch(conn, root)
        # ``created`` covered; ``sendAlert`` has no inbound edge.
        rows = conn.execute("SELECT COUNT(*) FROM edges WHERE target_id = 299").fetchone()
        assert rows[0] == 0


class TestJobDispatch:
    def test_bus_dispatch_new_recognized(self, tmp_path):
        """``Bus::dispatch(new SyncJob(...))`` -> ``SyncJob::handle`` is
        reached via a ``laravel_job`` edge anchored on the containing
        *method* (W774 — was the *class* under the prior ``MIN(id)``
        attribution, which gave ``roam impact SyncJob::handle`` a
        misleading caller-of-record).
        """
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Http").mkdir()
        (root / "app" / "Http" / "Controllers").mkdir()
        (root / "app" / "Http" / "Controllers" / "OrderController.php").write_text(
            "<?php\nclass OrderController {\n"
            "  public function store() {\n"
            "    Bus::dispatch(new SyncJob($payload));\n"
            "  }\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Http/Controllers/OrderController.php")
        _add_file(conn, 2, "app/Jobs/SyncJob.php")
        # Realistic line ranges: class wraps lines 2..6, method wraps 3..5.
        # The Bus::dispatch call is on line 4 — inside store().
        _add_symbol(
            conn,
            100,
            1,
            "OrderController",
            "App\\Http\\Controllers\\OrderController",
            "class",
            line_start=2,
            line_end=6,
        )
        _add_symbol(
            conn, 101, 1, "store", "App\\Http\\Controllers\\OrderController\\store", "method", line_start=3, line_end=5
        )
        _add_symbol(conn, 200, 2, "SyncJob", "App\\Jobs\\SyncJob", "class", line_start=1, line_end=3)
        _add_symbol(conn, 201, 2, "handle", "App\\Jobs\\SyncJob\\handle", "method", line_start=2, line_end=2)

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'laravel_job'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        # W774: edge source is the *method* that called dispatch (store, 101),
        # NOT the enclosing class (OrderController, 100). The class is also
        # *not* present as a source — the prior MIN(id) bug would have it.
        assert (101, 201) in edges, (
            "Expected dispatch attribution to the containing method (W774). "
            "If this asserts (100, 201), the MIN(id) anti-pattern has regressed."
        )
        assert (100, 201) not in edges

    def test_static_dispatch_recognized(self, tmp_path):
        """``SyncJob::dispatch(...)`` -> ``SyncJob::handle`` reached.

        W774: the edge source is the *method* (``schedule``) that
        contained the dispatch call, not the enclosing ``Kernel`` class.
        """
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Console").mkdir()
        (root / "app" / "Console" / "Kernel.php").write_text(
            "<?php\nclass Kernel {\n  public function schedule() {\n    SyncJob::dispatch();\n  }\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Console/Kernel.php")
        _add_file(conn, 2, "app/Jobs/SyncJob.php")
        _add_symbol(conn, 100, 1, "Kernel", "App\\Console\\Kernel", "class", line_start=2, line_end=6)
        _add_symbol(conn, 101, 1, "schedule", "App\\Console\\Kernel\\schedule", "method", line_start=3, line_end=5)
        _add_symbol(conn, 200, 2, "SyncJob", "App\\Jobs\\SyncJob", "class", line_start=1, line_end=3)
        _add_symbol(conn, 201, 2, "handle", "App\\Jobs\\SyncJob\\handle", "method", line_start=2, line_end=2)

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'laravel_job'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        # W774: containing method, not containing class.
        assert (101, 201) in edges
        assert (100, 201) not in edges


class TestQueueHandler:
    def test_shouldqueue_class_handle_reached(self, tmp_path):
        """``class SyncJob implements ShouldQueue`` -> self-edge from
        ``SyncJob`` -> ``SyncJob::handle`` so ``handle`` is not dead."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Jobs").mkdir()
        (root / "app" / "Jobs" / "SyncJob.php").write_text(
            "<?php\nclass SyncJob implements ShouldQueue {\n  public function handle() {}\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Jobs/SyncJob.php")
        _add_symbol(conn, 100, 1, "SyncJob", "App\\Jobs\\SyncJob", "class")
        _add_symbol(conn, 101, 1, "handle", "App\\Jobs\\SyncJob\\handle", "method")

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'laravel_queue'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        assert (100, 101) in edges

    def test_shouldqueue_with_multiple_interfaces(self, tmp_path):
        """Comma-separated interfaces with ShouldQueue mid-list are
        still recognised."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Jobs").mkdir()
        (root / "app" / "Jobs" / "SyncJob.php").write_text(
            "<?php\nclass SyncJob implements Bar, ShouldQueue, Baz {\n  public function handle() {}\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Jobs/SyncJob.php")
        _add_symbol(conn, 100, 1, "SyncJob", "App\\Jobs\\SyncJob", "class")
        _add_symbol(conn, 101, 1, "handle", "App\\Jobs\\SyncJob\\handle", "method")

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'laravel_queue' AND target_id = 101").fetchone()
        assert rows[0] == 1


class TestArtisanCommand:
    def test_console_command_handle_reached(self, tmp_path):
        """``class FooCommand extends Command`` -> self-edge from
        ``FooCommand`` -> ``FooCommand::handle`` so ``handle`` is not dead."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Console").mkdir()
        (root / "app" / "Console" / "Commands").mkdir()
        (root / "app" / "Console" / "Commands" / "FooCommand.php").write_text(
            "<?php\nclass FooCommand extends Command {\n  public function handle() {}\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Console/Commands/FooCommand.php")
        _add_symbol(conn, 100, 1, "FooCommand", "App\\Console\\Commands\\FooCommand", "class")
        _add_symbol(conn, 101, 1, "handle", "App\\Console\\Commands\\FooCommand\\handle", "method")

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'laravel_artisan'").fetchall()
        edges = {(r["source_id"], r["target_id"]) for r in rows}
        assert (100, 101) in edges

    def test_fully_qualified_command_recognized(self, tmp_path):
        """``extends Illuminate\\Console\\Command`` is also recognised."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Console").mkdir()
        (root / "app" / "Console" / "Commands").mkdir()
        (root / "app" / "Console" / "Commands" / "FooCommand.php").write_text(
            "<?php\nclass FooCommand extends Illuminate\\Console\\Command {\n  public function handle() {}\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Console/Commands/FooCommand.php")
        _add_symbol(conn, 100, 1, "FooCommand", "App\\Console\\Commands\\FooCommand", "class")
        _add_symbol(conn, 101, 1, "handle", "App\\Console\\Commands\\FooCommand\\handle", "method")

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'laravel_artisan'").fetchone()
        assert rows[0] == 1

    def test_non_command_class_not_covered(self, tmp_path):
        """A class extending some unrelated parent does NOT trip the
        Artisan detector."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Foo.php").write_text(
            "<?php\nclass Foo extends BaseController {\n  public function handle() {}\n}\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Foo.php")
        _add_symbol(conn, 100, 1, "Foo", "App\\Foo", "class")
        _add_symbol(conn, 101, 1, "handle", "App\\Foo\\handle", "method")

        resolve_laravel_dispatch(conn, root)
        rows = conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'laravel_artisan'").fetchone()
        assert rows[0] == 0


# ---------------------------------------------------------------------------
# W36.11 — synthetic file-anchor provenance
# ---------------------------------------------------------------------------


class TestSyntheticFileAnchorProvenance:
    """Validates that route/observer/job edges originating from
    symbol-less files anchor on a per-file synthetic ``module`` symbol
    rather than on the target class (the W36.9 provenance bug).
    """

    def test_route_edge_anchored_on_route_file_not_target_class(self, tmp_path):
        """The W36.9 -> W36.11 provenance regression: edge.source.file_id
        must be ``routes/web.php``, NOT the controller's file. This makes
        ``roam impact FooController::index`` report routes/web.php as
        the caller rather than naming the controller class as a self-caller.
        """
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [FooController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        # The edge into ``index`` should originate from a synthetic anchor
        # whose ``file_id`` is the route file (1), not the controller file (2).
        row = conn.execute(
            """
            SELECT s.file_id AS source_file_id, s.name AS source_name,
                   s.kind AS source_kind, f.path AS source_path
            FROM edges e
            JOIN symbols s ON e.source_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE e.kind = 'laravel_route' AND e.target_id = 101
            """
        ).fetchone()
        assert row is not None, "Expected a laravel_route edge targeting index"
        assert row["source_name"] == SYNTHETIC_FILE_ANCHOR_NAME
        assert row["source_file_id"] == 1
        assert row["source_path"] == "routes/web.php"
        assert row["source_kind"] == "module"

    def test_anchor_excluded_from_dead_export_scan(self, tmp_path):
        """The synthetic anchor MUST have ``is_exported = 0`` so the
        dead-detector's ``WHERE is_exported = 1`` filter skips it. If
        this regresses, every route file would itself become a dead
        export — defeating the entire post-resolver."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [FooController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        anchor = conn.execute(
            "SELECT is_exported, kind FROM symbols WHERE name = ?",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        ).fetchone()
        assert anchor is not None
        assert anchor["is_exported"] == 0
        assert anchor["kind"] == "module"

    def test_observer_registration_anchored_on_registration_file(self, tmp_path):
        """Observer registration in a Provider where the Model class
        isn't in the symbol table (cross-package install, test fixture)
        must anchor on the Provider file rather than the Observer class
        itself (which would produce a misleading self-edge)."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Providers").mkdir()
        (root / "app" / "Providers" / "AppServiceProvider.php").write_text("<?php\nFoo::observe(FooObserver::class);\n")

        conn = _make_conn()
        _add_file(conn, 1, "app/Providers/AppServiceProvider.php")
        _add_file(conn, 3, "app/Observers/FooObserver.php")
        # Note: NO Foo Model class — only the Observer is indexed.
        _add_symbol(conn, 200, 3, "FooObserver", "App\\Observers\\FooObserver", "class")
        _add_symbol(conn, 201, 3, "created", "App\\Observers\\FooObserver\\created", "method")

        resolve_laravel_dispatch(conn, root)
        row = conn.execute(
            """
            SELECT s.name AS source_name, s.file_id AS source_file_id,
                   f.path AS source_path
            FROM edges e
            JOIN symbols s ON e.source_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE e.kind = 'laravel_observer' AND e.target_id = 201
            """
        ).fetchone()
        assert row is not None
        # Provenance: the registration file, not the Observer class (200).
        assert row["source_name"] == SYNTHETIC_FILE_ANCHOR_NAME
        assert row["source_file_id"] == 1
        assert row["source_path"] == "app/Providers/AppServiceProvider.php"

    def test_idempotent_re_run_does_not_duplicate_anchors(self, tmp_path):
        """Two successive resolver runs must produce the same single
        anchor row per file — not accumulate one per run."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [FooController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        first_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE name = ?",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        ).fetchone()[0]
        resolve_laravel_dispatch(conn, root)
        second_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE name = ?",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        ).fetchone()[0]
        assert first_count == 1
        assert second_count == 1

    def test_anchor_pruned_when_no_edges_resolve(self, tmp_path):
        """If a route file's class-string pattern matches but the target
        method is not in the symbol table, no edge is emitted and no
        anchor row should remain. Keeps the symbols table clean across
        re-indexes when route targets are unresolvable."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text("<?php\nRoute::get('/foo', [GhostController::class, 'index']);\n")

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        # No GhostController class anywhere -> target unresolvable.

        resolve_laravel_dispatch(conn, root)
        count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE name = ?",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        ).fetchone()[0]
        assert count == 0

    def test_file_with_real_class_still_uses_real_anchor(self, tmp_path):
        """A route file that *does* have a class symbol must keep using
        that real symbol as the anchor — no spurious synthetic row is
        created when one isn't needed."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text(
            "<?php\nclass RouteHelper {}\nRoute::get('/foo', [FooController::class, 'index']);\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        # routes/web.php has a class symbol of its own.
        _add_symbol(conn, 50, 1, "RouteHelper", "RouteHelper", "class")
        _add_symbol(
            conn,
            100,
            2,
            "FooController",
            "App\\Http\\Controllers\\FooController",
            "class",
        )
        _add_symbol(
            conn,
            101,
            2,
            "index",
            "App\\Http\\Controllers\\FooController\\index",
            "method",
        )

        resolve_laravel_dispatch(conn, root)
        # No synthetic anchor — the real class symbol (id=50) was used.
        anchor_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE name = ?",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        ).fetchone()[0]
        assert anchor_count == 0
        # Edge source is the real class symbol.
        row = conn.execute("SELECT source_id FROM edges WHERE kind = 'laravel_route' AND target_id = 101").fetchone()
        assert row["source_id"] == 50


# ---------------------------------------------------------------------------
# W774 — containing-symbol attribution (replaces MIN(id) anti-pattern)
# ---------------------------------------------------------------------------


class TestW774ContainingSymbolAttribution:
    """W749/W774 — every synthesised laravel edge attributes to the
    smallest *containing* symbol for the dispatch line, not whichever
    symbol owns the lowest id in the file. The prior ``MIN(id)`` pick
    silently credited the controller class for every dispatch its
    methods made, polluting ``roam impact``'s caller list with one
    spurious class entry per dispatching file.
    """

    def test_job_dispatch_picks_innermost_method_not_outer_class(self, tmp_path):
        """Two methods in the same controller, each dispatching a
        different job. Each edge must attribute to its own method —
        the controller class itself must not appear as a source for
        any of the four resulting laravel_job edges."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Http").mkdir()
        (root / "app" / "Http" / "Controllers").mkdir()
        # Two methods each dispatching one job.
        (root / "app" / "Http" / "Controllers" / "OrderController.php").write_text(
            "<?php\n"  # 1
            "class OrderController {\n"  # 2
            "  public function store() {\n"  # 3
            "    Bus::dispatch(new SyncJob);\n"  # 4
            "  }\n"  # 5
            "  public function ship() {\n"  # 6
            "    Bus::dispatch(new ShipJob);\n"  # 7
            "  }\n"  # 8
            "}\n"  # 9
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Http/Controllers/OrderController.php")
        _add_file(conn, 2, "app/Jobs/SyncJob.php")
        _add_file(conn, 3, "app/Jobs/ShipJob.php")
        _add_symbol(
            conn,
            100,
            1,
            "OrderController",
            "App\\Http\\Controllers\\OrderController",
            "class",
            line_start=2,
            line_end=9,
        )
        _add_symbol(
            conn, 101, 1, "store", "App\\Http\\Controllers\\OrderController\\store", "method", line_start=3, line_end=5
        )
        _add_symbol(
            conn, 102, 1, "ship", "App\\Http\\Controllers\\OrderController\\ship", "method", line_start=6, line_end=8
        )
        _add_symbol(conn, 200, 2, "SyncJob", "App\\Jobs\\SyncJob", "class", line_start=1, line_end=3)
        _add_symbol(conn, 201, 2, "handle", "App\\Jobs\\SyncJob\\handle", "method", line_start=2, line_end=2)
        _add_symbol(conn, 300, 3, "ShipJob", "App\\Jobs\\ShipJob", "class", line_start=1, line_end=3)
        _add_symbol(conn, 301, 3, "handle", "App\\Jobs\\ShipJob\\handle", "method", line_start=2, line_end=2)

        resolve_laravel_dispatch(conn, root)
        edges = {
            (r["source_id"], r["target_id"])
            for r in conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'laravel_job'").fetchall()
        }
        # Each dispatch attributes to its own containing method.
        assert (101, 201) in edges
        assert (102, 301) in edges
        # The class is NEVER credited (would be the MIN(id) bug).
        assert all(src != 100 for src, _ in edges), (
            f"W774 regression: OrderController class attributed as caller. Edges: {edges}"
        )

    def test_route_inside_method_attributes_to_method(self, tmp_path):
        """An inline ``Route::get(...)`` inside a service-provider's
        ``boot()`` method must attribute to ``boot`` — not to the
        provider *class* (the MIN(id) caller for that file)."""
        root = _setup_laravel_root(tmp_path)
        (root / "app").mkdir()
        (root / "app" / "Providers").mkdir()
        (root / "app" / "Providers" / "RouteServiceProvider.php").write_text(
            "<?php\n"  # 1
            "class RouteServiceProvider {\n"  # 2
            "  public function boot() {\n"  # 3
            "    Route::get('/foo', [FooController::class, 'index']);\n"  # 4
            "  }\n"  # 5
            "}\n"  # 6
        )

        conn = _make_conn()
        _add_file(conn, 1, "app/Providers/RouteServiceProvider.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        _add_symbol(
            conn,
            50,
            1,
            "RouteServiceProvider",
            "App\\Providers\\RouteServiceProvider",
            "class",
            line_start=2,
            line_end=6,
        )
        _add_symbol(
            conn, 51, 1, "boot", "App\\Providers\\RouteServiceProvider\\boot", "method", line_start=3, line_end=5
        )
        _add_symbol(
            conn, 100, 2, "FooController", "App\\Http\\Controllers\\FooController", "class", line_start=1, line_end=5
        )
        _add_symbol(
            conn, 101, 2, "index", "App\\Http\\Controllers\\FooController\\index", "method", line_start=2, line_end=4
        )

        resolve_laravel_dispatch(conn, root)
        row = conn.execute("SELECT source_id FROM edges WHERE kind = 'laravel_route' AND target_id = 101").fetchone()
        assert row is not None
        # boot() (51) — not RouteServiceProvider class (50).
        assert row["source_id"] == 51

    def test_no_containing_symbol_falls_through_to_synthetic_anchor(self, tmp_path):
        """When the dispatch line lies *outside* every symbol's range
        (top-level statement in a file whose only declared symbols are
        function/class bodies elsewhere), the resolver must NOT silently
        attribute to the lowest-id symbol — it must synthesise a file
        anchor instead (W774 + Pattern 2 silent-fallback discipline)."""
        root = _setup_laravel_root(tmp_path)
        (root / "routes").mkdir()
        (root / "routes" / "web.php").write_text(
            "<?php\n"  # 1
            "function helper() { return 1; }\n"  # 2
            "Route::get('/foo', [FooController::class, 'index']);\n"  # 3
        )
        (root / "app").mkdir()
        (root / "app" / "Http").mkdir()
        (root / "app" / "Http" / "Controllers").mkdir()
        (root / "app" / "Http" / "Controllers" / "FooController.php").write_text(
            "<?php\nclass FooController { public function index() {} }\n"
        )

        conn = _make_conn()
        _add_file(conn, 1, "routes/web.php")
        _add_file(conn, 2, "app/Http/Controllers/FooController.php")
        # helper() lives only on line 2 — does NOT contain line 3 where Route::get is.
        _add_symbol(conn, 50, 1, "helper", "helper", "function", line_start=2, line_end=2)
        _add_symbol(
            conn, 100, 2, "FooController", "App\\Http\\Controllers\\FooController", "class", line_start=2, line_end=2
        )
        _add_symbol(
            conn, 101, 2, "index", "App\\Http\\Controllers\\FooController\\index", "method", line_start=2, line_end=2
        )

        resolve_laravel_dispatch(conn, root)
        row = conn.execute(
            """
            SELECT s.name AS source_name, s.file_id AS source_file_id
            FROM edges e
            JOIN symbols s ON e.source_id = s.id
            WHERE e.kind = 'laravel_route' AND e.target_id = 101
            """
        ).fetchone()
        assert row is not None
        # The Route::get line (3) is outside helper()'s range (2..2).
        # Resolver must synthesise a file anchor — never fall through to
        # the lowest-id symbol (50 = helper).
        assert row["source_name"] == SYNTHETIC_FILE_ANCHOR_NAME
        assert row["source_file_id"] == 1
