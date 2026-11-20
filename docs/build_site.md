# `tools/build_site.py` — the website, ready for any host

**Role:** turns the project into a complete download website in one folder, `site\`: the landing page, every page of
these docs (rendered from Markdown, with a page list, contents and a pager), the download files and their checksums.
It is plain files — no server program, no database, nothing loaded from other sites — so any web host can serve it,
and it can be previewed on your own computer.

```
.venv\Scripts\python tools\build_release.py      # first: the download files in dist\
.venv\Scripts\python tools\build_site.py         # then: the site in site\, checked
site OK: 54 files, 24.7 MB, 41 documentation pages -> ...\site
.venv\Scripts\python tools\build_site.py --serve # the same, then open http://127.0.0.1:8000
```

**Privacy by design.** The site never links to the source repository and never names its owner. The build reads the
owner's name from the repository's GitHub address (`git remote get-url origin`) and refuses to finish when any page,
style sheet, script or text file contains it — with or without the dashes. The check caught a real case on its first
run: an example commit address in the build log. The download files themselves are not searched: the license inside
the zip must name its copyright holder.

**Checked before it is called done.** Every page is parsed: every tag that is opened must be closed in order, every
link and image must point to a file of the site (and a `#section` to an id that exists on that page), and no
`{{placeholder}}` may be left. Any problem is listed and the build exits with 1.

## Putting it online
The folder works on any static host. Three ways that need no programming, none of which shows a GitHub account:
- **Netlify:** sign in at netlify.com, open "Add new site → Deploy manually" and drag the `site` folder onto the page.
  Netlify also reads the `_headers` file (security headers below).
- **Cloudflare Pages:** "Create → Pages → Upload assets", then the `site` folder. Cloudflare limits a single file to
  25 MiB: the Windows zip is 22.8 MB, so it fits.
- **Your own server:** copy the folder's contents into the web root of nginx, Apache or Caddy.

With your own address known, build with `--url https://your.site/` to add canonical links and a `sitemap.xml` for
search engines. To keep the large downloads somewhere else (a file host, a bucket), build with
`--downloads-url https://files.example/v0.6.0/` and upload the three files of `dist\` there.

## The website's own files (`website\`)
- `index.html` — the landing page. `{{name}}` places are filled in by the build: the version, the download file names
  and sizes, where the downloads are, the site's address. It has no inline styles or scripts, so the strict security
  policy below allows everything it needs.
- `page.html` — the frame of every documentation page: the top bar, the page list, the article, the pager.
- `404.html` — "page not found"; its links start with `/`, because a host shows it at any address.
- `style.css` — the whole design: system fonts (nothing downloaded), colours as variables so the dark theme only
  redefines them, a grid that folds to one column on phones, and print rules that leave out the menus.
- `site.js` — three conveniences, none required: a Copy button on code blocks; on a computer that is not Windows the
  main button leads to the pip instructions; on documentation pages the page list is open on wide screens and folded
  on small ones.
- `_headers` — security headers for hosts that read this file (Netlify, Cloudflare Pages; others ignore it): only the
  site's own files may load, it may not be framed by other sites, browsers must not guess file types, and no address
  is passed on when a visitor follows an outside link.

## Module header
```python
  1 | """Build the website: one self-contained folder to put on any web host.
  2 | 
  3 | Usage (from the project folder, after tools/build_release.py has made the download files):
  4 |     python tools/build_site.py                          # writes site/ and checks it
  5 |     python tools/build_site.py --url https://example.org/          # + canonical links, sitemap.xml
  6 |     python tools/build_site.py --downloads-url https://files.example.org/v0.6.0/
  7 |     python tools/build_site.py --serve                  # build, then show it at http://127.0.0.1:8000
  8 | 
  9 | site/index.html          the landing page (website/index.html, filled in)
 10 | site/docs/*.html         every page of docs/, rendered from Markdown, with a page list and a pager
 11 | site/downloads/          the release files and SHA256SUMS.txt (not with --downloads-url)
 12 | site/assets/, style.css, site.js, 404.html, _headers, robots.txt (and sitemap.xml with --url)
 13 | 
 14 | The site loads nothing from other sites and links nowhere near the source repository: the build
 15 | checks every link and every tag, and refuses to finish if a page shows the repository owner's name.
 16 | """
 17 | from __future__ import annotations
 18 | 
 19 | import argparse
 20 | import html
 21 | import re
 22 | import shutil
 23 | import subprocess
 24 | import sys
 25 | from functools import partial
 26 | from html.parser import HTMLParser
 27 | from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
 28 | from pathlib import Path
 29 | from typing import Callable
 30 | from urllib.parse import urlsplit
 31 | 
 32 | ROOT = Path(__file__).resolve().parent.parent
 33 | sys.path.insert(0, str(ROOT))
 34 | from localagent import __version__  # noqa: E402  (after the path is set)
 35 | 
 36 | WEBSITE, DOCS = ROOT / "website", ROOT / "docs"
 37 | STATIC = ("style.css", "site.js", "404.html", "_headers")
 38 | ASSETS = ("logo.svg", "demo.svg", "social.png")
 39 | GUIDES = {                                # narrative pages: file name -> (menu title, one line)
 40 |     "04-start-from-zero": ("Start from zero", "every step from an empty computer"),
 41 |     "05-user-manual": ("User manual", "every command, flag, setting and tool"),
 42 |     "01-setup-and-models": ("Online and offline", "the online services, local models and modes"),
 43 |     "06-run-and-verify": ("Run and verify", "every way to run it, and how to check it works"),
 44 |     "00-overview": ("How it works", "the architecture, and one request through the code"),
 45 |     "02-roadmap": ("Roadmap", "what is built and what could come next"),
 46 |     "03-build-log": ("Build log", "every decision and measurement, and why"),
 47 | }
 48 | AREAS = {                                 # code pages, grouped as the overview groups them
 49 |     "The agent": ("agent", "context", "tools", "verify", "sandbox", "runners", "checkpoint", "session",
 50 |                   "meter"),
 51 |     "Models": ("providers", "llm", "openai_compat", "backends", "resilient", "ratelimit", "localserver",
 52 |                "models", "fetch"),
 53 |     "Commands and settings": ("cli", "wizard", "doctor", "config", "settings", "server", "web", "mcp",
 54 |                               "package"),
 55 |     "Building and testing": ("tests", "evals", "build_docs", "build_release", "build_site", "check_commit",
 56 |                              "docker"),
 57 | }
 58 | VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
 59 |                   "track", "wbr"})
 60 | FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$")
 61 | HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
 62 | RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
 63 | ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
 64 | TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
 65 | ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
 66 | CODE_SPAN = re.compile(r"(`+)(.+?)\1")
 67 | LINK = re.compile(r"(!?)\[([^\]]+)\]\(([^)\s]+)\)")
 68 | ANGLE_URL = re.compile(r"<(https?://[^>\s]+)>")
 69 | BARE_URL = re.compile(r"https?://[^\s<>\"'`]+")
 70 | MARKER = re.compile(r"\x00(\d+)\x00")
 71 | DOC_NAME = re.compile(r"(?:docs/)?([\w.-]+)\.md(#[\w-]+)?")
 72 | EMPHASIS = (
 73 |     (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "strong"),
 74 |     (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), "em"),
 75 |     (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "em"),
 76 | )
 77 | Link = Callable[[str], "str | None"]       # a Markdown link target -> the site's address, or None
 78 | 
 79 | 
