"""W570 — drift-guards for ``pyproject.toml`` ``[tool.setuptools.package-data]``.

These tests pin the wheel-visibility contract for files that the source
checkout can resolve via ``Path(__file__).parent`` but a ``pip install``
wheel can ONLY resolve if the matching ``package-data`` glob is present
in ``pyproject.toml``.

Background — W554 moved ``control-mapping.yaml`` into
``src/roam/templates/audit_report/`` and added
``"roam.templates.audit_report" = ["*.yaml", "*.yml"]`` to package-data.
A future PR could drop that line while keeping the YAML in place: the
source checkout (and every editable-install dev box) would still work
because the resolver's filesystem-walk fallback wins, but every
``pip install roam-code`` user would silently lose OSCAL emission.

W471 has the same shape for the SLSA-SRC-L3 workflow template inside
``src/roam/templates/ci/``: ``cmd_ci_setup._templates_dir()`` reads it
via the filesystem path under the installed package, which only resolves
if ``"roam.templates.ci" = ["*"]`` is preserved.

Failure mode these tests catch: regression in pyproject.toml that
drops a package-data entry while leaving the asset on disk. The asset
is still importable from source but invisible to a wheel — exactly
the silent-break class W554 was created to prevent.
"""

from __future__ import annotations

from importlib.resources import as_file, files


class TestAuditReportPackageDataDriftW570:
    """W570 drift-guard for ``roam.templates.audit_report`` (W554)."""

    def test_control_mapping_yaml_reachable_via_importlib_resources(self) -> None:
        """``control-mapping.yaml`` must resolve through ``importlib.resources``.

        This is the wheel-safe path used by both
        ``cmd_ci_setup._resolve_control_mapping_yaml`` and
        ``cmd_evidence_oscal._default_control_map_path``. If
        ``pyproject.toml`` drops the ``roam.templates.audit_report``
        package-data entry, ``files(...)`` still returns a Traversable
        but ``is_file()`` returns ``False`` on the wheel install — this
        test fails the moment that regresses.
        """
        resource = files("roam.templates.audit_report") / "control-mapping.yaml"
        assert resource.is_file(), (
            "control-mapping.yaml is not reachable via "
            "importlib.resources.files('roam.templates.audit_report'). "
            "Check pyproject.toml [tool.setuptools.package-data] still "
            'includes "roam.templates.audit_report" = ["*.yaml", "*.yml"] '
            "(W554)."
        )

    def test_control_mapping_yaml_materialises_as_real_file(self) -> None:
        """``as_file`` must produce a real on-disk Path with parseable YAML.

        ``cmd_evidence_oscal.load_control_map`` calls ``Path.read_text``
        on the resolved path, so the resource must materialise as a
        readable file (not just exist as a Traversable handle).
        """
        resource = files("roam.templates.audit_report") / "control-mapping.yaml"
        with as_file(resource) as path:
            assert path.exists(), f"as_file did not materialise resource at {path}"
            text = path.read_text(encoding="utf-8")
            # Cheap shape sanity — the YAML must contain the top-level
            # 'controls:' key (matches W554's existing test). We don't
            # parse with PyYAML here to keep this test importable even
            # under the minimal install matrix.
            assert "controls:" in text, (
                "control-mapping.yaml resolved but does not contain the "
                "expected top-level 'controls:' key — wheel may have "
                "shipped a stale or empty copy."
            )


