"""Dependency risk dashboard -- pin coverage, risk scoring, supply-chain health."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import click

from roam.db.connection import find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.output.sarif import to_sarif, write_sarif
from roam import __version__


class Dependency(NamedTuple):
    name: str
    version_spec: str
    is_dev: bool
    pin_status: str
    risk_level: str
    source_file: str
    ecosystem: str


def _pin_status_python(spec: str) -> str:
    if not spec or spec.strip() in ("*", ""):
        return "unpinned"
    if spec.startswith("=="):
        return "exact"
    if spec.startswith("~="):
        return "range"
    if "*" in spec:
        return "unpinned"
    if re.search(r"[><!]", spec):
        return "range"
    return "unpinned"


def _pin_status_npm(spec: str) -> str:
    if not spec or spec in ("*", "latest", "x", ""):
        return "unpinned"
    if re.match(r"^=?[0-9]", spec):
        return "exact"
    if spec.startswith(("^", "~")):
        return "range"
    if spec.startswith((">", "<")):
        return "range"
    if re.search(r"[*xX]", spec):
        return "unpinned"
    return "range"


def _pin_status_go(spec: str) -> str:
    if not spec:
        return "unpinned"
    if re.match(r"v\d+\.\d+\.\d+-\d{14}-[0-9a-f]+", spec):
        return "exact"
    if re.match(r"v\d+\.\d+\.\d+", spec):
        return "exact"
    return "range"


def _pin_status_rust(spec: str) -> str:
    if not spec or spec in ("*", ""):
        return "unpinned"
    if spec.startswith("=") and not spec.startswith(">="):
        return "exact"
    if spec.startswith(("^", "~")):
        return "range"
    if re.match(r"^\d", spec):
        return "range"
    if re.search(r"[><!]", spec):
        return "range"
    if "*" in spec:
        return "unpinned"
    return "range"


def _pin_status_ruby(spec: str) -> str:
    if not spec:
        return "unpinned"
    if re.match(r"^=\s*\d", spec):
        return "exact"
    if spec.startswith("~>"):
        return "range"
    if re.search(r"[><!]", spec):
        return "range"
    return "unpinned"


def _risk_level(pin_status: str, is_dev: bool) -> str:  # noqa: ARG001
    return {"exact": "low", "range": "medium", "unpinned": "high"}[pin_status]


def _parse_requirements_txt(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = re.sub(r"\s+#.*$", "", line)
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[.*?\])?(.*)?$", line)
        if not m:
            continue
        name = m.group(1).strip()
        spec = (m.group(3) or "").strip()
        pin = _pin_status_python(spec)
        deps.append(Dependency(name=name, version_spec=spec, is_dev=False,
                               pin_status=pin, risk_level=_risk_level(pin, False),
                               source_file=source, ecosystem="python"))
    return deps


_SETUP_INSTALL_REQUIRES_RE = re.compile(r"install_requires\s*=\s*\[([^\]]*)\]", re.DOTALL)


def _parse_setup_py(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    m = _SETUP_INSTALL_REQUIRES_RE.search(text)
    if m:
        for raw in re.findall(r'[\x27\x22]([^\x27\x22]+)[\x27\x22]', m.group(1)):
            name_m = re.match(r"^([A-Za-z0-9_.\-]+)(.*)?$", raw.strip())
            if not name_m:
                continue
            name = name_m.group(1)
            spec = name_m.group(2).strip() if name_m.group(2) else ""
            pin = _pin_status_python(spec)
            deps.append(Dependency(name=name, version_spec=spec, is_dev=False,
                                   pin_status=pin, risk_level=_risk_level(pin, False),
                                   source_file=source, ecosystem="python"))
    return deps


def _parse_setup_cfg(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    in_install = False
    in_continuation = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_install = stripped == "[options]"
            in_continuation = False
            continue
        if in_install:
            if re.match(r"install_requires\s*=", stripped):
                in_continuation = True
                continue
            if (in_continuation and line.startswith((" ", "	"))
                    and stripped and not stripped.startswith(";")):
                m2 = re.match(r"^([A-Za-z0-9_.\-]+)(.*)?$", stripped)
                if m2:
                    name = m2.group(1)
                    spec = m2.group(2).strip() if m2.group(2) else ""
                    pin = _pin_status_python(spec)
                    deps.append(Dependency(name=name, version_spec=spec, is_dev=False,
                                           pin_status=pin, risk_level=_risk_level(pin, False),
                                           source_file=source, ecosystem="python"))
            elif stripped and not line.startswith((" ", "	")):
                in_continuation = False
    return deps


def _parse_pyproject_toml(path: Path, source: str) -> list[Dependency]:
    """Parse pyproject.toml -- [project] PEP 621 and [tool.poetry] formats."""
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps

    # PEP 621 [project] format
    sec_m = re.search(r"^\[project\]", text, re.MULTILINE)
    if sec_m:
        dep_m = re.search(
            r"^dependencies\s*=\s*\[([^\]]*)\]",
            text[sec_m.start():],
            re.MULTILINE | re.DOTALL,
        )
        if dep_m:
            for raw in re.findall(r'[\x27\x22]([^\x27\x22]+)[\x27\x22]', dep_m.group(1)):
                raw = raw.strip()
                nm = re.match(r"^([A-Za-z0-9_.\-]+)(\[.*?\])?(.*)?$", raw)
                if nm:
                    name = nm.group(1)
                    spec = (nm.group(3) or "").strip()
                    pin = _pin_status_python(spec)
                    deps.append(Dependency(name=name, version_spec=spec, is_dev=False,
                                           pin_status=pin, risk_level=_risk_level(pin, False),
                                           source_file=source, ecosystem="python"))
        opt_m = re.search(
            r"^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)",
            text, re.MULTILINE | re.DOTALL,
        )
        if opt_m:
            for raw in re.findall(r'[\x27\x22]([^\x27\x22]+)[\x27\x22]', opt_m.group(1)):
                raw = raw.strip()
                nm = re.match(r"^([A-Za-z0-9_.\-]+)(\[.*?\])?(.*)?$", raw)
                if nm:
                    name = nm.group(1)
                    spec = (nm.group(3) or "").strip()
                    pin = _pin_status_python(spec)
                    deps.append(Dependency(name=name, version_spec=spec, is_dev=True,
                                           pin_status=pin, risk_level=_risk_level(pin, True),
                                           source_file=source, ecosystem="python"))

    # [tool.poetry.dependencies]
    poetry_m = re.search(r"^\[tool\.poetry\.dependencies\](.*?)(?=^\[|\Z)",
                         text, re.MULTILINE | re.DOTALL)
    if poetry_m:
        for line in poetry_m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kv = re.match(r"^([A-Za-z0-9_.\-]+)\s*=\s*(.+)$", line)
            if not kv:
                continue
            name = kv.group(1)
            if name.lower() == "python":
                continue
            val = kv.group(2).strip().strip('"').strip("'")
            tv = re.search(r'version\s*=\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]', val)
            if tv:
                val = tv.group(1)
            pin = _pin_status_python(val)
            deps.append(Dependency(name=name, version_spec=val, is_dev=False,
                                   pin_status=pin, risk_level=_risk_level(pin, False),
                                   source_file=source, ecosystem="python"))

    # [tool.poetry.dev-dependencies]
    poetry_dev_m = re.search(r"^\[tool\.poetry\.dev-dependencies\](.*?)(?=^\[|\Z)",
                              text, re.MULTILINE | re.DOTALL)
    if poetry_dev_m:
        for line in poetry_dev_m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kv = re.match(r"^([A-Za-z0-9_.\-]+)\s*=\s*(.+)$", line)
            if not kv:
                continue
            name = kv.group(1)
            val = kv.group(2).strip().strip('"').strip("'")
            tv = re.search(r'version\s*=\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]', val)
            if tv:
                val = tv.group(1)
            pin = _pin_status_python(val)
            deps.append(Dependency(name=name, version_spec=val, is_dev=True,
                                   pin_status=pin, risk_level=_risk_level(pin, True),
                                   source_file=source, ecosystem="python"))

    return deps

def _parse_package_json(path: Path, source: str) -> list[Dependency]:
    import json as _json
    deps: list[Dependency] = []
    try:
        data = _json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return deps
    for section, is_dev in [("dependencies", False), ("devDependencies", True),
                              ("peerDependencies", True), ("optionalDependencies", True)]:
        for name, spec in (data.get(section) or {}).items():
            if not isinstance(spec, str):
                continue
            pin = _pin_status_npm(spec)
            deps.append(Dependency(name=name, version_spec=spec, is_dev=is_dev,
                                   pin_status=pin, risk_level=_risk_level(pin, is_dev),
                                   source_file=source, ecosystem="javascript"))
    return deps
def _parse_go_mod(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    for block in re.findall(r"require\s*\((.*?)\)", text, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                name, ver = parts[0], parts[1]
                is_indirect = "// indirect" in line
                pin = _pin_status_go(ver)
                deps.append(Dependency(name=name, version_spec=ver,
                                       is_dev=is_indirect, pin_status=pin,
                                       risk_level=_risk_level(pin, is_indirect),
                                       source_file=source, ecosystem="go"))
    for m in re.finditer(r"^require\s+(\S+)\s+(\S+)", text, re.MULTILINE):
        name, ver = m.group(1), m.group(2)
        pin = _pin_status_go(ver)
        deps.append(Dependency(name=name, version_spec=ver, is_dev=False,
                               pin_status=pin, risk_level=_risk_level(pin, False),
                               source_file=source, ecosystem="go"))
    return deps

def _parse_cargo_toml(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    section_re = re.compile(r"^\[([^\]]+)\]", re.MULTILINE)
    sections = [(m.group(1), m.start()) for m in section_re.finditer(text)]

    def _get_section_body(section_name: str) -> str:
        for i, (sname, start) in enumerate(sections):
            if sname.strip() == section_name:
                end = sections[i + 1][1] if i + 1 < len(sections) else len(text)
                return text[start:end]
        return ""

    for sec_name, is_dev in [("dependencies", False), ("dev-dependencies", True),
                               ("build-dependencies", True)]:
        body = _get_section_body(sec_name)
        if not body:
            continue
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            kv = re.match(r"^([A-Za-z0-9_\-]+)\s*=\s*(.+)$", line)
            if not kv:
                continue
            name = kv.group(1)
            val = kv.group(2).strip()
            tv = re.search(r'version\s*=\s*[\x27\x22]([^\x27\x22]*)[\x27\x22]', val)
            if tv:
                spec = tv.group(1)
            else:
                spec = val.strip(chr(34)).strip(chr(39))
            pin = _pin_status_rust(spec)
            deps.append(Dependency(name=name, version_spec=spec, is_dev=is_dev,
                                   pin_status=pin, risk_level=_risk_level(pin, is_dev),
                                   source_file=source, ecosystem="rust"))
    return deps
def _parse_pom_xml(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    for dep_block in re.findall(r"<dependency>(.*?)</dependency>", text, re.DOTALL):
        group_m = re.search(r"<groupId>([^<]+)</groupId>", dep_block)
        art_m = re.search(r"<artifactId>([^<]+)</artifactId>", dep_block)
        ver_m = re.search(r"<version>([^<]+)</version>", dep_block)
        scope_m = re.search(r"<scope>([^<]+)</scope>", dep_block)
        if not (group_m and art_m):
            continue
        name = f"{group_m.group(1).strip()}:{art_m.group(1).strip()}"
        spec = ver_m.group(1).strip() if ver_m else ""
        scope = (scope_m.group(1).strip() if scope_m else "compile").lower()
        is_dev = scope in ("test", "provided", "system")
        pin = "exact" if spec and re.match(r"^\d+\.\d+", spec) else _pin_status_python(spec)
        deps.append(Dependency(name=name, version_spec=spec, is_dev=is_dev,
                               pin_status=pin, risk_level=_risk_level(pin, is_dev),
                               source_file=source, ecosystem="java"))
    return deps

def _parse_build_gradle(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    for m in re.finditer(
        r'(\w+)\s+[\x27\x22]([^\x27\x22]+:[^\x27\x22]+:[^\x27\x22]+)[\x27\x22]', text
    ):
        config = m.group(1).lower()
        parts = m.group(2).split(":")
        if len(parts) < 3:
            continue
        name = f"{parts[0]}:{parts[1]}"
        spec = parts[2]
        is_dev = config in ("testimplementation", "testcompile", "testruntime",
                            "debugimplementation", "androidtestimplementation")
        pin = "exact" if re.match(r"^\d+\.\d+", spec) else "range"
        deps.append(Dependency(name=name, version_spec=spec, is_dev=is_dev,
                               pin_status=pin, risk_level=_risk_level(pin, is_dev),
                               source_file=source, ecosystem="java"))
    return deps

def _parse_gemfile(path: Path, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return deps
    in_test_group = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"group\s+.*:(?:test|development|staging)", stripped):
            in_test_group = True
        if stripped == "end" and in_test_group:
            in_test_group = False
            continue
        if stripped.startswith("#"):
            continue
        m = re.match(
            r'gem\s+[\x27\x22]([^\x27\x22]+)[\x27\x22](?:,\s*[\x27\x22]([^\x27\x22]*)[\x27\x22])?',
            stripped,
        )
        if not m:
            continue
        name = m.group(1)
        spec = m.group(2) or ""
        pin = _pin_status_ruby(spec)
        deps.append(Dependency(name=name, version_spec=spec, is_dev=in_test_group,
                               pin_status=pin, risk_level=_risk_level(pin, in_test_group),
                               source_file=source, ecosystem="ruby"))
    return deps

_DEP_FILES: dict[str, tuple] = {
    "requirements.txt":      (_parse_requirements_txt, "python"),
    "requirements-dev.txt":  (_parse_requirements_txt, "python"),
    "requirements_dev.txt":  (_parse_requirements_txt, "python"),
    "requirements-test.txt": (_parse_requirements_txt, "python"),
    "constraints.txt":       (_parse_requirements_txt, "python"),
    "setup.py":              (_parse_setup_py, "python"),
    "setup.cfg":             (_parse_setup_cfg, "python"),
    "pyproject.toml":        (_parse_pyproject_toml, "python"),
    "package.json":          (_parse_package_json, "javascript"),
    "go.mod":                (_parse_go_mod, "go"),
    "Cargo.toml":            (_parse_cargo_toml, "rust"),
    "pom.xml":               (_parse_pom_xml, "java"),
    "build.gradle":          (_parse_build_gradle, "java"),
    "build.gradle.kts":      (_parse_build_gradle, "java"),
    "Gemfile":               (_parse_gemfile, "ruby"),
}


_DEV_FILE_MARKERS = frozenset([
    "requirements-dev.txt", "requirements_dev.txt",
    "requirements-test.txt", "requirements-ci.txt",
])


def discover_and_parse(project_root: Path) -> list[Dependency]:
    all_deps: list[Dependency] = []
    for filename, (parser_fn, _eco) in _DEP_FILES.items():
        candidate = project_root / filename
        if candidate.is_file():
            parsed = parser_fn(candidate, filename)
            if filename in _DEV_FILE_MARKERS:
                parsed = [d._replace(is_dev=True) for d in parsed]
            all_deps.extend(parsed)
    seen: set[tuple[str, str, str]] = set()
    unique: list[Dependency] = []
    for dep in all_deps:
        key = (dep.name.lower(), dep.ecosystem, dep.source_file)
        if key not in seen:
            seen.add(key)
            unique.append(dep)
    return unique


def compute_risk_score(deps: list[Dependency]) -> dict:
    if not deps:
        return dict(score=100, pin_coverage=1.0, dev_ratio=0.0, total=0,
                    direct_count=0, dev_count=0, exact_count=0, range_count=0, unpinned_count=0)
    total = len(deps)
    direct = [d for d in deps if not d.is_dev]
    dev = [d for d in deps if d.is_dev]
    exact = sum(1 for d in deps if d.pin_status == "exact")
    rng = sum(1 for d in deps if d.pin_status == "range")
    unpinned = sum(1 for d in deps if d.pin_status == "unpinned")
    pin_coverage = exact / total
    dev_ratio = len(dev) / total
    diversity_penalty = 1.0 - (unpinned / total)
    raw = pin_coverage * 0.6 + dev_ratio * 0.2 + diversity_penalty * 0.2
    score = max(0, min(100, round(raw * 100)))
    return dict(score=score, pin_coverage=round(pin_coverage, 4),
                dev_ratio=round(dev_ratio, 4), total=total,
                direct_count=len(direct), dev_count=len(dev),
                exact_count=exact, range_count=rng, unpinned_count=unpinned)


def top_risky(deps: list[Dependency], n: int = 5) -> list[Dependency]:
    _pin_rank = {"unpinned": 2, "range": 1, "exact": 0}
    return sorted(deps, key=lambda d: (_pin_rank[d.pin_status], not d.is_dev), reverse=True)[:n]

def supply_chain_to_sarif(deps: list[Dependency], score: int) -> dict:
    rules = [
        {"id": "supply-chain/unpinned-dependency",
         "shortDescription": "Dependency has no version pin",
         "helpUri": "https://github.com/AbanteAI/roam-code#supply-chain",
         "defaultLevel": "warning"},
        {"id": "supply-chain/range-dependency",
         "shortDescription": "Dependency uses a version range instead of exact pin",
         "helpUri": "https://github.com/AbanteAI/roam-code#supply-chain",
         "defaultLevel": "note"},
    ]
    results = []
    for dep in deps:
        if dep.pin_status == "exact":
            continue
        rule_id = ("supply-chain/unpinned-dependency"
                   if dep.pin_status == "unpinned"
                   else "supply-chain/range-dependency")
        level = "warning" if dep.pin_status == "unpinned" else "note"
        spec_str = f" ({dep.version_spec})" if dep.version_spec else ""
        dev_str = " [dev]" if dep.is_dev else ""
        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {"text": (f"{dep.name}{spec_str} in {dep.source_file}"
                                 f" -- {dep.pin_status}{dev_str} ({dep.ecosystem})")},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": dep.source_file.replace("\\", "/")}}}],
        })
    return to_sarif("roam-code", __version__, rules, results)

@click.command("supply-chain")
@click.option("--top", default=5, show_default=True,
              help="Number of riskiest dependencies to highlight")
@click.pass_context
def supply_chain(ctx, top):
    """Dependency risk dashboard: pin coverage, risk scoring, supply-chain health."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    try:
        project_root = find_project_root()
    except Exception:
        project_root = Path.cwd()

    deps = discover_and_parse(project_root)
    metrics = compute_risk_score(deps)
    score = metrics["score"]
    risky = top_risky(deps, top)
    found_files = sorted({d.source_file for d in deps})
    ecosystems: dict[str, int] = {}
    for d in deps:
        ecosystems[d.ecosystem] = ecosystems.get(d.ecosystem, 0) + 1
    if not deps:
        verdict = "No dependency files found"
    elif score >= 80:
        _pct = int(metrics["pin_coverage"] * 100)
        verdict = f"Supply chain healthy ({score}/100) -- {_pct}% pinned"
    elif score >= 60:
        _u = metrics["unpinned_count"]
        _r = metrics["range_count"]
        verdict = f"Supply chain fair ({score}/100) -- {_u} unpinned, {_r} ranges"
    else:
        _u = metrics["unpinned_count"]
        verdict = f"Supply chain risky ({score}/100) -- {_u} unpinned dependencies"

    if sarif_mode:
        sarif = supply_chain_to_sarif(deps, score)
        click.echo(write_sarif(sarif))
        return

    if json_mode:
        envelope = json_envelope(
            "supply-chain",
            summary=dict(
                verdict=verdict, risk_score=score,
                total_dependencies=metrics["total"],
                direct_count=metrics["direct_count"],
                dev_count=metrics["dev_count"],
                pin_coverage_pct=round(metrics["pin_coverage"] * 100, 1),
                unpinned_count=metrics["unpinned_count"],
                range_count=metrics["range_count"],
                exact_count=metrics["exact_count"],
                files_scanned=found_files,
                ecosystems=ecosystems,
            ),
            budget=token_budget,
            risk_score=score,
            total_dependencies=metrics["total"],
            direct_count=metrics["direct_count"],
            dev_count=metrics["dev_count"],
            exact_count=metrics["exact_count"],
            range_count=metrics["range_count"],
            unpinned_count=metrics["unpinned_count"],
            pin_coverage_pct=round(metrics["pin_coverage"] * 100, 1),
            files_scanned=found_files,
            ecosystems=ecosystems,
            top_risky=[dict(name=d.name, version_spec=d.version_spec,
                            pin_status=d.pin_status, risk_level=d.risk_level,
                            is_dev=d.is_dev, ecosystem=d.ecosystem,
                            source_file=d.source_file) for d in risky],
            all_dependencies=[dict(name=d.name, version_spec=d.version_spec,
                                   pin_status=d.pin_status, risk_level=d.risk_level,
                                   is_dev=d.is_dev, ecosystem=d.ecosystem,
                                   source_file=d.source_file) for d in deps],
        )
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if not deps:
        click.echo("  No dependency files found.")
        return

    _t = metrics["total"]
    _dc = metrics["direct_count"]
    _dv = metrics["dev_count"]
    _pct = int(metrics["pin_coverage"] * 100)
    _ex = metrics["exact_count"]
    _rng = metrics["range_count"]
    _unp = metrics["unpinned_count"]
    click.echo(f"Risk Score: {score}/100  |  Total: {_t}  |  Direct: {_dc}  |  Dev: {_dv}")
    click.echo(f"Pin Coverage: {_pct}%  |  Exact: {_ex}  |  Range: {_rng}  |  Unpinned: {_unp}")
    if found_files:
        click.echo(f"Files: " + ", ".join(found_files))
    if ecosystems:
        eco_str = "  ".join(f"{k}:{v}" for k, v in sorted(ecosystems.items()))
        click.echo(f"Ecosystems: {eco_str}")
    click.echo()

    if risky:
        _topn = min(top, len(risky))
        click.echo(f"=== Top {_topn} Riskiest Dependencies ===")
        rows = []
        for d in risky:
            spec = d.version_spec if d.version_spec else "(none)"
            dev_str = "dev" if d.is_dev else "direct"
            rows.append([d.name, spec, d.pin_status, d.risk_level, dev_str, d.ecosystem, d.source_file])
        click.echo(format_table(
            ["Name", "Version Spec", "Pin Status", "Risk", "Type", "Ecosystem", "File"],
            rows,
        ))
        click.echo()

    recs = []
    if metrics["unpinned_count"] > 0:
        _u = metrics["unpinned_count"]
        recs.append(f"Pin {_u} unpinned dependencies to exact versions")
    if metrics["range_count"] > 5:
        _r = metrics["range_count"]
        recs.append(f"Consider pinning {_r} range-versioned dependencies")
    if not recs:
        recs.append("Dependency pinning looks good")
    click.echo("Recommendations:")
    for rec in recs:
        click.echo(f"  - {rec}")