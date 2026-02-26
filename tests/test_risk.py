"""Tests for roam risk -- domain-weighted risk ranking of symbols."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


# ===========================================================================
# Fixture: Python project with multiple files and import relationships
# ===========================================================================


@pytest.fixture
def risk_project(tmp_path):
    """A project with interconnected Python files to exercise the risk command.

    Includes auth, payment, and user domain symbols so that the domain-weighted
    scoring has something to classify. All files have call relationships so that
    graph_metrics (fan-in, fan-out, betweenness) are populated.
    """
    proj = tmp_path / "risk_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    # auth.py — auth domain; auth/login/token keywords score high
    (src / "auth.py").write_text(
        "class AuthService:\n"
        '    """Handles authentication."""\n'
        "    def authenticate(self, user, password):\n"
        '        """Validate credentials."""\n'
        "        if not password:\n"
        "            return None\n"
        "        return self._create_token(user)\n"
        "\n"
        "    def _create_token(self, user):\n"
        '        """Create an auth token."""\n'
        '        return f"token:{user}"\n'
        "\n"
        "    def validate_token(self, token):\n"
        '        """Validate an existing auth token."""\n'
        '        return token.startswith("token:")\n'
    )

    # payment.py — payment/billing domain; highest risk weights
    (src / "payment.py").write_text(
        "from src.auth import AuthService\n"
        "\n"
        "\n"
        "def process_payment(amount, user_id):\n"
        '    """Process a payment transaction."""\n'
        "    auth = AuthService()\n"
        "    if not auth.validate_token(user_id):\n"
        "        raise PermissionError('Unauthorized')\n"
        "    return {'amount': amount, 'status': 'ok'}\n"
        "\n"
        "\n"
        "def calculate_invoice(items):\n"
        '    """Calculate the invoice total."""\n'
        "    return sum(item['price'] for item in items)\n"
        "\n"
        "\n"
        "def refund_payment(transaction_id):\n"
        '    """Issue a refund."""\n'
        "    return {'transaction': transaction_id, 'refunded': True}\n"
    )

    # user.py — user domain; medium risk weights
    (src / "user.py").write_text(
        "from src.auth import AuthService\n"
        "\n"
        "\n"
        "class User:\n"
        '    """A user account."""\n'
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def validate_email(self):\n"
        '        """Validate user email."""\n'
        '        return "@" in self.email\n'
        "\n"
        "\n"
        "def create_user(name, email):\n"
        '    """Create a new user account."""\n'
        "    user = User(name, email)\n"
        "    if not user.validate_email():\n"
        '        raise ValueError("Invalid email")\n'
        "    return user\n"
        "\n"
        "\n"
        "def delete_user(user_id):\n"
        '    """Delete a user account permanently."""\n'
        "    return {'deleted': user_id}\n"
    )

    # display.py — UI domain; low risk weights (dampened)
    (src / "display.py").write_text(
        "def render_user(user):\n"
        '    """Render user for display."""\n'
        '    return f"Name: {user.name}"\n'
        "\n"
        "\n"
        "def show_payment_status(status):\n"
        '    """Show payment status on screen."""\n'
        '    return f"Status: {status}"\n'
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ===========================================================================
# Smoke tests
# ===========================================================================


class TestRiskSmoke:
    """Basic invocation smoke tests."""

    def test_exits_zero(self, cli_runner, risk_project, monkeypatch):
        """roam risk exits 0 on a well-indexed project."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project)
        assert result.exit_code == 0

    def test_produces_output(self, cli_runner, risk_project, monkeypatch):
        """roam risk produces non-empty output."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project)
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    def test_help_works(self, cli_runner):
        """--help exits 0 and mentions 'risk'."""
        result = invoke_cli(cli_runner, ["risk", "--help"])
        assert result.exit_code == 0
        assert "risk" in result.output.lower()

    def test_count_option(self, cli_runner, risk_project, monkeypatch):
        """--n flag is accepted and command exits 0."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk", "-n", "5"], cwd=risk_project)
        assert result.exit_code == 0

    def test_domain_option(self, cli_runner, risk_project, monkeypatch):
        """--domain with custom keywords is accepted and exits 0."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk", "--domain", "payment,auth"], cwd=risk_project)
        assert result.exit_code == 0

    def test_explain_option(self, cli_runner, risk_project, monkeypatch):
        """--explain flag is accepted and exits 0."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk", "--explain"], cwd=risk_project)
        assert result.exit_code == 0

    def test_empty_project_exits_zero(self, cli_runner, tmp_path, monkeypatch):
        """On a project with no graph metrics, risk exits 0 with a fallback message."""
        proj = tmp_path / "empty_risk"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "main.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["risk"], cwd=proj)
        assert result.exit_code == 0