```
- The docstring is the usage summary. Imports: `argparse` (options), `html` (escaping), `re`, `shutil` (copying),
  `subprocess` (asking git for the repository address), `sys`, `partial`, `HTMLParser` (the page checks), the standard
  library's small web server (`--serve`), `Path`, `Callable`, `urlsplit`; then the agent's `__version__`, after the
  project folder is put on the import path.
- `WEBSITE` and `DOCS` are the source folders. `STATIC` and `ASSETS` name the files copied as they are.
- `GUIDES` gives each narrative page a short menu title and one line for the documentation's front page, in reading
  order: start from zero first, the build log last. `AREAS` groups the code pages the way `00-overview.md` does.
- `VOID` lists the HTML tags that never have a closing tag (the tag check must not wait for them).
- The regular expressions recognise Markdown: code fences, headings, rules (`---`), list items, the `|---|` line under
  a table's header, backslash escapes, code spans, links and images, `<https://...>`, bare addresses, the markers of
  kept HTML (see `_inline`), and file names of documentation pages (`cli.md`).
- `EMPHASIS`: `**bold**`, `*italic*` and `_italic_`. An asterisk or underscore inside a word does not count, so `17*3`
  and `snake_case_names` stay as they are.
- `Link` is the type of the function that turns a link target into the site's address (or `None`).

## Small helpers
```python
 80 | def escape(text: str) -> str:
 81 |     """Text made safe for HTML (also inside attribute values). ``{`` is written as ``&#123;``,
 82 |     which looks the same: then no text from the docs can ever look like a {{placeholder}}."""
 83 |     return html.escape(text, quote=True).replace("{", "&#123;")
 84 | 
 85 | 
```
- `<`, `>`, `&` and quotes become HTML entities, so no text can ever become a tag; `{` becomes `&#123;` (it looks the
  same). Without that, three pages that document Docker's `{{.Names}}` and the slash-command template were reported as
  unfilled templates by the site check.

```python
 86 | def plain(markdown: str) -> str:
 87 |     """Inline Markdown as plain text: for titles, descriptions and heading ids. Code keeps its
 88 |     underscores (`read_secret`); outside code, emphasis marks are dropped."""
 89 |     text = LINK.sub(lambda m: m.group(2), markdown)
 90 |     parts = re.split(r"(`+[^`]*`+)", text)
 91 |     for index, part in enumerate(parts):
 92 |         parts[index] = part.strip("`") if index % 2 else re.sub(r"\*+|(?<!\w)_+|_+(?!\w)", "", part)
 93 |     return "".join(parts).strip()
 94 | 
 95 | 
```
```python
 96 | def slug(text: str, used: set[str]) -> str:
 97 |     """A heading's id: lower case, words joined by "-", unique on its page."""
 98 |     base = re.sub(r"[^\w\s-]", "", plain(text).lower()).strip()
 99 |     base = re.sub(r"[\s-]+", "-", base) or "section"
