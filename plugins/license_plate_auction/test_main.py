import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import main as auction


class AuctionPageTests(unittest.TestCase):
    def test_no_auction_is_unavailable(self):
        html = """
            <html><body>
                <span>There is no available\n auction series.</span>
            </body></html>
        """

        self.assertFalse(auction.parse_auction_page(html))

    def test_auction_message_is_available(self):
        html = """
            <html><body>
                <a href="/auction">The TTA auction series is now available</a>
            </body></html>
        """

        self.assertTrue(auction.parse_auction_page(html))

    def test_status_in_comment_does_not_count(self):
        html = """
            <html><body>
                <!-- The TTA auction series is now available -->
                Service temporarily unavailable
            </body></html>
        """

        with self.assertRaisesRegex(ValueError, "recognizable auction status"):
            auction.parse_auction_page(html)

    def test_frameset_is_rejected(self):
        html = """
            <html><frameset><frame src="TPRTDLogo.jsp?lang=en"></frameset></html>
        """

        with self.assertRaisesRegex(ValueError, "recognizable auction status"):
            auction.parse_auction_page(html)


class CommandTests(unittest.TestCase):
    def run_main(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = auction.main()
        return code, stdout.getvalue(), stderr.getvalue()

    @patch("main.check_auction", return_value=False)
    def test_no_auction_is_silent_success(self, _check):
        code, stdout, stderr = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    @patch("main.check_auction", return_value=True)
    def test_auction_prints_alert(self, _check):
        code, stdout, stderr = self.run_main()

        self.assertEqual(code, 0)
        self.assertIn("License plate auction available", stdout)
        self.assertIn(auction.DEFAULT_URL, stdout)
        self.assertEqual(stderr, "")

    @patch("main.check_auction", side_effect=URLError("offline"))
    def test_http_failure_returns_error(self, _check):
        code, stdout, stderr = self.run_main()

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("auction check failed", stderr)
        self.assertIn("offline", stderr)


class RegistrationTests(unittest.TestCase):
    def test_plugin_registration(self):
        plugin_path = Path(__file__).with_name("plugin.json")
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))

        self.assertEqual(plugin["invocation_file"], "main.py")
        self.assertEqual(plugin["cron_time"], "30 7 * * *")
        self.assertEqual(plugin["min_allowed_role"], "guest")
        self.assertTrue(plugin["adhoc"])
        self.assertTrue(plugin["cron"])


if __name__ == "__main__":
    unittest.main()
