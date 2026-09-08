import argparse
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pdf_to_epub


class PdfToEpubTests(unittest.TestCase):
    def test_parse_rejects_conflicting_ocr_flags(self):
        with self.assertRaises(SystemExit):
            pdf_to_epub.parse_args(["book.pdf", "--force-ocr", "--no-ocr"])

    def test_validate_accepts_minimal_epub_container(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "book.epub"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", "<container/>")
            pdf_to_epub.validate_epub(target)

    def test_digital_pdf_skips_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.pdf"
            source.write_bytes(b"%PDF-test")
            output = Path(directory) / "book.epub"

            def fake_run(command, capture=False):
                if command[0] == "ebook-convert":
                    with zipfile.ZipFile(command[2], "w") as archive:
                        archive.writestr("mimetype", "application/epub+zip")
                        archive.writestr("META-INF/container.xml", "<container/>")
                return argparse.Namespace(stdout="")

            args = pdf_to_epub.parse_args([str(source), str(output)])
            with patch.object(pdf_to_epub, "require_commands"), patch.object(
                pdf_to_epub, "has_usable_text", return_value=True
            ), patch.object(pdf_to_epub, "run", side_effect=fake_run) as runner:
                self.assertEqual(pdf_to_epub.convert(args), output.resolve())
            self.assertFalse(any(call.args[0][0] == "ocrmypdf" for call in runner.call_args_list))


if __name__ == "__main__":
    unittest.main()
