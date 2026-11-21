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
@@ tools/build_site.py header @@
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
@@ tools/build_site.py escape @@
- `<`, `>`, `&` and quotes become HTML entities, so no text can ever become a tag; `{` becomes `&#123;` (it looks the
  same). Without that, three pages that document Docker's `{{.Names}}` and the slash-command template were reported as
  unfilled templates by the site check.

@@ tools/build_site.py plain @@
@@ tools/build_site.py slug @@
- `plain`: link labels instead of links; code loses its backticks but keeps its underscores (`read_secret`), and
  outside code the emphasis marks go — for titles and ids. The first version dropped every underscore, and the
  phone-width screenshot showed the contents of `wizard.md` listing "readsecret".
- `slug`: a heading's id, e.g. "`Config.from_env`" → `configfrom_env`; a second heading with the same words gets `-2`.

## Inline Markdown
@@ tools/build_site.py inline @@
@@ tools/build_site.py _inline @@
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

@@ tools/build_site.py wrap @@
- Puts the text a pattern found into a tag; a named function instead of a lambda (which mypy cannot type here).

## Blocks
@@ tools/build_site.py starts_block @@
@@ tools/build_site.py interrupts @@
@@ tools/build_site.py indent_of @@
- `starts_block`: a fence, heading, rule, list item, table row or quote. `interrupts` is the paragraph's version: a
  numbered line ends a paragraph only when it starts at 1, as in CommonMark — the build log has sentences that wrap
  onto a new line starting with a year.

@@ tools/build_site.py Renderer @@
@@ tools/build_site.py Renderer.__init__ @@
@@ tools/build_site.py Renderer.render @@
- One renderer per page: it remembers the ids already used and every heading (for the contents box).

@@ tools/build_site.py Renderer.blocks @@
@@ tools/build_site.py Renderer.heading @@
- Walks the lines and hands each block to its method. Quotes are rendered recursively (a quote may hold a list).
  A paragraph is all lines up to a blank line or a line that interrupts it.
- Headings get their id and, from level 2 on, a `#` link to themselves (shown when the mouse is over the heading).

@@ tools/build_site.py Renderer.code_block @@
- Everything up to the closing fence (the same character, at least as many) exactly as written, escaped; the
  language becomes a class name.

@@ tools/build_site.py Renderer.table @@
- Cells are split at `|` but not at `\|` (an escaped pipe, as in `/model online\|offline`). `:---:` centres a column,
  `---:` aligns it right — with classes, not inline styles, which the security policy would block. Rows with too few
  cells are filled up. The table sits in a box that scrolls sideways on phones.

@@ tools/build_site.py Renderer.list_block @@
- An item owns the lines indented under it — at least to where its text starts — so nested lists and code blocks
  inside items work, dedented and rendered recursively. A blank line inside an item or between items makes the list
  "loose": its items keep their paragraphs; in a tight list the first paragraph is unwrapped. A numbered list that does
  not start at 1 keeps its number (`start="3"`).

## Pages and their data
@@ tools/build_site.py page_title @@
@@ tools/build_site.py describe @@
- `page_title`: from "`` `localagent/fetch.py` `` — downloads you can trust" the menu title `fetch.py` and the line
  "downloads you can trust"; a folder page (`` `tests/` ``) becomes `tests`.
- `describe`: the first paragraph, at most 155 characters, cut at a word: the page's description for search engines
  and link previews.

@@ tools/build_site.py doc_order @@
- The menu: the sections of `SECTIONS`, then the code reference by area. A page that no group names lands in "More",
  so a new page is never left out.

@@ tools/build_site.py fill @@
@@ tools/build_site.py size_text @@
@@ tools/build_site.py canonical @@
- `fill`: replaces every `{{name}}` of a template, and refuses to go on when the template asks for a value nobody gave.
  Values are put in as they are, never read again for places.
- `size_text`: "114 KB", "22.8 MB". `canonical`: the tag naming a page's one true address, when the address is known.

@@ tools/build_site.py json_script @@
- Data for a page's script (sessions, the setup builder's tables) is JSON inside `<script type="application/json">`.
  `<` is written as `<`, so no `</script>` inside a string can end the element; a `{{` inside a string becomes
  `{{`, so it cannot look like an unfilled place. Both read back as the same data.

@@ tools/build_site.py file_info @@
@@ tools/build_site.py release_date @@
@@ tools/build_site.py release_section @@
@@ tools/build_site.py test_count @@
- `file_info`: a download's name, size and SHA-256 — computed from the file itself, so the page can never show a
  checksum of another build.
- `release_date` and `release_section`: this version's date and notes from `CHANGELOG.md`.
- `test_count`: the tests counted exactly as the test runner counts them, for the home page's numbers.