class TestCiTemplatesPackageDataDriftW570:
    """W570 drift-guard for ``roam.templates.ci`` (W471 + earlier).

    ``cmd_ci_setup._templates_dir()`` uses a filesystem walk
    (``Path(__file__).parent.parent / 'templates' / 'ci'``) which only
    finds the templates inside an installed wheel if
    ``"roam.templates.ci" = ["*"]`` ships them. The wheel-safe path
    here is the same ``importlib.resources`` shape — if the package
    data entry is dropped, the source-side filesystem walk would still
    succeed during development but every pip-install user would see
    ``roam ci-setup --platform <X>`` fail with "Template file not
    found" on the first non-GitHub platform.
    """

    def test_slsa_src_l3_template_reachable(self) -> None:
        """``slsa-src-l3.yml`` (W471) must ship in the wheel."""
        resource = files("roam.templates.ci") / "slsa-src-l3.yml"
        assert resource.is_file(), (
            "slsa-src-l3.yml is not reachable via importlib.resources — "
            "pyproject.toml [tool.setuptools.package-data] may have dropped "
            '"roam.templates.ci" = ["*"] (W471).'
        )

    def test_non_github_ci_templates_reachable(self) -> None:
        """The non-GitHub platform templates (GitLab, Bitbucket, Azure,
        Jenkins) must all ship.

        These are the ``_PLATFORMS`` entries in ``cmd_ci_setup.py``;
        each one fails ``roam ci-setup --platform <X>`` with a
        packaging-error message if it's missing from the wheel.
        """
        expected_templates = (
            "gitlab-ci.yml",
            "bitbucket-pipelines.yml",
            "azure-pipelines.yml",
            "Jenkinsfile",
            "agent-review.yml",
        )
        missing = [name for name in expected_templates if not (files("roam.templates.ci") / name).is_file()]
        assert not missing, (
            f"CI templates not reachable via importlib.resources: {missing}. "
            f'Check pyproject.toml ships "roam.templates.ci" = ["*"].'
        )


class TestTaintRulesPackageDataDriftW610:
    """W610 drift-guard for ``roam.security.taint_rules`` (v12.12.1).

    Pre-12.12.1 the wheel shipped the package code but NOT the YAML rule
    files — ``cmd_taint`` silently loaded zero rules on every
    ``pip install`` user. v12.12.1 fixed packaging with
    ``"roam.security.taint_rules" = ["*.yaml", "*.yml"]``; this guard
    pins that contract by forcing the resolution through the same
    ``importlib.resources`` path that survives a wheel install.

    A representative sample of rule files is asserted rather than the
    full set: the contract is "the directory is shipped as package data"
    and a sample proves that. Listing every file would force test churn
    on every new rule, which dilutes the drift-guard intent.
    """

    def test_python_ssti_yaml_reachable_via_importlib(self) -> None:
        """``python_ssti.yaml`` (W373/W374/W375 family) must ship."""
        resource = files("roam.security.taint_rules") / "python_ssti.yaml"
        assert resource.is_file(), (
            "python_ssti.yaml is not reachable via "
            "importlib.resources.files('roam.security.taint_rules'). "
            "Check pyproject.toml [tool.setuptools.package-data] still "
            'includes "roam.security.taint_rules" = ["*.yaml", "*.yml"] '
            "(v12.12.1 / W610)."
        )

    def test_taint_rule_sample_set_reachable(self) -> None:
        """A representative cross-language sample must all resolve.

        Covers Python (sqli + deserialization), Java (sqli +
        deserialization), JS (xss + ssrf), PHP (command_injection),
        Vue (v_html). If any one fails the package-data glob has been
        dropped, narrowed, or the directory layout has shifted under
        a wheel install.
        """
        expected_rules = (
            "python_sqli.yaml",
            "python_deserialization.yaml",
            "java_sqli.yaml",
            "java_deserialization.yaml",
            "js_xss.yaml",
            "js_ssrf.yaml",
            "php_command_injection.yaml",
            "vue_v_html.yaml",
        )
        missing = [name for name in expected_rules if not (files("roam.security.taint_rules") / name).is_file()]
        assert not missing, (
            f"Taint rule YAMLs not reachable via importlib.resources: "
            f"{missing}. Check pyproject.toml ships "
            f'"roam.security.taint_rules" = ["*.yaml", "*.yml"]. '
            f"This is the v12.12.1 silent-empty class — pre-fix, "
            f"``roam taint`` loaded zero rules under a pip install."
        )

    def test_taint_rule_materialises_with_content(self) -> None:
        """``as_file`` must produce a real file with parseable rule content.

        A stale or truncated wheel could ship a zero-byte YAML that
        ``is_file()`` accepts but the loader rejects with a confusing
        empty-rule error. Match on the well-known ``id:`` top-level
        key to keep the test importable without PyYAML.
        """
        resource = files("roam.security.taint_rules") / "python_ssti.yaml"
        with as_file(resource) as path:
            assert path.exists(), f"as_file did not materialise taint rule at {path}"
            text = path.read_text(encoding="utf-8")
            assert "id:" in text, (
                "python_ssti.yaml resolved but does not contain the "
                "expected top-level 'id:' key — wheel may have shipped "
                "an empty or corrupted copy."
            )


