"""Tests for backend fixes round 2: I.10.3, I.10.6, and JSONC/MDX aliases.

Covers:
1. I.10.3 - Cross-model column attribution fix in missing-index
2. I.10.6 - ServiceProvider route registration in auth-gaps
3. JSONC / MDX grammar aliases in language registry
"""

from __future__ import annotations


# ===========================================================================
# 1. I.10.3 - _infer_table_from_context stops at semicolon boundaries
# ===========================================================================

class TestInferTableStopsAtSemicolon:
    """Cross-model attribution should not bleed across statement boundaries."""

    def test_infer_table_stops_at_semicolon(self):
        """When two model calls are separated by `;`, the second query should
        attribute to the second model, not the first."""
        from roam.commands.cmd_missing_index import _infer_table_from_context

        # Simulate PHP code where User::find() is followed by Branch::where()
        source = (
            "$user = User::find($id);\n"
            "$branch = Branch::where('status', 'active')\n"
            "    ->orderBy('created_at')\n"
            "    ->get();"
        )
        # Position the match at the orderBy call (inside the Branch chain)
        pos = source.index("->orderBy")
        table = _infer_table_from_context(source, pos)
        # Should resolve to 'branches' (from Branch), NOT 'users' (from User)
        assert table == "branches", f"Expected 'branches', got '{table}'"

    def test_infer_table_same_chain(self):
        """Within the same method chain (no `;` separator), attribution should
        still work correctly."""
        from roam.commands.cmd_missing_index import _infer_table_from_context

        source = (
            "$orders = Order::where('user_id', $userId)\n"
            "    ->where('status', 'pending')\n"
            "    ->orderBy('total')\n"
            "    ->get();"
        )
        pos = source.index("->orderBy")
        table = _infer_table_from_context(source, pos)
        assert table == "orders", f"Expected 'orders', got '{table}'"

    def test_infer_table_no_semicolon_fallback(self):
        """If there is no semicolon at all, fall back to full window search."""
        from roam.commands.cmd_missing_index import _infer_table_from_context

        source = (
            "class OrderService extends BaseService {\n"
            "    public function getOrders() {\n"
            "        return Order::where('status', 'active')\n"
            "            ->orderBy('name')\n"
            "            ->get()\n"
            "    }\n"
        )
        pos = source.index("->orderBy")
        table = _infer_table_from_context(source, pos)
        assert table == "orders", f"Expected 'orders', got '{table}'"

    def test_infer_table_multiple_statements_picks_nearest(self):
        """With multiple semicolons, should pick the model from the most
        recent statement."""
        from roam.commands.cmd_missing_index import _infer_table_from_context

        source = (
            "$a = Alpha::find(1);\n"
            "$b = Beta::find(2);\n"
            "$c = Gamma::where('field', 'value')\n"
            "    ->orderBy('name')\n"
            "    ->get();"
        )
        pos = source.index("->orderBy")
        table = _infer_table_from_context(source, pos)
        assert table == "gammas", f"Expected 'gammas', got '{table}'"


# ===========================================================================
# 2. I.10.6 - ServiceProvider route registration detection
# ===========================================================================

