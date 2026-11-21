# `tools/build_site.py` — the website, ready for any host

**Role:** turns the project into the product's website, in one folder of plain files: `site\`. No server program, no
database, no framework, nothing loaded from other sites — any web host can serve it, and you can preview it on your
own computer.

```
.venv\Scripts\python tools\build_release.py      # first: the download files in dist\
.venv\Scripts\python tools\build_site.py         # then: the site in site\, checked
site OK: 73 files, 25.5 MB, 52 pages -> ...\site
.venv\Scripts\python tools\build_site.py --serve # the same, then open http://127.0.0.1:8000
```

| Page | What it is for |
|---|---|
| `index.html` | the first impression: the headline, two clear buttons, and a terminal that replays **real recorded sessions** (fix a bug, explain a project, add a feature, offline); then what it works with, the numbers, features, how a change is checked, the models, the modes, questions |
| `download.html` | one card per system with its own download button, size and steps; every checksum; how to check a download; requirements; the release notes |
| `setup.html` | the setup builder: choose your computer, online model, offline model and memory — get the exact steps and commands, shareable as a link |
| `docs/` | the user guides (`website\guide\`) and every page of `docs\`, with a menu, breadcrumbs, "On this page", a pager, and search on every page |
| `changelog.html`, `privacy.html`, `license.html`, `404.html` | what changed, what happens with data, the license and third-party software, "not found" |

**Nothing happens by itself.** No page navigates, downloads or changes a link on its own: a download starts only when
you click that file's button, "Download" opens the download page instead of jumping somewhere, and the only thing the
download page does with your system is mark its card "For this computer". (An earlier version silently pointed the
main button somewhere else on computers that were not Windows; it was removed.)

**Privacy by design.** The site never links to the source repository and never names its owner. The build reads the
owner's name from the repository's GitHub address and refuses to finish when any page, style sheet, script, data or
text file contains it. It caught two real cases: an example commit address in the build log, and the license page,
whose copyright line carries the owner's name — the page now shows the license terms and points to the `LICENSE` file
in every download, which is where the MIT license requires the notice to be.

**Checked before it is called done.** Every page is parsed: every tag opened must be closed in order; every link and
image must point to a file of the site, and a `#section` to an id on that page; nothing the security policy blocks
may appear (inline scripts, inline styles, event handlers); no `{{placeholder}}` may be left; every JSON file must
parse. Any problem is listed and the build exits with 1.

## Putting it online
The folder works on any static host. Three ways that need no programming, none of which shows a GitHub account:
- **Netlify:** sign in at netlify.com, "Add new site → Deploy manually", and drag the `site` folder onto the page.
  Netlify also reads the `_headers` file (the security headers below).
- **Cloudflare Pages:** "Create → Pages → Upload assets", then the `site` folder. Cloudflare limits one file to 25 MiB;
  the Windows zip is 22.8 MB, so it fits.
- **Your own server:** copy the folder's contents into the web root of nginx, Apache or Caddy.

With your own address known, build with `--url https://your.site/` for canonical links and a `sitemap.xml`. To keep the
big downloads elsewhere (a file host, a bucket), build with `--downloads-url https://files.example/v0.6.0/` and upload
the three files of `dist\` there.

## The website's source (`website\`)
- `layout.html` — the frame of every page: the head (title, description, link previews, icons, the web manifest, the
  fonts, the style sheet, the two scripts), the top bar (menu, search, theme switch, Download, the phone menu), the
  footer, and the search dialog. `{{root}}` is how far the page is from the site's top: nothing, `../`, or `/` for the
  404 page, which a host shows at any address.
- `home.html`, `download.html`, `setup.html`, `docs.html`, `article.html`, `notfound.html` — the bodies, with `{{name}}`
  places the build fills in.
- `guide\*.md` — the user guides: Get started, Choose a model, Troubleshooting. `pages\privacy.md` — the privacy page.
- `sessions.json` — the player's four sessions, recorded for real on 21 Nov 2026 (Gemini for three; the Qwen3-8B model
  on the laptop, offline, for the fourth) and only shortened. Recording the offline one found a bug in the chat: a
  local model's plain answer was printed twice (fixed, see `cli.md`).
- `css\style.css` — the whole design: the self-hosted Inter and JetBrains Mono fonts, colours as variables (the dark
  theme only redefines them; without JavaScript the system's theme is followed), the hero, the terminal, cards, the
  setup builder, the three-column docs, the search dialog, the footer, layouts for tablets and phones, and print rules.
- `js\theme.js` — runs in the head, before anything is drawn, and sets the theme you chose (remembered in the
  browser) or your system's: no flash of the wrong colours.
- `js\site.js` — the extras, each optional and each in its own function: the theme switch; the phone menu; Copy
  buttons; sections that rise into view (never hidden when motion is unwelcome or nothing runs); the session player
  (types the recorded lines; tabs, pause, replay; starts when it comes into view); the download page's "For this
  computer" mark; the setup builder; search (Ctrl+K or `/`); the docs' folding menu and the "On this page" list that
  follows your reading. A failing part is logged and the others still start.
- `fonts\` — the two fonts (latin subsets, 131 KB together) with their SIL Open Font License texts.
- `_headers` — security headers for hosts that read this file: only the site's own files may load, no framing by other
  sites, no type guessing, no addresses passed on to outside links.

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
  9 | site/index.html            the home page: the session player, what it does, the models, questions
 10 | site/download.html         every file with its size and checksum, how to check it, release notes
 11 | site/setup.html            the setup builder: your choices turned into exact steps
 12 | site/docs/*.html           the user guides and every page of docs/, with search, menu and contents
 13 | site/changelog.html, privacy.html, license.html, 404.html
 14 | site/downloads/            the release files and SHA256SUMS.txt (not with --downloads-url)
 15 | site/css, js, fonts, assets, search-index.json, site.webmanifest, robots.txt, _headers (and sitemap.xml)
 16 | 
 17 | The site loads nothing from other sites and never links to the source repository. The build checks
 18 | every page: tags, links, sections, the security policy (no inline scripts or styles), its JSON
 19 | files - and refuses to finish if a page shows the repository owner's name.
 20 | """
 21 | from __future__ import annotations
 22 | 
 23 | import argparse
 24 | import hashlib
 25 | import html
 26 | import json
 27 | import re
 28 | import shutil
 29 | import subprocess
 30 | import sys
 31 | import unittest
 32 | from functools import partial
 33 | from html.parser import HTMLParser
 34 | from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
 35 | from pathlib import Path
 36 | from typing import Callable
 37 | from urllib.parse import urlsplit
 38 | 
 39 | ROOT = Path(__file__).resolve().parent.parent
 40 | sys.path.insert(0, str(ROOT))
 41 | from localagent import __version__  # noqa: E402  (after the path is set)
 42 | from localagent.models import CATALOG  # noqa: E402
 43 | from localagent.providers import LOCAL_SERVERS, PROVIDERS  # noqa: E402
 44 | 
 45 | WEBSITE, DOCS = ROOT / "website", ROOT / "docs"
 46 | STATIC_FOLDERS = ("css", "js", "fonts")
 47 | ASSETS = ("logo.svg", "social.png", "favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png")
 48 | SECTIONS = (                              # the documentation menu: (section, ((page, title, one line), ...))
 49 |     ("Get started", (("getting-started", "Get started", "install, set up, and finish a first task"),
 50 |                      ("choosing-a-model", "Choose a model", "free, your own key, or on your computer"),
 51 |                      ("troubleshooting", "Troubleshooting", "the usual problems and their fixes"))),
 52 |     ("Using localagent", (("05-user-manual", "User manual", "every command, flag, setting and tool"),
 53 |                           ("01-setup-and-models", "Online and offline", "every service, local model and mode"),
 54 |                           ("06-run-and-verify", "Run and verify", "every way to run it, and how to check it"),
 55 |                           ("04-start-from-zero", "Start from source", "every step from an empty computer"))),
 56 |     ("How it is built", (("00-overview", "How it works", "the architecture, one request through the code"),
 57 |                          ("02-roadmap", "Roadmap", "what is built, and what could come next"),
 58 |                          ("03-build-log", "Build log", "every decision and measurement, and why"))),
 59 | )
 60 | AREAS = {                                 # the code reference, grouped as the overview groups it
 61 |     "The agent": ("agent", "context", "tools", "verify", "sandbox", "runners", "checkpoint", "session",
 62 |                   "meter"),
 63 |     "Models": ("providers", "llm", "openai_compat", "backends", "resilient", "ratelimit", "localserver",
 64 |                "models", "fetch"),
 65 |     "Commands and settings": ("cli", "wizard", "doctor", "config", "settings", "server", "web", "mcp",
 66 |                               "package"),
 67 |     "Building and testing": ("tests", "evals", "build_docs", "build_release", "build_site", "check_commit",
 68 |                              "docker"),
 69 | }
 70 | LOCAL_HOW = {                             # the setup builder: how each local server is started
 71 |     "Ollama": ("Install Ollama from ollama.com, then download a model once:", "ollama pull qwen3:8b", "qwen3:8b"),
 72 |     "LM Studio": ("In LM Studio, download a model (for example Qwen3 8B) and start its local server on port 1234.",
 73 |                   "", "qwen/qwen3-8b"),
 74 |     "llama.cpp": ("Start llama.cpp's server with your model on port 8080:",
 75 |                   "llama-server -m your-model.gguf --port 8080 --jinja -c 8192", "your-model"),
 76 |     "vLLM": ("Start vLLM's OpenAI-compatible server on port 8000:", "vllm serve Qwen/Qwen3-8B --port 8000",
 77 |              "Qwen/Qwen3-8B"),
 78 |     "Jan": ("In Jan, start the local API server (port 1337) with the model you want.", "", "your-model"),
 79 | }
 80 | VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
 81 |                   "track", "wbr"})
 82 | FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$")
 83 | HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
 84 | RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
 85 | ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
 86 | TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
 87 | ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
 88 | CODE_SPAN = re.compile(r"(`+)(.+?)\1")
 89 | LINK = re.compile(r"(!?)\[([^\]]+)\]\(([^)\s]+)\)")
 90 | ANGLE_URL = re.compile(r"<(https?://[^>\s]+)>")
 91 | BARE_URL = re.compile(r"https?://[^\s<>\"'`]+")
 92 | MARKER = re.compile(r"\x00(\d+)\x00")
 93 | DOC_NAME = re.compile(r"(?:docs/)?([\w.-]+)\.md(#[\w-]+)?")
 94 | SITE_PAGE = re.compile(r"(\.\./)?[\w-]+\.html(#[\w-]+)?")
 95 | EMPHASIS = (
 96 |     (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "strong"),
 97 |     (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), "em"),
 98 |     (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "em"),
 99 | )
