FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=4 \
    XDG_CACHE_HOME=/opt/docling-cache \
    HEFESTO_DOCLING_ARTIFACTS_PATH=/opt/docling-models \
    HEFESTO_DOCLING_DOCUMENT_TIMEOUT=600 \
    HEFESTO_DOCLING_MEDIA_TIMEOUT=3600 \
    HEFESTO_DOCLING_IMAGE_SCALE=2.0
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential ffmpeg python3-dev tesseract-ocr-all \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[document]'
RUN docling-tools models download layout tableformer \
    --output-dir /opt/docling-models \
    && python -c "import whisper; whisper.load_model('small')" \
    && apt-get purge --yes --auto-remove build-essential python3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY migrations ./migrations
USER 65532:65532
ENTRYPOINT ["comunicamundo-v8-worker"]
