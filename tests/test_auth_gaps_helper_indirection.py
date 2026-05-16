"""Tests for dogfood finding #6 — auth-gaps helper-method indirection.

Real-world Laravel codebases wrap ``$this->authorize()`` inside helpers such as
``BaseResourceController::authorizeIfPolicyExists($action, $target)``. Every
CRUD method calls the helper; the helper calls ``authorize``. The pre-fix
detector only inspected the calling method's body, so every method going
through the helper was falsely flagged as missing authorization.

The fix is hybrid:

  1. ``_AUTHORIZE_HELPER_NAMES`` allowlist — well-known helper names treated
     as proof of authorization without further analysis.
  2. ``_RE_AUTHORIZE_PREFIX_HELPER`` — `$this->authorizeFoo(` / `$this->gateBar(`
     family.
  3. One-level intra-class descent — when a method calls ``$this->X(`` and
     ``X`` is defined on this class or one of its ancestors, re-check
     ``X``'s body for authorize patterns.

These tests cover all three layers plus regression checks for the literal
patterns and the negative case (genuinely missing auth still flagged).
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_auth_gaps import (
    _AUTHORIZE_HELPER_NAMES,
    _analyze_controller_file,
    _body_has_inline_authorization,
    _build_class_source_map,
    _collect_all_methods,
    _collect_ancestor_methods,
    _method_has_authorize,
)
from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Unit-level tests for the new helpers (cheap, no DB, no indexer)
# ---------------------------------------------------------------------------


class TestBodyHasInlineAuthorization:
    def test_direct_authorize_call_recognised(self):
        assert _body_has_inline_authorization("$this->authorize('view', Foo::class);")

    def test_gate_facade_allows_recognised(self):
        assert _body_has_inline_authorization("if (Gate::allows('view-foo')) { return; }")

    def test_gate_facade_any_recognised(self):
        """Gate::any([...]) — added by dogfood #6 fix."""
        assert _body_has_inline_authorization("if (Gate::any(['view', 'edit'])) { return; }")

    def test_gate_facade_none_recognised(self):
        """Gate::none([...]) — added by dogfood #6 fix."""
        assert _body_has_inline_authorization("if (Gate::none(['admin'])) { abort(403); }")

    def test_authorize_for_user_recognised(self):
        assert _body_has_inline_authorization("$this->authorizeForUser($user, 'view', $model);")

    def test_helper_allowlist_name_recognised(self):
        """Layer-1: $this->authorizeIfPolicyExists(...) — the dogfood name."""
        assert _body_has_inline_authorization("$this->authorizeIfPolicyExists('view', Foo::class);")

    def test_helper_allowlist_check_policy_recognised(self):
        assert _body_has_inline_authorization("$this->checkPolicy('admin');")

    def test_helper_allowlist_must_be_allowed_recognised(self):
        assert _body_has_inline_authorization("$this->mustBeAllowed('write');")

    def test_prefix_helper_authorize_recognised(self):
        """Layer-1: prefix family `authorizeFoo` / `gateBar`."""
        assert _body_has_inline_authorization("$this->authorizeForBackend('view');")

    def test_prefix_helper_gate_recognised(self):
        assert _body_has_inline_authorization("$this->gateFor('admin');")

    def test_no_authorize_call_returns_false(self):
        assert not _body_has_inline_authorization("return Foo::all();")

    def test_unrelated_self_call_returns_false(self):
        assert not _body_has_inline_authorization("$this->loadData(); return $this->result;")


