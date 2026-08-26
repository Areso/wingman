import re
import sys
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_URL = (
    "https://rtd.mcw.gov.cy/WebPhase1/gui/Common/TPRTDLogo.jsp?lang=en"
)
NO_AUCTION_TEXT = "there is no available auction series"
AUCTION_TEXT = "auction series"


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored_element_depth = 0

    def handle_starttag(self, tag, _attrs):
        if tag.lower() in {"script", "style"}:
            self.ignored_element_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self.ignored_element_depth:
            self.ignored_element_depth -= 1

    def handle_data(self, data):
        if not self.ignored_element_depth:
            self.parts.append(data)


def parse_auction_page(html: str) -> bool:
    parser = VisibleTextParser()
    parser.feed(html)
    visible_text = re.sub(r"\s+", " ", " ".join(parser.parts)).casefold()

    if NO_AUCTION_TEXT in visible_text:
        return False
    if AUCTION_TEXT in visible_text:
        return True

    raise ValueError("the RTD page did not contain a recognizable auction status")


def check_auction(url: str = DEFAULT_URL, timeout: int = 10) -> bool:
    request = Request(
        url,
        headers={"User-Agent": "Wingman license plate auction monitor/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")
    return parse_auction_page(html)


def main() -> int:
    try:
        auction_available = check_auction()
    except (OSError, URLError, ValueError) as error:
        print(f"License plate auction check failed: {error}", file=sys.stderr)
        return 1

    if auction_available:
        print(f"License plate auction available: {DEFAULT_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