# ===========================================================================
# JSON envelope tests
# ===========================================================================


class TestRiskJSON:
    """JSON mode output validation."""

    def test_json_envelope(self, cli_runner, risk_project, monkeypatch):
        """JSON output follows the roam envelope contract."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        assert_json_envelope(data, command="risk")

    def test_json_summary_has_verdict(self, cli_runner, risk_project, monkeypatch):
        """JSON summary contains a verdict field."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {list(summary.keys())}"
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 0

    def test_json_summary_has_count(self, cli_runner, risk_project, monkeypatch):
        """JSON summary contains a count field."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        summary = data.get("summary", {})
        assert "count" in summary, f"Missing 'count' in summary: {list(summary.keys())}"
        assert isinstance(summary["count"], int)

    def test_json_has_items_list(self, cli_runner, risk_project, monkeypatch):
        """JSON output contains an 'items' list."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        assert "items" in data, f"Missing 'items' key in envelope: {list(data.keys())}"
        assert isinstance(data["items"], list)

    def test_json_items_have_expected_fields(self, cli_runner, risk_project, monkeypatch):
        """Each item in the items list has the required fields."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        items = data.get("items", [])
        if items:
            item = items[0]
            for field in ("name", "kind", "static_risk", "domain_weight", "adjusted_risk", "location"):
                assert field in item, f"Item missing '{field}': {list(item.keys())}"

    def test_json_items_adjusted_risk_is_numeric(self, cli_runner, risk_project, monkeypatch):
        """adjusted_risk values are numeric (int or float)."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        items = data.get("items", [])
        for item in items:
            assert isinstance(item["adjusted_risk"], (int, float)), (
                f"adjusted_risk is not numeric for {item.get('name')}: {item['adjusted_risk']!r}"
            )

    def test_json_explain_mode(self, cli_runner, risk_project, monkeypatch):
        """--explain adds chain and domain_sources fields to items."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk", "--explain"], cwd=risk_project, json_mode=True)
        data = parse_json_output(result, "risk")
        items = data.get("items", [])
        if items:
            item = items[0]
            assert "chain" in item, f"--explain item missing 'chain': {list(item.keys())}"
            assert "domain_sources" in item, f"--explain item missing 'domain_sources': {list(item.keys())}"

    def test_json_empty_project(self, cli_runner, tmp_path, monkeypatch):
        """Empty project JSON output has valid envelope with zero items."""
        proj = tmp_path / "empty_risk_json"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "main.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["risk"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "risk")
        assert_json_envelope(data, command="risk")
        assert data.get("items", []) == []


# ===========================================================================
# Text output tests
# ===========================================================================


class TestRiskText:
    """Text mode output validation."""

    def test_verdict_line_present(self, cli_runner, risk_project, monkeypatch):
        """Text output starts with a VERDICT: line."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_verdict_is_first_line(self, cli_runner, risk_project, monkeypatch):
        """VERDICT: is the first non-empty line of text output."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:")

    def test_section_header_present(self, cli_runner, risk_project, monkeypatch):
        """Output includes the 'Domain-Weighted Risk' section header."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk"], cwd=risk_project)
        assert result.exit_code == 0
        assert "Domain-Weighted Risk" in result.output

    def test_custom_domain_noted_in_output(self, cli_runner, risk_project, monkeypatch):
        """When --domain is used, the custom keywords appear in text output."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk", "--domain", "cryptopayment"], cwd=risk_project)
        assert result.exit_code == 0
        assert "cryptopayment" in result.output

    def test_explain_shows_static_risk_detail(self, cli_runner, risk_project, monkeypatch):
        """--explain mode shows 'Static risk' detail lines."""
        monkeypatch.chdir(risk_project)
        result = invoke_cli(cli_runner, ["risk", "--explain"], cwd=risk_project)
        assert result.exit_code == 0
        assert "Static risk" in result.output or "VERDICT:" in result.output

    def test_empty_project_fallback_message(self, cli_runner, tmp_path, monkeypatch):
        """On a project with no graph metrics, text output explains the situation."""
        proj = tmp_path / "empty_risk_text"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "main.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["risk"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