100 |     name, number = base, 2
101 |     while name in used:
102 |         name, number = f"{base}-{number}", number + 1
103 |     used.add(name)
104 |     return name
105 | 
106 | 
```
- `plain`: link labels instead of links; code loses its backticks but keeps its underscores (`read_secret`), and
  outside code the emphasis marks go — for titles and ids. The first version dropped every underscore, and the
  phone-width screenshot showed the contents of `wizard.md` listing "readsecret".
- `slug`: a heading's id, e.g. "`Config.from_env`" → `configfrom_env`; a second heading with the same words gets `-2`.

## Inline Markdown
```python
107 | def inline(text: str, link: Link) -> str:
108 |     """One line of Markdown as HTML: code, links, bare addresses, bold and italics. Everything
109 |     else is escaped, so text can never become a tag."""
110 |     kept: list[str] = []
111 |     text = _inline(text, link, kept)
112 |     while "\x00" in text:                                # a kept fragment may hold others
113 |         text = MARKER.sub(lambda m: kept[int(m.group(1))], text)
114 |     return text
115 | 
116 | 
```
```python
117 | def _inline(text: str, link: Link, kept: list[str]) -> str:
118 |     """The work of ``inline``. Finished HTML is put aside in ``kept`` and replaced by a marker
119 |     (NUL, its number, NUL), so later steps cannot change it; ``inline`` puts it back."""
120 |     def keep(fragment: str) -> str:
121 |         kept.append(fragment)
122 |         return f"\x00{len(kept) - 1}\x00"
123 | 
124 |     def code(match: re.Match) -> str:
125 |         content = match.group(2).strip()
126 |         target = link(content) if DOC_NAME.fullmatch(content) else None
127 |         span = f"<code>{escape(content)}</code>"
128 |         return keep(f'<a href="{escape(target)}">{span}</a>' if target else span)
129 | 
130 |     def anchor(match: re.Match) -> str:
131 |         image, label, target = match.groups()
132 |         address = link(target)
133 |         if image:
134 |             return keep(f'<img src="{escape(address)}" alt="{escape(label)}" loading="lazy">'
135 |                         if address else escape(label))
136 |         shown = _inline(label, link, kept)
137 |         return keep(f'<a href="{escape(address)}">{shown}</a>' if address else shown)
138 | 
139 |     def angle(match: re.Match) -> str:
140 |         address = escape(match.group(1))
141 |         return keep(f'<a href="{address}">{address}</a>')
142 | 
143 |     def bare(match: re.Match) -> str:
144 |         address = match.group(0).rstrip(".,;:!?")
145 |         if address.endswith(")") and "(" not in address:
146 |             address = address[:-1]
147 |         return keep(f'<a href="{escape(address)}">{escape(address)}</a>') + match.group(0)[len(address):]
148 | 
149 |     text = ESCAPED.sub(lambda m: keep(escape(m.group(1))), text)
150 |     text = CODE_SPAN.sub(code, text)
151 |     text = LINK.sub(anchor, text)
152 |     text = ANGLE_URL.sub(angle, text)
153 |     parts = re.split(r"(\x00\d+\x00)", text)
154 |     for index in range(0, len(parts), 2):                # only text, never a kept fragment
155 |         parts[index] = BARE_URL.sub(bare, parts[index])
156 |     text = escape("".join(parts))
157 |     for pattern, tag in EMPHASIS:
158 |         text = pattern.sub(partial(wrap, tag), text)
159 |     return text
160 | 
161 | 
```
- The order matters, and each finished piece of HTML is set aside (`keep`) and replaced by a marker — NUL, a number,
  NUL — so a later step cannot touch it: backslash escapes first, then code spans (whose content is never formatted),
  then links (their label is formatted with the same list, so a label may hold code), then `<https://...>`, then bare
  addresses in the remaining text, then everything else is escaped, then bold and italics. `inline` finally puts the
  pieces back; kept pieces may contain markers of others, hence the loop.
- A code span that names a documentation page (`` `cli.md` ``) becomes a link to it — that is how the pages refer to
  each other, so the site is navigable without changing the docs.
- A bare address keeps sentence punctuation outside the link: in "... at `https://example.org/x.`" the full stop is
  not part of the address, and a closing bracket only when the address has no opening one.
- A link to a file that is not on the site (`../LICENSE`) is shown as its label, not as a broken link.

```python
162 | def wrap(tag: str, match: re.Match) -> str:
163 |     """The text a pattern found, inside ``<tag>``: **bold** becomes <strong>bold</strong>."""
164 |     return f"<{tag}>{match.group(1)}</{tag}>"
165 | 
166 | 
```
- Puts the text a pattern found into a tag; a named function instead of a lambda (which mypy cannot type here).

## Blocks
```python
167 | def starts_block(line: str) -> bool:
168 |     """True when a line begins something other than paragraph text."""
169 |     stripped = line.strip()
170 |     return bool(FENCE.match(line) or HEADING.match(line) or RULE.match(line) or ITEM.match(line)
171 |                 or stripped.startswith(("|", ">")))
172 | 
173 | 
```
```python
174 | def interrupts(line: str) -> bool:
175 |     """True when a line ends a paragraph: any block start, except a numbered line that does not
176 |     start at 1 - a sentence may well begin with "2026." after a line break."""
177 |     item = ITEM.match(line)
178 |     if item and item.group(2)[0].isdigit() and item.group(2)[:-1] != "1":
179 |         return False
180 |     return starts_block(line)
181 | 
182 | 
```
```python
183 | def indent_of(line: str) -> int:
184 |     """How many spaces a line starts with."""
185 |     return len(line) - len(line.lstrip(" "))
186 | 
187 | 
```
- `starts_block`: a fence, heading, rule, list item, table row or quote. `interrupts` is the paragraph's version: a
  numbered line ends a paragraph only when it starts at 1, as in CommonMark — the build log has sentences that wrap
  onto a new line starting with a year.

