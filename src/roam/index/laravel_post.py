r"""Post-indexing pass for Laravel dynamic-dispatch idioms.

Why this exists
---------------

Laravel routes controllers, observers, policies, scopes, jobs, queue
handlers, and Artisan commands via framework conventions that the static
call-graph cannot see:

1. ``Route::get('/foo', [FooController::class, 'index'])`` registers the
   controller method by *string*; the explicit call to
   ``FooController::index`` lives inside the framework, not the app code.
2. Eloquent ``scopeActive($q)`` is invokable as ``Bar::active()`` via the
   framework's ``__callStatic`` magic; no symbolic call to ``scopeActive``
   exists in user code.
3. Policy classes (``App\Policies\FooPolicy``) are auto-discovered by Laravel
   from the matching model (``App\Models\Foo``) — there is no explicit
   ``use`` or ``register`` statement linking them in app code.
4. ``Foo::observe(FooObserver::class)`` binds an Observer to a model; the
   framework dispatches model lifecycle events to standard observer
   methods (``created``, ``updated``, ``deleted``, ...) on the Observer
   class with no symbolic call in app code.
5. ``Bus::dispatch(new SyncJob(...))`` and ``SyncJob::dispatch()`` queue a
   job; the worker invokes ``SyncJob::handle()`` later with no symbolic
   call to ``handle`` in the dispatch site.
6. Classes ``implements ShouldQueue`` are queue handlers; the framework
   worker invokes their ``handle()`` method.
7. Classes ``extends Illuminate\Console\Command`` are Artisan commands;
   the console runner invokes their ``handle()`` method.

The result is ``roam dead`` reporting controller methods, scope methods,
policy methods, observer methods, job handlers, queue handlers, and
Artisan command handlers as dead exports. This post-resolver synthesises
edges into the ``edges`` table so the dead-detector — and every other
consumer of the call-graph (``impact``, ``preflight``, ``trace``) — sees
the implicit references.

Gating
------

Runs only when the project has ``artisan`` or a composer.json that lists
``laravel/framework``. Skipping non-Laravel PHP projects costs one
filesystem check.

Edge kinds emitted
------------------

* ``laravel_route``    — Route::*('path', [ClassName::class, 'method'])
* ``laravel_scope``    — Eloquent ``scope*`` methods, edge from the model class
* ``laravel_policy``   — Policy class methods, edge from the paired model class
* ``laravel_observer`` — Observer methods (creating/updated/...), edge from
                         the observed model class
* ``laravel_job``      — Job ``handle()`` method, edge from the dispatch site's
                         file anchor (or the Job class itself as fallback)
* ``laravel_queue``    — ``handle()`` method on classes ``implements ShouldQueue``,
                         self-edge from the class
* ``laravel_artisan``  — ``handle()`` method on Artisan command classes,
                         self-edge from the class

Each edge has ``bridge='laravel'`` and ``confidence=0.85`` (regex inference
of a Laravel convention, not a parsed AST callsite).

W36.11 — synthetic file anchors
-------------------------------

A PHP file may legitimately contain framework-dispatch idioms while
holding **zero indexed symbols** — the canonical case is
``routes/web.php``, which is all top-level ``Route::*`` statements with
no class or function definitions. Earlier waves anchored such edges on
the *target* class symbol (e.g. ``FooController -> FooController::index``
self-edge) to keep the call-graph well-formed without inventing rows.
That kept the dead-detector happy but produced **misleading provenance**
in ``roam impact`` — the caller-of-record was the controller class
itself, not the route file that registers the controller.

W36.11 resolves the provenance fuzziness with a single synthetic anchor
row per affected file. The anchor's ``name`` is the reserved sentinel
``<roam-synthetic-file-anchor>`` (the leading ``<`` makes it unparseable
as a PHP/Python/JS identifier, guaranteeing no collision with user
code), with ``kind='module'``, ``is_exported=0``, and the original
file_id. The dead-detector skips it (``is_exported = 0``); ``roam impact``
now reports ``routes/web.php`` as the caller via its real file path.

Anchors are inserted lazily (only for files whose scanner needs one)
and dropped+re-derived alongside the ``laravel_*`` edge kinds on every
resolver run, so re-indexing does not accumulate orphan rows.
"""