100 | Link = Callable[[str], "str | None"]       # a Markdown link target -> the site's address, or None
101 | 
102 | 
```
- The docstring lists every page and file the build writes. Imports: `argparse`, `hashlib` (the checksums shown on the
  download page), `html`, `json`, `re`, `shutil`, `subprocess` (asking git for the repository address), `sys`,
  `unittest` (counting the tests for the home page), `partial`, `HTMLParser` (the checks and the search index), the
  small web server (`--serve`), `Path`, `Callable`, `urlsplit`; then, from the program itself, its version, the model
  catalog, and the tables of online services and local servers — the site shows exactly what the program offers.
- `STATIC_FOLDERS` and `ASSETS` are copied as they are (the icons were drawn once from the logo with Edge).
- `SECTIONS` is the documentation menu: each section with its pages, their menu titles and one line each; the guides
  come first. `AREAS` groups the code reference the way `00-overview.md` does.
- `LOCAL_HOW` tells the setup builder how each local server is started, and a model name to show.
- The regular expressions recognise Markdown (fences, headings, rules, list items, table rules, escapes, code, links,
  addresses), the markers of kept HTML, documentation file names, and the site's own pages (`../download.html`).
- `EMPHASIS`: bold and italics; an asterisk or underscore inside a word does not count (`17*3`, `snake_case`).

## Small helpers
```python
103 | def escape(text: str) -> str:
104 |     """Text made safe for HTML (also inside attribute values). ``{`` is written as ``&#123;``,
105 |     which looks the same: then no text from the docs can ever look like a {{placeholder}}."""
106 |     return html.escape(text, quote=True).replace("{", "&#123;")
107 | 
108 | 
```
- `<`, `>`, `&` and quotes become HTML entities, so no text can ever become a tag; `{` becomes `&#123;` (it looks the
  same). Without that, three pages that document Docker's `{{.Names}}` and the slash-command template were reported as
  unfilled templates by the site check.