```python
188 | class Renderer:
189 |     """Markdown to HTML for the documentation: the parts the docs use (headings, paragraphs,
190 |     lists, code blocks, tables, quotes, rules, links), each kept simple and safe."""
191 | 
```
```python
192 |     def __init__(self, link: Link) -> None:
193 |         self.link = link
194 |         self.used: set[str] = set()
195 |         self.headings: list[tuple[int, str, str]] = []      # (level, id, text) for the contents
196 | 
```
```python
197 |     def render(self, text: str) -> str:
198 |         """A whole page."""
199 |         return self.blocks(text.replace("\r\n", "\n").split("\n"))
200 | 
```
- One renderer per page: it remembers the ids already used and every heading (for the contents box).

```python
201 |     def blocks(self, lines: list[str]) -> str:
202 |         """A run of lines: each block in turn."""
203 |         out: list[str] = []
204 |         i = 0
205 |         while i < len(lines):
206 |             line = lines[i]
207 |             if not line.strip():
208 |                 i += 1
209 |             elif FENCE.match(line):
210 |                 i = self.code_block(lines, i, out)
211 |             elif heading := HEADING.match(line):
212 |                 out.append(self.heading(len(heading.group(1)), heading.group(2)))
213 |                 i += 1
214 |             elif RULE.match(line):
215 |                 out.append("<hr>")
216 |                 i += 1
217 |             elif line.lstrip().startswith("|") and i + 1 < len(lines) and TABLE_RULE.match(lines[i + 1]):
218 |                 i = self.table(lines, i, out)
219 |             elif line.lstrip().startswith(">"):
220 |                 quoted = []
221 |                 while i < len(lines) and lines[i].lstrip().startswith(">"):
222 |                     quoted.append(re.sub(r"^\s*> ?", "", lines[i]))
223 |                     i += 1
224 |                 out.append(f"<blockquote>{self.blocks(quoted)}</blockquote>")
225 |             elif ITEM.match(line):
226 |                 i = self.list_block(lines, i, out)
227 |             else:
228 |                 text = [line.strip()]
229 |                 i += 1
230 |                 while i < len(lines) and lines[i].strip() and not interrupts(lines[i]):
231 |                     text.append(lines[i].strip())
232 |                     i += 1
233 |                 out.append(f"<p>{inline(chr(10).join(text), self.link)}</p>")
234 |         return "\n".join(out)
235 | 
```
```python
236 |     def heading(self, level: int, title: str) -> str:
237 |         """A heading with its id (remembered for the page's contents) and a "#" link to itself."""
238 |         ident = slug(title, self.used)
239 |         self.headings.append((level, ident, plain(title)))
240 |         mark = f'<a class="anchor" href="#{ident}" aria-label="Link to this section">#</a>' if level > 1 else ""
241 |         return f'<h{level} id="{ident}">{inline(title, self.link)}{mark}</h{level}>'
242 | 
```
- Walks the lines and hands each block to its method. Quotes are rendered recursively (a quote may hold a list).
  A paragraph is all lines up to a blank line or a line that interrupts it.
- Headings get their id and, from level 2 on, a `#` link to themselves (shown when the mouse is over the heading).

```python
243 |     def code_block(self, lines: list[str], i: int, out: list[str]) -> int:
244 |         """A fenced code block, shown exactly as written."""
245 |         match = FENCE.match(lines[i])
246 |         assert match is not None
247 |         fence, language = match.group(1), match.group(2)
248 |         body: list[str] = []
249 |         i += 1
250 |         while i < len(lines) and not (lines[i].strip().startswith(fence)
251 |                                       and set(lines[i].strip()) == {fence[0]}):
252 |             body.append(lines[i])
253 |             i += 1
254 |         kind = f' class="language-{escape(language)}"' if language else ""
255 |         out.append(f"<pre><code{kind}>{escape(chr(10).join(body))}</code></pre>")
256 |         return i + 1
257 | 
```
- Everything up to the closing fence (the same character, at least as many) exactly as written, escaped; the
  language becomes a class name.

```python
258 |     def table(self, lines: list[str], i: int, out: list[str]) -> int:
259 |         """A table: a header row, the |---| rule (which also sets each column's alignment), rows."""
260 |         def cells(row: str) -> list[str]:
261 |             row = row.strip().removeprefix("|")
262 |             if row.endswith("|") and not row.endswith("\\|"):
263 |                 row = row[:-1]
264 |             return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row)]
265 | 
266 |         head = cells(lines[i])
267 |         aligns = []
268 |         for rule in cells(lines[i + 1]):
269 |             aligns.append(' class="al-c"' if rule.startswith(":") and rule.endswith(":")
270 |                           else ' class="al-r"' if rule.endswith(":") else "")
271 |         aligns += [""] * (len(head) - len(aligns))
272 |         rows = []
273 |         i += 2
274 |         while i < len(lines) and lines[i].lstrip().startswith("|"):
275 |             values = cells(lines[i]) + [""] * len(head)
276 |             rows.append("<tr>" + "".join(f"<td{aligns[n]}>{inline(values[n], self.link)}</td>"
277 |                                          for n in range(len(head))) + "</tr>")
278 |             i += 1
279 |         header = "".join(f"<th{aligns[n]}>{inline(cell, self.link)}</th>" for n, cell in enumerate(head))
280 |         out.append(f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead>'
281 |                    f"<tbody>{''.join(rows)}</tbody></table></div>")
282 |         return i
283 | 
```
- Cells are split at `|` but not at `\|` (an escaped pipe, as in `/model online\|offline`). `:---:` centres a column,
  `---:` aligns it right — with classes, not inline styles, which the security policy would block. Rows with too few
  cells are filled up. The table sits in a box that scrolls sideways on phones.

