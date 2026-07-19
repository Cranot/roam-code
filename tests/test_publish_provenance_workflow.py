from __future__ import annotations

import re
import shutil
import subprocess
import textwrap

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
LOCK = ROOT / ".github" / "release-tools.lock"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job(text: str, name: str, next_name: str | None) -> str:
    start = text.index(f"  {name}:\n")
    end = len(text) if next_name is None else text.index(f"  {next_name}:\n", start)
    return text[start:end]


def _literal_run_blocks(text: str) -> list[tuple[int, str]]:
    """Extract YAML literal ``run: |`` bodies without executing the workflow."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(?P<indent> *)run: \|[-+]?", line)
        if match is None:
            continue
        parent_indent = len(match.group("indent"))
        body: list[str] = []
        for candidate in lines[index + 1 :]:
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if candidate.strip() and candidate_indent <= parent_indent:
                break
            body.append(candidate)
        blocks.append((index + 1, textwrap.dedent("\n".join(body)) + "\n"))
    return blocks


def test_release_builds_once_and_has_one_canonical_trigger() -> None:
    text = _text()
    trigger_block = text[text.index("on:\n") : text.index("concurrency:\n")]
    assert text.count("python -m build --no-isolation --outdir dist") == 1
    assert text.count("scripts/release_artifacts.py canonicalize-sdist") == 1
    assert text.index("scripts/release_artifacts.py canonicalize-sdist") < text.index("python -m twine check --strict")
    assert text.count("pypa/gh-action-pypi-publish@") == 1
    assert "\n  release:\n" not in trigger_block
    assert "types: [published]" not in trigger_block
    assert 'tags:\n      - "v*"' in trigger_block


def test_partial_pypi_recovery_accepts_only_an_exact_manifest_subset() -> None:
    text = _text()
    recover = _job(text, "recover", "publish")
    publish = _job(text, "publish", "sign-evidence")
    assert "Materialize an exact-subset PyPI recovery set" in recover
    assert "scripts/release_artifacts.py recover" in recover
    assert '--http-status "$status"' in recover
    assert "--output-dir release-bundle/publish-dist" in recover
    assert "roam.pypi-recovery/v1" in recover
    assert "200|404" in recover
    assert "publish_required == ((.missing | length) > 0)" in recover
    assert "Upload the immutable missing-file subset" in recover
    assert "if: steps.recovery.outputs.publish_required == 'true'" in recover
    assert "artifact-ids: ${{ needs.recover.outputs.artifact_id }}" in publish
    assert "if: needs.recover.outputs.publish_required == 'true'" in publish
    assert "packages-dir: publish-subset/" in publish
    assert "Manifest anchor changed before PyPI publication" in publish
    assert "Revalidated every missing distribution immediately before PyPI publication" in publish
    assert "skip-existing: false" in publish
    assert "skip-existing: true" not in text
    assert "|| true" not in text


def test_every_remote_download_has_a_hard_size_and_time_ceiling() -> None:
    text = _text()
    assert text.count("curl \\") == 5
    assert text.count("--max-filesize 16777216") == 4
    assert text.count("--max-filesize 104857600") == 1
    assert text.count("--connect-timeout 10") == 5
    assert text.count("--max-time 90") == 1


def test_publish_consumes_only_manifest_verified_bundle() -> None:
    text = _text()
    build = _job(text, "build", "recover")
    recover = _job(text, "recover", "publish")
    publish = _job(text, "publish", "sign-evidence")
    assert "Upload one immutable release bundle" in build
    assert "dist/\n            sbom/\n            release/release-manifest.json" in build
    assert "if-no-files-found: error" in build
    assert "artifact-ids: ${{ needs.build.outputs.artifact_id }}" in publish
    assert "artifact-ids: ${{ needs.build.outputs.artifact_id }}" in recover
    assert "artifact-ids: ${{ needs.recover.outputs.artifact_id }}" in publish
    assert "SUBSET_ARTIFACT_SHA256" in publish
    assert "EXPECTED_MANIFEST_SHA256" in publish
    assert "Downloaded dist/ contains a missing or unmanifested entry" in publish
    assert "packages-dir: publish-subset/" in publish
    assert publish.index("Verify exact bundle") < publish.index("gh-action-pypi-publish@")


def test_wheel_and_sdist_get_exact_pep740_subject_checks() -> None:
    text = _text()
    signing = _job(text, "sign-evidence", "release-evidence")
    assert "attestations: true" in text
    assert "https://docs.pypi.org/attestations/publish/v1" in text
    assert "https://pypi.org/integrity/roam-code/" in signing
    assert '$statement.subject == [{"name": $filename, "digest": {"sha256": $digest}}]' in signing
    assert '.publisher.workflow == "publish.yml"' in signing
    assert '.publisher.environment == "pypi"' in signing
    assert "Verified exact PyPI wheel/sdist digests and PEP 740 subjects" in signing


def test_pypi_provenance_is_cryptographically_verified_after_strict_json_checks() -> None:
    signing = _job(_text(), "sign-evidence", "release-evidence")
    crypto_name = "Cryptographically verify both PyPI distributions"
    crypto_start = signing.index(crypto_name)
    crypto_end = signing.index("- name: Install cosign", crypto_start)
    crypto = signing[crypto_start:crypto_end]

    assert signing.index("Verify PyPI artifact digests and PEP 740 subjects") < crypto_start
    assert crypto_start < signing.index("- name: Install cosign")
    assert "pypi-attestations verify pypi \\" in crypto
    assert '--repository "$repository_url"' in crypto
    assert '--provenance-file "$provenance"' in crypto
    assert '"$published"; then' in crypto
    assert 'distribution="release-bundle/dist/$filename"' in crypto
    assert 'provenance="$PYPI_INTEGRITY_DIR/${filename}.provenance.json"' in crypto
    assert ".artifacts[] | [.kind, .filename, .sha256, (.size | tostring)]" in crypto
    assert 'sha256sum "$distribution"' in crypto
    assert 'sha256sum "$published"' in crypto
    assert 'url.netloc != "files.pythonhosted.org"' in crypto
    assert 'url.scheme != "https"' in crypto
    assert 'cmp --silent "$distribution" "$published"' in crypto
    assert "Published PyPI artifact differs from the release manifest" in crypto
    assert '"$wheel_verified" -ne 1 || "$sdist_verified" -ne 1' in crypto
    assert "timeout --foreground --signal=TERM --kill-after=5s 90s" in crypto
    assert "Cryptographically verified the exact PyPI wheel and sdist" in crypto

    assert "unset ACTIONS_ID_TOKEN_REQUEST_TOKEN ACTIONS_ID_TOKEN_REQUEST_URL" in crypto
    assert "unset GH_TOKEN GITHUB_TOKEN PYPI_API_TOKEN SIGSTORE_ID_TOKEN TWINE_PASSWORD TWINE_USERNAME" in crypto
    assert '"$(stat -c \'%a\' "$PYPI_INTEGRITY_DIR")" != 700' in crypto
    assert "secrets." not in crypto
    assert "${{ secrets." not in crypto

    strict_checks = signing[:crypto_start]
    assert 'mktemp -d "${RUNNER_TEMP}/roam-pypi-integrity.XXXXXXXXXX"' in strict_checks
    assert "printf 'PYPI_INTEGRITY_DIR=%s\\n'" in strict_checks
    assert 'provenance_json="$integrity_dir/${filename}.provenance.json"' in strict_checks


def test_pypi_provenance_verifier_uses_the_hash_locked_exact_toolchain() -> None:
    signing = _job(_text(), "sign-evidence", "release-evidence")
    install_start = signing.index("Install the hash-locked PyPI provenance verifier")
    install_end = signing.index("- name: Download the published bundle", install_start)
    install = signing[install_start:install_end]

    assert 'python-version: "3.12.13"' in signing[:install_start]
    assert "lock=.github/release-tools.lock" in install
    assert install.count("--require-hashes") == 2
    assert install.count("--only-binary=:all:") == 2
    assert "--no-index" in install
    assert '--find-links "$wheelhouse"' in install
    assert "--timeout 15" in install
    assert "--retries 2" in install
    assert "timeout --foreground --signal=TERM --kill-after=10s 240s" in install
    assert "timeout --foreground --signal=TERM --kill-after=10s 180s" in install
    assert '"$verifierenv/bin/python" -m pip check' in install
    assert '"pypi-attestations 0.0.29"' in install
    assert "unset ACTIONS_ID_TOKEN_REQUEST_TOKEN ACTIONS_ID_TOKEN_REQUEST_URL" in install
    assert 'mktemp -d "${RUNNER_TEMP}/roam-pypi-verifier.XXXXXXXXXX"' in install
    assert '"$(stat -c \'%a\' "$toolroot")" != 700' in install
    assert "timeout-minutes: 30" in signing


def test_sbom_and_release_manifest_are_digest_bound_and_verified() -> None:
    text = _text()
    for field in (
        "roam:release_wheel_sha256",
        "roam:release_wheel_provenance",
        "roam:release_sdist_sha256",
        "roam:release_sdist_provenance",
    ):
        assert field in text
    assert "cosign verify-blob" in text
    assert "Verified all six immutable release-evidence assets byte-for-byte" in text


def test_oidc_is_granted_only_to_publish_and_sign_evidence() -> None:
    text = _text()
    global_scope = text[: text.index("jobs:\n")]
    resolve = _job(text, "resolve-ref", "build")
    build = _job(text, "build", "recover")
    recover = _job(text, "recover", "publish")
    publish = _job(text, "publish", "sign-evidence")
    signing = _job(text, "sign-evidence", "release-evidence")
    evidence = _job(text, "release-evidence", "smoke")
    smoke = _job(text, "smoke", None)
    assert "id-token" not in global_scope
    assert "id-token" not in resolve
    assert "id-token" not in build
    assert "id-token" not in recover
    assert "id-token: write" in publish
    assert "id-token: write" in signing
    assert "id-token" not in evidence
    assert "id-token" not in smoke
    assert text.count("id-token: write") == 2


def test_dispatch_input_is_env_mapped_and_strictly_allowlisted() -> None:
    text = _text()
    assert "github.event.inputs" not in text
    assert text.count("${{ inputs.tag }}") == 1
    assert "DISPATCH_TAG: ${{ inputs.tag }}" in text
    assert "tag_pattern='^v[0-9]+" in text
    assert '[[ ! "$tag" =~ $tag_pattern ]]' in text
    assert "group: publish-roam-code" in text
    assert "group: publish-${{" not in text


def test_every_publication_boundary_re_resolves_the_tag_commit() -> None:
    text = _text()
    publish = _job(text, "publish", "sign-evidence")
    signing = _job(text, "sign-evidence", "release-evidence")
    evidence = _job(text, "release-evidence", "smoke")
    assert "Re-resolve tag immediately before PyPI publication" in publish
    assert publish.index("Re-resolve tag immediately before PyPI publication") < publish.index(
        "gh-action-pypi-publish@"
    )
    assert "Re-resolve, sign, and verify each transparency-log subject" in signing
    signing_step = signing[signing.index("Re-resolve, sign, and verify each transparency-log subject") :]
    assert (
        signing_step.index("verify_evidence_anchor")
        < signing_step.index("resolve_expected_tag")
        < signing_step.index("cosign sign-blob")
    )
    release_upload = evidence[evidence.index("Verify, attach to a draft, and publish exactly once") :]
    assert "openssl dgst -sha256 -verify" in release_upload
    publication = release_upload[release_upload.index("verify_evidence_set signed-evidence") :]
    assert publication.index("verify_evidence_set signed-evidence") < publication.index("resolve_expected_tag")
    assert "cosign sign-blob" not in release_upload
    assert release_upload.index("resolve_expected_tag") < release_upload.index('gh release create "$TAG"')
    upload_index = release_upload.index('gh release upload "$TAG"')
    assert release_upload[:upload_index].rindex("resolve_expected_tag") < upload_index
    assert release_upload.rindex("resolve_expected_tag") > release_upload.index('gh release edit "$TAG"')
    assert "Unexpected GitHub Release lookup status" in release_upload
    assert text.count('gh api "repos/${REPO}/git/ref/tags/${TAG}"') == 3
    assert text.count('if [[ "$object_type" != commit || "$actual_sha" != "$EXPECTED_SHA" ]]') == 3
    recover = _job(text, "recover", "publish")
    assert "Confirm recovery helper is from the resolved commit" in recover
    assert "release-source" not in publish
    assert "python " not in publish


def test_python_release_toolchain_is_exactly_versioned_and_hash_locked() -> None:
    text = _text()
    lock = LOCK.read_text(encoding="utf-8")
    assert text.count('python-version: "3.12.13"') == 4
    assert 'python-version: "3.12"' not in text
    assert "runs-on: ubuntu-latest" not in text
    assert text.count("runs-on: ubuntu-24.04") == 7
    assert "python -m build --no-isolation --outdir dist" in text
    assert "source_date_epoch=$(git show -s --format=%ct HEAD)" in text
    assert "SOURCE_DATE_EPOCH=%s" in text
    assert "PYTHONHASHSEED=0" in text
    assert "--output-reproducible" in text
    assert text.count("--require-hashes") == 4
    assert text.count("--only-binary=:all:") >= 5
    assert '--requirement "$lock"' in text
    assert "--no-index" in text
    assert "--find-links /tmp/release-wheelhouse" in text
    assert "--force-reinstall" in text
    assert "cosign-release: v2.5.2" in text
    assert "pip install --disable-pip-version-check build twine" not in text
    assert "pip install --quiet --disable-pip-version-check cyclonedx-bom" not in text

    header_pattern = re.compile(r"^([a-z0-9][a-z0-9._-]*)==(\S+) " + re.escape("\\"))
    headers = [match.groups() for line in lock.splitlines() if (match := header_pattern.fullmatch(line))]
    assert len(headers) == 85
    locked_names = {name for name, _version in headers}
    assert {
        "build",
        "click",
        "cyclonedx-bom",
        "networkx",
        "pip",
        "pypi-attestations",
        "setuptools",
        "sigstore",
        "tree-sitter",
        "tree-sitter-language-pack",
        "tuf",
        "twine",
        "wheel",
    } <= locked_names
    assert headers.count(("pypi-attestations", "0.0.29")) == 1
    assert "# pypi-attestations==0.0.29" in lock
    assert "# Resolution cutoff: 2026-07-17T00:00:00Z" in lock
    assert "x86_64-manylinux_2_34" in lock
    blocks = re.split(r"(?m)(?=^[a-z0-9][a-z0-9._-]*==)", lock)
    package_blocks = [block for block in blocks if re.match(r"^[a-z0-9][a-z0-9._-]*==", block)]
    assert len(package_blocks) == len(headers)
    for block in package_blocks:
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", block)
        assert hashes, block.splitlines()[0]
    assert "http://" not in lock and "https://" not in lock
    assert not any(line.startswith(("--index-url", "--extra-index-url")) for line in lock.splitlines())


def test_all_actions_are_immutable_sha_pinned() -> None:
    uses = [line.strip() for line in _text().splitlines() if line.strip().startswith("uses:")]
    assert uses
    for line in uses:
        assert re.fullmatch(r"uses: [^@\s]+@[0-9a-f]{40}(?: # .+)?", line), line


def test_every_literal_shell_step_parses_as_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    blocks = _literal_run_blocks(_text())
    assert blocks
    for line_number, script in blocks:
        result = subprocess.run(
            [bash, "-n"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=20,
            check=False,
        )
        error = result.stderr.decode("utf-8", "replace")
        assert result.returncode == 0, f"run block at publish.yml:{line_number}: {error}"