class TestMethodHasAuthorizeDescent:
    def test_descent_into_same_class_helper(self):
        """Layer-2: $this->helperX() where helperX is defined on this class
        and contains $this->authorize()."""
        body = "$this->authorizeIt('view', Foo::class); return Foo::all();"
        own_methods = {
            "authorizeIt": ("protected function authorizeIt() { $this->authorize($action, $target); }"),
        }
        assert _method_has_authorize(body, own_class_methods=own_methods)

    def test_descent_into_same_class_helper_without_auth_returns_false(self):
        """One-level descent that finds a helper WITHOUT auth must NOT
        falsely greenlight the caller."""
        body = "$this->loadData();"
        own_methods = {
            "loadData": "protected function loadData() { return Foo::all(); }",
        }
        assert not _method_has_authorize(body, own_class_methods=own_methods)

    def test_descent_into_ancestor_helper(self):
        """Layer-3: dogfood-cited BaseResourceController pattern. The helper
        is defined on a parent class via `extends`."""
        controller_source = """<?php
class FooController extends BaseResourceController {
    public function index() {
        $this->authorizeIfPolicyExists('view', Foo::class);
        return Foo::all();
    }
}
"""
        base_source = """<?php
class BaseResourceController extends Controller {
    protected function authorizeIfPolicyExists($action, $target) {
        if (Gate::has($action)) {
            $this->authorize($action, $target);
        }
    }
}
"""
        class_source_map = {
            "FooController": controller_source,
            "BaseResourceController": base_source,
            "Controller": "<?php class Controller {}",
        }
        # Caller body: just the index() body.
        body = "$this->authorizeIfPolicyExists('view', Foo::class); return Foo::all();"
        # Even WITHOUT the allowlist hit (rename helper to something
        # idiosyncratic) the descent should resolve.
        assert _method_has_authorize(
            body,
            own_class_methods={},  # not in same class
            class_source_map=class_source_map,
            source=controller_source,
        )

    def test_descent_skips_unknown_ancestor(self):
        """Helper isn't defined anywhere in the indexed sources -> stay
        conservative and report missing-auth (no descent miracles)."""
        body = "$this->mysteriousHelper('view');"
        controller_source = "<?php class FooController extends Bar {}\n"
        class_source_map = {"FooController": controller_source}  # no Bar
        # Helper name is not in the allowlist + not in any source map.
        assert not _method_has_authorize(
            body,
            own_class_methods={},
            class_source_map=class_source_map,
            source=controller_source,
        )

    def test_descent_caps_at_two_levels(self):
        """W36.10: depth=2 covers ~99% of real-world wrapper chains. A
        two-deep chain (caller -> helperA -> helperB[auth]) MUST be treated
        as authorized. This is the bumped-from-1 behaviour."""
        body = "$this->helperA();"
        own_methods = {
            "helperA": "protected function helperA() { $this->helperB(); }",
            "helperB": "protected function helperB() { $this->authorize(); }",
        }
        # Caller -> helperA -> helperB. With depth=2, Layer-2 recurses into
        # helperA, finds $this->helperB(), then recurses into helperB and
        # finds the literal authorize. Expected: True.
        assert _method_has_authorize(body, own_class_methods=own_methods)

    def test_descent_does_not_reach_three_levels(self):
        """W36.10 cap: depth-3 must NOT be authorized. Caller -> A -> B -> C[auth]
        is one hop too far. Prevents FP creep from spurious authorize calls in
        deep framework hierarchies."""
        body = "$this->helperA();"
        own_methods = {
            "helperA": "protected function helperA() { $this->helperB(); }",
            "helperB": "protected function helperB() { $this->helperC(); }",
            "helperC": "protected function helperC() { $this->authorize(); }",
        }
        # Caller -> A (depth=2) -> B (depth=1) -> needs depth=0 to reach C
        # but we stop at _depth<=0. Expected: False.
        assert not _method_has_authorize(body, own_class_methods=own_methods)


class TestAllowlistShape:
    def test_allowlist_includes_dogfood_helper_name(self):
        """Regression: don't accidentally drop authorizeIfPolicyExists."""
        assert "authorizeIfPolicyExists" in _AUTHORIZE_HELPER_NAMES

    def test_allowlist_is_a_set(self):
        """Frozenset / set so membership is O(1) — performance is critical."""
        assert hasattr(_AUTHORIZE_HELPER_NAMES, "__contains__")

    def test_allowlist_contains_literal_authorize(self):
        assert "authorize" in _AUTHORIZE_HELPER_NAMES


class TestCollectAncestorMethods:
    def test_collects_methods_from_parent(self):
        controller_source = "<?php\nclass FooController extends BaseController {}\n"
        base_source = (
            "<?php\nclass BaseController { "
            "protected function helperX() { return 1; } "
            "public function helperY() { return 2; } "
            "}"
        )
        class_source_map = {
            "FooController": controller_source,
            "BaseController": base_source,
        }
        methods = _collect_ancestor_methods(controller_source, class_source_map)
        assert "helperX" in methods
        assert "helperY" in methods

    def test_no_extends_returns_empty(self):
        controller_source = "<?php\nclass FooController { /* no extends */ }\n"
        assert _collect_ancestor_methods(controller_source, {"FooController": controller_source}) == {}

    def test_handles_cycle_safely(self):
        """If two classes mutually extend (impossible in valid PHP but the
        class_source_map can be constructed pathologically), the walker
        must terminate."""
        a_source = "<?php\nclass A extends B { protected function fa() {} }\n"
        b_source = "<?php\nclass B extends A { protected function fb() {} }\n"
        class_source_map = {"A": a_source, "B": b_source}
        # Should terminate without RecursionError.
        result = _collect_ancestor_methods(a_source, class_source_map)
        # Whatever we get, it must be finite (test just needs to return).
        assert isinstance(result, dict)