```python
284 |     def list_block(self, lines: list[str], i: int, out: list[str]) -> int:
285 |         """A list. An item owns the lines indented under it (nested lists, code); a blank line
286 |         between items makes the list "loose" (each item a paragraph)."""
287 |         first = ITEM.match(lines[i])
288 |         assert first is not None
289 |         base, ordered = len(first.group(1)), first.group(2)[0].isdigit()
290 |         items: list[list[str]] = []
291 |         loose = False
292 |         while i < len(lines):
293 |             match = ITEM.match(lines[i])
294 |             if not match or len(match.group(1)) != base or match.group(2)[0].isdigit() != ordered:
295 |                 break
296 |             content = match.start(3)
297 |             item = [match.group(3)]
298 |             i += 1
299 |             while i < len(lines):
300 |                 line = lines[i]
301 |                 if not line.strip():
302 |                     after = i + 1
303 |                     while after < len(lines) and not lines[after].strip():
304 |                         after += 1
305 |                     if after < len(lines) and indent_of(lines[after]) >= content:
306 |                         item += [""] * (after - i)
307 |                         loose = True
308 |                         i = after
309 |                         continue
310 |                     break
311 |                 if indent_of(line) >= content:
312 |                     item.append(line[content:])
313 |                 elif indent_of(line) > base and not ITEM.match(line):
314 |                     item.append(line.strip())
315 |                 elif indent_of(line) <= base and not starts_block(line):
316 |                     item.append(line.strip())            # a "lazy" continuation line
317 |                 else:
318 |                     break
319 |                 i += 1
320 |             items.append(item)
321 |             after = i
322 |             while after < len(lines) and not lines[after].strip():
323 |                 after += 1
324 |             following = ITEM.match(lines[after]) if after < len(lines) else None
325 |             if after > i and following and len(following.group(1)) == base:
326 |                 loose = True
327 |                 i = after
328 |         parts = []
329 |         for item in items:
330 |             body = self.blocks(item)
331 |             if not loose:
332 |                 body = re.sub(r"^<p>(.*?)</p>", r"\1", body, count=1, flags=re.S)
333 |             parts.append(f"<li>{body}</li>")
334 |         tag = "ol" if ordered else "ul"
335 |         start = int(first.group(2)[:-1]) if ordered else 1
336 |         opening = f'<ol start="{start}">' if ordered and start != 1 else f"<{tag}>"
337 |         out.append(opening + "".join(parts) + f"</{tag}>")
338 |         return i
339 | 
340 | 
```
- An item owns the lines indented under it — at least to where its text starts — so nested lists and code blocks
  inside items work, dedented and rendered recursively. A blank line inside an item or between items makes the list
  "loose": its items keep their paragraphs; in a tight list the first paragraph is unwrapped. A numbered list that does
  not start at 1 keeps its number (`start="3"`).

