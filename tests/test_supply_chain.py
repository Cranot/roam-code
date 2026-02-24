from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from roam.commands.cmd_supply_chain import (
    Dependency, _pin_status_python, _pin_status_npm, _pin_status_go,
    _pin_status_rust, _pin_status_ruby, _risk_level,
    _parse_requirements_txt, _parse_package_json, _parse_go_mod,
    _parse_cargo_toml, _parse_pyproject_toml, _parse_gemfile,
    discover_and_parse, compute_risk_score, top_risky,
    supply_chain_to_sarif, supply_chain,
)


def test_pin_python_exact():
    assert _pin_status_python("=="+"1.2.3") == "exact"

def test_pin_python_compat():
    assert _pin_status_python("~="+"1.4") == "range"

def test_pin_python_range_ge():
    assert _pin_status_python(">="+" 1.2.0") == "range"

def test_pin_python_range_ne():
    assert _pin_status_python("!="+" 1.0.0") == "range"

def test_pin_python_empty():
    assert _pin_status_python("") == "unpinned"

def test_pin_python_star():
    assert _pin_status_python("*") == "unpinned"

def test_pin_npm_exact():
    assert _pin_status_npm("1.2.3") == "exact"

def test_pin_npm_caret():
    assert _pin_status_npm("^1.2.3") == "range"

def test_pin_npm_tilde():
    assert _pin_status_npm("~1.2.3") == "range"

def test_pin_npm_star():
    assert _pin_status_npm("*") == "unpinned"

def test_pin_npm_latest():
    assert _pin_status_npm("latest") == "unpinned"

def test_pin_npm_gte():
    assert _pin_status_npm(">="+"1.0") == "range"

def test_pin_go_exact():
    assert _pin_status_go("v1.2.3") == "exact"

def test_pin_go_pseudo():
    assert _pin_status_go("v0.0.0-20240101120000-abcdef123456") == "exact"

def test_pin_go_empty():
    assert _pin_status_go("") == "unpinned"

def test_pin_rust_exact():
    assert _pin_status_rust("="+"1.2.3") == "exact"

def test_pin_rust_caret():
    assert _pin_status_rust("^1.2.3") == "range"

def test_pin_rust_tilde():
    assert _pin_status_rust("~1.2") == "range"

def test_pin_rust_plain():
    assert _pin_status_rust("1.2.3") == "range"

def test_pin_rust_star():
    assert _pin_status_rust("*") == "unpinned"

def test_risk_exact():
    assert _risk_level("exact", False) == "low"

def test_risk_range():
    assert _risk_level("range", False) == "medium"

def test_risk_unpinned():
    assert _risk_level("unpinned", False) == "high"

def test_risk_dev_same():
    assert _risk_level("unpinned", True) == "high"


def test_pin_ruby_exact():
    assert _pin_status_ruby("= 1.2.3") == "exact"

def test_pin_ruby_pessimistic():
    assert _pin_status_ruby("~> 1.2") == "range"

def test_pin_ruby_empty():
    assert _pin_status_ruby("") == "unpinned"

