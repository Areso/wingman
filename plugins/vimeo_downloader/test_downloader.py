import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).with_name("vimeo_downloader.py")
spec = importlib.util.spec_from_file_location("downloader", MODULE_PATH)
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)

class DownloaderTests(unittest.TestCase):
    def test_url_defaults_to_video(self):
        mode, url = downloader.parse_request(["https://vimeo.com/example"])
        self.assertEqual(mode, "video")
        self.assertIn("vimeo.com", url)

    def test_audio_mode_from_wingman_single_argument(self):
        mode, url = downloader.parse_request(["audio https://vimeo.com/example"])
        self.assertEqual(mode, "audio")
        self.assertIn("vimeo.com", url)

    def test_rejects_other_hosts(self):
        with self.assertRaises(ValueError):
            downloader.validate_url("https://example.com/video")

    def test_accepts_expected_host(self):
        self.assertEqual(
            downloader.validate_url("https://vimeo.com/example"),
            "https://vimeo.com/example",
        )

    def test_rejects_credentials_in_url(self):
        with self.assertRaises(ValueError):
            downloader.validate_url("https://user:pass@vimeo.com/example")

if __name__ == "__main__":
    unittest.main()
