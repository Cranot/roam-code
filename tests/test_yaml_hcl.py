"""Tests for YAML (CI pipelines) and HCL (Terraform) language extractors."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract(source: str, file_path: str):
    """Direct extractor call (no tree-sitter needed — regex-only languages)."""
    from roam.languages.registry import get_extractor
    lang = "yaml" if file_path.endswith((".yml", ".yaml")) else "hcl"
    extractor = get_extractor(lang)
    src = source.encode("utf-8")
    symbols = extractor.extract_symbols(None, src, file_path)
    refs = extractor.extract_references(None, src, file_path)
    return symbols, refs


def _names(symbols):
    return [s["name"] for s in symbols]


def _kinds(symbols):
    return {s["name"]: s["kind"] for s in symbols}


def _ref_targets(refs):
    return [r["target_name"] for r in refs]


# ===========================================================================
# YAML — GitLab CI
# ===========================================================================

GITLAB_CI = """\
stages:
  - build
  - test
  - deploy

variables:
  IMAGE: alpine

.base_job:
  image: $IMAGE
  tags:
    - docker

build:
  extends: .base_job
  stage: build
  script:
    - make build

test:unit:
  stage: test
  needs: [build]
  script:
    - make test

.deploy_template:
  extends: [.base_job]
  when: manual

deploy:prod:
  extends: .deploy_template
  stage: deploy
  needs:
    - test:unit
"""


class TestGitlabCI:
    def test_jobs_extracted(self):
        syms, _ = _extract(GITLAB_CI, ".gitlab-ci.yml")
        names = _names(syms)
        assert "build" in names
        assert "test:unit" in names
        assert "deploy:prod" in names

    def test_templates_are_class(self):
        syms, _ = _extract(GITLAB_CI, ".gitlab-ci.yml")
        k = _kinds(syms)
        assert k[".base_job"] == "class"
        assert k[".deploy_template"] == "class"

    def test_jobs_are_function(self):
        syms, _ = _extract(GITLAB_CI, ".gitlab-ci.yml")
        k = _kinds(syms)
        assert k["build"] == "function"
        assert k["test:unit"] == "function"
        assert k["deploy:prod"] == "function"

    def test_stages_are_constant(self):
        syms, _ = _extract(GITLAB_CI, ".gitlab-ci.yml")
        k = _kinds(syms)
        assert k["build"] == "function"  # job, not stage constant collision
        stage_syms = [s for s in syms if s["kind"] == "constant"]
        stage_names = [s["name"] for s in stage_syms]
        assert "test" in stage_names
        assert "deploy" in stage_names

    def test_reserved_keys_excluded(self):
        syms, _ = _extract(GITLAB_CI, ".gitlab-ci.yml")
        names = _names(syms)
        assert "variables" not in names
        assert "image" not in names
        assert "stages" not in names

    def test_extends_refs(self):
        _, refs = _extract(GITLAB_CI, ".gitlab-ci.yml")
        targets = _ref_targets(refs)
        assert ".base_job" in targets
        assert ".deploy_template" in targets

    def test_needs_refs(self):
        _, refs = _extract(GITLAB_CI, ".gitlab-ci.yml")
        targets = _ref_targets(refs)
        assert "build" in targets
        assert "test:unit" in targets

    def test_extends_list_ref(self):
        """extends: [.a, .b] → two inherits refs."""
        src = """\
stages:
  - test

.a:
  image: a

.b:
  image: b

myjob:
  extends: [.a, .b]
  stage: test
  script:
    - echo hi
"""
        _, refs = _extract(src, ".gitlab-ci.yml")
        targets = _ref_targets(refs)
        assert ".a" in targets
        assert ".b" in targets


# ===========================================================================
# YAML — GitHub Actions
# ===========================================================================

GITHUB_ACTIONS = """\
name: CI Pipeline

on:
  push:
    branches: [main]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make build

  test:
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/checkout@v4
      - run: make test

  call-reusable:
    uses: org/repo/.github/workflows/deploy.yml@main
    needs: [build, test]