## Pages
```python
341 | def page_title(markdown: str) -> tuple[str, str]:
342 |     """(title, one line) from a page's first heading: "`localagent/fetch.py` — downloads you can
343 |     trust" gives ("fetch.py", "downloads you can trust")."""
344 |     first = next((line for line in markdown.splitlines() if line.startswith("# ")), "# Page")
345 |     title, _, rest = first[2:].partition(" — ")
346 |     name = plain(title)
347 |     return (name.rstrip("/").rsplit("/", 1)[-1] if "`" in title else name), plain(rest)
348 | 
349 | 
```
```python
350 | def describe(markdown: str) -> str:
351 |     """The first real paragraph as plain text, at most 155 characters: the page's description."""
352 |     for block in re.split(r"\n\s*\n", markdown):
353 |         text = block.strip()
354 |         if text and not starts_block(text.splitlines()[0]):
355 |             words = plain(" ".join(text.split()))
356 |             return words if len(words) <= 155 else words[:152].rsplit(" ", 1)[0] + "..."
357 |     return "localagent documentation"
358 | 
359 | 
```
- `page_title`: from "`` `localagent/fetch.py` `` — downloads you can trust" the menu title `fetch.py` and the line
  "downloads you can trust"; a folder page (`` `tests/` ``) becomes `tests`.
- `describe`: the first paragraph, at most 155 characters, cut at a word: the page's description for search engines.

```python
360 | def doc_order(stems: list[str]) -> list[tuple[str, list[str]]]:
361 |     """The documentation's groups, each with its pages in order; pages not named in a group
362 |     are added under "More", so a new page can never go missing."""
363 |     present = set(stems)
364 |     groups = [("Guides", [stem for stem in GUIDES if stem in present])]
365 |     groups += [(area, [stem for stem in names if stem in present]) for area, names in AREAS.items()]
366 |     named = {stem for _, members in groups for stem in members}
367 |     groups.append(("More", sorted(present - named)))
368 |     return [(title, members) for title, members in groups if members]
369 | 
370 | 
```
- The guides, then each area of code pages. A page that no group names lands in "More", so a new page is never left
  out of the menu.

```python
371 | def fill(template: str, values: dict[str, str]) -> str:
372 |     """Put the values into a template's {{name}} places; a name without a value is an error."""
373 |     missing = sorted(set(re.findall(r"{{(\w+)}}", template)) - set(values))
374 |     if missing:
375 |         raise SystemExit(f"the template needs values for: {', '.join(missing)}")
376 |     return re.sub(r"{{(\w+)}}", lambda m: values[m.group(1)], template)
377 | 
378 | 
```
```python
379 | def size_text(size: int) -> str:
380 |     """A file size the way people read it: "120 KB", "35.7 MB"."""
381 |     return f"{size / 1e3:.0f} KB" if size < 1e6 else f"{size / 1e6:.1f} MB"
382 | 
383 | 
```
```python
384 | def canonical(site_url: str, path: str) -> str:
385 |     """The <link rel="canonical"> tag for a page, when the site's address is known."""
386 |     return f'<link rel="canonical" href="{escape(site_url + path)}">' if site_url else ""
387 | 
388 | 
```
- `fill`: replaces every `{{name}}` of a template, and refuses to go on when the template asks for a value nobody gave
  (a typo in a template is an error, not a page with `{{nmae}}` on it).
- `size_text`: "101 KB", "22.8 MB" (decimal units, as download pages show them).
- `canonical`: the tag that names a page's one true address, only when the site's address is known.

```python
389 | def render_docs(out: Path, values: dict[str, str]) -> list[str]:
390 |     """Every docs/*.md page as docs/<name>.html, plus docs/index.html. Returns the page names."""
391 |     pages = {path.stem: path.read_text(encoding="utf-8") for path in sorted(DOCS.glob("*.md"))}
392 |     titles = {stem: page_title(text) for stem, text in pages.items()}
393 |     groups = doc_order(list(pages))
394 |     order = [stem for _, members in groups for stem in members]
395 | 
396 |     def link(target: str) -> str | None:
397 |         if target.startswith(("http://", "https://", "#")):
398 |             return target
399 |         path, _, part = target.partition("#")
400 |         name = Path(path).name
401 |         if name.endswith(".md") and name[:-3] in pages:
402 |             return name[:-3] + ".html" + (f"#{part}" if part else "")
403 |         return None                                     # a file that is not on the site
404 | 
405 |     def label(stem: str) -> str:
406 |         return GUIDES[stem][0] if stem in GUIDES else titles[stem][0]
407 | 
408 |     def blurb(stem: str) -> str:
409 |         return GUIDES[stem][1] if stem in GUIDES else titles[stem][1]
410 | 
411 |     def nav(current: str) -> str:
412 |         parts = []
413 |         for title, members in groups:
414 |             entries = []
415 |             for stem in members:
416 |                 here = ' aria-current="page"' if stem == current else ""
417 |                 entries.append(f'<li><a href="{stem}.html"{here}>{escape(label(stem))}</a></li>')
418 |             parts.append(f"<h2>{escape(title)}</h2><ul>{''.join(entries)}</ul>")
419 |         return "\n".join(parts)
420 | 
421 |     def pager(number: int) -> str:
422 |         links = ""
423 |         if number > 0:
424 |             back = order[number - 1]
425 |             links += f'<a class="prev" href="{back}.html"><small>Previous</small>{escape(label(back))}</a>'
426 |         if number + 1 < len(order):
427 |             ahead = order[number + 1]
428 |             links += f'<a class="next" href="{ahead}.html"><small>Next</small>{escape(label(ahead))}</a>'
429 |         return links
430 | 
431 |     template = (WEBSITE / "page.html").read_text(encoding="utf-8")
432 |     (out / "docs").mkdir(parents=True, exist_ok=True)
433 |     for number, stem in enumerate(order):
434 |         renderer = Renderer(link)
435 |         body = renderer.render(pages[stem])
436 |         sections = [(ident, text) for level, ident, text in renderer.headings if level == 2]
437 |         if len(sections) >= 4:                          # a long page gets its contents first
438 |             entries = "".join(f'<li><a href="#{ident}">{escape(text)}</a></li>' for ident, text in sections)
439 |             box = f'<nav class="toc" aria-label="On this page"><strong>On this page</strong><ul>{entries}</ul></nav>'
440 |             body = body.replace("</h1>", "</h1>\n" + box, 1)
441 |         page = fill(template, {**values, "title": escape(label(stem)), "nav": nav(stem), "content": body,
442 |                                "pager": pager(number), "description": escape(describe(pages[stem])),
443 |                                "canonical": canonical(values["site_url"], f"docs/{stem}.html")})
444 |         (out / "docs" / f"{stem}.html").write_text(page, encoding="utf-8")
445 |     cards = []
446 |     for title, members in groups:
447 |         entries = "".join(f'<li><a href="{stem}.html">{escape(label(stem))}<span>{escape(blurb(stem))}</span>'
448 |                           "</a></li>" for stem in members)
449 |         cards.append(f'<section class="group"><h2>{escape(title)}</h2><ul>{entries}</ul></section>')
450 |     intro = ("<h1>Documentation</h1><p>Everything about localagent: guides for using it, and a page for every "
451 |              "file of its code that explains each line — written so that a beginner can follow it.</p>")
452 |     index = fill(template, {**values, "title": "Documentation", "nav": nav("index"), "pager": "",
453 |                             "content": f'<div class="doc-index">{intro}{"".join(cards)}</div>',
454 |                             "description": "Guides and line-by-line code documentation for localagent.",
455 |                             "canonical": canonical(values["site_url"], "docs/index.html")})
456 |     (out / "docs" / "index.html").write_text(index, encoding="utf-8")
457 |     return order
458 | 
459 | 
```
- Reads every `docs\*.md` page. `link` turns a Markdown link target into a site address: outside addresses and
  `#sections` stay, a documentation page becomes its `.html`, anything else is not on the site (`None`).
- `nav` builds the page list with `aria-current="page"` on the page being shown (screen readers announce it, the style
  sheet highlights it); `pager` links the previous and next page in menu order.
- A page with four or more sections gets an "On this page" box right under its title.
- The front page of the docs lists every group with each page's title and line.