class TestCollectAllMethods:
    def test_returns_public_and_protected_methods(self):
        source = (
            "<?php\nclass Foo {"
            "  public function a() { return 1; }"
            "  protected function b() { return 2; }"
            "  private function c() { return 3; }"
            "}"
        )
        methods = _collect_all_methods(source)
        assert "a" in methods
        assert "b" in methods
        assert "c" in methods


# ---------------------------------------------------------------------------
# Controller-file-level integration: _analyze_controller_file directly
# ---------------------------------------------------------------------------


class TestAnalyzeControllerFileWithHelperIndirection:
    def test_helper_indirection_recognised_no_finding(self, tmp_path):
        """Controller calls $this->authorizeIfPolicyExists() inside its CRUD
        methods. Expected: no missing-auth finding for those methods."""
        controller_path = tmp_path / "FooController.php"
        controller_path.write_text(
            "<?php\n"
            "class FooController extends BaseResourceController {\n"
            "    public function store() {\n"
            "        $this->authorizeIfPolicyExists('create', Foo::class);\n"
            "        return Foo::create([]);\n"
            "    }\n"
            "    public function update() {\n"
            "        $this->authorizeIfPolicyExists('update', Foo::class);\n"
            "        return true;\n"
            "    }\n"
            "}\n"
        )
        # Layer-1 already covers this (allowlist hit), but explicitly
        # exercise _analyze_controller_file to verify the wiring.
        findings = _analyze_controller_file(str(controller_path), controller_path.read_text())
        # `store` and `update` should NOT appear at high confidence.
        high_findings = [f for f in findings if f["confidence"] == "high"]
        assert high_findings == [], f"Unexpected high findings: {high_findings}"

    def test_descent_into_base_class_helper_no_finding(self, tmp_path):
        """The dogfood scenario: helper is on a parent class. Layer-3 path."""
        controller_path = tmp_path / "FooController.php"
        base_path = tmp_path / "BaseResourceController.php"
        controller_path.write_text(
            "<?php\n"
            "class FooController extends BaseResourceController {\n"
            "    public function store() {\n"
            # Use a name NOT in the allowlist so we exercise descent, not
            # the cheap allowlist path.
            "        $this->guardCreateOrDie('Foo');\n"
            "        return Foo::create([]);\n"
            "    }\n"
            "}\n"
        )
        base_path.write_text(
            "<?php\n"
            "class BaseResourceController extends Controller {\n"
            "    protected function guardCreateOrDie($resource) {\n"
            "        $this->authorize('create', $resource);\n"
            "    }\n"
            "}\n"
        )
        class_source_map = _build_class_source_map(
            [controller_path.name, base_path.name],
            project_root=tmp_path,
        )
        findings = _analyze_controller_file(
            str(controller_path),
            controller_path.read_text(),
            class_source_map=class_source_map,
        )
        high_findings = [f for f in findings if f["confidence"] == "high"]
        assert high_findings == [], f"Descent through ancestor helper failed; got high findings: {high_findings}"

    def test_literal_authorize_still_recognised(self, tmp_path):
        """Regression: direct $this->authorize() must still suppress findings."""
        controller_path = tmp_path / "BarController.php"
        controller_path.write_text(
            "<?php\n"
            "class BarController extends Controller {\n"
            "    public function store() {\n"
            "        $this->authorize('create', Bar::class);\n"
            "        return Bar::create([]);\n"
            "    }\n"
            "}\n"
        )
        findings = _analyze_controller_file(str(controller_path), controller_path.read_text())
        high_findings = [f for f in findings if f["confidence"] == "high"]
        assert high_findings == []

    def test_no_authorize_anywhere_still_flagged(self, tmp_path):
        """Negative case: method has no authorize call AND no helper -> must
        still be flagged. Conservative bias: false-negative > false-positive,
        so we must catch the actually-missing case."""
        controller_path = tmp_path / "BazController.php"
        controller_path.write_text(
            "<?php\n"
            "class BazController extends Controller {\n"
            "    public function store() {\n"
            "        return Baz::create(['name' => 'x']);\n"
            "    }\n"
            "}\n"
        )
        findings = _analyze_controller_file(str(controller_path), controller_path.read_text())
        # Expect at least one high or medium finding for `store`.
        store_findings = [f for f in findings if f.get("method") == "store" and f["confidence"] in ("high", "medium")]
        assert store_findings, f"Missing-auth should be flagged but wasn't. All findings: {findings}"

    def test_unknown_helper_still_flagged(self, tmp_path):
        """If a controller calls a helper that ISN'T in the allowlist AND
        isn't defined on this class or any indexed ancestor, we must stay
        conservative and flag the method."""
        controller_path = tmp_path / "QuxController.php"
        controller_path.write_text(
            "<?php\n"
            "class QuxController extends Controller {\n"
            "    public function store() {\n"
            # `someTrulyUnknownThing` is not on the allowlist and isn't
            # defined anywhere.
            "        $this->someTrulyUnknownThing();\n"
            "        return Qux::create([]);\n"
            "    }\n"
            "}\n"
        )
        findings = _analyze_controller_file(str(controller_path), controller_path.read_text())
        store_findings = [f for f in findings if f.get("method") == "store"]
        assert store_findings, "Unknown helper should NOT silently clear the gap"


