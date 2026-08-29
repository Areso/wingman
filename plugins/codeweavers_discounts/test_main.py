import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import main as codeweavers


PAGE = """
<div class="row pricing-table">
  <div class="col-lg-4 p-0"><div>CrossOver Free</div><div class="pricing-table-price">FREE</div></div>
  <div class="col-lg-4 pricing-table-middle p-0">
    <div><br>CrossOver + <span>(12 Months Support)</span><img src="popular.png"></div>
    <div class="pricing-table-price">
      <div class="os_Mac">$64.00 USD</div><div class="os_Linux">$64.00 USD</div>
    </div>
  </div>
  <div class="col-lg-4 p-0">
    <div>CrossOver Life <span>(Lifetime Support)</span></div>
    <div class="pricing-table-price">
      <div class="os_Mac">$474.00 USD</div><div class="os_Linux">$474.00 USD</div>
    </div>
  </div>
</div>
"""
NO_PROMOTIONS_PAGE = """
<h1>Currently Available Promotions</h1>
<p><b>Sorry</b>, there are no promotions currently active.</p>
"""


class PlanTests(unittest.TestCase):
    def test_parses_paid_plan_names_and_deduplicates_os_prices(self):
        plans = codeweavers.parse_plans(PAGE)

        self.assertEqual(plans["crossover +"]["price"], Decimal("64.00"))
        self.assertEqual(plans["crossover life"]["price"], Decimal("474.00"))

    def test_discount_is_price_below_normal(self):
        discounts = codeweavers.find_discounts(
            codeweavers.parse_plans(PAGE),
            {"crossover +": Decimal("74"), "crossover life": Decimal("474")},
        )

        self.assertEqual([plan["name"] for plan, _normal in discounts], ["CrossOver +"])

    def test_missing_configured_plan_is_an_error(self):
        with self.assertRaisesRegex(codeweavers.DiscountCheckError, "not found"):
            codeweavers.find_discounts(
                codeweavers.parse_plans(PAGE), {"missing": Decimal("100")}
            )

    def test_unrecognized_page_is_an_error(self):
        with self.assertRaisesRegex(codeweavers.DiscountCheckError, "could be parsed"):
            codeweavers.parse_plans("<html><body>Unavailable</body></html>")

    def test_finds_keywords_only_in_visible_text(self):
        html = "<p>Special offer and SALE discount</p><script>sale</script>"
        self.assertEqual(
            codeweavers.find_promotion_keywords(html),
            ["sale", "special", "offer", "discount"],
        )
        self.assertEqual(
            codeweavers.find_promotion_keywords(
                "<script>sale offer</script><style>.discount { color: red; }</style>"
            ),
            [],
        )

    def test_ignores_standard_special_renewal_pricing_copy(self):
        html = "<p>Special Renewal Pricing</p><p>Normal store content</p>"

        self.assertEqual(codeweavers.find_promotion_keywords(html), [])

    def test_still_finds_special_outside_ignored_phrase(self):
        html = "<p>Special Renewal Pricing</p><p>Summer special</p>"

        self.assertEqual(codeweavers.find_promotion_keywords(html), ["special"])

    def test_normal_promotions_page_has_no_active_promotions(self):
        self.assertFalse(codeweavers.has_active_promotions(NO_PROMOTIONS_PAGE))

    def test_missing_normal_message_means_promotions_are_active(self):
        html = "<h1>Currently Available Promotions</h1><p>Save 25% today.</p>"

        self.assertTrue(codeweavers.has_active_promotions(html))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.toml"
        self.config_path.write_text(
            """[codeweavers_plugin]\nurl = "https://example.test/store"\npromotions_url = "https://example.test/store/promotions"\ntimeout_seconds = 5\n\n[normal_prices]\nCrossOver + = 74\nCrossOver Life = 474\n""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_main(self, page, promotions_page=NO_PROMOTIONS_PAGE):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("main.fetch_page", side_effect=[page, promotions_page]):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = codeweavers.main(self.config_path)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_discounts_is_silent(self):
        normal_page = PAGE.replace("$64.00", "$74.00")
        code, stdout, stderr = self.run_main(normal_page)

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_discount_prints_alert(self):
        code, stdout, stderr = self.run_main(PAGE)

        self.assertEqual(code, 0)
        self.assertIn("CrossOver + is $64 (normal $74)", stdout)
        self.assertIn("https://example.test/store", stdout)
        self.assertEqual(stderr, "")

    def test_promotion_keyword_prints_alert_at_normal_prices(self):
        normal_page = PAGE.replace("$64.00", "$74.00").replace(
            '<div class="row pricing-table">',
            '<p>Summer special offer</p><div class="row pricing-table">',
        )
        code, stdout, stderr = self.run_main(normal_page)

        self.assertEqual(code, 0)
        self.assertIn("promotion keywords found: special, offer", stdout)
        self.assertEqual(stderr, "")

    def test_active_promotions_page_prints_alert(self):
        normal_page = PAGE.replace("$64.00", "$74.00")
        code, stdout, stderr = self.run_main(
            normal_page, "<h1>Currently Available Promotions</h1><p>Save now.</p>"
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            stdout,
            "CodeWeavers promotions page has something to offer: "
            "https://example.test/store/promotions\n",
        )
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