"""


class TestGitHubActions:
    def test_workflow_name(self):
        syms, _ = _extract(GITHUB_ACTIONS, ".github/workflows/ci.yml")
        k = _kinds(syms)
        assert k.get("CI Pipeline") == "module"

    def test_jobs_extracted(self):
        syms, _ = _extract(GITHUB_ACTIONS, ".github/workflows/ci.yml")
        names = _names(syms)
        assert "build" in names
        assert "test" in names
        assert "call-reusable" in names

    def test_jobs_are_function(self):
        syms, _ = _extract(GITHUB_ACTIONS, ".github/workflows/ci.yml")
        k = _kinds(syms)
        assert k["build"] == "function"
        assert k["test"] == "function"

    def test_reusable_workflow_is_class(self):
        syms, _ = _extract(GITHUB_ACTIONS, ".github/workflows/ci.yml")
        k = _kinds(syms)
        assert k.get("deploy") == "class"

    def test_needs_refs(self):
        _, refs = _extract(GITHUB_ACTIONS, ".github/workflows/ci.yml")
        targets = _ref_targets(refs)
        assert "build" in targets

    def test_uses_ref(self):
        _, refs = _extract(GITHUB_ACTIONS, ".github/workflows/ci.yml")
        targets = _ref_targets(refs)
        assert any("deploy.yml" in t or "deploy" in t for t in targets)


# ===========================================================================
# YAML — Generic fallback
# ===========================================================================

GENERIC_YAML = """\
database:
  host: localhost
  port: 5432

app:
  name: myapp
  debug: false
"""


class TestGenericYaml:
    def test_top_level_keys(self):
        syms, _ = _extract(GENERIC_YAML, "config.yml")
        names = _names(syms)
        assert "database" in names
        assert "app" in names

    def test_top_level_kind_is_variable(self):
        syms, _ = _extract(GENERIC_YAML, "config.yml")
        k = _kinds(syms)
        assert k["database"] == "variable"
        assert k["app"] == "variable"

    def test_nested_keys_not_extracted(self):
        syms, _ = _extract(GENERIC_YAML, "config.yml")
        names = _names(syms)
        assert "host" not in names
        assert "name" not in names


# ===========================================================================
# HCL — Terraform
# ===========================================================================

TERRAFORM_MAIN = """\
terraform {
  required_version = ">= 1.0"
}

provider "aws" {
  region = var.region
}

variable "region" {
  default = "us-east-1"
}

variable "instance_type" {
  default = "t3.micro"
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "private" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
}

output "vpc_id" {
  value = aws_vpc.main.id
}

module "web" {
  source = "./modules/web"
  vpc_id = aws_vpc.main.id
}

locals {
  env_prefix = "prod"
  full_name  = "${local.env_prefix}-app"
}
"""


class TestTerraform:
    def test_resources_extracted(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        names = _names(syms)
        assert "main" in names
        assert "private" in names

    def test_resource_is_class(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["main"] == "class"
        assert k["private"] == "class"

    def test_resource_qualified_name(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        qnames = {s["name"]: s.get("qualified_name") for s in syms}
        assert qnames["main"] == "aws_vpc.main"
        assert qnames["private"] == "aws_subnet.private"

    def test_variables_extracted(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["region"] == "variable"
        assert k["instance_type"] == "variable"

    def test_variable_qualified_name(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        qnames = {s["name"]: s.get("qualified_name") for s in syms}
        assert qnames["region"] == "var.region"

    def test_output_is_function(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["vpc_id"] == "function"

    def test_module_is_module(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["web"] == "module"

    def test_data_source_is_class(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["ubuntu"] == "class"

    def test_data_qualified_name(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        qnames = {s["name"]: s.get("qualified_name") for s in syms}
        assert qnames["ubuntu"] == "data.aws_ami.ubuntu"

    def test_provider_is_module(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["aws"] == "module"

    def test_locals_extracted(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        names = _names(syms)
        assert "env_prefix" in names
        assert "full_name" in names

    def test_locals_are_variable(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k["env_prefix"] == "variable"
        assert k["full_name"] == "variable"

    def test_locals_qualified_name(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        qnames = {s["name"]: s.get("qualified_name") for s in syms}
        assert qnames["env_prefix"] == "local.env_prefix"

    def test_terraform_block_is_module(self):
        syms, _ = _extract(TERRAFORM_MAIN, "main.tf")
        k = _kinds(syms)
        assert k.get("terraform") == "module"

    def test_var_refs(self):
        _, refs = _extract(TERRAFORM_MAIN, "main.tf")
        targets = _ref_targets(refs)
        assert "region" in targets

    def test_resource_refs(self):
        _, refs = _extract(TERRAFORM_MAIN, "main.tf")
        targets = _ref_targets(refs)
        # aws_vpc.main.id → resource_name = "main"
        assert "main" in targets

    def test_module_refs(self):
        _, refs = _extract(TERRAFORM_MAIN, "main.tf")
        targets = _ref_targets(refs)
        assert "main" in targets  # aws_vpc.main.id in module block

    def test_local_refs(self):
        src = """\