```python
109 | def plain(markdown: str) -> str:
110 |     """Inline Markdown as plain text: for titles, descriptions and heading ids. Code keeps its
111 |     underscores (`read_secret`); outside code, emphasis marks are dropped."""
112 |     text = LINK.sub(lambda m: m.group(2), markdown)
113 |     parts = re.split(r"(`+[^`]*`+)", text)
114 |     for index, part in enumerate(parts):
115 |         parts[index] = part.strip("`") if index % 2 else re.sub(r"\*+|(?<!\w)_+|_+(?!\w)", "", part)
116 |     return "".join(parts).strip()
117 | 
118 | 
```
```python
119 | def slug(text: str, used: set[str]) -> str:
120 |     """A heading's id: lower case, words joined by "-", unique on its page."""
121 |     base = re.sub(r"[^\w\s-]", "", plain(text).lower()).strip()
122 |     base = re.sub(r"[\s-]+", "-", base) or "section"
123 |     name, number = base, 2
124 |     while name in used:
125 |         name, number = f"{base}-{number}", number + 1
126 |     used.add(name)
127 |     return name
128 | 
129 | 
```
- `plain`: link labels instead of links; code loses its backticks but keeps its underscores (`read_secret`), and
  outside code the emphasis marks go — for titles and ids. The first version dropped every underscore, and the
  phone-width screenshot showed the contents of `wizard.md` listing "readsecret".
- `slug`: a heading's id, e.g. "`Config.from_env`" → `configfrom_env`; a second heading with the same words gets `-2`.

## Inline Markdown
```python
130 | def inline(text: str, link: Link) -> str:
131 |     """One line of Markdown as HTML: code, links, bare addresses, bold and italics. Everything
132 |     else is escaped, so text can never become a tag."""
133 |     kept: list[str] = []
134 |     text = _inline(text, link, kept)
135 |     while "\x00" in text:                                # a kept fragment may hold others
136 |         text = MARKER.sub(lambda m: kept[int(m.group(1))], text)
137 |     return text
138 | 
139 | 
```
```python
140 | def _inline(text: str, link: Link, kept: list[str]) -> str:
141 |     """The work of ``inline``. Finished HTML is put aside in ``kept`` and replaced by a marker
142 |     (NUL, its number, NUL), so later steps cannot change it; ``inline`` puts it back."""
143 |     def keep(fragment: str) -> str:
144 |         kept.append(fragment)
145 |         return f"\x00{len(kept) - 1}\x00"
146 | 
147 |     def code(match: re.Match) -> str:
148 |         content = match.group(2).strip()
149 |         target = link(content) if DOC_NAME.fullmatch(content) else None
150 |         span = f"<code>{escape(content)}</code>"
151 |         return keep(f'<a href="{escape(target)}">{span}</a>' if target else span)
152 | 
153 |     def anchor(match: re.Match) -> str:
154 |         image, label, target = match.groups()
155 |         address = link(target)
156 |         if image:
157 |             return keep(f'<img src="{escape(address)}" alt="{escape(label)}" loading="lazy">'
158 |                         if address else escape(label))
159 |         shown = _inline(label, link, kept)
160 |         return keep(f'<a href="{escape(address)}">{shown}</a>' if address else shown)
161 | 
162 |     def angle(match: re.Match) -> str:
163 |         address = escape(match.group(1))
164 |         return keep(f'<a href="{address}">{address}</a>')
165 | 
166 |     def bare(match: re.Match) -> str:
167 |         address = match.group(0).rstrip(".,;:!?")
168 |         if address.endswith(")") and "(" not in address:
169 |             address = address[:-1]
170 |         return keep(f'<a href="{escape(address)}">{escape(address)}</a>') + match.group(0)[len(address):]
171 | 
172 |     text = ESCAPED.sub(lambda m: keep(escape(m.group(1))), text)
173 |     text = CODE_SPAN.sub(code, text)
174 |     text = LINK.sub(anchor, text)
175 |     text = ANGLE_URL.sub(angle, text)
176 |     parts = re.split(r"(\x00\d+\x00)", text)
177 |     for index in range(0, len(parts), 2):                # only text, never a kept fragment
178 |         parts[index] = BARE_URL.sub(bare, parts[index])
179 |     text = escape("".join(parts))
180 |     for pattern, tag in EMPHASIS:
181 |         text = pattern.sub(partial(wrap, tag), text)
182 |     return text
183 | 
184 | 
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
185 | def wrap(tag: str, match: re.Match) -> str:
186 |     """The text a pattern found, inside ``<tag>``: **bold** becomes <strong>bold</strong>."""
187 |     return f"<{tag}>{match.group(1)}</{tag}>"
188 | 
189 | 
```
- Puts the text a pattern found into a tag; a named function instead of a lambda (which mypy cannot type here).

## Blocks
```python
190 | def starts_block(line: str) -> bool:
191 |     """True when a line begins something other than paragraph text."""
192 |     stripped = line.strip()
193 |     return bool(FENCE.match(line) or HEADING.match(line) or RULE.match(line) or ITEM.match(line)
194 |                 or stripped.startswith(("|", ">")))
195 | 
196 | 
```
```python
197 | def interrupts(line: str) -> bool:
198 |     """True when a line ends a paragraph: any block start, except a numbered line that does not
199 |     start at 1 - a sentence may well begin with "2026." after a line break."""
200 |     item = ITEM.match(line)
201 |     if item and item.group(2)[0].isdigit() and item.group(2)[:-1] != "1":
202 |         return False
203 |     return starts_block(line)
204 | 
205 | 
```
```python
206 | def indent_of(line: str) -> int:
207 |     """How many spaces a line starts with."""
208 |     return len(line) - len(line.lstrip(" "))
209 | 
210 | 
```
- `starts_block`: a fence, heading, rule, list item, table row or quote. `interrupts` is the paragraph's version: a
  numbered line ends a paragraph only when it starts at 1, as in CommonMark — the build log has sentences that wrap
  onto a new line starting with a year.

```python
211 | class Renderer:
212 |     """Markdown to HTML for the documentation: the parts the docs use (headings, paragraphs,
213 |     lists, code blocks, tables, quotes, rules, links), each kept simple and safe."""
214 | 
```
```python
215 |     def __init__(self, link: Link) -> None:
216 |         self.link = link
217 |         self.used: set[str] = set()
218 |         self.headings: list[tuple[int, str, str]] = []      # (level, id, text) for the contents
219 | 
```
```python
220 |     def render(self, text: str) -> str:
221 |         """A whole page."""
222 |         return self.blocks(text.replace("\r\n", "\n").split("\n"))
223 | 
```
- One renderer per page: it remembers the ids already used and every heading (for the contents box).

```python
224 |     def blocks(self, lines: list[str]) -> str:
225 |         """A run of lines: each block in turn."""
226 |         out: list[str] = []
227 |         i = 0
228 |         while i < len(lines):
229 |             line = lines[i]
230 |             if not line.strip():
231 |                 i += 1
232 |             elif FENCE.match(line):
233 |                 i = self.code_block(lines, i, out)
234 |             elif heading := HEADING.match(line):
235 |                 out.append(self.heading(len(heading.group(1)), heading.group(2)))
236 |                 i += 1
237 |             elif RULE.match(line):
238 |                 out.append("<hr>")
239 |                 i += 1
240 |             elif line.lstrip().startswith("|") and i + 1 < len(lines) and TABLE_RULE.match(lines[i + 1]):
241 |                 i = self.table(lines, i, out)
242 |             elif line.lstrip().startswith(">"):
243 |                 quoted = []
244 |                 while i < len(lines) and lines[i].lstrip().startswith(">"):
245 |                     quoted.append(re.sub(r"^\s*> ?", "", lines[i]))
246 |                     i += 1
247 |                 out.append(f"<blockquote>{self.blocks(quoted)}</blockquote>")
248 |             elif ITEM.match(line):
249 |                 i = self.list_block(lines, i, out)
250 |             else:
251 |                 text = [line.strip()]
252 |                 i += 1
253 |                 while i < len(lines) and lines[i].strip() and not interrupts(lines[i]):
254 |                     text.append(lines[i].strip())
255 |                     i += 1
256 |                 out.append(f"<p>{inline(chr(10).join(text), self.link)}</p>")
257 |         return "\n".join(out)
258 | 
```
```python
259 |     def heading(self, level: int, title: str) -> str:
260 |         """A heading with its id (remembered for the page's contents) and a "#" link to itself."""
261 |         ident = slug(title, self.used)
262 |         self.headings.append((level, ident, plain(title)))
263 |         mark = f'<a class="anchor" href="#{ident}" aria-label="Link to this section">#</a>' if level > 1 else ""
264 |         return f'<h{level} id="{ident}">{inline(title, self.link)}{mark}</h{level}>'
265 | 
```
- Walks the lines and hands each block to its method. Quotes are rendered recursively (a quote may hold a list).
  A paragraph is all lines up to a blank line or a line that interrupts it.
- Headings get their id and, from level 2 on, a `#` link to themselves (shown when the mouse is over the heading).

```python
266 |     def code_block(self, lines: list[str], i: int, out: list[str]) -> int:
267 |         """A fenced code block, shown exactly as written."""
268 |         match = FENCE.match(lines[i])
269 |         assert match is not None
270 |         fence, language = match.group(1), match.group(2)
271 |         body: list[str] = []
272 |         i += 1
273 |         while i < len(lines) and not (lines[i].strip().startswith(fence)
274 |                                       and set(lines[i].strip()) == {fence[0]}):
275 |             body.append(lines[i])
276 |             i += 1
277 |         kind = f' class="language-{escape(language)}"' if language else ""
278 |         out.append(f"<pre><code{kind}>{escape(chr(10).join(body))}</code></pre>")
279 |         return i + 1
280 | 
```
- Everything up to the closing fence (the same character, at least as many) exactly as written, escaped; the
  language becomes a class name.

```python
281 |     def table(self, lines: list[str], i: int, out: list[str]) -> int:
282 |         """A table: a header row, the |---| rule (which also sets each column's alignment), rows."""
283 |         def cells(row: str) -> list[str]:
284 |             row = row.strip().removeprefix("|")
285 |             if row.endswith("|") and not row.endswith("\\|"):
286 |                 row = row[:-1]
287 |             return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row)]
288 | 
289 |         head = cells(lines[i])
290 |         aligns = []
291 |         for rule in cells(lines[i + 1]):
292 |             aligns.append(' class="al-c"' if rule.startswith(":") and rule.endswith(":")
293 |                           else ' class="al-r"' if rule.endswith(":") else "")
294 |         aligns += [""] * (len(head) - len(aligns))
295 |         rows = []
296 |         i += 2
297 |         while i < len(lines) and lines[i].lstrip().startswith("|"):
298 |             values = cells(lines[i]) + [""] * len(head)
299 |             rows.append("<tr>" + "".join(f"<td{aligns[n]}>{inline(values[n], self.link)}</td>"
300 |                                          for n in range(len(head))) + "</tr>")
301 |             i += 1
302 |         header = "".join(f"<th{aligns[n]}>{inline(cell, self.link)}</th>" for n, cell in enumerate(head))
303 |         out.append(f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead>'
304 |                    f"<tbody>{''.join(rows)}</tbody></table></div>")
305 |         return i
306 | 
```
- Cells are split at `|` but not at `\|` (an escaped pipe, as in `/model online\|offline`). `:---:` centres a column,
  `---:` aligns it right — with classes, not inline styles, which the security policy would block. Rows with too few
  cells are filled up. The table sits in a box that scrolls sideways on phones.