## Building
```python
460 | def build(out: Path, dist: Path, site_url: str = "", downloads_url: str = "") -> list[str]:
461 |     """Write the whole site into ``out`` (a previous build there is replaced). Returns the
462 |     rendered documentation pages."""
463 |     if site_url and not site_url.endswith("/"):
464 |         site_url += "/"
465 |     if downloads_url and not downloads_url.endswith("/"):
466 |         downloads_url += "/"
467 |     zip_file = dist / f"localagent-{__version__}-windows-x64.zip"
468 |     wheel = dist / f"localagent-{__version__}-py3-none-any.whl"
469 |     sums = dist / "SHA256SUMS.txt"
470 |     missing = [path.name for path in (zip_file, wheel, sums) if not path.is_file()]
471 |     if missing:
472 |         raise SystemExit(f"missing in {dist}: {', '.join(missing)} - run tools/build_release.py first")
473 |     if out.exists():
474 |         if any(out.iterdir()) and not (out / "index.html").is_file():
475 |             raise SystemExit(f"{out} is not empty and is not a site built before: choose another --out")
476 |         shutil.rmtree(out)
477 |     (out / "assets").mkdir(parents=True)
478 |     for name in STATIC:
479 |         shutil.copy2(WEBSITE / name, out / name)
480 |     for name in ASSETS:
481 |         shutil.copy2(ROOT / "assets" / name, out / "assets" / name)
482 |     if not downloads_url:
483 |         (out / "downloads").mkdir()
484 |         for path in (zip_file, wheel, sums):
485 |             shutil.copy2(path, out / "downloads" / path.name)
486 |     values = {"version": __version__, "zip_name": zip_file.name, "zip_size": size_text(zip_file.stat().st_size),
487 |               "wheel_name": wheel.name, "wheel_size": size_text(wheel.stat().st_size),
488 |               "downloads": escape(downloads_url or "downloads/"), "site_url": escape(site_url)}
489 |     pages = render_docs(out, values)
490 |     home = fill((WEBSITE / "index.html").read_text(encoding="utf-8"),
491 |                 {**values, "canonical": canonical(site_url, "")})
492 |     (out / "index.html").write_text(home, encoding="utf-8")
493 |     robots = "User-agent: *\nAllow: /\n"
494 |     if site_url:
495 |         addresses = ["", "docs/index.html"] + [f"docs/{stem}.html" for stem in pages]
496 |         entries = "".join(f"<url><loc>{escape(site_url + address)}</loc></url>" for address in addresses)
497 |         (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
498 |                                          f'"http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n',
499 |                                          encoding="utf-8")
500 |         robots += f"Sitemap: {site_url}sitemap.xml\n"
501 |     (out / "robots.txt").write_text(robots, encoding="utf-8")
502 |     return pages
503 | 
504 | 
```
- Addresses get a trailing `/` so file names can be appended. The three release files must exist — otherwise the
  message says to run `build_release.py` first.
- A previous site in `--out` is replaced, but a folder that is not empty and has no `index.html` is never deleted: a
  mistyped `--out` must not wipe a folder of yours.
- Copies the static files, the assets and — unless `--downloads-url` — the downloads; renders the docs; fills the
  landing page; writes `robots.txt` (all pages may be indexed) and, with `--url`, `sitemap.xml` listing every page.

## Checking
```python
505 | class PageCheck(HTMLParser):
506 |     """Reads one page: every link and source address, every id, and whether each tag that was
507 |     opened is closed again, in order."""
508 | 
```
```python
509 |     def __init__(self) -> None:
510 |         super().__init__(convert_charrefs=True)
511 |         self.targets: list[str] = []
512 |         self.ids: set[str] = set()
513 |         self.open: list[str] = []
514 |         self.problems: list[str] = []
515 | 
```
```python
516 |     def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
517 |         for name, value in attrs:
518 |             if name in ("href", "src") and value:
519 |                 self.targets.append(value)
520 |             if name == "id" and value:
521 |                 self.ids.add(value)
522 |         if tag not in VOID:
523 |             self.open.append(tag)
524 | 
```
```python
525 |     def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
526 |         self.handle_starttag(tag, attrs)
527 |         if tag not in VOID:
528 |             self.open.pop()
529 | 
```
```python
530 |     def handle_endtag(self, tag: str) -> None:
531 |         if tag in VOID:
532 |             return
533 |         if self.open and self.open[-1] == tag:
534 |             self.open.pop()
535 |             return
536 |         self.problems.append(f"line {self.getpos()[0]}: </{tag}> closes "
537 |                              f"<{self.open[-1] if self.open else 'nothing'}>")
538 |         if tag in self.open:                            # recover, so one slip is reported once
539 |             while self.open.pop() != tag:
540 |                 pass
541 | 
542 | 
```
- Python's own HTML reader calls these for every tag. Opening a tag (that is not void) pushes it on a stack; closing
  must close the tag on top. A wrong one is reported with its line, and the stack is repaired so one mistake is not
  reported a hundred times. `href`, `src` and `id` values are collected for the link check.

