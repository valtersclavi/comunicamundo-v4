FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=4 \
    HEFESTO_DOCLING_ARTIFACTS_PATH=/opt/docling-models \
    HEFESTO_DOCLING_DOCUMENT_TIMEOUT=120 \
    HEFESTO_DOCLING_IMAGE_SCALE=2.0
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends tesseract-ocr-all \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[document]'
RUN docling-tools models download layout tableformer \
    --output-dir /opt/docling-models
COPY migrations ./migrations
USER 65532:65532
ENTRYPOINT ["comunicamundo-v4-worker"]
