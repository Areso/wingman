import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class DockerWrapperTests(unittest.TestCase):
    def test_same_directory_mount_and_argument_forwarding(self):
        plugin_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            input_pdf = root / "book with spaces.pdf"
            input_pdf.write_bytes(b"%PDF-test")
            capture = root / "docker-args"
            docker = bin_dir / "docker"
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
            docker.chmod(0o755)
            env = os.environ | {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "CAPTURE": str(capture),
            }

            subprocess.run(
                [str(plugin_dir / "run.sh"), str(input_pdf), "--title", "Test Book"],
                check=True,
                env=env,
            )
            args = capture.read_text().splitlines()

            self.assertIn("run", args)
            self.assertIn("--network", args)
            self.assertIn("none", args)
            self.assertIn(f"type=bind,source={root},target=/work", args)
            self.assertIn("/work/book with spaces.pdf", args)
            self.assertIn("/work/book with spaces.epub", args)
            self.assertEqual(args[-2:], ["--title", "Test Book"])


if __name__ == "__main__":
    unittest.main()
