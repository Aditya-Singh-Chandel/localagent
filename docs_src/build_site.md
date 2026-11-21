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
@@ tools/build_site.py header @@
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

## Pages
@@ tools/build_site.py page_title @@
@@ tools/build_site.py describe @@
- `page_title`: from "`` `localagent/fetch.py` `` — downloads you can trust" the menu title `fetch.py` and the line
  "downloads you can trust"; a folder page (`` `tests/` ``) becomes `tests`.
- `describe`: the first paragraph, at most 155 characters, cut at a word: the page's description for search engines.

@@ tools/build_site.py doc_order @@
- The guides, then each area of code pages. A page that no group names lands in "More", so a new page is never left
  out of the menu.

@@ tools/build_site.py fill @@
@@ tools/build_site.py size_text @@
@@ tools/build_site.py canonical @@
- `fill`: replaces every `{{name}}` of a template, and refuses to go on when the template asks for a value nobody gave
  (a typo in a template is an error, not a page with `{{nmae}}` on it).
- `size_text`: "101 KB", "22.8 MB" (decimal units, as download pages show them).
- `canonical`: the tag that names a page's one true address, only when the site's address is known.

@@ tools/build_site.py render_docs @@
- Reads every `docs\*.md` page. `link` turns a Markdown link target into a site address: outside addresses and
  `#sections` stay, a documentation page becomes its `.html`, anything else is not on the site (`None`).
- `nav` builds the page list with `aria-current="page"` on the page being shown (screen readers announce it, the style
  sheet highlights it); `pager` links the previous and next page in menu order.
- A page with four or more sections gets an "On this page" box right under its title.
- The front page of the docs lists every group with each page's title and line.

## Building
@@ tools/build_site.py build @@
- Addresses get a trailing `/` so file names can be appended. The three release files must exist — otherwise the
  message says to run `build_release.py` first.
- A previous site in `--out` is replaced, but a folder that is not empty and has no `index.html` is never deleted: a
  mistyped `--out` must not wipe a folder of yours.
- Copies the static files, the assets and — unless `--downloads-url` — the downloads; renders the docs; fills the
  landing page; writes `robots.txt` (all pages may be indexed) and, with `--url`, `sitemap.xml` listing every page.

## Checking
@@ tools/build_site.py PageCheck @@
@@ tools/build_site.py PageCheck.__init__ @@
@@ tools/build_site.py PageCheck.handle_starttag @@
@@ tools/build_site.py PageCheck.handle_startendtag @@
@@ tools/build_site.py PageCheck.handle_endtag @@
- Python's own HTML reader calls these for every tag. Opening a tag (that is not void) pushes it on a stack; closing
  must close the tag on top. A wrong one is reported with its line, and the stack is repaired so one mistake is not
  reported a hundred times. `href`, `src` and `id` values are collected for the link check.

@@ tools/build_site.py check_site @@
- For every page: no `{{`, balanced tags, nothing left open. For every link: outside addresses are skipped; `/x` is
  relative to the site's root, anything else to the page's folder; a folder means its `index.html`; the file must exist
  and a `#section` must be an id of that page.
- Then every text file is searched for the private words, ignoring letter case.

@@ tools/build_site.py private_words @@
- Asks git for the `origin` address and takes the owner from `github.com/<owner>/` (also the `git@github.com:<owner>/`
  form), with and without dashes. No git, no remote: nothing to search for.

@@ tools/build_site.py serve @@
- Python's built-in file server on `127.0.0.1` only (other computers cannot see it), until Ctrl+C. Good for a preview;
  for the real site use a proper host.

@@ tools/build_site.py main @@
@@ tools/build_site.py footer @@
- Reads the options (both addresses must start with `http://` or `https://`), builds, checks with the owner's name,
  prints the problems (exit 1) or the number of files, their size and the pages (exit 0), and serves when asked.
