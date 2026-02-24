FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apk add --no-cache git

WORKDIR /opt/roam
COPY pyproject.toml README.md /opt/roam/
COPY src /opt/roam/src

RUN pip install --upgrade pip && pip install .

RUN addgroup -S roam && adduser -S -G roam roam
USER roam
WORKDIR /workspace

ENTRYPOINT ["roam"]
CMD ["--help"]
