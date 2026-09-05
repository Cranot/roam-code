# Roam containers

Official container publication is **on hold** pending image-wide security
review. No public image is promised by the next package release. Build locally
using the instructions below; the registry examples describe the intended
interface after publication is enabled.

The release workflow can publish the CLI **and MCP dependencies** to
`ghcr.io/cranot/roam-code` after the matching PyPI release and its evidence checks
pass, but only when the repository variable `ROAM_CONTAINER_PUBLISH` is exactly
`true`. An unset or false value skips container publication without skipping
package verification. The initial supported platform is `linux/amd64`; ARM
hosts need emulation or a local build. Do not assume a PyPI version has a
matching container tag.

```sh
docker pull ghcr.io/cranot/roam-code:latest
docker run --rm ghcr.io/cranot/roam-code:latest --version
```

Use a numeric release tag for a selected version, or `@sha256:<digest>` for
byte-stable deployments. `latest` follows the current stable GitHub release;
recovering an older release does not move it backward. Version tags are reused
only after checking their source revision and signature; they are not rebuilt
in place to pick up new dependencies. Candidate `build-*` tags are workflow
artifacts, not supported release aliases.

## Analyze a local repository

Roam must write its index under the mounted repository's `.roam/` directory.
On Linux, run with the repository owner's UID/GID so the index remains editable
on the host. The image itself defaults to non-root UID/GID 1000. Do not make
your entire repository world-writable to work around mount permissions.

```sh
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/workspace" ghcr.io/cranot/roam-code:latest index
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/workspace" ghcr.io/cranot/roam-code:latest health
```

PowerShell with Docker Desktop (Linux containers):

```powershell
docker run --rm -v "${PWD}:/workspace" ghcr.io/cranot/roam-code:latest index
docker run --rm -v "${PWD}:/workspace" ghcr.io/cranot/roam-code:latest health
```

For stdio MCP, keep stdin open and do not allocate a TTY:

```sh
docker run --rm -i --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/workspace" ghcr.io/cranot/roam-code:latest mcp --no-auto-index
```

Initialize separately. The container does not implicitly expose an HTTP port
or mount credentials. For local-only commands you can add `--network none`;
commands that intentionally fetch external data need networking. The image
preloads Roam's production grammars during the build and seals the root-owned
parser cache; changing the runtime HOME does not trigger a fresh download. Mount only
repositories the process is allowed to read/write. See [network boundaries](network-boundary.md)
and [MCP protocol compatibility](mcp-protocol-compatibility.md).

## Build locally

From a source checkout, the same Dockerfile remains available:

```sh
docker build -t roam-code .
docker run --rm roam-code --version
```

The glibc-based Python image is digest-pinned because language-parser wheels
are not reliably available on musl/Alpine. Runtime dependencies come from
`uv.lock`, including the MCP extra. The runtime uses Debian trixie and applies
available OS updates at build time. Bootstrap installers and their cache are
removed after installation; the image is a Roam runtime, not a package-building
environment. OS packages and build tooling still depend
on their configured package sources, so local builds are not claimed to be
byte-identical to a published image. The `.dockerignore` excludes private
`internal/`, `.roam/`, environment files and development artifacts. Keep other
private files out of a build context as well.

## Release evidence and maintenance

Keep `ROAM_CONTAINER_PUBLISH` unset or `false` while publication is held. Both
the release caller and the reusable container workflow enforce this opt-in.
Enable it only after reviewing the complete image scan and accepting the
remaining exposure. A skipped publication job means **not published**, not a
successful container release. The hold does not waive any package release gate.

The workflow checks the exact released source commit, builds an amd64 candidate
with SBOM and provenance attestations, tests indexing/search and MCP dependency
availability, signs the tested digest with Sigstore, and verifies that signature.
An anonymous pull must succeed before version/latest aliases are promoted.
The source label links the package back to this repository.

The first publication defaults to private on GHCR. A maintainer must change
the package's visibility to **public** in GitHub package settings and rerun the
failed container job. This is a real release check, not a reason to skip anonymous
pull verification. See [GitHub's container registry guidance](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

SBOM/provenance describe a build; they do not assert that it contains no known
vulnerabilities. Base-image and dependency updates require a new tested release.
Audit the complete image separately from the Python lock: inherited OS packages
and bootstrap tools are outside the lock's coverage. Preserve the full scanner
report, including unfixed advisories, and review package, architecture and actual
exposure before interpreting severity labels. A green dependency audit or a
successful offline smoke test is not a vulnerability-free image certificate.
There is no separate automatic security-rebuild cadence or multi-architecture
support promise. Keep the base digest reviewed alongside dependencies, preserve
release digests, and verify all release checks before announcing an image.
