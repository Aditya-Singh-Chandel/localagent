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
15 | MAX_FETCH_BYTES = 2_000_000
16 | BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section",
17 |               "article", "header", "footer", "table", "ul", "ol"}
18 | HIDDEN_TAGS = {"script", "style", "noscript", "svg", "head"}
19 | 
20 | 
```
- `re` tidies whitespace; `urllib` downloads; `HTMLParser` (standard library) walks through HTML.
- `MAX_FETCH_BYTES` caps the download. `BLOCK_TAGS` are tags that start a new line (paragraphs, list items, headings,
  table rows ...). `HIDDEN_TAGS` hold no readable text (`script`, `style`, the `head` section ...).

## `_TextExtractor`
```python
21 | class _TextExtractor(HTMLParser):
22 |     """Collects visible text; block tags become line breaks."""
23 | 
```
```python
24 |     def __init__(self) -> None:
25 |         super().__init__(convert_charrefs=True)
26 |         self.parts: list[str] = []
27 |         self._hidden = 0                       # depth inside <script>, <style>, ...
28 | 
```
```python
29 |     def handle_starttag(self, tag: str, attrs: list) -> None:
30 |         if tag in HIDDEN_TAGS:
31 |             self._hidden += 1
32 |         elif tag in BLOCK_TAGS:
33 |             self.parts.append("\n")
34 | 
```
```python
35 |     def handle_endtag(self, tag: str) -> None:
36 |         if tag in HIDDEN_TAGS and self._hidden:
37 |             self._hidden -= 1
38 |         elif tag in BLOCK_TAGS:
39 |             self.parts.append("\n")
40 | 
```
```python
41 |     def handle_data(self, data: str) -> None:
42 |         if not self._hidden:
43 |             self.parts.append(data)
44 | 
45 | 
```
- The parser calls these methods as it meets each opening tag, closing tag and piece of text.
- `_hidden` counts how deep we are inside hidden tags; text is only kept at depth 0.
- Block tags add a line break on opening and closing, so paragraphs stay separate. `convert_charrefs=True` turns
  entities like `&amp;` into real characters.

## `html_to_text`
```python
46 | def html_to_text(html: str) -> str:
47 |     """Visible text of an HTML page with tidy whitespace."""
48 |     parser = _TextExtractor()
49 |     parser.feed(html)
50 |     parser.close()
51 |     text = "".join(parser.parts)
52 |     text = re.sub(r"[ \t\r\f\v]+", " ", text)              # runs of spaces -> one space
53 |     text = re.sub(r"\n\s*\n+", "\n\n", text)               # many blank lines -> one
54 |     return "\n".join(line.strip() for line in text.splitlines()).strip()
55 | 
56 | 
```
- Runs the parser, joins the pieces, collapses runs of spaces/tabs to one space and runs of blank lines to one blank
  line, strips each line, and trims the ends.

## `fetch_text`
```python
57 | def fetch_text(url: str, timeout: float = 20.0) -> str:
58 |     """Download ``url`` and return its text; raises ValueError with a readable reason."""
59 |     if not url.startswith(("http://", "https://")):
60 |         raise ValueError("only http:// and https:// addresses can be fetched")
61 |     request = urllib.request.Request(url, headers={"User-Agent": "localagent/0.5 (docs reader)"})
62 |     try:
63 |         with urllib.request.urlopen(request, timeout=timeout) as response:
64 |             raw = response.read(MAX_FETCH_BYTES)
65 |             content_type = response.headers.get("Content-Type", "")
66 |             charset = response.headers.get_content_charset() or "utf-8"
67 |     except urllib.error.HTTPError as exc:
68 |         exc.close()
69 |         raise ValueError(f"the server answered HTTP {exc.code}") from None
70 |     except (urllib.error.URLError, OSError) as exc:
71 |         raise ValueError(f"cannot reach the address ({exc})") from None
72 |     try:
73 |         text = raw.decode(charset, errors="replace")
74 |     except LookupError:                                   # unknown charset name: assume UTF-8
75 |         text = raw.decode("utf-8", errors="replace")
76 |     if "html" in content_type.lower() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
77 |         text = html_to_text(text)
78 |     return text
```
- Refuses non-web addresses. Sends a polite `User-Agent` (some sites block requests without one).
- Reads at most 2 MB, notes the content type and the character set the server declared (default UTF-8).
- HTTP errors (404, 403 ...) and network problems become `ValueError` with a short reason; the toolbox shows it to the
  model as `ERROR: could not fetch ...`.
- An unknown character-set name falls back to UTF-8. HTML (by content type, or by how the text starts) is converted
  to plain text; other text (JSON, plain text, Markdown) is returned as is.
