#!/usr/bin/env python3
"""Wingman plugin: convert text or scanned PDFs to EPUB."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


class ConversionError(RuntimeError):
    pass


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"Required command is not installed: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise ConversionError(f"Command failed ({command[0]}){suffix}") from exc


def require_commands(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise ConversionError(
            "Missing dependencies: "
            + ", ".join(missing)
            + ". Run ./install_dependencies.sh (Debian/Ubuntu), or install equivalent packages."
        )


def page_count(pdf: Path) -> int:
    result = run(["pdfinfo", str(pdf)], capture=True)
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise ConversionError("Could not determine the PDF page count")


def has_usable_text(pdf: Path, sample_pages: int, chars_per_page: int) -> bool:
    pages = max(1, min(page_count(pdf), sample_pages))
    result = run(
        ["pdftotext", "-f", "1", "-l", str(pages), str(pdf), "-"], capture=True
    )
    visible = sum(not char.isspace() for char in result.stdout)
    return visible >= pages * chars_per_page


def validate_epub(epub: Path) -> None:
    if not epub.is_file() or epub.stat().st_size == 0:
        raise ConversionError("Calibre did not create a non-empty EPUB")
    if not zipfile.is_zipfile(epub):
        raise ConversionError("Output is not a valid EPUB ZIP container")
    with zipfile.ZipFile(epub) as archive:
        names = set(archive.namelist())
        if "mimetype" not in names or "META-INF/container.xml" not in names:
            raise ConversionError("Output is missing required EPUB container files")
        if archive.read("mimetype") != b"application/epub+zip":
            raise ConversionError("Output has an invalid EPUB mimetype")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a digital or scanned PDF into a validated EPUB."
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("output_epub", type=Path, nargs="?")
    parser.add_argument("--language", default="eng", help="Tesseract language(s), e.g. eng or eng+deu")
    parser.add_argument("--title", help="Override EPUB title")
    parser.add_argument("--author", help="Override EPUB author")
    parser.add_argument("--force-ocr", action="store_true", help="OCR every page before conversion")
    parser.add_argument("--no-ocr", action="store_true", help="Fail instead of OCRing a scanned PDF")
    parser.add_argument("--sample-pages", type=int, default=5)
    parser.add_argument("--chars-per-page", type=int, default=40)
    args = parser.parse_args(argv)
    if args.force_ocr and args.no_ocr:
        parser.error("--force-ocr and --no-ocr cannot be used together")
    if args.sample_pages < 1 or args.chars_per_page < 1:
        parser.error("text-detection thresholds must be positive")
    return args


def convert(args: argparse.Namespace) -> Path:
    source = args.input_pdf.expanduser().resolve()
    if not source.is_file():
        raise ConversionError(f"Input PDF not found: {source}")
    if source.suffix.lower() != ".pdf":
        raise ConversionError(f"Input must have a .pdf extension: {source}")

    output = (args.output_epub or source.with_suffix(".epub")).expanduser().resolve()
    if output.suffix.lower() != ".epub":
        raise ConversionError("Output path must have an .epub extension")
    if output == source:
        raise ConversionError("Input and output paths must differ")
    output.parent.mkdir(parents=True, exist_ok=True)

    require_commands(["pdfinfo", "pdftotext", "ebook-convert"])
    digital = has_usable_text(source, args.sample_pages, args.chars_per_page)
    needs_ocr = args.force_ocr or not digital
    if needs_ocr and args.no_ocr:
        raise ConversionError("PDF has too little extractable text; OCR is disabled")

    with tempfile.TemporaryDirectory(prefix="wingman-pdf-to-epub-") as temp_dir:
        conversion_source = source
        if needs_ocr:
            require_commands(["ocrmypdf", "tesseract"])
            conversion_source = Path(temp_dir) / "ocr.pdf"
            ocr_command = [
                "ocrmypdf",
                "--output-type", "pdf",
                "--rotate-pages",
                "--deskew",
                "--language", args.language,
            ]
            ocr_command.append("--force-ocr" if args.force_ocr else "--skip-text")
            ocr_command.extend([str(source), str(conversion_source)])
            run(ocr_command)

        temporary_epub = Path(temp_dir) / "output.epub"
        calibre_command = ["ebook-convert", str(conversion_source), str(temporary_epub)]
        if args.title:
            calibre_command.extend(["--title", args.title])
        if args.author:
            calibre_command.extend(["--authors", args.author])
        run(calibre_command)
        validate_epub(temporary_epub)
        os.replace(temporary_epub, output)

    return output


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        output = convert(args)
        print(f"Converted PDF to EPUB: {output}")
        return 0
    except ConversionError as exc:
        print(f"PDF to EPUB conversion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
