import contextlib
import io
import unittest

import main


class UnixTimeTests(unittest.TestCase):
    def test_unix_epoch_to_utc(self):
        self.assertEqual(main.unix_to_utc("0"), "1970-01-01T00:00:00Z")

    def test_unix_timestamp_to_utc(self):
        self.assertEqual(main.unix_to_utc("1710000000"), "2024-03-09T16:00:00Z")

    def test_fractional_and_negative_unix_timestamp(self):
        self.assertEqual(main.unix_to_utc("-0.5"), "1969-12-31T23:59:59.500000Z")

    def test_utc_to_unix_epoch(self):
        self.assertEqual(main.utc_to_unix("1970-01-01T00:00:00Z"), "0")

    def test_utc_to_integer_unix_timestamp_keeps_trailing_zeroes(self):
        self.assertEqual(main.utc_to_unix("2024-03-09T16:00:00Z"), "1710000000")

    def test_utc_with_space_and_fraction_to_unix(self):
        self.assertEqual(
            main.utc_to_unix("1969-12-31 23:59:59.500000 UTC"), "-0.5"
        )

    def test_rejects_naive_or_non_utc_date_time(self):
        for value in ("2024-01-01T00:00:00", "2024-01-01T01:00:00+01:00"):
            with self.subTest(value=value), self.assertRaises(main.ConversionError):
                main.utc_to_unix(value)

    def test_wingman_single_argument_request(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = main.main(["to-utc 0"])
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "1970-01-01T00:00:00Z\n")

    def test_invalid_request_returns_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main.main(["to-utc", "not-a-number"])
        self.assertEqual(result, 2)
        self.assertIn("invalid Unix timestamp", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
