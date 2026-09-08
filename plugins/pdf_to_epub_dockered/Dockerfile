FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Wingman PDF to EPUB"
LABEL org.opencontainers.image.source="https://github.com/Areso/wingman"

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/tmp \
    QT_QPA_PLATFORM=offscreen

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        calibre \
        ocrmypdf \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pdf_to_epub.py /app/pdf_to_epub.py

ENTRYPOINT ["python3", "/app/pdf_to_epub.py"]
