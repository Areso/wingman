# PDF to EPUB plugin for Wingman

Converts text-based and scanned PDFs to EPUB. It samples the PDF's extractable text first. Text PDFs go directly to Calibre; scanned PDFs are deskewed, auto-rotated, OCRed with OCRmyPDF/Tesseract, and then converted. The final EPUB container is validated before it replaces the destination.

## Install

From the Wingman repository root:

```bash
unzip wingman-pdf-to-epub.zip -d plugins/
cd plugins/wingman-pdf-to-epub
chmod +x pdf_to_epub.py install_dependencies.sh
./install_dependencies.sh
```

Restart Wingman so it reloads `plugin.json`. The plugin is owner-only because it reads and writes local filesystem paths.

## Usage

Pass the local PDF path as user input:

```text
/data/books/example.pdf
```

An optional second positional path controls the output:

```text
/data/books/example.pdf /data/exports/example.epub
```

Direct smoke tests:

```bash
python3 pdf_to_epub.py /data/books/text.pdf
python3 pdf_to_epub.py /data/books/scan.pdf /data/books/scan.epub --language eng
python3 pdf_to_epub.py /data/books/mixed.pdf --language eng+deu --title "My Book" --author "A. Writer"
```

Supported switches:

- `--language CODE`: Tesseract language or `+`-joined languages; default `eng`.
- `--title TITLE` and `--author AUTHOR`: override EPUB metadata.
- `--force-ocr`: rasterize and OCR every page.
- `--no-ocr`: fail if the PDF lacks usable text.
- `--sample-pages N` and `--chars-per-page N`: tune scan detection.

Paths containing spaces work when Wingman supplies parameters as a JSON argument array. The process inherits Wingman's filesystem permissions, so it can only read and write locations available to the Wingman service account.

## Dependencies

- Calibre (`ebook-convert`)
- OCRmyPDF and Tesseract
- Poppler (`pdfinfo`, `pdftotext`)

On Debian/Ubuntu, install extra OCR languages separately, such as `tesseract-ocr-deu` for German. On macOS, install the full additional language set with `brew install tesseract-lang`. Conversion quality depends on the PDF layout; complex multi-column pages may need Calibre-specific tuning after conversion.

## Security

The plugin invokes dependencies with argument arrays, never a shell, and does not use or print secrets. Treat PDFs as untrusted input and keep the conversion tools patched.