class TestLanguageExtractorsPackageDataDriftW610:
    """W610 drift-guard for ``roam.languages.extractors`` (v12.12.1).

    Same silent-empty class as the taint rules. Only ``kotlin.yaml``
    ships today, so the guard pins that single file — adding more
    YAMLs here will need the test extended, which is exactly the right
    failure mode (a brand new extractor going dark in the wheel would
    be invisible without this guard).
    """

    def test_kotlin_extractor_yaml_reachable_via_importlib(self) -> None:
        """``kotlin.yaml`` must ship under
        ``"roam.languages.extractors" = ["*.yaml", "*.yml"]``."""
        resource = files("roam.languages.extractors") / "kotlin.yaml"
        assert resource.is_file(), (
            "kotlin.yaml is not reachable via "
            "importlib.resources.files('roam.languages.extractors'). "
            "Check pyproject.toml [tool.setuptools.package-data] still "
            'includes "roam.languages.extractors" = ["*.yaml", "*.yml"] '
            "(v12.12.1 / W610)."
        )


class TestRootPackageDataDriftW610:
    """W610 drift-guard for ``roam`` root package (v12.12.2 / W624).

    ``mcp-server-card.json`` lives at ``src/roam/mcp-server-card.json``
    and is resolved at runtime by ``roam mcp --card`` via
    ``importlib.resources.files("roam") / "mcp-server-card.json"``
    wrapped in ``as_file()`` (W624 migrated this from the legacy
    ``Path(__file__).parent / ...`` filesystem walk, mirroring the
    W554/W570/W577 importlib.resources discipline). Without the
    ``"roam" = ["mcp-server-card.json"]`` package-data entry, the wheel
    omits the card and ``roam mcp --card`` returns nothing on every pip
    install — the v12.12.2 silent-break this drift-guard catches.
    """

    def test_mcp_server_card_reachable_via_importlib(self) -> None:
        """``mcp-server-card.json`` must ship inside the wheel."""
        resource = files("roam") / "mcp-server-card.json"
        assert resource.is_file(), (
            "mcp-server-card.json is not reachable via "
            "importlib.resources.files('roam'). Check pyproject.toml "
            "[tool.setuptools.package-data] still includes "
            '"roam" = ["mcp-server-card.json"] (v12.12.2 / W610). '
            "Loader: src/roam/mcp_server.py resolves the card via "
            "importlib.resources.files('roam') / 'mcp-server-card.json' "
            "(W624) — the wheel must ship the file for that resolver "
            "to find it."
        )

    def test_mcp_server_card_materialises_with_json_shape(self) -> None:
        """``as_file`` must produce a real file with the expected shape.

        Cheap structural check: an MCP server card is JSON containing
        the well-known ``"name"`` key at the top level. Catches the
        empty-file class without bringing in a JSON parser dependency
        on the import path.
        """
        resource = files("roam") / "mcp-server-card.json"
        with as_file(resource) as path:
            assert path.exists(), f"as_file did not materialise server card at {path}"
            text = path.read_text(encoding="utf-8")
            assert '"name"' in text, (
                "mcp-server-card.json resolved but does not contain a "
                "top-level 'name' key — wheel may have shipped an "
                "empty or stale copy."
            )
