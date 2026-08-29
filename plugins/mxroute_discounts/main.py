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


class DiscountCheckError(Exception):
    pass


class PlanParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.plans = []
        self.plan = None
        self.plan_depth = 0
        self.capture = None
        self.capture_depth = 0
        self.capture_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())

        if self.plan is None:
            if tag == "div" and "plan" in classes:
                self.plan = {"name": "", "price": "", "url": ""}
                self.plan_depth = 1
            return

        self.plan_depth += 1
        if self.capture is not None:
            self.capture_depth += 1
        elif tag == "div" and "name" in classes:
            self.capture = "name"
            self.capture_depth = 1
            self.capture_parts = []
        elif tag == "div" and "price" in classes:
            self.capture = "price"
            self.capture_depth = 1
            self.capture_parts = []

        if tag == "a" and "cta" in classes:
            self.plan["url"] = attributes.get("href", "")

    def handle_endtag(self, _tag):
        if self.plan is None:
            return

        if self.capture is not None:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                self.plan[self.capture] = " ".join(self.capture_parts).strip()
                self.capture = None
                self.capture_parts = []

        self.plan_depth -= 1
        if self.plan_depth == 0:
            self.plans.append(self.plan)
            self.plan = None

    def handle_data(self, data):
        if self.capture is not None:
            self.capture_parts.append(data)


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
        name = plan["name"].strip()
        prices = [
            Decimal(match.replace(",", ""))
            for match in MONEY_PATTERN.findall(plan["price"])
        ]
        if name and prices:
            plans[name.casefold()] = {
                "name": name,
                "price": min(prices),
                "url": plan["url"],
            }

    if not plans:
        raise DiscountCheckError("no MXroute plans could be parsed")
    return plans


def find_promotion_keywords(html):
    parser = VisibleTextParser()
    parser.feed(html)
    visible_text = " ".join(parser.parts).casefold()
    return [
        keyword
        for keyword in PROMOTION_KEYWORDS
        if re.search(rf"\b{re.escape(keyword)}\b", visible_text)
    ]


def load_config(config_path=DEFAULT_CONFIG_PATH):
    parser = configparser.ConfigParser()
    try:
        with Path(config_path).open("r", encoding="utf-8") as config_file:
            parser.read_file(config_file)
        settings = parser["mxroute_plugin"]
        configured_prices = parser["normal_prices"]
    except FileNotFoundError as error:
        raise DiscountCheckError(f"config file not found: {config_path}") from error
    except (KeyError, configparser.Error) as error:
        raise DiscountCheckError(f"invalid config file: {config_path}") from error

    url = settings.get("url", "").strip().strip('"\'')
    if not url:
        raise DiscountCheckError("url is missing from config.toml")

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

    return url, timeout, normal_prices


def fetch_page(url, timeout):
    request = Request(
        url,
        headers={"User-Agent": "Wingman MXroute discount monitor/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def find_discounts(plans, normal_prices):
    missing = sorted(set(normal_prices) - set(plans))
    if missing:
        raise DiscountCheckError(
            "configured plans not found on MXroute: " + ", ".join(missing)
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
        url, timeout, normal_prices = load_config(config_path)
        html = fetch_page(url, timeout)
        plans = parse_plans(html)
        discounts = find_discounts(plans, normal_prices)
        promotion_keywords = find_promotion_keywords(html)
    except (DiscountCheckError, OSError, URLError) as error:
        print(f"MXroute discount check failed: {error}", file=sys.stderr)
        return 1

    for plan, normal_price in discounts:
        order_url = plan["url"] or url
        print(
            f"MXroute discount: {plan['name']} is ${format_price(plan['price'])}/year "
            f"(normal ${format_price(normal_price)}/year): {order_url}"
        )
    if promotion_keywords:
        print(
            "MXroute promotion keywords found: "
            + ", ".join(promotion_keywords)
            + f": {url}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