def test_requirements_pinned(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests=="+"2.28.0" + chr(10) + "click=="+"8.0.0" + chr(10))
    deps = _parse_requirements_txt(f, "requirements.txt")
    assert len(deps) == 2
    req = next(d for d in deps if d.name == "requests")
    assert req.pin_status == "exact"
    assert req.ecosystem == "python"

def test_requirements_range(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("flask>=2.0,<3.0" + chr(10))
    deps = _parse_requirements_txt(f, "requirements.txt")
    assert deps[0].pin_status == "range"

def test_requirements_unpinned(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests" + chr(10))
    deps = _parse_requirements_txt(f, "requirements.txt")
    assert deps[0].pin_status == "unpinned"
    assert deps[0].risk_level == "high"

def test_requirements_comments(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("# comment" + chr(10) + "-r other.txt" + chr(10) + "requests=="+"2.28.0" + chr(10))
    deps = _parse_requirements_txt(f, "requirements.txt")
    assert len(deps) == 1

def test_requirements_inline_comment(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests=="+"2.28.0  # important" + chr(10))
    deps = _parse_requirements_txt(f, "requirements.txt")
    assert deps[0].pin_status == "exact"

def test_requirements_extras(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests[security]=="+"2.28.0" + chr(10))
    deps = _parse_requirements_txt(f, "requirements.txt")
    assert deps[0].name == "requests"
    assert deps[0].pin_status == "exact"

def test_requirements_missing_file(tmp_path):
    assert _parse_requirements_txt(tmp_path / "nonexistent.txt", "r.txt") == []


def test_package_json_exact(tmp_path):
    f = tmp_path / "package.json"
    f.write_text(json.dumps({"dependencies": {"express": "4.18.0"}, "devDependencies": {"jest": "^29.0.0"}}))
    deps = _parse_package_json(f, "package.json")
    express = next(d for d in deps if d.name == "express")
    jest = next(d for d in deps if d.name == "jest")
    assert express.pin_status == "exact" and express.is_dev is False
    assert jest.pin_status == "range" and jest.is_dev is True

def test_package_json_star(tmp_path):
    f = tmp_path / "package.json"
    f.write_text(json.dumps({"dependencies": {"lodash": "*"}}))
    assert _parse_package_json(f, "package.json")[0].pin_status == "unpinned"

def test_package_json_invalid(tmp_path):
    f = tmp_path / "package.json"
    f.write_text("not-json")
    assert _parse_package_json(f, "package.json") == []

def test_package_json_peer_optional(tmp_path):
    f = tmp_path / "package.json"
    f.write_text(json.dumps({"peerDependencies": {"react": ">=16"}, "optionalDependencies": {"fs": "^2.0"}}))
    assert all(d.is_dev for d in _parse_package_json(f, "package.json"))


def test_go_mod_block(tmp_path):
    f = tmp_path / "go.mod"
    f.write_text(
        "module example.com/app" + chr(10) + chr(10) + "require (" + chr(10)
        + chr(9) + "github.com/gin-gonic/gin v1.9.1" + chr(10)
        + chr(9) + "golang.org/x/net v0.0.0-20240101120000-abcdef123456 // indirect" + chr(10)
        + ")" + chr(10)
    )
    deps = _parse_go_mod(f, "go.mod")
    gin = next(d for d in deps if "gin" in d.name)
    net = next(d for d in deps if "x/net" in d.name)
    assert gin.pin_status == "exact" and gin.is_dev is False
    assert net.is_dev is True

def test_go_mod_empty(tmp_path):
    f = tmp_path / "go.mod"
    f.write_text("module example.com/app" + chr(10))
    assert _parse_go_mod(f, "go.mod") == []

def test_cargo_exact_and_range(tmp_path):
    f = tmp_path / "Cargo.toml"
    f.write_text(
        "[dependencies]" + chr(10)
        + "serde = " + chr(34) + "=1.0.193" + chr(34) + chr(10)
        + "tokio = { version = " + chr(34) + "1.0" + chr(34) + ", features = [" + chr(34) + "full" + chr(34) + "] }" + chr(10)
        + "[dev-dependencies]" + chr(10)
        + "criterion = " + chr(34) + "0.5" + chr(34) + chr(10)
    )
    deps = _parse_cargo_toml(f, "Cargo.toml")
    serde = next(d for d in deps if d.name == "serde")
    tokio = next(d for d in deps if d.name == "tokio")
    criterion = next(d for d in deps if d.name == "criterion")
    assert serde.pin_status == "exact"
    assert tokio.pin_status == "range"
    assert criterion.is_dev is True

def test_cargo_caret(tmp_path):
    f = tmp_path / "Cargo.toml"
    f.write_text("[dependencies]" + chr(10) + "anyhow = " + chr(34) + "^1.0" + chr(34) + chr(10))
    assert _parse_cargo_toml(f, "Cargo.toml")[0].pin_status == "range"


def test_pyproject_pep621(tmp_path):
    f = tmp_path / "pyproject.toml"
    f.write_text(
        "[project]" + chr(10) + "name = " + chr(34) + "mypkg" + chr(34) + chr(10)
        + "dependencies = [" + chr(10)
        + "  " + chr(34) + "click==8.0.0" + chr(34) + "," + chr(10)
        + "  " + chr(34) + "requests>=2.0" + chr(34) + "," + chr(10)
        + "]" + chr(10)
    )
    deps = _parse_pyproject_toml(f, "pyproject.toml")
    click_dep = next((d for d in deps if d.name == "click"), None)
    req = next((d for d in deps if d.name == "requests"), None)
    assert click_dep is not None and click_dep.pin_status == "exact"
    assert req is not None and req.pin_status == "range"

def test_pyproject_poetry(tmp_path):
    f = tmp_path / "pyproject.toml"
    f.write_text(
        "[tool.poetry.dependencies]" + chr(10)
        + "python = " + chr(34) + "^3.8" + chr(34) + chr(10)
        + "fastapi = " + chr(34) + "^0.100" + chr(34) + chr(10)
        + chr(10) + "[tool.poetry.dev-dependencies]" + chr(10)
        + "pytest = " + chr(34) + "^7.0" + chr(34) + chr(10)
    )
    deps = _parse_pyproject_toml(f, "pyproject.toml")
    assert next((d for d in deps if d.name == "python"), None) is None
    fastapi = next((d for d in deps if d.name == "fastapi"), None)
    pytest_dep = next((d for d in deps if d.name == "pytest"), None)
    assert fastapi is not None and fastapi.is_dev is False
    assert pytest_dep is not None and pytest_dep.is_dev is True

def test_pyproject_optional_deps(tmp_path):
    f = tmp_path / "pyproject.toml"
    f.write_text(
        "[project]" + chr(10)
        + "dependencies = [" + chr(34) + "requests==2.28.0" + chr(34) + "]" + chr(10)
        + "[project.optional-dependencies]" + chr(10)
        + "dev = [" + chr(34) + "pytest==7.0.0" + chr(34) + "]" + chr(10)
    )
    deps = _parse_pyproject_toml(f, "pyproject.toml")
    req = next((d for d in deps if d.name == "requests"), None)
    pt = next((d for d in deps if d.name == "pytest"), None)
    assert req is not None and req.is_dev is False
    assert pt is not None and pt.is_dev is True


def test_gemfile_basic(tmp_path):
    sq = chr(39)
    f = tmp_path / "Gemfile"
    f.write_text(
        "gem " + sq + "rails" + sq + ", " + sq + "= 7.0.0" + sq + chr(10)
        + "gem " + sq + "puma" + sq + chr(10)
    )
    deps = _parse_gemfile(f, "Gemfile")
    rails = next(d for d in deps if d.name == "rails")
    puma = next(d for d in deps if d.name == "puma")
    assert rails.pin_status == "exact"
    assert puma.pin_status == "unpinned"

def test_gemfile_dev_group(tmp_path):
    sq = chr(39)
    f = tmp_path / "Gemfile"
    f.write_text(
        "gem " + sq + "rails" + sq + chr(10)
        + "group :test do" + chr(10)
        + "  gem " + sq + "rspec" + sq + chr(10)
        + "end" + chr(10)
    )
    deps = _parse_gemfile(f, "Gemfile")
    rails = next(d for d in deps if d.name == "rails")
    rspec = next(d for d in deps if d.name == "rspec")
    assert rails.is_dev is False
    assert rspec.is_dev is True

def test_gemfile_tilde(tmp_path):
    sq = chr(39)
    f = tmp_path / "Gemfile"
    f.write_text("gem " + sq + "nokogiri" + sq + ", " + sq + "~> 1.15" + sq + chr(10))
    assert _parse_gemfile(f, "Gemfile")[0].pin_status == "range"


def test_compute_risk_score_empty():
    m = compute_risk_score([])
    assert m["score"] == 100 and m["total"] == 0


def test_compute_risk_score_all_exact():
    deps = [
        Dependency("a", "==1.0", False, "exact", "low", "r.txt", "python"),
        Dependency("b", "==2.0", False, "exact", "low", "r.txt", "python"),
    ]
    m = compute_risk_score(deps)
    assert m["score"] >= 75
    assert m["total"] == 2


def test_compute_risk_score_all_unpinned():
    deps = [
        Dependency("a", "", False, "unpinned", "high", "r.txt", "python"),
        Dependency("b", "", False, "unpinned", "high", "r.txt", "python"),
    ]
    m = compute_risk_score(deps)
    assert m["score"] < 50


def test_compute_risk_score_fields():
    deps = [
        Dependency("a", "==1.0", False, "exact", "low", "r.txt", "python"),
        Dependency("b", "", False, "unpinned", "high", "r.txt", "python"),
    ]
    m = compute_risk_score(deps)
    assert "score" in m
    assert "total" in m
    assert "unpinned_count" in m
    assert "exact_count" in m


def test_compute_risk_score_mixed_dev():
    deps = [
        Dependency("a", "==1.0", False, "exact", "low", "r.txt", "python"),
        Dependency("b", "", True, "unpinned", "high", "r.txt", "python"),
    ]
    m = compute_risk_score(deps)
    assert m["total"] == 2
    assert isinstance(m["score"], int)


def test_top_risky_ordering():
    deps = [
        Dependency("a", "==1.0", False, "exact", "low", "r.txt", "python"),
        Dependency("b", "", False, "unpinned", "high", "r.txt", "python"),
        Dependency("c", ">=1.0", False, "range", "medium", "r.txt", "python"),
    ]
    result = top_risky(deps, n=3)
    assert result[0].risk_level == "high"
    assert result[1].risk_level == "medium"


def test_top_risky_limit():
    deps = [Dependency("d"+str(i), "", False, "unpinned", "high", "r.txt", "python") for i in range(10)]
    assert len(top_risky(deps, n=3)) == 3


def test_top_risky_empty():
    assert top_risky([], n=5) == []


def test_top_risky_all_low():
    deps = [Dependency("a", "==1.0", False, "exact", "low", "r.txt", "python")]
    result = top_risky(deps, n=5)
    assert len(result) == 1  # returns all within n limit


def test_discover_and_parse_no_files(tmp_path):
    deps = discover_and_parse(tmp_path)
    assert deps == []


def test_discover_and_parse_requirements(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests==2.28.0" + chr(10))
    deps = discover_and_parse(tmp_path)
    assert any(d.name == "requests" for d in deps)


def test_discover_and_parse_package_json(tmp_path):
    f = tmp_path / "package.json"
    f.write_text("{" + chr(34) + "dependencies" + chr(34) + ": {" + chr(34) + "express" + chr(34) + ": " + chr(34) + "4.18.0" + chr(34) + "}}")
    deps = discover_and_parse(tmp_path)
    assert any(d.ecosystem == "javascript" for d in deps)


def test_discover_and_parse_multiple(tmp_path):
    (tmp_path / "requirements.txt").write_text("click==8.0.0" + chr(10))
    (tmp_path / "package.json").write_text("{" + chr(34) + "dependencies" + chr(34) + ": {" + chr(34) + "lodash" + chr(34) + ": " + chr(34) + "4.17.21" + chr(34) + "}}")
    deps = discover_and_parse(tmp_path)
    ecosystems = {d.ecosystem for d in deps}
    assert "python" in ecosystems
    assert "javascript" in ecosystems


def test_discover_and_parse_dev_requirements(tmp_path):
    f = tmp_path / "requirements-dev.txt"
    f.write_text("pytest==7.0.0" + chr(10))
    deps = discover_and_parse(tmp_path)
    assert any(d.name == "pytest" and d.is_dev for d in deps)


def test_sarif_unpinned(tmp_path):
    deps = [Dependency("a", "", False, "unpinned", "high", "requirements.txt", "python")]
    sarif = supply_chain_to_sarif(deps, 50)
    assert sarif["version"] == "2.1.0"
    runs = sarif["runs"]
    assert len(runs) == 1
    results = runs[0]["results"]
    assert len(results) == 1
    assert results[0]["level"] == "warning"


def test_sarif_range(tmp_path):
    deps = [Dependency("b", ">=1.0", False, "range", "medium", "requirements.txt", "python")]
    sarif = supply_chain_to_sarif(deps, 50)
    results = sarif["runs"][0]["results"]
    assert results[0]["level"] == "note"


def test_sarif_exact_skipped(tmp_path):
    deps = [Dependency("c", "==1.0", False, "exact", "low", "requirements.txt", "python")]
    sarif = supply_chain_to_sarif(deps, 50)
    results = sarif["runs"][0]["results"]
    assert len(results) == 0


def test_sarif_mixed(tmp_path):
    deps = [
        Dependency("a", "", False, "unpinned", "high", "requirements.txt", "python"),
        Dependency("b", ">=1.0", False, "range", "medium", "requirements.txt", "python"),
        Dependency("c", "==1.0", False, "exact", "low", "requirements.txt", "python"),
    ]
    sarif = supply_chain_to_sarif(deps, 50)
    results = sarif["runs"][0]["results"]
    assert len(results) == 2


def test_cli_no_dep_files(tmp_path):
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": False, "sarif": False, "budget": 0})
    assert result.exit_code == 0
    assert "VERDICT" in result.output or "No dependency" in result.output


def test_cli_json_output(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.28.0" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": True, "sarif": False, "budget": 0})
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["command"] == "supply-chain"
    assert "verdict" in data["summary"]


def test_cli_top_risky(tmp_path):
    f = tmp_path / "requirements.txt"
    pkgs = ["pkg" + str(i) for i in range(10)]
    content = chr(10).join(p for p in pkgs) + chr(10)
    f.write_text(content)
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, ["--top", "3"], obj={"json": False, "sarif": False, "budget": 0})
    assert result.exit_code == 0


def test_cli_sarif_output(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": False, "sarif": True, "budget": 0})
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["version"] == "2.1.0"


def test_cli_budget_truncation(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.28.0" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": True, "sarif": False, "budget": 50})
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_cli_verdict_in_output(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.28.0" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": False, "sarif": False, "budget": 0})
    assert result.exit_code == 0
    assert "VERDICT" in result.output


def test_cli_pin_coverage_100(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.28.0" + chr(10) + "click==8.0.0" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": True, "sarif": False, "budget": 0})
    data = json.loads(result.output)
    assert data["summary"]["risk_score"] >= 75


def test_cli_ecosystems_field(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.28.0" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": True, "sarif": False, "budget": 0})
    data = json.loads(result.output)
    assert "ecosystems" in data["summary"] or "score" in data["summary"]


def test_json_all_dependencies_key(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.28.0" + chr(10))
    runner = CliRunner()
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=tmp_path):
        result = runner.invoke(supply_chain, [], obj={"json": True, "sarif": False, "budget": 0})
    data = json.loads(result.output)
    assert "all_dependencies" in data or "top_risky" in data


def test_setup_py_parser(tmp_path):
    f = tmp_path / "setup.py"
    f.write_text(
        "from setuptools import setup" + chr(10)
        + "setup(" + chr(10)
        + "    name=" + chr(39) + "mypkg" + chr(39) + "," + chr(10)
        + "    install_requires=[" + chr(10)
        + "        " + chr(39) + "requests==2.28.0" + chr(39) + "," + chr(10)
        + "    ]," + chr(10)
        + ")" + chr(10)
    )
    from roam.commands.cmd_supply_chain import _parse_setup_py
    deps = _parse_setup_py(f, "setup.py")
    assert any(d.name == "requests" for d in deps)


def test_setup_cfg_parser(tmp_path):
    f = tmp_path / "setup.cfg"
    f.write_text(
        "[options]" + chr(10)
        + "install_requires =" + chr(10)
        + "    click==8.0.0" + chr(10)
    )
    from roam.commands.cmd_supply_chain import _parse_setup_cfg
    deps = _parse_setup_cfg(f, "setup.cfg")
    assert any(d.name == "click" for d in deps)


def test_pom_xml_parser(tmp_path):
    f = tmp_path / "pom.xml"
    f.write_text(
        "<?xml version=" + chr(34) + "1.0" + chr(34) + "?>" + chr(10)
        + "<project>" + chr(10)
        + "  <dependencies>" + chr(10)
        + "    <dependency>" + chr(10)
        + "      <groupId>org.springframework</groupId>" + chr(10)
        + "      <artifactId>spring-core</artifactId>" + chr(10)
        + "      <version>5.3.20</version>" + chr(10)
        + "    </dependency>" + chr(10)
        + "  </dependencies>" + chr(10)
        + "</project>" + chr(10)
    )
    from roam.commands.cmd_supply_chain import _parse_pom_xml
    deps = _parse_pom_xml(f, "pom.xml")
    assert any(d.name == "org.springframework:spring-core" for d in deps)


def test_build_gradle_parser(tmp_path):
    f = tmp_path / "build.gradle"
    f.write_text(
        "dependencies {" + chr(10)
        + "    implementation " + chr(39) + "org.springframework:spring-core:5.3.20" + chr(39) + chr(10)
        + "    testImplementation " + chr(39) + "junit:junit:4.13" + chr(39) + chr(10)
        + "}" + chr(10)
    )
    from roam.commands.cmd_supply_chain import _parse_build_gradle
    deps = _parse_build_gradle(f, "build.gradle")
    assert len(deps) >= 1
    test_dep = next((d for d in deps if "junit" in d.name), None)
    assert test_dep is not None and test_dep.is_dev is True