```python
307 |     def list_block(self, lines: list[str], i: int, out: list[str]) -> int:
308 |         """A list. An item owns the lines indented under it (nested lists, code); a blank line
309 |         between items makes the list "loose" (each item a paragraph)."""
310 |         first = ITEM.match(lines[i])
311 |         assert first is not None
312 |         base, ordered = len(first.group(1)), first.group(2)[0].isdigit()
313 |         items: list[list[str]] = []
314 |         loose = False
315 |         while i < len(lines):
316 |             match = ITEM.match(lines[i])
317 |             if not match or len(match.group(1)) != base or match.group(2)[0].isdigit() != ordered:
318 |                 break
319 |             content = match.start(3)
320 |             item = [match.group(3)]
321 |             i += 1
322 |             while i < len(lines):
323 |                 line = lines[i]
324 |                 if not line.strip():
325 |                     after = i + 1
326 |                     while after < len(lines) and not lines[after].strip():
327 |                         after += 1
328 |                     if after < len(lines) and indent_of(lines[after]) >= content:
329 |                         item += [""] * (after - i)
330 |                         loose = True
331 |                         i = after
332 |                         continue
333 |                     break
334 |                 if indent_of(line) >= content:
335 |                     item.append(line[content:])
336 |                 elif indent_of(line) > base and not ITEM.match(line):
337 |                     item.append(line.strip())
338 |                 elif indent_of(line) <= base and not starts_block(line):
339 |                     item.append(line.strip())            # a "lazy" continuation line
340 |                 else:
341 |                     break
342 |                 i += 1
343 |             items.append(item)
344 |             after = i
345 |             while after < len(lines) and not lines[after].strip():
346 |                 after += 1
347 |             following = ITEM.match(lines[after]) if after < len(lines) else None
348 |             if after > i and following and len(following.group(1)) == base:
349 |                 loose = True
350 |                 i = after
351 |         parts = []
352 |         for item in items:
353 |             body = self.blocks(item)
354 |             if not loose:
355 |                 body = re.sub(r"^<p>(.*?)</p>", r"\1", body, count=1, flags=re.S)
356 |             parts.append(f"<li>{body}</li>")
357 |         tag = "ol" if ordered else "ul"
358 |         start = int(first.group(2)[:-1]) if ordered else 1
359 |         opening = f'<ol start="{start}">' if ordered and start != 1 else f"<{tag}>"
360 |         out.append(opening + "".join(parts) + f"</{tag}>")
361 |         return i
362 | 
363 | 
```
- An item owns the lines indented under it — at least to where its text starts — so nested lists and code blocks
  inside items work, dedented and rendered recursively. A blank line inside an item or between items makes the list
  "loose": its items keep their paragraphs; in a tight list the first paragraph is unwrapped. A numbered list that does
  not start at 1 keeps its number (`start="3"`).

