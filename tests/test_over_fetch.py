"""Tests for roam over-fetch -- Laravel model over-serialization detection."""

from __future__ import annotations

import pytest

from roam.commands.cmd_over_fetch import (
    _count_resource_fields,
    _extract_method_bodies_with_lines,
)
from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def overfetch_project(tmp_path):
    """Laravel project with a model that has many fillable fields and no $hidden."""
    proj = tmp_path / "overfetch_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    models = proj / "app" / "Models"
    models.mkdir(parents=True)
    # Model with many fillable fields, no $hidden
    (models / "Order.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class Order extends Model {\n"
        "    protected $fillable = [\n"
        "        'user_id', 'product_id', 'quantity', 'price',\n"
        "        'discount', 'tax', 'total', 'status',\n"
        "        'shipping_address', 'billing_address',\n"
        "        'payment_method', 'payment_status',\n"
        "        'tracking_number', 'notes', 'internal_notes',\n"
        "        'created_by', 'updated_by', 'deleted_by',\n"
        "        'ip_address', 'user_agent', 'session_id',\n"
        "        'referral_code', 'coupon_code', 'gift_message',\n"
        "        'priority', 'weight', 'dimensions',\n"
        "        'warehouse_id', 'shelf_location', 'batch_number',\n"
        "        'customs_value', 'hs_code',\n"
        "    ];\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def no_models_project(tmp_path):
    proj = tmp_path / "no_models"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestOverFetchSmoke:
    def test_exits_zero(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project)
        assert result.exit_code == 0

    def test_no_models_exits_zero(self, cli_runner, no_models_project, monkeypatch):
        monkeypatch.chdir(no_models_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=no_models_project)
        assert result.exit_code == 0


class TestOverFetchJSON:
    def test_json_envelope(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project, json_mode=True)
        data = parse_json_output(result, "over-fetch")
        assert_json_envelope(data, "over-fetch")

    def test_json_summary_has_verdict(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project, json_mode=True)
        data = parse_json_output(result, "over-fetch")
        assert "verdict" in data["summary"]


class TestOverFetchText:
    def test_verdict_line(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project)
        assert "VERDICT:" in result.output


class TestOverFetchBraceAwareness:
    """A `{`/`}`/`[`/`]` inside a comment or string literal must not shift
    where a method body / return-array ends.

    Root cause: line-/char-based delimiter counting that counted delimiters
    appearing inside COMMENTS and STRINGS. PHP route/controller files write
    URL params like ``{id}`` inside doc comments and string literals; a stray
    brace/bracket there drifted the depth walk. Same defect class root-caused
    and fixed in cmd_auth_gaps (``_brace_deltas``).
    """

    def test_stray_closing_brace_in_comment_does_not_truncate_body(self):
        # A `}` inside a `//` comment must NOT prematurely close index()'s
        # body (which would drop the paginate/return lines and mis-parse the
        # rest of the class).
        source = (
            "<?php\n"
            "class ThingController {\n"
            "    public function index()\n"
            "    {\n"
            "        // the frontend appends a closing brace } to the payload\n"
            "        $rows = Thing::query()->paginate();\n"
            "        return $rows;\n"
            "    }\n"
            "\n"
            "    public function show()\n"
            "    {\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
        )
        methods = _extract_method_bodies_with_lines(source)
        assert [m["name"] for m in methods] == ["index", "show"]
        index_body = next(m["body"] for m in methods if m["name"] == "index")
        # Body reaches the real closing brace, past the comment brace.
        assert "paginate()" in index_body
        assert "return $rows;" in index_body
        # And must not swallow the following method.
        assert "function show" not in index_body

    def test_stray_brace_in_string_does_not_overrun_body(self):
        # `'/users/{id}'` is a realistic (balanced) route param; the lone `{`
        # in `'tenant_{'` is the stray that, when counted, over-extends
        # first()'s body to swallow second() -- so only ONE method would be
        # found without the fix.
        source = (
            "<?php\n"
            "class RouteController {\n"
            "    public function first()\n"
            "    {\n"
            "        $tpl = '/users/{id}';\n"
            "        $prefix = 'tenant_{';\n"
            "        $x = User::query()->paginate();\n"
            "        return $x;\n"
            "    }\n"
            "\n"
            "    public function second()\n"
            "    {\n"
            "        return 2;\n"
            "    }\n"
            "}\n"
        )
        methods = _extract_method_bodies_with_lines(source)
        assert [m["name"] for m in methods] == ["first", "second"]
        first_body = next(m["body"] for m in methods if m["name"] == "first")
        assert "paginate()" in first_body
        assert "function second" not in first_body

    def test_heredoc_with_unpaired_apostrophe_does_not_poison_body(self):
        # The REAL app's Services hold heredoc SQL (FileTableService.php). A
        # heredoc body is prose/SQL with unpaired apostrophes (`-- don't`); a
        # quote-aware scanner without heredoc support flips into string state
        # at that apostrophe and swallows the rest of the file — the method
        # body would truncate and the second method vanish.
        source = (
            "<?php\n"
            "class FileTableService {\n"
            "    public function rebuild()\n"
            "    {\n"
            "        $sql = <<<SQL\n"
            '            INSERT INTO "{$schema}"."article_ledger_accounts"\n'
            "            -- don't touch existing rows {\n"
            "            SELECT gen_random_uuid()\n"
            "        SQL;\n"
            "        DB::statement($sql);\n"
            "        $rows = Model::query()->paginate();\n"
            "        return $rows;\n"
            "    }\n"
            "\n"
            "    public function after()\n"
            "    {\n"
            "        return 2;\n"
            "    }\n"
            "}\n"
        )
        methods = _extract_method_bodies_with_lines(source)
        assert [m["name"] for m in methods] == ["rebuild", "after"]
        body = next(m["body"] for m in methods if m["name"] == "rebuild")
        # Body reaches past the heredoc to the real end; second method intact.
        assert "paginate()" in body
        assert "function after" not in body

    def test_count_resource_fields_ignores_bracket_in_comment(self, tmp_path):
        # A `]` inside a `//` comment in the toArray() return must not close
        # the array-body walk early (which would undercount exposed fields).
        (tmp_path / "OrderResource.php").write_text(
            "<?php\n"
            "class OrderResource {\n"
            "    public function toArray($request)\n"
            "    {\n"
            "        return [\n"
            "            'id' => $this->id,   // note: trailing bracket ] here\n"
            "            'name' => $this->name,\n"
            "            'email' => $this->email,\n"
            "        ];\n"
            "    }\n"
            "}\n"
        )
        assert _count_resource_fields(tmp_path, "OrderResource.php") == 3

    def test_count_resource_fields_ignores_bracket_in_string(self, tmp_path):
        # A lone `]` inside a string value must not close the array walk early.
        (tmp_path / "LabelResource.php").write_text(
            "<?php\n"
            "class LabelResource {\n"
            "    public function toArray($request)\n"
            "    {\n"
            "        return [\n"
            "            'closing' => ']',\n"
            "            'name' => $this->name,\n"
            "            'slug' => $this->slug,\n"
            "        ];\n"
            "    }\n"
            "}\n"
        )
        assert _count_resource_fields(tmp_path, "LabelResource.php") == 3