from __future__ import annotations

import re
from pathlib import Path

from roam.index._containing_symbol import (
    build_file_symbol_ranges,
    containing_symbol_for_line,
)

# Audit A6: Laravel post-resolver is bridge-shaped (emits edges with
# bridge='laravel') but is implemented as a module-level resolver rather
# than a ``LanguageBridge`` subclass — the only consumer is the indexer,
# not the registry. Module-level VERSION fills the same role as the ABC
# class attribute: bump when the dispatch-inference regexes change in a
# way that meaningfully alters the emitted edges. Stamped onto every
# row via ``edges.bridge_version`` so consumers can detect drift.
VERSION: str = "1.0.0"


# Matches the class-string callable form: [ClassName::class, 'method']
# - Class name may be namespaced with backslashes (e.g. App\Http\Controllers\FooController)
# - Method name in single or double quotes
_ROUTE_CLASS_STRING_RE = re.compile(
    r"\[\s*([A-Za-z_][\w\\]*)::class\s*,\s*['\"]([A-Za-z_]\w*)['\"]\s*\]"
)

# Eloquent scope method convention: scope + Capital letter + rest of camelCase name.
# "scope" alone (no suffix) is rejected.
_ELOQUENT_SCOPE_RE = re.compile(r"^scope[A-Z]\w*$")

# Observer registration: ``Foo::observe(FooObserver::class)``.
# Group 1: the model class (short name, no leading namespace segments).
# Group 2: the observer class (may be fully qualified with backslashes).
_OBSERVER_REGISTER_RE = re.compile(
    r"\b([A-Za-z_]\w*)::observe\(\s*([A-Za-z_][\w\\]*)::class\s*\)"
)

# Standard Eloquent observer method names. A registered Observer's methods
# matching these names are invoked by the framework on the corresponding
# model lifecycle event. Custom helper methods on the Observer class are
# *not* covered (they remain dead unless called explicitly).
_OBSERVER_METHODS = (
    "retrieved", "creating", "created", "updating", "updated",
    "saving", "saved", "deleting", "deleted", "restoring", "restored",
    "trashed", "forceDeleted", "replicating",
)

# Job dispatch — two forms:
#   ``Bus::dispatch(new SyncJob(...))``        -> group 1 = SyncJob
#   ``SyncJob::dispatch(...)``                 -> group 2 = SyncJob
# The alternation keeps both forms in a single sweep. ``dispatch`` on a
# class is overloaded in Laravel for jobs and notifications; the resolver
# only emits an edge when the named class actually has a ``handle()``
# method in the symbol table, so notification dispatches are filtered out
# naturally.
_JOB_DISPATCH_RE = re.compile(
    r"Bus::dispatch(?:Now|Sync|AfterResponse)?\(\s*new\s+([A-Za-z_][\w\\]*)\b"
    r"|\b([A-Za-z_][\w\\]*)::dispatch(?:Now|Sync|AfterResponse|If|Unless)?\("
)

# Queue handler: ``class Foo ... implements ... ShouldQueue ...``.
# The interface list may include other interfaces before/after ShouldQueue
# separated by commas. Group 1 captures the class name. The fully
# qualified ``Illuminate\Contracts\Queue\ShouldQueue`` is also matched —
# the negative-lookbehind avoids matching ``NotShouldQueue`` style names.
# Multiline matching: ``implements`` may span lines in formatted code.
_SHOULDQUEUE_CLASS_RE = re.compile(
    r"\bclass\s+([A-Za-z_]\w*)\b"
    r"(?:\s+extends\s+[\w\\]+)?"
    r"\s+implements\s+([^{]*?)\bShouldQueue\b",
    re.DOTALL,
)