locals {
  base = "hello"
}

output "greeting" {
  value = local.base
}
"""
        _, refs = _extract(src, "main.tf")
        targets = _ref_targets(refs)
        assert "base" in targets


class TestTfvars:
    def test_assignments_extracted(self):
        src = """\
region       = "us-east-1"
instance_type = "t3.micro"
enable_logging = true
"""
        syms, refs = _extract(src, "terraform.tfvars")
        names = _names(syms)
        assert "region" in names
        assert "instance_type" in names
        assert "enable_logging" in names
        assert refs == []

    def test_kinds_are_variable(self):
        src = 'env = "prod"\n'
        syms, _ = _extract(src, "vars.tfvars")
        assert syms[0]["kind"] == "variable"


# ===========================================================================
# Registry integration
# ===========================================================================

class TestRegistryIntegration:
    def test_yaml_in_supported_languages(self):
        from roam.languages.registry import get_supported_languages
        assert "yaml" in get_supported_languages()

    def test_hcl_in_supported_languages(self):
        from roam.languages.registry import get_supported_languages
        assert "hcl" in get_supported_languages()

    def test_yml_extension_detected(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("ci.yml") == "yaml"

    def test_yaml_extension_detected(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("config.yaml") == "yaml"

    def test_tf_extension_detected(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("main.tf") == "hcl"

    def test_hcl_extension_detected(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("nomad.hcl") == "hcl"

    def test_tfvars_extension_detected(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("vars.tfvars") == "hcl"

    def test_yaml_is_regex_only(self):
        from roam.index.parser import REGEX_ONLY_LANGUAGES
        assert "yaml" in REGEX_ONLY_LANGUAGES

    def test_hcl_is_regex_only(self):
        from roam.index.parser import REGEX_ONLY_LANGUAGES
        assert "hcl" in REGEX_ONLY_LANGUAGES

    def test_parse_file_yaml_returns_source(self):
        """parse_file() should return (None, source, 'yaml') for YAML files."""
        import tempfile, os
        from roam.index.parser import parse_file
        from pathlib import Path
        src = b"stages:\n  - test\n"
        with tempfile.NamedTemporaryFile(suffix=".yml", delete=False) as f:
            f.write(src)
            tmp = f.name
        try:
            tree, source, lang = parse_file(Path(tmp))
            assert tree is None
            assert source == src
            assert lang == "yaml"
        finally:
            os.unlink(tmp)

    def test_parse_file_hcl_returns_source(self):
        """parse_file() should return (None, source, 'hcl') for .tf files."""
        import tempfile, os
        from roam.index.parser import parse_file
        from pathlib import Path
        src = b'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\n'
        with tempfile.NamedTemporaryFile(suffix=".tf", delete=False) as f:
            f.write(src)
            tmp = f.name
        try:
            tree, source, lang = parse_file(Path(tmp))
            assert tree is None
            assert source == src
            assert lang == "hcl"
        finally:
            os.unlink(tmp)
