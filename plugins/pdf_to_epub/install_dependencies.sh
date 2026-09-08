#!/usr/bin/env bash
set -euo pipefail

case "$(uname -s)" in
  Darwin)
    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew is required on macOS. Install it from https://brew.sh and rerun this script." >&2
      exit 1
    fi

    brew update
    brew install --cask calibre
    brew install ocrmypdf poppler tesseract

    echo "PDF-to-EPUB dependencies installed. For additional OCR languages, run: brew install tesseract-lang"
    ;;
  Linux)
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "This Linux installer supports Debian/Ubuntu. Install Calibre, OCRmyPDF, Tesseract, and Poppler with your distribution's package manager." >&2
      exit 1
    fi

    sudo apt-get update
    sudo apt-get install -y calibre ocrmypdf poppler-utils tesseract-ocr

    echo "PDF-to-EPUB dependencies installed. Add Tesseract language packs as needed (for example, tesseract-ocr-deu)."
    ;;
  *)
    echo "Unsupported operating system: $(uname -s). Install Calibre, OCRmyPDF, Tesseract, and Poppler manually." >&2
    exit 1
    ;;
esac
