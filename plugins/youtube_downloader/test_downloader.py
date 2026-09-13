import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).with_name("youtube_downloader.py")
spec = importlib.util.spec_from_file_location("downloader", MODULE_PATH)
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)

class DownloaderTests(unittest.TestCase):
    def test_url_defaults_to_video(self):
        mode, url = downloader.parse_request(["https://youtu.be/example"])
        self.assertEqual(mode, "video")
        self.assertIn("youtu.be", url)

    def test_audio_mode_from_wingman_single_argument(self):
        mode, url = downloader.parse_request(["audio https://youtu.be/example"])
        self.assertEqual(mode, "audio")
        self.assertIn("youtu.be", url)

    def test_rejects_other_hosts(self):
        with self.assertRaises(ValueError):
            downloader.validate_url("https://example.com/video")

    def test_accepts_expected_host(self):
        self.assertEqual(
            downloader.validate_url("https://youtu.be/example"),
            "https://youtu.be/example",
        )

    def test_rejects_credentials_in_url(self):
        with self.assertRaises(ValueError):
            downloader.validate_url("https://user:pass@youtu.be/example")

    def test_video_format_falls_back_from_separate_to_combined_streams(self):
        selector = downloader.format_selector("video")
        self.assertTrue(selector.startswith("bestvideo*[ext=mp4]+bestaudio[ext=m4a]/"))
        self.assertTrue(selector.endswith("/best[ext=mp4]/best"))

    def test_audio_format_has_unrestricted_fallback(self):
        self.assertEqual(
            downloader.format_selector("audio"),
            "bestaudio[ext=m4a]/bestaudio/best",
        )

if __name__ == "__main__":
    unittest.main()
