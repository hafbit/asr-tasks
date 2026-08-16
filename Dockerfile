# syntax=docker/dockerfile:1.7

FROM --platform=linux/amd64 python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md requirements-model.txt ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade "pip==26.0.1" && \
    pip install . && \
    pip install -r requirements-model.txt

FROM builder AS model-downloader

ARG PARAFORMER_REVISION=v2.0.5
ARG VAD_REVISION=v2.0.4
ARG PUNCTUATION_REVISION=v2.0.4

RUN python -m asr_tasks.download_models \
    --destination /opt/asr-models \
    --paraformer-revision "$PARAFORMER_REVISION" \
    --vad-revision "$VAD_REVISION" \
    --punctuation-revision "$PUNCTUATION_REVISION"

FROM --platform=linux/amd64 python:3.11-slim-bookworm AS runtime-base

LABEL org.opencontainers.image.source="https://github.com/hafbit/asr-tasks" \
      org.opencontainers.image.title="asr-tasks"

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASR_DATA_DIR=/data \
    MODELSCOPE_CACHE=/data/models \
    HF_HOME=/data/models \
    TORCH_HOME=/data/models/torch

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl ffmpeg libsndfile1 tini && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 10001 asr && \
    useradd --uid 10001 --gid asr --create-home --shell /usr/sbin/nologin asr && \
    mkdir -p /app /data && chown -R asr:asr /app /data

COPY --from=builder /opt/venv /opt/venv
COPY --chown=asr:asr alembic.ini /app/alembic.ini
COPY --chown=asr:asr migrations /app/migrations

WORKDIR /app
USER asr
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "asr-tasks"]
CMD ["all"]

FROM runtime-base AS runtime

FROM runtime-base AS full

USER root
COPY --from=model-downloader --chown=asr:asr /opt/asr-models /opt/asr-models
USER asr

ENV ASR_MODEL_CACHE_DIR=/opt/asr-models \
    ASR_PARAFORMER_MODEL=/opt/asr-models/paraformer \
    ASR_VAD_MODEL=/opt/asr-models/vad \
    ASR_PUNCTUATION_MODEL=/opt/asr-models/punctuation \
    ASR_OFFLINE_MODE=true \
    MODELSCOPE_CACHE=/opt/asr-models \
    HF_HOME=/opt/asr-models \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_OFFLINE=1 \
    MODELSCOPE_OFFLINE=1
