# python:3.12-slim-bookworm rather than -alpine because tree-sitter and
# tree-sitter-language-pack ship glibc-only manylinux wheels for several
# language packs; alpine's musl forces source builds and inflates the
# image with toolchain dependencies. Slim-bookworm keeps the image small
# (~150 MB final) and lands wheels directly.
FROM python:3.12-slim-bookworm

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
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git for repo discovery + tree-sitter native deps. ca-certificates so
# external HTTPS checks (--check-external in stale-refs) work.
RUN apt-get update \
 && apt-get install --no-install-recommends -y git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/roam
COPY pyproject.toml README.md LICENSE /opt/roam/
COPY src /opt/roam/src

# Pin setuptools >= 77 explicitly so even an old base image emits the
# PEP 639 License-Expression metadata. Matches the build-system pin
# in pyproject.toml.
RUN pip install --upgrade 'pip>=24' 'setuptools>=77' \
 && pip install . \
 && roam --version

# Non-root for defense in depth; a small default home so the cache dir
# the agent might create lands somewhere predictable.
RUN groupadd -r roam && useradd -r -g roam -m -d /home/roam roam
USER roam
WORKDIR /workspace

# Smoke check the entrypoint resolves at build time too — the post-pip
# `roam --version` above already does this, but keep the layer cache
# stable by separating the runtime-user assertion.
HEALTHCHECK --interval=30s --timeout=5s --start-period=2s --retries=2 \
    CMD roam --version || exit 1

ENTRYPOINT ["roam"]
CMD ["--help"]