```python
543 | def check_site(out: Path, private: list[str]) -> list[str]:
544 |     """Everything that would be wrong with the site: unfinished templates, unbalanced tags, links
545 |     to files or sections that do not exist, and any of the ``private`` words on any page."""
546 |     out = out.resolve()
547 |     problems: list[str] = []
548 |     pages: dict[Path, PageCheck] = {}
549 |     for path in sorted(out.rglob("*.html")):
550 |         text = path.read_text(encoding="utf-8")
551 |         name = path.relative_to(out).as_posix()
552 |         if "{{" in text:
553 |             problems.append(f"{name}: a {{{{placeholder}}}} was not filled in")
554 |         check = PageCheck()
555 |         check.feed(text)
556 |         check.close()
557 |         problems += [f"{name}: {problem}" for problem in check.problems]
558 |         if check.open:
559 |             problems.append(f"{name}: never closed: {', '.join(check.open)}")
560 |         pages[path] = check
561 |     for path, check in pages.items():
562 |         name = path.relative_to(out).as_posix()
563 |         for target in check.targets:
564 |             if target.startswith(("http://", "https://", "mailto:", "data:")):
565 |                 continue
566 |             address, _, part = target.partition("#")
567 |             if address.startswith("/"):
568 |                 file = out / address.lstrip("/")
569 |             else:
570 |                 file = (path.parent / address) if address else path
571 |             if address.endswith("/") or file.is_dir():
572 |                 file = file / "index.html"
573 |             file = file.resolve()
574 |             if not file.is_file():
575 |                 problems.append(f"{name}: link to a missing file: {target}")
576 |             elif part and file in pages and part not in pages[file].ids:
577 |                 problems.append(f"{name}: link to a missing section: {target}")
578 |     for path in sorted(out.rglob("*")):
579 |         if path.is_file() and path.suffix in (".html", ".css", ".js", ".txt", ".xml", ".svg", ""):
580 |             text = path.read_text(encoding="utf-8", errors="replace").lower()
581 |             for word in private:
582 |                 if word.lower() in text:
583 |                     problems.append(f"{path.relative_to(out).as_posix()}: shows '{word}'")
584 |     return problems
585 | 
586 | 
```
- For every page: no `{{`, balanced tags, nothing left open. For every link: outside addresses are skipped; `/x` is
  relative to the site's root, anything else to the page's folder; a folder means its `index.html`; the file must exist
  and a `#section` must be an id of that page.
- Then every text file is searched for the private words, ignoring letter case.

```python
587 | def private_words() -> list[str]:
588 |     """The repository owner's name as it appears in its GitHub address, also with spaces for "-":
589 |     the site must never show it. Empty when this folder has no GitHub remote."""
590 |     try:
591 |         remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
592 |                                 text=True, timeout=10).stdout.strip()
593 |     except (OSError, subprocess.SubprocessError):
594 |         return []
595 |     found = re.search(r"github\.com[/:]([^/]+)/", remote)
596 |     if not found:
597 |         return []
598 |     owner = found.group(1)
599 |     return sorted({owner, owner.replace("-", " ")})
600 | 
601 | 
```
- Asks git for the `origin` address and takes the owner from `github.com/<owner>/` (also the `git@github.com:<owner>/`
  form), with and without dashes. No git, no remote: nothing to search for.

```python
602 | def serve(out: Path, port: int) -> None:
603 |     """Show the site at http://127.0.0.1:<port>/ until Ctrl+C (only this computer can see it)."""
604 |     server = ThreadingHTTPServer(("127.0.0.1", port), partial(SimpleHTTPRequestHandler, directory=str(out)))
605 |     print(f"the site is at http://127.0.0.1:{port}/  (Ctrl+C stops)")
606 |     try:
607 |         server.serve_forever()
608 |     except KeyboardInterrupt:
609 |         pass
610 |     finally:
611 |         server.server_close()
612 | 
613 | 
```
- Python's built-in file server on `127.0.0.1` only (other computers cannot see it), until Ctrl+C. Good for a preview;
  for the real site use a proper host.

```python
614 | def main(argv: list[str]) -> int:
615 |     """Build the site, check it, and say where it is."""
616 |     parser = argparse.ArgumentParser(description="Build the localagent website into one folder.")
617 |     parser.add_argument("--out", type=Path, default=ROOT / "site", help="where to write it (default: site/)")
618 |     parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="where the release files are")
619 |     parser.add_argument("--url", default="", help="the site's own address, for canonical links and sitemap.xml")
620 |     parser.add_argument("--downloads-url", default="", help="serve the downloads from this address instead")
621 |     parser.add_argument("--serve", action="store_true", help="show the site at http://127.0.0.1:PORT/ afterwards")
622 |     parser.add_argument("--port", type=int, default=8000, help="the port for --serve (default 8000)")
623 |     args = parser.parse_args(argv)
624 |     for address in (args.url, args.downloads_url):
625 |         if address and urlsplit(address).scheme not in ("http", "https"):
626 |             raise SystemExit(f"{address}: an address must start with https:// (or http://)")
627 |     pages = build(args.out, args.dist, args.url, args.downloads_url)
628 |     problems = check_site(args.out, private_words())
629 |     if problems:
630 |         print("SITE PROBLEMS:\n  " + "\n  ".join(problems))
631 |         return 1
632 |     files = [path for path in args.out.rglob("*") if path.is_file()]
633 |     total = sum(path.stat().st_size for path in files)
634 |     print(f"site OK: {len(files)} files, {size_text(total)}, {len(pages)} documentation pages -> {args.out}")
635 |     if args.serve:
636 |         serve(args.out, args.port)
637 |     return 0
638 | 
639 | 
```
```python
640 | if __name__ == "__main__":
641 |     raise SystemExit(main(sys.argv[1:]))
```
- Reads the options (both addresses must start with `http://` or `https://`), builds, checks with the owner's name,
  prints the problems (exit 1) or the number of files, their size and the pages (exit 0), and serves when asked.
