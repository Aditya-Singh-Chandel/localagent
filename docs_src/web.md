# `localagent/web.py` — reading web pages for the `fetch_url` tool

**Role in the flow:** lets the model read documentation (a library's API page, an error explanation) the way Claude
Code's WebFetch does. The toolbox asks you first (or follows a permission rule), then calls `fetch_text`.

Safety and cost limits: only `http://` and `https://` (no `file://` tricks), at most 2 MB downloaded, HTML reduced to
visible text, and the toolbox cuts the result to `max_output_chars` like every tool result. Pages are fetched from
your computer (not the sandbox), which is why every fetch needs approval: the address itself could carry data out, so
you see each URL before it is opened.

## Module header
@@ localagent/web.py header @@
- `re` tidies whitespace; `urllib` downloads; `HTMLParser` (standard library) walks through HTML; `__version__` goes
  into the `User-Agent`.
- `MAX_FETCH_BYTES` caps the download. `BLOCK_TAGS` are tags that start a new line (paragraphs, list items, headings,
  table rows ...). `HIDDEN_TAGS` hold no readable text (`script`, `style`, the `head` section ...).

## `_TextExtractor`
@@ localagent/web.py _TextExtractor @@
@@ localagent/web.py _TextExtractor.__init__ @@
@@ localagent/web.py _TextExtractor.handle_starttag @@
@@ localagent/web.py _TextExtractor.handle_endtag @@
@@ localagent/web.py _TextExtractor.handle_data @@
- The parser calls these methods as it meets each opening tag, closing tag and piece of text.
- `_hidden` counts how deep we are inside hidden tags; text is only kept at depth 0.
- Block tags add a line break on opening and closing, so paragraphs stay separate. `convert_charrefs=True` turns
  entities like `&amp;` into real characters.

## `html_to_text`
@@ localagent/web.py html_to_text @@
- Runs the parser, joins the pieces, collapses runs of spaces/tabs to one space and runs of blank lines to one blank
  line, strips each line, and trims the ends.

## `fetch_text`
@@ localagent/web.py fetch_text @@
- Refuses non-web addresses. Sends a polite `User-Agent` such as `localagent/0.6.0 (docs reader)` (some sites block
  requests without one).
- Reads at most 2 MB, notes the content type and the character set the server declared (default UTF-8).
- HTTP errors (404, 403 ...) and network problems become `ValueError` with a short reason; the toolbox shows it to the
  model as `ERROR: could not fetch ...`.
- An unknown character-set name falls back to UTF-8. HTML (by content type, or by how the text starts) is converted
  to plain text; other text (JSON, plain text, Markdown) is returned as is.