## Pages and their data
```python
364 | def page_title(markdown: str) -> tuple[str, str]:
365 |     """(title, one line) from a page's first heading: "`localagent/fetch.py` — downloads you can
366 |     trust" gives ("fetch.py", "downloads you can trust")."""
367 |     first = next((line for line in markdown.splitlines() if line.startswith("# ")), "# Page")
368 |     title, _, rest = first[2:].partition(" — ")
369 |     name = plain(title)
370 |     return (name.rstrip("/").rsplit("/", 1)[-1] if "`" in title else name), plain(rest)
371 | 
372 | 
```
```python
373 | def describe(markdown: str) -> str:
374 |     """The first real paragraph as plain text, at most 155 characters: the page's description."""
375 |     for block in re.split(r"\n\s*\n", markdown):
376 |         text = block.strip()
377 |         if text and not starts_block(text.splitlines()[0]):
378 |             words = plain(" ".join(text.split()))
379 |             return words if len(words) <= 155 else words[:152].rsplit(" ", 1)[0] + "..."
380 |     return "localagent documentation"
381 | 
382 | 
383 | 
```
- `page_title`: from "`` `localagent/fetch.py` `` — downloads you can trust" the menu title `fetch.py` and the line
  "downloads you can trust"; a folder page (`` `tests/` ``) becomes `tests`.
- `describe`: the first paragraph, at most 155 characters, cut at a word: the page's description for search engines
  and link previews.

```python
384 | def doc_order(stems: list[str]) -> list[tuple[str, list[str]]]:
385 |     """The documentation menu: each section with its pages in order, then the code reference, one
386 |     group per area. A page that no group names lands in "More", so a new page never goes missing."""
387 |     present = set(stems)
388 |     groups = [(section, [stem for stem, _, _ in members if stem in present]) for section, members in SECTIONS]
389 |     groups += [(area, [stem for stem in names if stem in present]) for area, names in AREAS.items()]
390 |     named = {stem for _, members in groups for stem in members}
391 |     groups.append(("More", sorted(present - named)))
392 |     return [(title, members) for title, members in groups if members]
393 | 
394 | 
```
- The menu: the sections of `SECTIONS`, then the code reference by area. A page that no group names lands in "More",
  so a new page is never left out.

```python
395 | def fill(template: str, values: dict[str, str]) -> str:
396 |     """Put the values into a template's {{name}} places; a name without a value is an error."""
397 |     missing = sorted(set(re.findall(r"{{(\w+)}}", template)) - set(values))
398 |     if missing:
399 |         raise SystemExit(f"the template needs values for: {', '.join(missing)}")
400 |     return re.sub(r"{{(\w+)}}", lambda m: values[m.group(1)], template)
401 | 
402 | 
```
```python
403 | def size_text(size: int) -> str:
404 |     """A file size the way people read it: "120 KB", "22.8 MB"."""
405 |     return f"{size / 1e3:.0f} KB" if size < 1e6 else f"{size / 1e6:.1f} MB"
406 | 
407 | 
```
```python
408 | def canonical(site_url: str, path: str) -> str:
409 |     """The <link rel="canonical"> tag for a page, when the site's address is known."""
410 |     return f'<link rel="canonical" href="{escape(site_url + path)}">' if site_url else ""
411 | 
412 | 
```
- `fill`: replaces every `{{name}}` of a template, and refuses to go on when the template asks for a value nobody gave.
  Values are put in as they are, never read again for places.
- `size_text`: "114 KB", "22.8 MB". `canonical`: the tag naming a page's one true address, when the address is known.

```python
413 | def json_script(data: object) -> str:
414 |     """Data for a page's script, safe inside <script type="application/json">: no "</" can end the
415 |     element early, and no "{{" can look like a template place."""
416 |     return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("{{", "{\\u007b")
417 | 
418 | 
```
- Data for a page's script (sessions, the setup builder's tables) is JSON inside `<script type="application/json">`.
  `<` is written as `<`, so no `</script>` inside a string can end the element; a `{{` inside a string becomes
  `{{`, so it cannot look like an unfilled place. Both read back as the same data.

```python
419 | def file_info(path: Path) -> dict[str, str]:
420 |     """A download file's name, its size as people read it, and its SHA-256 checksum."""
421 |     return {"name": path.name, "size": size_text(path.stat().st_size),
422 |             "sha": hashlib.sha256(path.read_bytes()).hexdigest()}
423 | 
424 | 
```
```python
425 | def release_date(changelog: str) -> str:
426 |     """The date of this version's section of CHANGELOG.md ("## 0.6.0 — 21 November 2026")."""
427 |     found = re.search(rf"^## {re.escape(__version__)} — (.+)$", changelog, re.M)
428 |     return found.group(1).strip() if found else "not yet released"
429 | 
430 | 
```
```python
431 | def release_section(changelog: str) -> str:
432 |     """This version's part of CHANGELOG.md, without its heading."""
433 |     found = re.search(rf"^## {re.escape(__version__)} .*?$(.*?)(?=^## |\Z)", changelog, re.M | re.S)
434 |     return found.group(1).strip() if found else ""
435 | 
436 | 
```
```python
437 | def test_count() -> int:
438 |     """How many automated tests there are, counted exactly as the test runner counts them."""
439 |     return unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT)).countTestCases()
440 | 
441 | 
```
- `file_info`: a download's name, size and SHA-256 — computed from the file itself, so the page can never show a
  checksum of another build.
- `release_date` and `release_section`: this version's date and notes from `CHANGELOG.md`.
- `test_count`: the tests counted exactly as the test runner counts them, for the home page's numbers.

```python
442 | def read(name: str) -> str:
443 |     """A file of the website's source folder."""
444 |     return (WEBSITE / name).read_text(encoding="utf-8")
445 | 
446 | 
```
```python
447 | def page(layout: str, values: dict[str, str], body: str, *, root: str, title: str, description: str,
448 |          path: str | None, current: str = "", head: str = "") -> str:
449 |     """A whole page: the shared frame around ``body``, with this page's title, description and
450 |     address. ``current`` marks the page's own entry in the top menu."""
451 |     site_url = values["site_url"]
452 |     whole = fill(layout, {**values, "root": root, "title": escape(title), "description": escape(description),
453 |                           "canonical": canonical(site_url, path) if path is not None else "",
454 |                           "og_image": (site_url or root) + "assets/social.png", "head_extra": head, "body": body})
455 |     if current:
456 |         whole = whole.replace(f'data-page="{current}"', f'data-page="{current}" aria-current="page"')
457 |     return whole
458 | 
459 | 
```
- `page` wraps a body into the frame: the title, the description (also for link previews), the canonical address, the
  preview image (absolute when the site's address is known), extra head tags (the 404 page asks not to be indexed).
  `current` adds `aria-current="page"` to the page's own entry in the top menu — screen readers announce it, the style
  sheet highlights it.

```python
460 | def make_link(pages: dict[str, str], prefix: str) -> Link:
461 |     """The link rule for pages ``prefix`` away from the docs folder ("" inside it, "docs/" from the
462 |     top): outside addresses and #sections stay, the site's own pages stay, a documentation page
463 |     becomes its .html, and anything else is not on the site (None)."""
464 |     def link(target: str) -> str | None:
465 |         if target.startswith(("http://", "https://", "#", "mailto:")) or SITE_PAGE.fullmatch(target):
466 |             return target
467 |         path, _, part = target.partition("#")
468 |         name = Path(path).name
469 |         if name.endswith(".md") and name[:-3] in pages:
470 |             return prefix + name[:-3] + ".html" + (f"#{part}" if part else "")
471 |         return None
472 |     return link
473 | 
474 | 
```
- The link rule for a page: outside addresses and `#sections` stay, the site's own pages (`../download.html`) stay, a
  documentation page becomes its `.html` — with `docs/` in front for pages at the top — and anything else is shown as
  its label (`None`).

```python
475 | def session_lines(lines: list[list[str]]) -> str:
476 |     """One recorded session as HTML lines, the way the player draws them - so the page shows a
477 |     complete session even without JavaScript."""
478 |     rows = []
479 |     for kind, text, *extra in lines:
480 |         if kind in ("user", "cmd"):
481 |             prompt = "you&gt; " if kind == "user" else "$ "
482 |             rows.append(f'<div><span class="prompt">{prompt}</span><span class="{kind}">{escape(text)}</span></div>')
483 |         elif kind == "tool":
484 |             rows.append(f'<div><span class="tool">{escape(text)}</span>'
485 |                         f'<span class="args">{escape(extra[0] if extra else "")}</span></div>')
486 |         else:
487 |             rows.append(f'<div class="{kind}">{escape(text)}</div>')
488 |     return "".join(rows)
489 | 
490 | 
```
```python
491 | def home_body(values: dict[str, str]) -> str:
492 |     """The home page: the session player (first session shown complete), the numbers, the rest."""
493 |     sessions = json.loads(read("sessions.json"))
494 |     first = sessions[0]
495 |     tabs = "".join(f'<button type="button" role="tab" aria-selected="{"true" if number == 0 else "false"}" '
496 |                    f'data-session="{escape(session["id"])}">{escape(session["tab"])}</button>'
497 |                    for number, session in enumerate(sessions))
498 |     return fill(read("home.html"), {**values, "session_tabs": tabs, "session_lines": session_lines(first["lines"]),
499 |                                     "session_summary": escape(first["summary"]),
500 |                                     "session_folder": escape(first["folder"]), "sessions_json": json_script(sessions),
501 |                                     "test_count": str(test_count()), "provider_count": f"{len(PROVIDERS) - 1}+",
502 |                                     "server_count": str(len(LOCAL_SERVERS))})
503 | 
504 | 
```
- `session_lines`: a recorded session as the player draws it, so the page shows a complete session even when
  JavaScript is off. Every text is escaped.
- `home_body`: the tabs (one per session), the first session in full, all sessions as data for the player, and the
  numbers: the tests, the online services ("7+": the named ones, plus any compatible address), the local servers.

```python
505 | def choices(name: str, options: list[tuple[str, str, str]], checked: str) -> str:
506 |     """Radio cards for the setup builder: (value, title, small print) each; ``checked`` is chosen."""
507 |     cards = []
508 |     for value, title, note in options:
509 |         ident = f"{name}-{value}"
510 |         mark = " checked" if value == checked else ""
511 |         cards.append(f'<div class="choice"><input type="radio" name="{name}" id="{ident}" value="{escape(value)}"'
512 |                      f'{mark}><label for="{ident}"><strong>{escape(title)}</strong><small>{escape(note)}</small>'
513 |                      "</label></div>")
514 |     return "".join(cards)
515 | 
516 | 
```
```python
517 | def server_id(title: str) -> str:
518 |     """A local server's short name: "LM Studio" -> "lmstudio", "llama.cpp" -> "llamacpp"."""
519 |     return re.sub(r"[^a-z]", "", title.lower())
520 | 
521 | 
```
```python
522 | def memory_label(memory: int, smallest_other: int) -> str:
523 |     """How a model's memory threshold reads: 14 -> "16 GB or more"; 0 -> "Less than 8 GB"."""
524 |     common = next(size for size in (4, 8, 16, 32, 64, 128) if size >= max(memory, smallest_other))
525 |     return f"{common} GB or more" if memory else f"Less than {common} GB"
526 | 
527 | 
```
```python
528 | def setup_body(values: dict[str, str]) -> str:
529 |     """The setup builder: its choices and the data its script turns into steps - all from the
530 |     program's own tables (providers.py, models.py), so the page cannot drift from the program."""
531 |     online = [{"id": provider.name, "title": provider.title[0].upper() + provider.title[1:], "note": provider.note,
532 |                "env": provider.key_env, "page": provider.key_page,
533 |                "model": provider.suggested[0] if provider.suggested else "", "menu": number,
534 |                "menu_title": provider.title} for number, provider in enumerate(PROVIDERS, 1)]
535 |     local: list[dict[str, str]] = [{"id": "download", "title": "A free model, downloaded for you",
536 |                                     "menu_label": "download a free model for me"}]
537 |     for server in LOCAL_SERVERS:
538 |         how, command, model = LOCAL_HOW[server.title]
539 |         local.append({"id": server_id(server.title), "title": server.title, "kind": server.kind,
540 |                       "base": server.base if server.kind == "openai" else "", "model": model, "how": how,
541 |                       "command": command, "menu_label": f"{server.title} at {server.base}"})
542 |     local.append({"id": "remote", "title": "A server on another computer", "kind": "openai",
543 |                   "base": "http://192.168.1.20:8080/v1", "model": "your-model", "command": "",
544 |                   "how": "Start the model server on that computer so that it listens on the network (llama.cpp: "
545 |                          "add --host 0.0.0.0), and note its address.",
546 |                   "menu_label": "a model server at another address"})
547 |     models = [{"name": model.name, "size": round(model.size / 1e9, 1), "memory": model.memory_gb} for model in CATALOG]
548 |     data = {"zip": values["zip_name"], "zip_size": values["zip_size"], "wheel": values["wheel_name"],
549 |             "skip_number": len(PROVIDERS) + 1, "online": online, "local": local, "models": models}
550 |     online_cards = choices("online", [(provider.name, provider.title[0].upper() + provider.title[1:], provider.note)
551 |                                      for provider in PROVIDERS]
552 |                            + [("none", "No online model", "work offline only")], "gemini")
553 |     offline_cards = choices("offline", [("download", "A free model for me", "downloaded and started for you")]
554 |                             + [(server_id(server.title), server.title, f"already running, port {urlsplit(server.base).port}")
555 |                                for server in LOCAL_SERVERS]
556 |                             + [("remote", "Another computer", "a server on your network"),
557 |                                ("none", "No offline model", "online only")], "download")
558 |     thresholds = [model.memory_gb for model in CATALOG if model.memory_gb]
559 |     ram_cards = choices("ram", [(str(model.memory_gb), memory_label(model.memory_gb, min(thresholds)),
560 |                                  f"{model.name} · {round(model.size / 1e9, 1)} GB download") for model in CATALOG],
561 |                         str(CATALOG[0].memory_gb))
562 |     return fill(read("setup.html"), {**values, "online_choices": online_cards, "offline_choices": offline_cards,
563 |                                      "ram_choices": ram_cards, "setup_json": json_script(data)})
564 | 
565 | 
```
- `choices`: radio buttons drawn as cards (the button is invisible but stays in place, so keyboards and screen readers
  use it as usual).
- `server_id`: "LM Studio" → `lmstudio`, the value in the page's address. `memory_label`: a model's memory threshold
  as people say it — 14 → "16 GB or more", 0 → "Less than 8 GB".
- `setup_body`: the online services come from `PROVIDERS` (with their setup menu numbers, so the steps can say
  "choose 2) OpenAI"), the local servers from `LOCAL_SERVERS` plus how to start each, the models and memory choices
  from `CATALOG`. The page cannot drift from the program: change a table, rebuild, and the builder follows.

## Search
```python
566 | class SectionText(HTMLParser):
567 |     """Reads a rendered page and collects the text under each h2 and h3 heading - code blocks and
568 |     the "#" links left out: the snippets of the search index."""
569 | 
```
```python
570 |     def __init__(self) -> None:
571 |         super().__init__(convert_charrefs=True)
572 |         self.sections: list[list[str]] = [["", "", ""]]      # [id, heading, text]; the first: the intro
573 |         self.in_heading = False
574 |         self.in_code = 0
575 |         self.in_anchor = False
576 | 
```
```python
577 |     def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
578 |         found = dict(attrs)
579 |         if tag in ("h2", "h3"):
580 |             self.sections.append([found.get("id") or "", "", ""])
581 |             self.in_heading = True
582 |         elif tag == "pre":
583 |             self.in_code += 1
584 |         elif tag == "a" and found.get("class") == "anchor":
585 |             self.in_anchor = True
586 | 
```
```python
587 |     def handle_endtag(self, tag: str) -> None:
588 |         if tag in ("h2", "h3"):
589 |             self.in_heading = False
590 |         elif tag == "pre":
591 |             self.in_code -= 1
592 |         elif tag == "a":
593 |             self.in_anchor = False
594 | 
```
```python
595 |     def handle_data(self, data: str) -> None:
596 |         if not self.in_code and not self.in_anchor:
597 |             self.sections[-1][1 if self.in_heading else 2] += data
598 | 
599 | 
```
```python
600 | def search_entries(body: str, title: str, url: str, description: str, guide: bool) -> list[dict]:
601 |     """The search index's entries for one page: the page itself, then each of its sections."""
602 |     reader = SectionText()
603 |     reader.feed(body)
604 |     entries: list[dict] = [{"p": title, "h": "", "u": url, "x": description, **({"g": 1} if guide else {})}]
605 |     for ident, heading, text in reader.sections[1:]:
606 |         if ident and heading.strip():
607 |             snippet = " ".join(text.split())
608 |             entries.append({"p": title, "h": " ".join(heading.split()), "u": f"{url}#{ident}",
609 |                             "x": snippet[:140] + ("…" if len(snippet) > 140 else ""), **({"g": 1} if guide else {})})
610 |     return entries
611 | 
612 | 
```
- Reads a rendered page and collects the text under each `h2`/`h3` heading by its id — code blocks and the `#` links
  left out. `search_entries` makes one entry for the page and one for each section: page title, heading, address with
  `#id`, and the first 140 characters as the snippet. Entries of the user guides carry `"g": 1`, and the search ranks
  them first — a person asking about Ollama wants "Choose a model" before the code of the Ollama client.

## The documentation pages
```python
613 | def render_docs(pages: dict[str, str], layout: str, values: dict[str, str], write: Callable[[str, str], None],
614 |                 index: list[dict]) -> list[str]:
615 |     """Every documentation page as docs/<name>.html, and docs/index.html. Returns the pages in
616 |     menu order; their search entries go into ``index``."""
617 |     groups = doc_order(list(pages))
618 |     order = [stem for _, members in groups for stem in members]
619 |     titles = {stem: page_title(text) for stem, text in pages.items()}
620 |     menu = {stem: (title, line) for _, members in SECTIONS for stem, title, line in members}
621 |     section_of = {stem: title for title, members in groups for stem in members}
622 |     link = make_link(pages, "")
623 |     template = read("docs.html")
624 | 
625 |     def label(stem: str) -> str:
626 |         return menu[stem][0] if stem in menu else titles[stem][0]
627 | 
628 |     def blurb(stem: str) -> str:
629 |         return menu[stem][1] if stem in menu else titles[stem][1]
630 | 
631 |     def sidebar(current: str) -> str:
632 |         parts, reference = [], False
633 |         for title, members in groups:
634 |             if title in AREAS:
635 |                 if not reference:
636 |                     parts.append("<h2>Code reference</h2>")
637 |                     reference = True
638 |                 parts.append(f'<p class="sub">{escape(title)}</p>')
639 |             else:
640 |                 parts.append(f"<h2>{escape(title)}</h2>")
641 |             entries = []
642 |             for stem in members:
643 |                 here = ' aria-current="page"' if stem == current else ""
644 |                 entries.append(f'<li><a href="{stem}.html"{here}>{escape(label(stem))}</a></li>')
645 |             parts.append(f"<ul>{''.join(entries)}</ul>")
646 |         return "\n".join(parts)
647 | 
648 |     def pager(number: int) -> str:
649 |         links = ""
650 |         if number > 0:
651 |             back = order[number - 1]
652 |             links += f'<a class="prev" href="{back}.html"><small>Previous</small>{escape(label(back))}</a>'
653 |         if number + 1 < len(order):
654 |             ahead = order[number + 1]
655 |             links += f'<a class="next" href="{ahead}.html"><small>Next</small>{escape(label(ahead))}</a>'
656 |         return links
657 | 
658 |     for number, stem in enumerate(order):
659 |         renderer = Renderer(link)
660 |         body = renderer.render(pages[stem])
661 |         sections = [(ident, text) for level, ident, text in renderer.headings if level == 2]
662 |         toc = ""
663 |         if len(sections) >= 3:
664 |             entries = "".join(f'<li><a href="#{ident}">{escape(text)}</a></li>' for ident, text in sections)
665 |             toc = f'<aside class="toc" aria-label="On this page"><strong>On this page</strong><ul>{entries}</ul></aside>'
666 |         middle = "" if section_of[stem] == label(stem) else f"<li>{escape(section_of[stem])}</li>"
667 |         crumbs = (f'<li><a href="index.html">Docs</a></li>{middle}'
668 |                   f'<li><span aria-current="page">{escape(label(stem))}</span></li>')
669 |         inner = fill(template, {"layout_class": "" if toc else " no-toc", "sidebar": sidebar(stem),
670 |                                 "breadcrumbs": crumbs, "content": body, "pager": pager(number), "toc": toc})
671 |         description = describe(pages[stem])
672 |         write(f"docs/{stem}.html", page(layout, values, inner, root="../", title=f"{label(stem)} — localagent docs",
673 |                                         description=description, path=f"docs/{stem}.html", current="docs"))
674 |         guide = section_of[stem] in ("Get started", "Using localagent")
675 |         index.extend(search_entries(body, label(stem), f"docs/{stem}.html", description, guide))
676 |     cards = ['<h1>Documentation</h1><p class="lead-text">Guides for using localagent, and a page for every file of '
677 |              "its code that explains each line — written so that a beginner can follow it.</p>"
678 |              '<p><button class="btn btn-secondary" type="button" data-search-open>Search the documentation</button></p>']
679 |     reference = False
680 |     for title, members in groups:
681 |         entries = "".join(f'<li><a href="{stem}.html"><strong>{escape(label(stem))}</strong>'
682 |                           f"<span>{escape(blurb(stem))}</span></a></li>" for stem in members)
683 |         if title in AREAS:
684 |             if not reference:
685 |                 cards.append('<h2 id="code-reference">Code reference</h2><p>Every file of the program, explained line '
686 |                              "by line.</p>")
687 |                 reference = True
688 |             cards.append(f"<h3>{escape(title)}</h3>")
689 |         else:
690 |             cards.append(f'<h2 id="{slug(title, set())}">{escape(title)}</h2>')
691 |         cards.append(f'<ul class="doc-cards">{entries}</ul>')
692 |     inner = fill(template, {"layout_class": " no-toc", "sidebar": sidebar("index"), "pager": "",
693 |                             "breadcrumbs": '<li><span aria-current="page">Docs</span></li>',
694 |                             "content": f'<div class="doc-index">{"".join(cards)}</div>', "toc": ""})
695 |     write("docs/index.html", page(layout, values, inner, root="../", title="Documentation — localagent",
696 |                                   description="Guides for using localagent, and line-by-line documentation of its code.",
697 |                                   path="docs/index.html", current="docs"))
698 |     return order
699 | 
700 | 
```
- One renderer per page; a page with three or more sections gets the "On this page" list (shown beside the text on
  wide screens). The breadcrumbs name the section, unless it has the page's own name ("Docs / Get started", not "Get
  started / Get started" twice).
- The menu marks the current page; the code reference is one group with a small heading per area.
- The docs' front page lists every section as cards, the code reference under `#code-reference` (the footer links
  there), and has a search button.

```python
701 | def article(layout: str, values: dict[str, str], *, name: str, eyebrow: str, heading: str, intro: str,
702 |             content: str, current: str = "") -> str:
703 |     """A simple page with a title band: the changelog, privacy, the license."""
704 |     body = fill(read("article.html"), {"eyebrow": escape(eyebrow), "heading": escape(heading),
705 |                                        "intro": escape(intro), "content": content})
706 |     return page(layout, values, body, root="", title=f"{heading} — localagent", description=intro,
707 |                 path=name, current=current)
708 | 
709 | 
```
- A simple page with a title band (the changelog, privacy, the license).

## Building
```python
710 | def build(out: Path, dist: Path, site_url: str = "", downloads_url: str = "") -> list[str]:
711 |     """Write the whole site into ``out`` (a previous build there is replaced). Returns every page
712 |     written."""
713 |     if site_url and not site_url.endswith("/"):
714 |         site_url += "/"
715 |     if downloads_url and not downloads_url.endswith("/"):
716 |         downloads_url += "/"
717 |     zip_file = dist / f"localagent-{__version__}-windows-x64.zip"
718 |     wheel = dist / f"localagent-{__version__}-py3-none-any.whl"
719 |     sums = dist / "SHA256SUMS.txt"
720 |     missing = [path.name for path in (zip_file, wheel, sums) if not path.is_file()]
721 |     if missing:
722 |         raise SystemExit(f"missing in {dist}: {', '.join(missing)} - run tools/build_release.py first")
723 |     if out.exists():
724 |         if any(out.iterdir()) and not (out / "index.html").is_file():
725 |             raise SystemExit(f"{out} is not empty and is not a site built before: choose another --out")
726 |         shutil.rmtree(out)
727 |     out.mkdir(parents=True)
728 |     for folder in STATIC_FOLDERS:
729 |         shutil.copytree(WEBSITE / folder, out / folder)
730 |     shutil.copy2(WEBSITE / "_headers", out / "_headers")
731 |     (out / "assets").mkdir()
732 |     for name in ASSETS:
733 |         shutil.copy2(ROOT / "assets" / name, out / "assets" / name)
734 |     if not downloads_url:
735 |         (out / "downloads").mkdir()
736 |         for path in (zip_file, wheel, sums):
737 |             shutil.copy2(path, out / "downloads" / path.name)
738 |     changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
739 |     zipped, packaged = file_info(zip_file), file_info(wheel)
740 |     values = {"version": __version__, "site_url": escape(site_url), "downloads": escape(downloads_url or "downloads/"),
741 |               "zip_name": zipped["name"], "zip_size": zipped["size"], "zip_sha": zipped["sha"],
742 |               "wheel_name": packaged["name"], "wheel_size": packaged["size"], "wheel_sha": packaged["sha"],
743 |               "release_date": escape(release_date(changelog))}
744 |     layout = read("layout.html")
745 |     written: list[str] = []
746 | 
747 |     def write(path: str, text: str) -> None:
748 |         (out / path).parent.mkdir(parents=True, exist_ok=True)
749 |         (out / path).write_text(text, encoding="utf-8")
750 |         written.append(path)
751 | 
752 |     pages = {path.stem: path.read_text(encoding="utf-8") for path in sorted((WEBSITE / "guide").glob("*.md"))}
753 |     pages.update({path.stem: path.read_text(encoding="utf-8") for path in sorted(DOCS.glob("*.md"))})
754 |     index: list[dict] = []
755 |     render_docs(pages, layout, values, write, index)
756 |     top = make_link(pages, "docs/")
757 |     write("index.html", page(layout, values, home_body(values), root="", path="", title="localagent — the coding agent "
758 |                              "that keeps working offline", description="A free coding agent for your terminal: it edits, "
759 |                              "runs your tests and checks every change - with a free online model, your own key, or a "
760 |                              "model on your own computer."))
761 |     notes = Renderer(top).render(release_section(changelog))
762 |     write("download.html", page(layout, values, fill(read("download.html"), {**values, "release_notes": notes}),
763 |                                 root="", path="download.html", current="download",
764 |                                 title=f"Download localagent {__version__}",
765 |                                 description="Download localagent for Windows, macOS and Linux - every file with its "
766 |                                             "size and checksum."))
767 |     write("setup.html", page(layout, values, setup_body(values), root="", path="setup.html", current="setup",
768 |                              title="Setup builder — localagent", description="Choose your computer and your models, "
769 |                              "and get the exact steps to set localagent up."))
770 |     history = changelog.split("\n", 1)[1]                  # without its "# Changelog" title
771 |     write("changelog.html", article(layout, values, name="changelog.html", eyebrow="Changelog", current="changelog",
772 |                                     heading="What's new", intro="Every version of localagent and what changed in it.",
773 |                                     content=Renderer(top).render(history)))
774 |     write("privacy.html", article(layout, values, name="privacy.html", eyebrow="Privacy", heading="Privacy",
775 |                                   intro="What this website and the localagent program do with your data: as little "
776 |                                         "as possible.", content=Renderer(top).render(read("pages/privacy.md"))))
777 |     terms = "\n".join(line for line in (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()
778 |                       if not line.startswith("Copyright")).strip()   # the notice itself travels in each download
779 |     notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").replace("# ", "## ", 1)
780 |     write("license.html", article(layout, values, name="license.html", eyebrow="License", heading="License",
781 |                                   intro="localagent is free and open source under the MIT License.",
782 |                                   content='<h2 id="mit-license">MIT License</h2><p>The terms below apply to '
783 |                                           "localagent. Its copyright notice is in the <code>LICENSE</code> file that "
784 |                                           f"comes with every download.</p><pre><code>{escape(terms)}</code></pre>"
785 |                                           f"{Renderer(top).render(notices)}"))
786 |     write("404.html", page(layout, values, fill(read("notfound.html"), {"root": "/"}), root="/", path=None,
787 |                            title="Page not found — localagent", description="This page does not exist.",
788 |                            head='<meta name="robots" content="noindex">'))
789 |     index += [{"p": "Download", "h": "", "u": "download.html", "x": "Every file with its size and checksum.", "g": 1},
790 |               {"p": "Setup builder", "h": "", "u": "setup.html", "x": "Your choices turned into exact steps.", "g": 1},
791 |               {"p": "Changelog", "h": "", "u": "changelog.html", "x": "What changed in every version.", "g": 1},
792 |               {"p": "Privacy", "h": "", "u": "privacy.html", "x": "What happens with your data.", "g": 1},
793 |               {"p": "License", "h": "", "u": "license.html", "x": "MIT, and the third-party software.", "g": 1}]
794 |     (out / "search-index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
795 |                                            encoding="utf-8")
796 |     manifest = {"name": "localagent", "short_name": "localagent", "start_url": "./index.html", "scope": "./",
797 |                 "display": "browser", "background_color": "#050914", "theme_color": "#050914",
798 |                 "description": "A coding agent for your terminal that keeps working offline.",
799 |                 "icons": [{"src": "assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
800 |                           {"src": "assets/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
801 |     (out / "site.webmanifest").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
802 |     robots = "User-agent: *\nAllow: /\n"
803 |     if site_url:
804 |         entries = "".join(f"<url><loc>{escape(site_url + path)}</loc></url>"
805 |                           for path in written if path != "404.html")
806 |         (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
807 |                                          f'"http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n',
808 |                                          encoding="utf-8")
809 |         robots += f"Sitemap: {site_url}sitemap.xml\n"
810 |     (out / "robots.txt").write_text(robots, encoding="utf-8")
811 |     return written
812 | 
813 | 
```
- Addresses get a trailing `/`. The three release files must exist, or the message says to run `build_release.py`. A
  previous site is replaced, but a folder that is not empty and has no `index.html` is never deleted.
- Copies the styles, scripts, fonts, `_headers` and icons, and — unless `--downloads-url` — the downloads.
- Renders the guides and `docs\` (`website\guide` first, so its pages lead the menu), then the home page, the download
  page (with this version's notes), the setup builder, the changelog (without its own title), privacy, the license (the
  MIT terms without the copyright line, which travels in `LICENSE` with every download, and the third-party software)
  and the 404 page.
- Writes the search index (compact JSON, about 115 KB, loaded only when you search), the web manifest (name, colours,
  icons), `robots.txt` and, with `--url`, `sitemap.xml` with every page but the 404.

## Checking
```python
814 | class PageCheck(HTMLParser):
815 |     """Reads one page: every link and source address, every id, whether each tag that was opened
816 |     is closed again in order, and anything the security policy would block (inline scripts,
817 |     inline styles, event handlers)."""
818 | 
```
```python
819 |     def __init__(self) -> None:
820 |         super().__init__(convert_charrefs=True)
821 |         self.targets: list[str] = []
822 |         self.ids: set[str] = set()
823 |         self.open: list[str] = []
824 |         self.problems: list[str] = []
825 | 
```
```python
826 |     def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
827 |         found = dict(attrs)
828 |         where = f"line {self.getpos()[0]}"
829 |         for name, value in attrs:
830 |             if name in ("href", "src") and value:
831 |                 self.targets.append(value)
832 |             if name == "id" and value:
833 |                 self.ids.add(value)
834 |             if name == "style" or name.startswith("on"):
835 |                 self.problems.append(f"{where}: <{tag} {name}=...> - the security policy blocks inline "
836 |                                      f"{'styles' if name == 'style' else 'event handlers'}")
837 |         if tag == "script" and not found.get("src") and found.get("type") != "application/json":
838 |             self.problems.append(f"{where}: an inline <script> - the security policy blocks it")
839 |         if tag not in VOID:
840 |             self.open.append(tag)
841 | 
```
```python
842 |     def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
843 |         self.handle_starttag(tag, attrs)
844 |         if tag not in VOID:
845 |             self.open.pop()
846 | 
```
```python
847 |     def handle_endtag(self, tag: str) -> None:
848 |         if tag in VOID:
849 |             return
850 |         if self.open and self.open[-1] == tag:
851 |             self.open.pop()
852 |             return
853 |         self.problems.append(f"line {self.getpos()[0]}: </{tag}> closes "
854 |                              f"<{self.open[-1] if self.open else 'nothing'}>")
855 |         if tag in self.open:                            # recover, so one slip is reported once
856 |             while self.open.pop() != tag:
857 |                 pass
858 | 
859 | 
```
- Python's own HTML reader calls these for every tag. Opening a tag (that is not void) pushes it on a stack; closing
  must close the tag on top — a wrong one is reported with its line, and the stack is repaired so one mistake is not
  reported a hundred times. `href`, `src` and `id` values are collected for the link check.
- What the security policy (`_headers`) would block is reported too: a `style` attribute, an `on...` event handler, a
  `<script>` without `src` — except `type="application/json"`, which holds data and never runs.

```python
860 | def check_site(out: Path, private: list[str]) -> list[str]:
861 |     """Everything that would be wrong with the site: unfinished templates, unbalanced tags, what
862 |     the security policy blocks, links to files or sections that do not exist, broken JSON, and
863 |     any of the ``private`` words on any page."""
864 |     out = out.resolve()
865 |     problems: list[str] = []
866 |     pages: dict[Path, PageCheck] = {}
867 |     for path in sorted(out.rglob("*.html")):
868 |         text = path.read_text(encoding="utf-8")
869 |         name = path.relative_to(out).as_posix()
870 |         if "{{" in text:
871 |             problems.append(f"{name}: a {{{{placeholder}}}} was not filled in")
872 |         check = PageCheck()
873 |         check.feed(text)
874 |         check.close()
875 |         problems += [f"{name}: {problem}" for problem in check.problems]
876 |         if check.open:
877 |             problems.append(f"{name}: never closed: {', '.join(check.open)}")
878 |         pages[path] = check
879 |     for path, check in pages.items():
880 |         name = path.relative_to(out).as_posix()
881 |         for target in check.targets:
882 |             if target.startswith(("http://", "https://", "mailto:", "data:")):
883 |                 continue
884 |             address, _, part = target.partition("#")
885 |             if address.startswith("/"):
886 |                 file = out / address.lstrip("/")
887 |             else:
888 |                 file = (path.parent / address) if address else path
889 |             if address.endswith("/") or file.is_dir():
890 |                 file = file / "index.html"
891 |             file = file.resolve()
892 |             if not file.is_file():
893 |                 problems.append(f"{name}: link to a missing file: {target}")
894 |             elif part and file in pages and part not in pages[file].ids:
895 |                 problems.append(f"{name}: link to a missing section: {target}")
896 |     for path in sorted([*out.rglob("*.json"), *out.rglob("*.webmanifest")]):
897 |         try:
898 |             json.loads(path.read_text(encoding="utf-8"))
899 |         except ValueError as exc:
900 |             problems.append(f"{path.relative_to(out).as_posix()}: not valid JSON ({exc})")
901 |     for path in sorted(out.rglob("*")):
902 |         if path.is_file() and path.suffix in (".html", ".css", ".js", ".json", ".webmanifest", ".txt", ".xml",
903 |                                               ".svg", ""):
904 |             text = path.read_text(encoding="utf-8", errors="replace").lower()
905 |             for word in private:
906 |                 if word.lower() in text:
907 |                     problems.append(f"{path.relative_to(out).as_posix()}: shows '{word}'")
908 |     return problems
909 | 
910 | 
```
- For every page: no `{{`, balanced tags, nothing the policy blocks. For every link: outside addresses are skipped;
  `/x` is relative to the site's root, anything else to the page's folder; a folder means its `index.html`; the file
  must exist and a `#section` must be an id on that page. Every `.json` and `.webmanifest` must parse. Then every text
  file is searched for the private words, ignoring letter case.

```python
911 | def private_words() -> list[str]:
912 |     """The repository owner's name as it appears in its GitHub address, also with spaces for "-":
913 |     the site must never show it. Empty when this folder has no GitHub remote."""
914 |     try:
915 |         remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
916 |                                 text=True, timeout=10).stdout.strip()
917 |     except (OSError, subprocess.SubprocessError):
918 |         return []
919 |     found = re.search(r"github\.com[/:]([^/]+)/", remote)
920 |     if not found:
921 |         return []
922 |     owner = found.group(1)
923 |     return sorted({owner, owner.replace("-", " ")})
924 | 
925 | 
```
- Asks git for the `origin` address and takes the owner from `github.com/<owner>/` (also the `git@github.com:<owner>/`
  form), with and without dashes. No git, no remote: nothing to search for.

```python
926 | def serve(out: Path, port: int) -> None:
927 |     """Show the site at http://127.0.0.1:<port>/ until Ctrl+C (only this computer can see it)."""
928 |     server = ThreadingHTTPServer(("127.0.0.1", port), partial(SimpleHTTPRequestHandler, directory=str(out)))
929 |     print(f"the site is at http://127.0.0.1:{port}/  (Ctrl+C stops)")
930 |     try:
931 |         server.serve_forever()
932 |     except KeyboardInterrupt:
933 |         pass
934 |     finally:
935 |         server.server_close()
936 | 
937 | 
```
- Python's built-in file server on `127.0.0.1` only (other computers cannot see it), until Ctrl+C — for a preview.

```python
938 | def main(argv: list[str]) -> int:
939 |     """Build the site, check it, and say where it is."""
940 |     parser = argparse.ArgumentParser(description="Build the localagent website into one folder.")
941 |     parser.add_argument("--out", type=Path, default=ROOT / "site", help="where to write it (default: site/)")
942 |     parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="where the release files are")
943 |     parser.add_argument("--url", default="", help="the site's own address, for canonical links and sitemap.xml")
944 |     parser.add_argument("--downloads-url", default="", help="serve the downloads from this address instead")
945 |     parser.add_argument("--serve", action="store_true", help="show the site at http://127.0.0.1:PORT/ afterwards")
946 |     parser.add_argument("--port", type=int, default=8000, help="the port for --serve (default 8000)")
947 |     args = parser.parse_args(argv)
948 |     for address in (args.url, args.downloads_url):
949 |         if address and urlsplit(address).scheme not in ("http", "https"):
950 |             raise SystemExit(f"{address}: an address must start with https:// (or http://)")
951 |     pages = build(args.out, args.dist, args.url, args.downloads_url)
952 |     problems = check_site(args.out, private_words())
953 |     if problems:
954 |         print("SITE PROBLEMS:\n  " + "\n  ".join(problems))
955 |         return 1
956 |     files = [path for path in args.out.rglob("*") if path.is_file()]
957 |     total = sum(path.stat().st_size for path in files)
958 |     print(f"site OK: {len(files)} files, {size_text(total)}, {len(pages)} pages -> {args.out}")
959 |     if args.serve:
960 |         serve(args.out, args.port)
961 |     return 0
962 | 
963 | 
```
```python
964 | if __name__ == "__main__":
965 |     raise SystemExit(main(sys.argv[1:]))
```
- Reads the options (both addresses must start with `http://` or `https://`), builds, checks with the owner's name,
  prints the problems (exit 1) or the files, their size and the pages (exit 0), and serves when asked.

## How it was tested
Beyond `tests/test_site.py`, the built site was served and checked the way a visitor meets it: every page and link
crawled, the downloads compared byte for byte, screenshots on a desktop (light and dark) and at a true phone width of
390 px (inside a frame: headless Edge will not make a window narrower than 504 px), and the interactive parts driven in
a real browser — search found the right pages and said "Nothing found" for nonsense; the player switched to the offline
session, replayed it and disabled Pause at the end. The screenshots found and fixed: a grid of four plus two feature
cards (now three by three), badges stretched across their cards, documentation cards indented by the list style, a
repeated breadcrumb, and search ranking code pages above the guides.