# Artisan command: ``class Foo extends Command`` or
# ``class Foo extends Illuminate\Console\Command``. The optional namespace
# prefix is the Laravel-canonical FQN; aliased imports resolve to the
# bare ``Command`` token via ``use``.
_ARTISAN_COMMAND_RE = re.compile(
    r"\bclass\s+([A-Za-z_]\w*)\s+extends\s+"
    r"(?:Illuminate\\Console\\)?Command\b"
)

# Laravel marker files used to gate the pass.
_LARAVEL_MARKERS = ("artisan", "composer.json")

# W36.11: reserved sentinel for synthetic file-anchor symbols. The leading
# ``<`` cannot appear in a PHP, Python, JS, or Go identifier — pure convention
# (no schema column needed). Match this name to detect anchors at query time:
# the dead-detector excludes them via ``is_exported = 0``, but consumers that
# want to *suppress* them in caller listings (e.g. a future
# ``--hide-synthetic-anchors`` flag on ``roam impact``) can match by name.
SYNTHETIC_FILE_ANCHOR_NAME = "<roam-synthetic-file-anchor>"


def _is_laravel_project(project_root: Path) -> bool:
    """Cheap probe: artisan binary OR composer.json mentions laravel/framework."""
    if (project_root / "artisan").exists():
        return True
    composer = project_root / "composer.json"
    if not composer.exists():
        return False
    try:
        text = composer.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "laravel/framework" in text or "illuminate/" in text


def _build_class_method_lookup(conn) -> dict[tuple[str, str], int]:
    """Index PHP methods by (class_name, method_name) -> symbol_id.

    Class name is the short name (last segment of qualified_name minus the
    method), so ``[FooController::class, 'index']`` can be resolved even
    when the file references the FQN as
    ``App\\Http\\Controllers\\FooController``. The PHP extractor stores
    qualified names with backslash separators
    (``App\\Http\\Controllers\\FooController\\index``); we parse the chain
    rather than relying on ``parent_id`` because the PHP extractor does
    not currently link method->class via that column.
    """
    out: dict[tuple[str, str], int] = {}
    rows = conn.execute(
        """
        SELECT s.id, s.name, s.qualified_name
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'php' AND s.kind = 'method'
        """
    ).fetchall()
    for r in rows:
        method_name = r["name"]
        qn = r["qualified_name"] or ""
        if "\\" in qn:
            parts = qn.split("\\")
            if len(parts) >= 2 and parts[-1] == method_name:
                cls = parts[-2]
                out.setdefault((cls, method_name), r["id"])
    return out