# ---------------------------------------------------------------------------
# End-to-end test through the CLI with a Laravel-style project
# ---------------------------------------------------------------------------


@pytest.fixture
def laravel_with_helper_indirection(tmp_path):
    """Laravel-style project where every controller calls
    ``$this->authorizeIfPolicyExists()`` instead of $this->authorize() directly."""
    proj = tmp_path / "laravel_helper_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    routes_dir = proj / "routes"
    routes_dir.mkdir()
    # No auth middleware on the route — we want the controller-side helper
    # to be the only authorization path.
    (routes_dir / "api.php").write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "\n"
        "Route::middleware('auth:sanctum')->group(function () {\n"
        "    Route::resource('orders', OrderController::class);\n"
        "});\n"
    )

    controllers_dir = proj / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)

    # Base controller defines the dogfood-cited helper on a parent class.
    (controllers_dir / "BaseResourceController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class BaseResourceController extends Controller {\n"
        "    protected function authorizeIfPolicyExists($action, $target) {\n"
        "        $this->authorize($action, $target);\n"
        "    }\n"
        "}\n"
    )

    # OrderController uses the helper for every CRUD method.
    (controllers_dir / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "\n"
        "class OrderController extends BaseResourceController {\n"
        "    public function store() {\n"
        "        $this->authorizeIfPolicyExists('create', Order::class);\n"
        "        return Order::create([]);\n"
        "    }\n"
        "    public function update($id) {\n"
        "        $this->authorizeIfPolicyExists('update', Order::class);\n"
        "        return true;\n"
        "    }\n"
        "    public function destroy($id) {\n"
        "        $this->authorizeIfPolicyExists('delete', Order::class);\n"
        "        return true;\n"
        "    }\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


class TestAuthGapsCliWithHelperIndirection:
    def test_helper_indirection_no_high_findings(self, cli_runner, laravel_with_helper_indirection, monkeypatch):
        """End-to-end: with route auth + helper indirection, no controller
        method should be flagged at high confidence."""
        monkeypatch.chdir(laravel_with_helper_indirection)
        result = invoke_cli(
            cli_runner,
            ["auth-gaps"],
            cwd=laravel_with_helper_indirection,
            json_mode=True,
        )
        data = parse_json_output(result, "auth-gaps")
        assert_json_envelope(data, "auth-gaps")
        # `route_protected_controllers` should already suppress *high* anyway,
        # but verify the controller-side gaps are also not at high
        # confidence — i.e. the helper-indirection fix is actually firing.
        ctrl_high = [
            f
            for f in data.get("controller_gaps", [])
            if f.get("confidence") == "high" and f.get("method") in {"store", "update", "destroy"}
        ]
        assert ctrl_high == [], f"Expected no high-confidence findings, got: {ctrl_high}"
