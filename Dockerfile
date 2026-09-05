# python:3.12-slim-trixie rather than -alpine because tree-sitter and
# tree-sitter-language-pack ship glibc-only manylinux wheels for several
# language packs; alpine's musl forces source builds and inflates the
# image with toolchain dependencies. Use the current stable Debian family
# and upgrade inherited OS packages when building a new release.
FROM python:3.12-slim-trixie@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# OCI image labels — visible in registries, support tooling, scanners.
LABEL org.opencontainers.image.title="roam-code" \
      org.opencontainers.image.description="Architectural sight for AI coding agents — local code graph, MCP server, 28 languages." \
      org.opencontainers.image.source="https://github.com/Cranot/roam-code" \
      org.opencontainers.image.documentation="https://roam-code.com/docs/" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Cranot"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_PROGRESS=1 \
    ROAM_TREE_SITTER_CACHE_DIR=/opt/roam/parsers

# git for repo discovery + tree-sitter native deps. ca-certificates so
# external HTTPS checks (--check-external in stale-refs) work.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install --no-install-recommends -y git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/roam
COPY pyproject.toml uv.lock README.md LICENSE /opt/roam/
COPY src /opt/roam/src

# Match the reviewed CI uv version; materialize runtime/MCP dependencies from
# the repository lock, not whatever a fresh unconstrained pip solve picks today.
RUN pip install 'uv==0.11.29' \
 && uv sync --locked --no-default-groups --extra mcp --no-editable \
 && .venv/bin/roam --version \
 && pip uninstall -y pip uv \
 && rm -rf /root/.cache/uv

# Bootstrap installers and their vendored/build dependencies are not runtime
# requirements. Remove them in the same layer; the installed venv is retained.

ENV PATH="/opt/roam/.venv/bin:${PATH}"

# Acquire every production grammar during the build, then seal the root-owned
# cache. Changing HOME or running without networking must not trigger downloads.
RUN python -c 'from roam.parser_pack import SEALED_PRODUCTION_GRAMMARS, get_parser; [get_parser(name) for name in SEALED_PRODUCTION_GRAMMARS]'
ENV ROAM_TREE_SITTER_CACHE_SEALED=1

# Non-root for defense in depth; a small default home so the cache dir
# the agent might create lands somewhere predictable.
RUN groupadd --gid 1000 roam && useradd --uid 1000 --gid roam -m -d /home/roam roam \
 && mkdir /workspace && chown roam:roam /workspace
USER roam
WORKDIR /workspace

# Smoke check the entrypoint resolves at build time too — the post-pip
# `roam --version` above already does this, but keep the layer cache
# stable by separating the runtime-user assertion.
HEALTHCHECK --interval=30s --timeout=5s --start-period=2s --retries=2 \
    CMD roam --version || exit 1

ENTRYPOINT ["roam"]
CMD ["--help"]