def _build_class_id_by_name(conn) -> dict[str, int]:
    """Map PHP class short name -> class symbol id (first match wins).

    Used by the scope/policy/queue/artisan resolvers to anchor edges on
    the class that holds the dispatched method — the conceptually
    correct caller for those four idioms (Laravel's ``__callStatic`` /
    ``handle`` framework dispatch routes through the class).

    Route, observer-registration, and job-dispatch idioms anchor on
    the *file* via ``_ensure_file_anchor_symbol`` instead, since for
    those the conceptual caller is the route/registration/dispatch
    site rather than the target class.
    """
    out: dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT s.id, s.name
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'php' AND s.kind = 'class'
        """
    ).fetchall()
    for r in rows:
        out.setdefault(r["name"], r["id"])
    return out


def _build_file_symbol_ranges(conn) -> dict[int, list[tuple[int, int, int]]]:
    """Per-file ``[(line_start, line_end, symbol_id), ...]`` ranges for
    PHP symbols. Powers the W774 "innermost containing symbol" lookup
    that replaced the prior ``MIN(id)`` synthetic-source.

    Synthetic anchors (see ``_ensure_file_anchor_symbol``) are filtered
    out by ``build_file_symbol_ranges`` so a previous resolver run's
    anchor cannot leak in as a "containing" symbol for a file that has
    since gained real symbols.
    """
    return build_file_symbol_ranges(conn, language="php")


def _ensure_file_anchor_symbol(
    conn,
    file_id: int,
    anchor_cache: dict[int, int],
) -> int:
    """Return the id of a synthetic ``module``-kind symbol anchored on
    ``file_id``, creating one if it does not exist.

    Convention (no schema migration): the anchor row uses the reserved
    sentinel name ``<roam-synthetic-file-anchor>`` and ``is_exported=0``.
    The leading ``<`` makes the name unparseable as a real identifier in
    any indexed language, eliminating the chance of collision with user
    code. ``is_exported=0`` keeps the row out of the dead-detector's
    ``WHERE is_exported = 1`` filter, so anchors never themselves show up
    as dead exports.

    Cached per-run via ``anchor_cache`` so the same file is anchored
    once even when multiple scanners (route + job + observer) ask for
    its anchor in the same pass.

    Note: this writes to the DB inside the resolver's outer transaction.
    Callers must commit after edge insertion (matching the existing
    ``resolve_laravel_dispatch`` pattern of bracketing all writes in one
    ``with conn:`` block).
    """
    if file_id in anchor_cache:
        return anchor_cache[file_id]
    # Re-check the DB in case a prior call in this same run created the
    # row but the cache was reset (defensive — anchor_cache should cover it).
    existing = conn.execute(
        "SELECT id FROM symbols WHERE file_id = ? AND name = ? LIMIT 1",
        (file_id, SYNTHETIC_FILE_ANCHOR_NAME),
    ).fetchone()
    if existing is not None:
        anchor_cache[file_id] = existing["id"] if hasattr(existing, "keys") else existing[0]
        return anchor_cache[file_id]
    cur = conn.execute(
        "INSERT INTO symbols (file_id, name, qualified_name, kind, line_start, is_exported) "
        "VALUES (?, ?, ?, 'module', 1, 0)",
        (file_id, SYNTHETIC_FILE_ANCHOR_NAME, SYNTHETIC_FILE_ANCHOR_NAME),
    )
    anchor_cache[file_id] = cur.lastrowid
    return cur.lastrowid


def _scan_file_for_route_strings(
    project_root: Path,
    file_path: str,
    file_id: int,
    file_symbol_ranges: dict[int, list[tuple[int, int, int]]],
    class_method_lookup: dict[tuple[str, str], int],
    class_id_by_name: dict[str, int],
    edges: list[tuple[int, int, str, int | None]],
    seen: set[tuple[int, int, str]],
    conn,
    anchor_cache: dict[int, int],
) -> None:
    """Find ``[Class::class, 'method']`` patterns and emit edges.

    Edge source preference (W774 — replaces the W36.11 lowest-id pick):
    1. The smallest user-defined symbol whose ``[line_start, line_end]``
       contains the route declaration's line. For inline controllers
       this is the constructor / method that holds the ``Route::*``
       call; for top-level ``Route::get(...)`` in a class file this is
       the class itself.
    2. A synthetic file anchor (W36.11) — for the common
       ``routes/web.php`` case where the file is all top-level
       ``Route::*`` calls with no class. The anchor's file_id is the
       route file, so ``roam impact FooController::index`` correctly
       reports ``routes/web.php`` as the caller.
    """
    try:
        source = (project_root / file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "::class" not in source:
        return

    ranges = file_symbol_ranges.get(file_id, [])

    for m in _ROUTE_CLASS_STRING_RE.finditer(source):
        cls_qn = m.group(1)
        method = m.group(2)
        cls_short = cls_qn.rsplit("\\", 1)[-1]
        target_id = class_method_lookup.get((cls_short, method))
        if target_id is None:
            continue
        line = source.count("\n", 0, m.start()) + 1
        source_sym_id = containing_symbol_for_line(ranges, line) if ranges else None
        if source_sym_id is None:
            # W36.11: synthesize a file-anchor symbol so provenance points
            # at the route file rather than the target class. This is
            # the correct outcome for symbol-less files (routes/web.php)
            # and for files whose only symbols sit on lines AFTER the
            # route declaration — never silent-fall to MIN(id) (W774).
            source_sym_id = _ensure_file_anchor_symbol(conn, file_id, anchor_cache)
        if target_id == source_sym_id:
            continue
        key = (source_sym_id, target_id, "laravel_route")
        if key in seen:
            continue
        seen.add(key)
        edges.append((source_sym_id, target_id, "laravel_route", line))


def _emit_scope_edges(
    conn,
    class_id_by_name: dict[str, int],
    edges: list[tuple[int, int, str, int | None]],
    seen: set[tuple[int, int, str]],
) -> None:
    """For each Eloquent scope method, emit an edge from the parent class.

    A method qualifies when:
    - it lives in a PHP class
    - its name matches ``scope[A-Z]\\w*``

    The class itself acts as the edge source — Laravel's ``__callStatic``
    routes ``Class::active()`` to ``Class::scopeActive()``, so the class
    is effectively a caller-of-record.

    Note: the PHP extractor does not currently populate ``parent_id``
    for methods, so we recover the class from the method's
    ``qualified_name`` chain (``App\\Models\\Bar\\scopeActive`` -> ``Bar``).
    """
    rows = conn.execute(
        """
        SELECT s.id AS method_id, s.name AS method_name,
               s.qualified_name AS qn, s.line_start AS line
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'php' AND s.kind = 'method'
        """
    ).fetchall()
    for r in rows:
        if not _ELOQUENT_SCOPE_RE.match(r["method_name"]):
            continue
        qn = r["qn"] or ""
        if "\\" not in qn:
            continue
        parts = qn.split("\\")
        if len(parts) < 2 or parts[-1] != r["method_name"]:
            continue
        class_name = parts[-2]
        class_id = class_id_by_name.get(class_name)
        if class_id is None or class_id == r["method_id"]:
            continue
        key = (class_id, r["method_id"], "laravel_scope")
        if key in seen:
            continue
        seen.add(key)
        edges.append((class_id, r["method_id"], "laravel_scope", r["line"]))


def _emit_policy_edges(
    conn,
    edges: list[tuple[int, int, str, int | None]],
    seen: set[tuple[int, int, str]],
) -> None:
    """For each Policy class paired with a Model, emit edges from Model
    class -> each public Policy method.

    Convention: ``App\\Policies\\FooPolicy`` pairs with ``App\\Models\\Foo``.
    Pairing is by short name with the ``Policy`` suffix stripped.

    Note: parent_id is not populated on PHP methods, so we recover the
    Policy class from each method's ``qualified_name`` chain.
    """
    # Build short-name -> class_id for classes whose file lives under app/Models/
    model_rows = conn.execute(
        """
        SELECT s.id, s.name
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'php'
          AND s.kind = 'class'
          AND (f.path LIKE '%app/Models/%'
               OR f.path LIKE '%app\\Models\\%')
        """
    ).fetchall()
    models_by_name: dict[str, int] = {}
    for r in model_rows:
        models_by_name.setdefault(r["name"], r["id"])
    if not models_by_name:
        return

    policy_method_rows = conn.execute(
        """
        SELECT s.id AS method_id, s.name AS method_name,
               s.qualified_name AS qn, s.line_start AS line,
               s.visibility AS visibility
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'php'
          AND s.kind = 'method'
          AND (f.path LIKE '%app/Policies/%'
               OR f.path LIKE '%app\\Policies\\%')
        """
    ).fetchall()
    for r in policy_method_rows:
        if (r["visibility"] or "public") != "public":
            continue
        qn = r["qn"] or ""
        if "\\" not in qn:
            continue
        parts = qn.split("\\")
        if len(parts) < 2 or parts[-1] != r["method_name"]:
            continue
        policy_name = parts[-2]
        if not policy_name.endswith("Policy"):
            continue
        model_name = policy_name[: -len("Policy")]
        model_id = models_by_name.get(model_name)
        if model_id is None or model_id == r["method_id"]:
            continue
        key = (model_id, r["method_id"], "laravel_policy")
        if key in seen:
            continue
        seen.add(key)
        edges.append((model_id, r["method_id"], "laravel_policy", r["line"]))


def _scan_file_for_observer_registrations(
    project_root: Path,
    file_path: str,
    file_id: int,
    class_method_lookup: dict[tuple[str, str], int],
    class_id_by_name: dict[str, int],
    edges: list[tuple[int, int, str, int | None]],
    seen: set[tuple[int, int, str]],
    conn,
    anchor_cache: dict[int, int],
) -> None:
    """Find ``Foo::observe(FooObserver::class)`` patterns and emit edges
    from the observed model class to each standard observer method on the
    Observer class.

    Edge source preference:
    1. The observed model's class symbol — Laravel's event dispatcher
       routes model lifecycle events to the registered observer, so the
       model class is the conceptual caller.
    2. W36.11: when the model class is absent from the symbol table
       (test fixtures, removed code, or registration in a Provider where
       the model lives in another package), synthesize a file anchor on
       the *registration file* rather than the observer class. This
       avoids the self-edge that would falsely report
       ``FooObserver -> FooObserver::created`` and instead names
       ``app/Providers/AppServiceProvider.php`` as the registration site.

    Custom helper methods on the Observer class (not in
    ``_OBSERVER_METHODS``) are *not* covered: they remain dead unless
    called explicitly.
    """
    try:
        source = (project_root / file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "::observe(" not in source:
        return

    for m in _OBSERVER_REGISTER_RE.finditer(source):
        model_short = m.group(1)
        observer_qn = m.group(2)
        observer_short = observer_qn.rsplit("\\", 1)[-1]
        source_sym_id = class_id_by_name.get(model_short)
        if source_sym_id is None:
            # W36.11: anchor on the registration file rather than the
            # observer class itself. Self-edges (Observer -> Observer
            # method) were the original provenance bug — anchoring on
            # the file is the correct architectural fix.
            source_sym_id = _ensure_file_anchor_symbol(conn, file_id, anchor_cache)
        line = source.count("\n", 0, m.start()) + 1
        for method_name in _OBSERVER_METHODS:
            target_id = class_method_lookup.get((observer_short, method_name))
            if target_id is None:
                continue
            if target_id == source_sym_id:
                continue
            key = (source_sym_id, target_id, "laravel_observer")
            if key in seen:
                continue
            seen.add(key)
            edges.append((source_sym_id, target_id, "laravel_observer", line))


def _scan_file_for_job_dispatches(
    project_root: Path,
    file_path: str,
    file_id: int,
    file_symbol_ranges: dict[int, list[tuple[int, int, int]]],
    class_method_lookup: dict[tuple[str, str], int],
    class_id_by_name: dict[str, int],
    edges: list[tuple[int, int, str, int | None]],
    seen: set[tuple[int, int, str]],
    conn,
    anchor_cache: dict[int, int],
) -> None:
    """Find ``Bus::dispatch(new Job(...))`` and ``Job::dispatch(...)``
    patterns and emit edges to the job class's ``handle()`` method.

    The regex's two alternation branches mean exactly one capture group
    holds the job class name per match; the other is ``None``. The
    matched class is only treated as a job when it actually has a
    ``handle`` method in the symbol table — that filter naturally drops
    notification ``Class::dispatch()`` calls (notifications use ``via``,
    not ``handle``).

    Edge source preference (W774 — replaces the W36.11 lowest-id pick):
    1. The smallest user-defined symbol whose ``[line_start, line_end]``
       contains the dispatch line. For the canonical
       ``OrderController::store() { Bus::dispatch(new Job($x)); }``
       case this is the *method* (``store``) — previously the resolver
       credited the controller class because it had the lowest id.
    2. A synthetic file anchor when the dispatch site is in a file with
       no symbols (rare — most ``::dispatch`` sites are inside method
       bodies — but possible for inline closure-only files).
    """
    try:
        source = (project_root / file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "dispatch(" not in source:
        return

    ranges = file_symbol_ranges.get(file_id, [])

    for m in _JOB_DISPATCH_RE.finditer(source):
        job_qn = m.group(1) or m.group(2)
        if not job_qn:
            continue
        job_short = job_qn.rsplit("\\", 1)[-1]
        target_id = class_method_lookup.get((job_short, "handle"))
        if target_id is None:
            continue
        line = source.count("\n", 0, m.start()) + 1
        source_sym_id = containing_symbol_for_line(ranges, line) if ranges else None
        if source_sym_id is None:
            # W36.11: synthesize a file-anchor symbol so provenance
            # points at the dispatching file rather than the job class.
            # Never silent-fall to MIN(id) (W774).
            source_sym_id = _ensure_file_anchor_symbol(conn, file_id, anchor_cache)
        if target_id == source_sym_id:
            continue
        key = (source_sym_id, target_id, "laravel_job")
        if key in seen:
            continue
        seen.add(key)
        edges.append((source_sym_id, target_id, "laravel_job", line))


def _scan_file_for_queue_and_artisan(
    project_root: Path,
    file_path: str,
    class_method_lookup: dict[tuple[str, str], int],
    class_id_by_name: dict[str, int],
    edges: list[tuple[int, int, str, int | None]],
    seen: set[tuple[int, int, str]],
) -> None:
    """Find ``class X implements ShouldQueue`` and
    ``class X extends Command`` declarations; emit a self-edge from the
    class to its ``handle()`` method.

    Queue handlers via the ``use Queueable;`` trait alone (without the
    ``ShouldQueue`` interface) are NOT detected here — Laravel's queue
    documentation treats ``ShouldQueue`` as the canonical marker, and
    trait-based detection is materially more complex to do reliably.
    Trait-only queue handlers are deferred to a follow-up wave.
    """
    try:
        source = (project_root / file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "class " not in source:
        return

    # Idiom 6: ShouldQueue interface
    if "ShouldQueue" in source:
        for m in _SHOULDQUEUE_CLASS_RE.finditer(source):
            class_name = m.group(1)
            class_id = class_id_by_name.get(class_name)
            if class_id is None:
                continue
            target_id = class_method_lookup.get((class_name, "handle"))
            if target_id is None or target_id == class_id:
                continue
            key = (class_id, target_id, "laravel_queue")
            if key in seen:
                continue
            seen.add(key)
            line = source.count("\n", 0, m.start()) + 1
            edges.append((class_id, target_id, "laravel_queue", line))

    # Idiom 7: Artisan command
    if "Command" in source:
        for m in _ARTISAN_COMMAND_RE.finditer(source):
            class_name = m.group(1)
            class_id = class_id_by_name.get(class_name)
            if class_id is None:
                continue
            target_id = class_method_lookup.get((class_name, "handle"))
            if target_id is None or target_id == class_id:
                continue
            key = (class_id, target_id, "laravel_artisan")
            if key in seen:
                continue
            seen.add(key)
            line = source.count("\n", 0, m.start()) + 1
            edges.append((class_id, target_id, "laravel_artisan", line))


def resolve_laravel_dispatch(conn, project_root: Path | None = None) -> int:
    """Insert synthetic edges for Laravel dynamic-dispatch idioms.

    Returns the total number of edges inserted across all seven resolvers.
    Idempotent: deletes existing ``laravel_*`` edges AND any prior
    synthetic file-anchor rows (``name = SYNTHETIC_FILE_ANCHOR_NAME``)
    before re-deriving them each run. Cleanup-before-write matches the
    pattern used by ``resolve_registry_dispatch``.

    W36.11: file-anchor symbols are inserted on demand by scanners that
    need a non-class edge source for a file with no user-defined
    symbols. Edges synthesised this run pick those anchor ids up; the
    next reindex drops them and starts over.
    """
    if project_root is None:
        try:
            from roam.db.connection import find_project_root

            project_root = find_project_root()
        except Exception:
            return 0
    if project_root is None or not _is_laravel_project(Path(project_root)):
        return 0

    # W36.11: drop prior anchor rows BEFORE rebuilding lookups so the
    # ``_build_file_symbol_ranges`` query doesn't pick up a stale anchor
    # as a "real" containing symbol. Edge rows that reference the dropped
    # anchors are cleaned up by ON DELETE CASCADE on edges.source_id /
    # target_id (when the production schema is in use). The fallback
    # explicit DELETE further down covers in-memory test schemas that
    # don't declare the FK cascade.
    with conn:
        conn.execute(
            "DELETE FROM symbols WHERE name = ?",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        )

    edges: list[tuple[int, int, str, int | None]] = []
    seen: set[tuple[int, int, str]] = set()
    anchor_cache: dict[int, int] = {}

    class_id_by_name = _build_class_id_by_name(conn)
    class_method_lookup = _build_class_method_lookup(conn)
    file_symbol_ranges = _build_file_symbol_ranges(conn)
    php_files = conn.execute(
        "SELECT id, path FROM files WHERE language = 'php'"
    ).fetchall()

    # Idiom 1: Route class-string
    for r in php_files:
        _scan_file_for_route_strings(
            Path(project_root),
            r["path"],
            r["id"],
            file_symbol_ranges,
            class_method_lookup,
            class_id_by_name,
            edges,
            seen,
            conn,
            anchor_cache,
        )

    # Idiom 2: Eloquent scope methods
    _emit_scope_edges(conn, class_id_by_name, edges, seen)

    # Idiom 3: Policy auto-discovery
    _emit_policy_edges(conn, edges, seen)

    # Idiom 4: Observer registration (Foo::observe(FooObserver::class))
    for r in php_files:
        _scan_file_for_observer_registrations(
            Path(project_root),
            r["path"],
            r["id"],
            class_method_lookup,
            class_id_by_name,
            edges,
            seen,
            conn,
            anchor_cache,
        )

    # Idiom 5: Job dispatch (Bus::dispatch / Class::dispatch)
    for r in php_files:
        _scan_file_for_job_dispatches(
            Path(project_root),
            r["path"],
            r["id"],
            file_symbol_ranges,
            class_method_lookup,
            class_id_by_name,
            edges,
            seen,
            conn,
            anchor_cache,
        )

    # Idioms 6 + 7: ShouldQueue interface + Artisan command extends Command
    for r in php_files:
        _scan_file_for_queue_and_artisan(
            Path(project_root),
            r["path"],
            class_method_lookup,
            class_id_by_name,
            edges,
            seen,
        )

    _LARAVEL_EDGE_KINDS = (
        "laravel_route",
        "laravel_scope",
        "laravel_policy",
        "laravel_observer",
        "laravel_job",
        "laravel_queue",
        "laravel_artisan",
    )
    delete_sql = (
        "DELETE FROM edges WHERE kind IN ("
        + ",".join(f"'{k}'" for k in _LARAVEL_EDGE_KINDS)
        + ")"
    )

    if not edges:
        with conn:
            conn.execute(delete_sql)
            # Drop any anchor rows created earlier in this run that no
            # edges ended up referencing. Keeps the symbols table clean
            # when (e.g.) all route methods were unresolved.
            conn.execute(
                "DELETE FROM symbols WHERE name = ?",
                (SYNTHETIC_FILE_ANCHOR_NAME,),
            )
        return 0

    with conn:
        conn.execute(delete_sql)
        # A6: stamp ``bridge_version`` alongside ``bridge`` so consumers
        # can spot a Laravel resolver bump without re-running the index.
        # Literal substitution (not a placeholder) keeps the row insert
        # at the same arity as before; VERSION is module-controlled, not
        # user input, so the SQL injection vector is closed.
        conn.executemany(
            "INSERT INTO edges (source_id, target_id, kind, line, bridge, confidence, bridge_version) "
            f"VALUES (?, ?, ?, ?, 'laravel', 0.85, '{VERSION}')",
            edges,
        )
        # Prune anchor rows that ended up with no inbound or outbound
        # edges (defensive — every scanner that creates an anchor also
        # appends an edge that references it, but this keeps the
        # invariant explicit for future contributors).
        conn.execute(
            "DELETE FROM symbols "
            "WHERE name = ? "
            "  AND id NOT IN (SELECT source_id FROM edges UNION "
            "                 SELECT target_id FROM edges)",
            (SYNTHETIC_FILE_ANCHOR_NAME,),
        )
    return len(edges)
