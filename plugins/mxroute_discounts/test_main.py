import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import main as mxroute


PAGE = """
<section id="plans">
  <div class="plan">
    <div class="name">SMALL</div>
    <div class="price"><b>$59</b>/year</div>
    <a class="cta" href="https://example.test/small">Order</a>
  </div>
  <div class="plan popular">
    <div class="name">MEDIUM</div>
    <div class="price"><s>$69</s> <b>$49</b>/year</div>
    <a class="cta" href="https://example.test/medium">Order</a>
  </div>
  <div class="plan">
    <div class="name">LARGE</div>
    <div class="price"><b>$79</b>/year</div>
    <a class="cta" href="https://example.test/large">Order</a>
  </div>
</section>
"""


class PlanTests(unittest.TestCase):
    def test_parses_plan_names_prices_and_links(self):
        plans = mxroute.parse_plans(PAGE)

        self.assertEqual(plans["small"]["price"], Decimal("59"))
        self.assertEqual(plans["medium"]["price"], Decimal("49"))
        self.assertEqual(plans["medium"]["url"], "https://example.test/medium")

    def test_discount_is_price_below_normal(self):
        plans = mxroute.parse_plans(PAGE)
        discounts = mxroute.find_discounts(
            plans,
            {"small": Decimal("59"), "medium": Decimal("69"), "large": Decimal("79")},
        )

        self.assertEqual([plan["name"] for plan, _normal in discounts], ["MEDIUM"])

    def test_missing_configured_plan_is_an_error(self):
        with self.assertRaisesRegex(mxroute.DiscountCheckError, "not found"):
            mxroute.find_discounts(
                mxroute.parse_plans(PAGE), {"enterprise": Decimal("100")}
            )

    def test_unrecognized_page_is_an_error(self):
        with self.assertRaisesRegex(mxroute.DiscountCheckError, "could be parsed"):
            mxroute.parse_plans("<html><body>Unavailable</body></html>")

    def test_formats_whole_prices_without_dropping_zeroes(self):
        self.assertEqual(mxroute.format_price(Decimal("100")), "100")

    def test_finds_promotion_keywords_in_visible_text(self):
        html = "<p>Special offer and SALE discount</p><script>sale</script>"

        self.assertEqual(
            mxroute.find_promotion_keywords(html),
            ["sale", "special", "offer", "discount"],
        )

    def test_ignores_promotion_keywords_in_scripts_and_styles(self):
        html = "<script>sale offer</script><style>.discount { color: red; }</style>"

        self.assertEqual(mxroute.find_promotion_keywords(html), [])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.toml"
        self.config_path.write_text(
            """[mxroute_plugin]\nurl = \"https://example.test/#plans\"\ntimeout_seconds = 5\n\n[normal_prices]\nsmall = 59\nmedium = 69\nlarge = 79\n""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_main(self, page):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("main.fetch_page", return_value=page):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = mxroute.main(self.config_path)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_discounts_is_silent(self):
        normal_page = PAGE.replace("<s>$69</s> <b>$49</b>", "<b>$69</b>")
        code, stdout, stderr = self.run_main(normal_page)

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_discount_prints_alert(self):
        code, stdout, stderr = self.run_main(PAGE)

        self.assertEqual(code, 0)
        self.assertIn("MEDIUM is $49/year (normal $69/year)", stdout)
        self.assertIn("https://example.test/medium", stdout)
        self.assertEqual(stderr, "")

    def test_promotion_keyword_prints_alert_at_normal_prices(self):
        normal_page = PAGE.replace("<s>$69</s> <b>$49</b>", "<b>$69</b>")
        page_with_offer = normal_page.replace(
            "<section id=\"plans\">", '<p>Summer special offer</p><section id="plans">'
        )
        code, stdout, stderr = self.run_main(page_with_offer)

        self.assertEqual(code, 0)
        self.assertIn("promotion keywords found: special, offer", stdout)
        self.assertEqual(stderr, "")


class RegistrationTests(unittest.TestCase):
    def test_plugin_registration(self):
        plugin_path = Path(__file__).with_name("plugin.json")
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))

        self.assertEqual(plugin["invocation_file"], "main.py")
        self.assertEqual(plugin["cron_time"], "0 8 * * *")
        self.assertTrue(plugin["adhoc"])
        self.assertTrue(plugin["cron"])


if __name__ == "__main__":
    unittest.main()
