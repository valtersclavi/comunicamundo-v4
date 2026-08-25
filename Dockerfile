# Pin the Aug 22 known-good worker base from Actions run 32602746777.
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY constraints/be5292d-run-32602746777.txt /tmp/constraints.txt
COPY src ./src

RUN pip install --no-cache-dir --constraint /tmp/constraints.txt .

USER 65532:65532

ENTRYPOINT ["comunicamundo-v4-worker"]
