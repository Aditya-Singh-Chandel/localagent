# `localagent/web.py` — reading web pages for the `fetch_url` tool

**Role in the flow:** lets the model read documentation (a library's API page, an error explanation) the way Claude
Code's WebFetch does. The toolbox asks you first (or follows a permission rule), then calls `fetch_text`.

Safety and cost limits: only `http://` and `https://` (no `file://` tricks), at most 2 MB downloaded, HTML reduced to
visible text, and the toolbox cuts the result to `max_output_chars` like every tool result. Pages are fetched from
your computer (not the sandbox), which is why every fetch needs approval: the address itself could carry data out, so
you see each URL before it is opened.

## Module header
```python
 1 | """Read web pages as plain text for the ``fetch_url`` tool (e.g. library documentation).
 2 | 
 3 | Only http(s) is allowed, the download is capped at 2 MB, HTML is reduced to its
 4 | visible text (scripts, styles and tags removed), and the result is shortened by
 5 | the toolbox like every other tool output. Fetching always needs the user's
 6 | approval unless a permission rule allows the website.
 7 | """
 8 | from __future__ import annotations
 9 | 
10 | import re
11 | import urllib.error
12 | import urllib.request
13 | from html.parser import HTMLParser
14 | 
15 | from . import __version__
16 | 
17 | MAX_FETCH_BYTES = 2_000_000
18 | BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section",
19 |               "article", "header", "footer", "table", "ul", "ol"}
20 | HIDDEN_TAGS = {"script", "style", "noscript", "svg", "head"}
21 | 
22 | 
```
- `re` tidies whitespace; `urllib` downloads; `HTMLParser` (standard library) walks through HTML; `__version__` goes
  into the `User-Agent`.
- `MAX_FETCH_BYTES` caps the download. `BLOCK_TAGS` are tags that start a new line (paragraphs, list items, headings,
  table rows ...). `HIDDEN_TAGS` hold no readable text (`script`, `style`, the `head` section ...).

## `_TextExtractor`
```python
23 | class _TextExtractor(HTMLParser):
24 |     """Collects visible text; block tags become line breaks."""
25 | 
```
```python
26 |     def __init__(self) -> None:
27 |         super().__init__(convert_charrefs=True)
28 |         self.parts: list[str] = []
29 |         self._hidden = 0                       # depth inside <script>, <style>, ...
30 | 
```
```python
31 |     def handle_starttag(self, tag: str, attrs: list) -> None:
32 |         if tag in HIDDEN_TAGS:
33 |             self._hidden += 1
34 |         elif tag in BLOCK_TAGS:
35 |             self.parts.append("\n")
36 | 
```
```python
37 |     def handle_endtag(self, tag: str) -> None:
38 |         if tag in HIDDEN_TAGS and self._hidden:
39 |             self._hidden -= 1
40 |         elif tag in BLOCK_TAGS:
41 |             self.parts.append("\n")
42 | 
```
```python
43 |     def handle_data(self, data: str) -> None:
44 |         if not self._hidden:
45 |             self.parts.append(data)
46 | 
47 | 
```
- The parser calls these methods as it meets each opening tag, closing tag and piece of text.
- `_hidden` counts how deep we are inside hidden tags; text is only kept at depth 0.
- Block tags add a line break on opening and closing, so paragraphs stay separate. `convert_charrefs=True` turns
  entities like `&amp;` into real characters.

## `html_to_text`
```python
48 | def html_to_text(html: str) -> str:
49 |     """Visible text of an HTML page with tidy whitespace."""
50 |     parser = _TextExtractor()
51 |     parser.feed(html)
52 |     parser.close()
53 |     text = "".join(parser.parts)
54 |     text = re.sub(r"[ \t\r\f\v]+", " ", text)              # runs of spaces -> one space
55 |     text = re.sub(r"\n\s*\n+", "\n\n", text)               # many blank lines -> one
56 |     return "\n".join(line.strip() for line in text.splitlines()).strip()
57 | 
58 | 
```
- Runs the parser, joins the pieces, collapses runs of spaces/tabs to one space and runs of blank lines to one blank
  line, strips each line, and trims the ends.

## `fetch_text`
```python
59 | def fetch_text(url: str, timeout: float = 20.0) -> str:
60 |     """Download ``url`` and return its text; raises ValueError with a readable reason."""
61 |     if not url.startswith(("http://", "https://")):
62 |         raise ValueError("only http:// and https:// addresses can be fetched")
63 |     request = urllib.request.Request(url, headers={"User-Agent": f"localagent/{__version__} (docs reader)"})
64 |     try:
65 |         with urllib.request.urlopen(request, timeout=timeout) as response:
66 |             raw = response.read(MAX_FETCH_BYTES)
67 |             content_type = response.headers.get("Content-Type", "")
68 |             charset = response.headers.get_content_charset() or "utf-8"
69 |     except urllib.error.HTTPError as exc:
70 |         exc.close()
71 |         raise ValueError(f"the server answered HTTP {exc.code}") from None
72 |     except (urllib.error.URLError, OSError) as exc:
73 |         raise ValueError(f"cannot reach the address ({exc})") from None
74 |     try:
75 |         text = raw.decode(charset, errors="replace")
76 |     except LookupError:                                   # unknown charset name: assume UTF-8
77 |         text = raw.decode("utf-8", errors="replace")
78 |     if "html" in content_type.lower() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
79 |         text = html_to_text(text)
80 |     return text
```
- Refuses non-web addresses. Sends a polite `User-Agent` such as `localagent/0.6.0 (docs reader)` (some sites block
  requests without one).
- Reads at most 2 MB, notes the content type and the character set the server declared (default UTF-8).
- HTTP errors (404, 403 ...) and network problems become `ValueError` with a short reason; the toolbox shows it to the
  model as `ERROR: could not fetch ...`.
- An unknown character-set name falls back to UTF-8. HTML (by content type, or by how the text starts) is converted
  to plain text; other text (JSON, plain text, Markdown) is returned as is.
