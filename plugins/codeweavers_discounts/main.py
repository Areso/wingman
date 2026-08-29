import configparser
import re
import sys
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.toml")
MONEY_PATTERN = re.compile(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)")
PROMOTION_KEYWORDS = ("sale", "special", "offer", "discount")
IGNORED_PROMOTION_PHRASES = ("special renewal pricing",)
NO_ACTIVE_PROMOTIONS_PATTERN = re.compile(
    r"\bsorry\s*,?\s*there are no promotions currently active\b", re.IGNORECASE
)


class DiscountCheckError(Exception):
    pass


class PlanParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.plans = []
        self.card_depth = 0
        self.card_parts = []
        self.price_depth = 0
        self.price_parts = []

    def handle_starttag(self, tag, attrs):
        classes = set(dict(attrs).get("class", "").split())

        if not self.card_depth:
            if tag == "div" and "col-lg-4" in classes:
                self.card_depth = 1
                self.card_parts = []
                self.price_parts = []
            return

        if tag != "div":
            return

        self.card_depth += 1
        if self.price_depth:
            self.price_depth += 1
        elif "pricing-table-price" in classes:
            self.price_depth = 1

    def handle_endtag(self, tag):
        if not self.card_depth or tag != "div":
            return

        if self.price_depth:
            self.price_depth -= 1
        self.card_depth -= 1
        if not self.card_depth:
            text = " ".join(self.card_parts)
            if re.search(r"\bCrossOver\s+Life\b", text, re.IGNORECASE):
                name = "CrossOver Life"
            elif re.search(r"\bCrossOver\s*\+", text, re.IGNORECASE):
                name = "CrossOver +"
            else:
                return
            self.plans.append({"name": name, "price": " ".join(self.price_parts)})

    def handle_data(self, data):
        if self.card_depth:
            self.card_parts.append(data)
        if self.price_depth:
            self.price_parts.append(data)


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"script", "style"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


def parse_plans(html):
    parser = PlanParser()
    parser.feed(html)
    plans = {}

    for plan in parser.plans:
        prices = {
            Decimal(match.replace(",", ""))
            for match in MONEY_PATTERN.findall(plan["price"])
        }
        if prices:
            plans[plan["name"].casefold()] = {
                "name": plan["name"],
                "price": min(prices),
            }

    if not plans:
        raise DiscountCheckError("no CodeWeavers plans could be parsed")
    return plans


def find_promotion_keywords(html):
    parser = VisibleTextParser()
    parser.feed(html)
    visible_text = " ".join(parser.parts).casefold()
    for phrase in IGNORED_PROMOTION_PHRASES:
        phrase_pattern = re.escape(phrase).replace(r"\ ", r"\s+")
        visible_text = re.sub(rf"\b{phrase_pattern}\b", "", visible_text)
    return [
        keyword
        for keyword in PROMOTION_KEYWORDS
        if re.search(rf"\b{re.escape(keyword)}\b", visible_text)
    ]


def has_active_promotions(html):
    parser = VisibleTextParser()
    parser.feed(html)
    visible_text = " ".join(parser.parts)
    return NO_ACTIVE_PROMOTIONS_PATTERN.search(visible_text) is None


def load_config(config_path=DEFAULT_CONFIG_PATH):
    parser = configparser.ConfigParser()
    try:
        with Path(config_path).open("r", encoding="utf-8") as config_file:
            parser.read_file(config_file)
        settings = parser["codeweavers_plugin"]
        configured_prices = parser["normal_prices"]
    except FileNotFoundError as error:
        raise DiscountCheckError(f"config file not found: {config_path}") from error
    except (KeyError, configparser.Error) as error:
        raise DiscountCheckError(f"invalid config file: {config_path}") from error

    url = settings.get("url", "").strip().strip('"\'')
    if not url:
        raise DiscountCheckError("url is missing from config.toml")
    promotions_url = settings.get("promotions_url", "").strip().strip('"\'')
    if not promotions_url:
        raise DiscountCheckError("promotions_url is missing from config.toml")

    try:
        timeout = int(settings.get("timeout_seconds", "15").strip().strip('"\''))
    except ValueError as error:
        raise DiscountCheckError("timeout_seconds must be an integer") from error
    if timeout <= 0:
        raise DiscountCheckError("timeout_seconds must be positive")

    normal_prices = {}
    try:
        for name, value in configured_prices.items():
            normal_prices[name.casefold()] = Decimal(value.strip().strip('"\''))
    except InvalidOperation as error:
        raise DiscountCheckError("normal prices must be numbers") from error
    if not normal_prices:
        raise DiscountCheckError("normal_prices must contain at least one plan")

    return url, promotions_url, timeout, normal_prices


def fetch_page(url, timeout):
    request = Request(
        url,
        headers={"User-Agent": "Wingman CodeWeavers discount monitor/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def find_discounts(plans, normal_prices):
    missing = sorted(set(normal_prices) - set(plans))
    if missing:
        raise DiscountCheckError(
            "configured plans not found on CodeWeavers: " + ", ".join(missing)
        )

    return [
        (plans[name], normal_price)
        for name, normal_price in normal_prices.items()
        if plans[name]["price"] < normal_price
    ]


def format_price(price):
    formatted = format(price, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def main(config_path=DEFAULT_CONFIG_PATH):
    try:
        url, promotions_url, timeout, normal_prices = load_config(config_path)
        html = fetch_page(url, timeout)
        promotions_html = fetch_page(promotions_url, timeout)
        plans = parse_plans(html)
        discounts = find_discounts(plans, normal_prices)
        promotion_keywords = find_promotion_keywords(html)
        active_promotions = has_active_promotions(promotions_html)
    except (DiscountCheckError, OSError, URLError) as error:
        print(f"CodeWeavers discount check failed: {error}", file=sys.stderr)
        return 1

    for plan, normal_price in discounts:
        print(
            f"CodeWeavers discount: {plan['name']} is ${format_price(plan['price'])} "
            f"(normal ${format_price(normal_price)}): {url}"
        )
    if promotion_keywords:
        print(
            "CodeWeavers promotion keywords found: "
            + ", ".join(promotion_keywords)
            + f": {url}"
        )
    if active_promotions:
        print(
            "CodeWeavers promotions page has something to offer: "
            + promotions_url
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
