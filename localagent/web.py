"""Read web pages as plain text for the ``fetch_url`` tool (e.g. library documentation).

Only http(s) is allowed, the download is capped at 2 MB, HTML is reduced to its
visible text (scripts, styles and tags removed), and the result is shortened by
the toolbox like every other tool output. Fetching always needs the user's
approval unless a permission rule allows the website.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

from . import __version__

MAX_FETCH_BYTES = 2_000_000
BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section",
              "article", "header", "footer", "table", "ul", "ol"}
HIDDEN_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    """Collects visible text; block tags become line breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0                       # depth inside <script>, <style>, ...

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in HIDDEN_TAGS:
            self._hidden += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN_TAGS and self._hidden:
            self._hidden -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Visible text of an HTML page with tidy whitespace."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)              # runs of spaces -> one space
    text = re.sub(r"\n\s*\n+", "\n\n", text)               # many blank lines -> one
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def fetch_text(url: str, timeout: float = 20.0) -> str:
    """Download ``url`` and return its text; raises ValueError with a readable reason."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http:// and https:// addresses can be fetched")
    request = urllib.request.Request(url, headers={"User-Agent": f"localagent/{__version__} (docs reader)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_FETCH_BYTES)
            content_type = response.headers.get("Content-Type", "")
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        exc.close()
        raise ValueError(f"the server answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"cannot reach the address ({exc})") from None
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:                                   # unknown charset name: assume UTF-8
        text = raw.decode("utf-8", errors="replace")
    if "html" in content_type.lower() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        text = html_to_text(text)
    return text