@@ tools/build_site.py read @@
@@ tools/build_site.py page @@
- `page` wraps a body into the frame: the title, the description (also for link previews), the canonical address, the
  preview image (absolute when the site's address is known), extra head tags (the 404 page asks not to be indexed).
  `current` adds `aria-current="page"` to the page's own entry in the top menu — screen readers announce it, the style
  sheet highlights it.

@@ tools/build_site.py make_link @@
- The link rule for a page: outside addresses and `#sections` stay, the site's own pages (`../download.html`) stay, a
  documentation page becomes its `.html` — with `docs/` in front for pages at the top — and anything else is shown as
  its label (`None`).

@@ tools/build_site.py session_lines @@
@@ tools/build_site.py home_body @@
- `session_lines`: a recorded session as the player draws it, so the page shows a complete session even when
  JavaScript is off. Every text is escaped.
- `home_body`: the tabs (one per session), the first session in full, all sessions as data for the player, and the
  numbers: the tests, the online services ("7+": the named ones, plus any compatible address), the local servers.

@@ tools/build_site.py choices @@
@@ tools/build_site.py server_id @@
@@ tools/build_site.py memory_label @@
@@ tools/build_site.py setup_body @@
- `choices`: radio buttons drawn as cards (the button is invisible but stays in place, so keyboards and screen readers
  use it as usual).
- `server_id`: "LM Studio" → `lmstudio`, the value in the page's address. `memory_label`: a model's memory threshold
  as people say it — 14 → "16 GB or more", 0 → "Less than 8 GB".
- `setup_body`: the online services come from `PROVIDERS` (with their setup menu numbers, so the steps can say
  "choose 2) OpenAI"), the local servers from `LOCAL_SERVERS` plus how to start each, the models and memory choices
  from `CATALOG`. The page cannot drift from the program: change a table, rebuild, and the builder follows.

## Search
@@ tools/build_site.py SectionText @@
@@ tools/build_site.py SectionText.__init__ @@
@@ tools/build_site.py SectionText.handle_starttag @@
@@ tools/build_site.py SectionText.handle_endtag @@
@@ tools/build_site.py SectionText.handle_data @@
@@ tools/build_site.py search_entries @@
- Reads a rendered page and collects the text under each `h2`/`h3` heading by its id — code blocks and the `#` links
  left out. `search_entries` makes one entry for the page and one for each section: page title, heading, address with
  `#id`, and the first 140 characters as the snippet. Entries of the user guides carry `"g": 1`, and the search ranks
  them first — a person asking about Ollama wants "Choose a model" before the code of the Ollama client.

## The documentation pages
@@ tools/build_site.py render_docs @@
- One renderer per page; a page with three or more sections gets the "On this page" list (shown beside the text on
  wide screens). The breadcrumbs name the section, unless it has the page's own name ("Docs / Get started", not "Get
  started / Get started" twice).
- The menu marks the current page; the code reference is one group with a small heading per area.
- The docs' front page lists every section as cards, the code reference under `#code-reference` (the footer links
  there), and has a search button.

@@ tools/build_site.py article @@
- A simple page with a title band (the changelog, privacy, the license).

## Building
@@ tools/build_site.py build @@
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
@@ tools/build_site.py PageCheck @@
@@ tools/build_site.py PageCheck.__init__ @@
@@ tools/build_site.py PageCheck.handle_starttag @@
@@ tools/build_site.py PageCheck.handle_startendtag @@
@@ tools/build_site.py PageCheck.handle_endtag @@
- Python's own HTML reader calls these for every tag. Opening a tag (that is not void) pushes it on a stack; closing
  must close the tag on top — a wrong one is reported with its line, and the stack is repaired so one mistake is not
  reported a hundred times. `href`, `src` and `id` values are collected for the link check.
- What the security policy (`_headers`) would block is reported too: a `style` attribute, an `on...` event handler, a
  `<script>` without `src` — except `type="application/json"`, which holds data and never runs.

@@ tools/build_site.py check_site @@
- For every page: no `{{`, balanced tags, nothing the policy blocks. For every link: outside addresses are skipped;
  `/x` is relative to the site's root, anything else to the page's folder; a folder means its `index.html`; the file
  must exist and a `#section` must be an id on that page. Every `.json` and `.webmanifest` must parse. Then every text
  file is searched for the private words, ignoring letter case.

@@ tools/build_site.py private_words @@
- Asks git for the `origin` address and takes the owner from `github.com/<owner>/` (also the `git@github.com:<owner>/`
  form), with and without dashes. No git, no remote: nothing to search for.

@@ tools/build_site.py serve @@
- Python's built-in file server on `127.0.0.1` only (other computers cannot see it), until Ctrl+C — for a preview.

@@ tools/build_site.py main @@
@@ tools/build_site.py footer @@
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