class TestAuthGapsServiceProvider:
    """ServiceProvider boot() routes wrapped in auth middleware should mark
    controllers as protected."""

    def test_service_provider_auth_middleware_group(self):
        """Controllers inside Route::middleware('auth:sanctum')->group()
        in a ServiceProvider should be detected as protected."""
        from roam.commands.cmd_auth_gaps import _analyze_service_provider

        source = """<?php

namespace App\\Providers;

use Illuminate\\Support\\ServiceProvider;
use Illuminate\\Support\\Facades\\Route;

class RouteServiceProvider extends ServiceProvider
{
    public function boot()
    {
        Route::middleware('auth:sanctum')->group(function () {
            Route::get('/orders', [OrderController::class, 'index']);
            Route::post('/orders', [OrderController::class, 'store']);
        });
    }
}
"""
        protected = _analyze_service_provider("app/Providers/RouteServiceProvider.php", source)
        assert "OrderController" in protected

    def test_service_provider_no_auth(self):
        """Controllers in a ServiceProvider without auth middleware should NOT
        be marked as protected."""
        from roam.commands.cmd_auth_gaps import _analyze_service_provider

        source = """<?php

namespace App\\Providers;

use Illuminate\\Support\\ServiceProvider;
use Illuminate\\Support\\Facades\\Route;

class RouteServiceProvider extends ServiceProvider
{
    public function boot()
    {
        Route::prefix('api')->group(function () {
            Route::get('/public', [PublicController::class, 'index']);
        });
    }
}
"""
        protected = _analyze_service_provider("app/Providers/RouteServiceProvider.php", source)
        assert "PublicController" not in protected

    def test_service_provider_non_provider_class(self):
        """A file that does not extend ServiceProvider should return no
        protected controllers, even if it contains Route::middleware."""
        from roam.commands.cmd_auth_gaps import _analyze_service_provider

        source = """<?php

class SomeRandomClass
{
    public function setup()
    {
        Route::middleware('auth:sanctum')->group(function () {
            Route::get('/orders', [OrderController::class, 'index']);
        });
    }
}
"""
        protected = _analyze_service_provider("app/SomeRandomClass.php", source)
        assert len(protected) == 0

    def test_service_provider_array_middleware(self):
        """ServiceProvider with array syntax Route::middleware(['auth:sanctum'])
        should also detect protected controllers."""
        from roam.commands.cmd_auth_gaps import _analyze_service_provider

        source = """<?php

namespace App\\Providers;

use Illuminate\\Support\\ServiceProvider;

class ApiServiceProvider extends ServiceProvider
{
    public function boot()
    {
        Route::middleware(['auth:sanctum'])->group(function () {
            Route::resource('users', UserController::class);
        });
    }
}
"""
        protected = _analyze_service_provider("app/Providers/ApiServiceProvider.php", source)
        assert "UserController" in protected


# ===========================================================================
# 3. JSONC / MDX grammar aliases
# ===========================================================================

class TestJsoncExtension:
    """JSONC files should be recognized by the language registry."""

    def test_jsonc_extension_detected(self):
        """A .jsonc file should be detected as the 'jsonc' language."""
        from roam.languages.registry import get_language_for_file
        lang = get_language_for_file("tsconfig.jsonc")
        assert lang == "jsonc"

    def test_jsonc_in_supported_languages(self):
        """jsonc should be in the set of supported languages."""
        from roam.languages.registry import get_supported_languages
        assert "jsonc" in get_supported_languages()

    def test_jsonc_grammar_alias(self):
        """jsonc should alias to the json tree-sitter grammar."""
        from roam.index.parser import GRAMMAR_ALIASES
        assert GRAMMAR_ALIASES.get("jsonc") == "json"

    def test_jsonc_in_extension_map(self):
        """The .jsonc extension should appear in supported extensions."""
        from roam.languages.registry import get_supported_extensions
        assert ".jsonc" in get_supported_extensions()


class TestMdxExtension:
    """MDX files should be recognized by the language registry."""

    def test_mdx_extension_detected(self):
        """A .mdx file should be detected as the 'mdx' language."""
        from roam.languages.registry import get_language_for_file
        lang = get_language_for_file("page.mdx")
        assert lang == "mdx"

    def test_mdx_in_supported_languages(self):
        """mdx should be in the set of supported languages."""
        from roam.languages.registry import get_supported_languages
        assert "mdx" in get_supported_languages()

    def test_mdx_grammar_alias(self):
        """mdx should alias to the markdown tree-sitter grammar."""
        from roam.index.parser import GRAMMAR_ALIASES
        assert GRAMMAR_ALIASES.get("mdx") == "markdown"

    def test_mdx_in_extension_map(self):
        """The .mdx extension should appear in supported extensions."""
        from roam.languages.registry import get_supported_extensions
        assert ".mdx" in get_supported_extensions()
