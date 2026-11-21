"""Build the website: one self-contained folder to put on any web host.

Usage (from the project folder, after tools/build_release.py has made the download files):
    python tools/build_site.py                          # writes site/ and checks it
    python tools/build_site.py --url https://example.org/          # + canonical links, sitemap.xml
    python tools/build_site.py --downloads-url https://files.example.org/v0.6.0/
    python tools/build_site.py --serve                  # build, then show it at http://127.0.0.1:8000

site/index.html          the landing page (website/index.html, filled in)
site/docs/*.html         every page of docs/, rendered from Markdown, with a page list and a pager
site/downloads/          the release files and SHA256SUMS.txt (not with --downloads-url)
site/assets/, style.css, site.js, 404.html, _headers, robots.txt (and sitemap.xml with --url)

The site loads nothing from other sites and links nowhere near the source repository: the build
checks every link and every tag, and refuses to finish if a page shows the repository owner's name.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from localagent import __version__  # noqa: E402  (after the path is set)

WEBSITE, DOCS = ROOT / "website", ROOT / "docs"
STATIC = ("style.css", "site.js", "404.html", "_headers")
ASSETS = ("logo.svg", "demo.svg", "social.png")
GUIDES = {                                # narrative pages: file name -> (menu title, one line)
    "04-start-from-zero": ("Start from zero", "every step from an empty computer"),
    "05-user-manual": ("User manual", "every command, flag, setting and tool"),
    "01-setup-and-models": ("Online and offline", "the online services, local models and modes"),
    "06-run-and-verify": ("Run and verify", "every way to run it, and how to check it works"),
    "00-overview": ("How it works", "the architecture, and one request through the code"),
    "02-roadmap": ("Roadmap", "what is built and what could come next"),
    "03-build-log": ("Build log", "every decision and measurement, and why"),
}
AREAS = {                                 # code pages, grouped as the overview groups them
    "The agent": ("agent", "context", "tools", "verify", "sandbox", "runners", "checkpoint", "session",
                  "meter"),
    "Models": ("providers", "llm", "openai_compat", "backends", "resilient", "ratelimit", "localserver",
               "models", "fetch"),
    "Commands and settings": ("cli", "wizard", "doctor", "config", "settings", "server", "web", "mcp",
                              "package"),
    "Building and testing": ("tests", "evals", "build_docs", "build_release", "build_site", "check_commit",
                             "docker"),
}
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
                  "track", "wbr"})
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
CODE_SPAN = re.compile(r"(`+)(.+?)\1")
LINK = re.compile(r"(!?)\[([^\]]+)\]\(([^)\s]+)\)")
ANGLE_URL = re.compile(r"<(https?://[^>\s]+)>")
BARE_URL = re.compile(r"https?://[^\s<>\"'`]+")
MARKER = re.compile(r"\x00(\d+)\x00")
DOC_NAME = re.compile(r"(?:docs/)?([\w.-]+)\.md(#[\w-]+)?")
EMPHASIS = (
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "strong"),
    (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), "em"),
    (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), "em"),
)
Link = Callable[[str], "str | None"]       # a Markdown link target -> the site's address, or None


def escape(text: str) -> str:
    """Text made safe for HTML (also inside attribute values). ``{`` is written as ``&#123;``,
    which looks the same: then no text from the docs can ever look like a {{placeholder}}."""
    return html.escape(text, quote=True).replace("{", "&#123;")


def plain(markdown: str) -> str:
    """Inline Markdown as plain text: for titles, descriptions and heading ids. Code keeps its
    underscores (`read_secret`); outside code, emphasis marks are dropped."""
    text = LINK.sub(lambda m: m.group(2), markdown)
    parts = re.split(r"(`+[^`]*`+)", text)
    for index, part in enumerate(parts):
        parts[index] = part.strip("`") if index % 2 else re.sub(r"\*+|(?<!\w)_+|_+(?!\w)", "", part)
    return "".join(parts).strip()


def slug(text: str, used: set[str]) -> str:
    """A heading's id: lower case, words joined by "-", unique on its page."""
    base = re.sub(r"[^\w\s-]", "", plain(text).lower()).strip()
    base = re.sub(r"[\s-]+", "-", base) or "section"
    name, number = base, 2
    while name in used:
        name, number = f"{base}-{number}", number + 1
    used.add(name)
    return name


def inline(text: str, link: Link) -> str:
    """One line of Markdown as HTML: code, links, bare addresses, bold and italics. Everything
    else is escaped, so text can never become a tag."""
    kept: list[str] = []
    text = _inline(text, link, kept)
    while "\x00" in text:                                # a kept fragment may hold others
        text = MARKER.sub(lambda m: kept[int(m.group(1))], text)
    return text


def _inline(text: str, link: Link, kept: list[str]) -> str:
    """The work of ``inline``. Finished HTML is put aside in ``kept`` and replaced by a marker
    (NUL, its number, NUL), so later steps cannot change it; ``inline`` puts it back."""
    def keep(fragment: str) -> str:
        kept.append(fragment)
        return f"\x00{len(kept) - 1}\x00"

    def code(match: re.Match) -> str:
        content = match.group(2).strip()
        target = link(content) if DOC_NAME.fullmatch(content) else None
        span = f"<code>{escape(content)}</code>"
        return keep(f'<a href="{escape(target)}">{span}</a>' if target else span)

    def anchor(match: re.Match) -> str:
        image, label, target = match.groups()
        address = link(target)
        if image:
            return keep(f'<img src="{escape(address)}" alt="{escape(label)}" loading="lazy">'
                        if address else escape(label))
        shown = _inline(label, link, kept)
        return keep(f'<a href="{escape(address)}">{shown}</a>' if address else shown)

    def angle(match: re.Match) -> str:
        address = escape(match.group(1))
        return keep(f'<a href="{address}">{address}</a>')

    def bare(match: re.Match) -> str:
        address = match.group(0).rstrip(".,;:!?")
        if address.endswith(")") and "(" not in address:
            address = address[:-1]
        return keep(f'<a href="{escape(address)}">{escape(address)}</a>') + match.group(0)[len(address):]

    text = ESCAPED.sub(lambda m: keep(escape(m.group(1))), text)
    text = CODE_SPAN.sub(code, text)
    text = LINK.sub(anchor, text)
    text = ANGLE_URL.sub(angle, text)
    parts = re.split(r"(\x00\d+\x00)", text)
    for index in range(0, len(parts), 2):                # only text, never a kept fragment
        parts[index] = BARE_URL.sub(bare, parts[index])
    text = escape("".join(parts))
    for pattern, tag in EMPHASIS:
        text = pattern.sub(partial(wrap, tag), text)
    return text


def wrap(tag: str, match: re.Match) -> str:
    """The text a pattern found, inside ``<tag>``: **bold** becomes <strong>bold</strong>."""
    return f"<{tag}>{match.group(1)}</{tag}>"


def starts_block(line: str) -> bool:
    """True when a line begins something other than paragraph text."""
    stripped = line.strip()
    return bool(FENCE.match(line) or HEADING.match(line) or RULE.match(line) or ITEM.match(line)
                or stripped.startswith(("|", ">")))


def interrupts(line: str) -> bool:
    """True when a line ends a paragraph: any block start, except a numbered line that does not
    start at 1 - a sentence may well begin with "2026." after a line break."""
    item = ITEM.match(line)
    if item and item.group(2)[0].isdigit() and item.group(2)[:-1] != "1":
        return False
    return starts_block(line)


def indent_of(line: str) -> int:
    """How many spaces a line starts with."""
    return len(line) - len(line.lstrip(" "))


class Renderer:
    """Markdown to HTML for the documentation: the parts the docs use (headings, paragraphs,
    lists, code blocks, tables, quotes, rules, links), each kept simple and safe."""

    def __init__(self, link: Link) -> None:
        self.link = link
        self.used: set[str] = set()
        self.headings: list[tuple[int, str, str]] = []      # (level, id, text) for the contents

    def render(self, text: str) -> str:
        """A whole page."""
        return self.blocks(text.replace("\r\n", "\n").split("\n"))

    def blocks(self, lines: list[str]) -> str:
        """A run of lines: each block in turn."""
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
            elif FENCE.match(line):
                i = self.code_block(lines, i, out)
            elif heading := HEADING.match(line):
                out.append(self.heading(len(heading.group(1)), heading.group(2)))
                i += 1
            elif RULE.match(line):
                out.append("<hr>")
                i += 1
            elif line.lstrip().startswith("|") and i + 1 < len(lines) and TABLE_RULE.match(lines[i + 1]):
                i = self.table(lines, i, out)
            elif line.lstrip().startswith(">"):
                quoted = []
                while i < len(lines) and lines[i].lstrip().startswith(">"):
                    quoted.append(re.sub(r"^\s*> ?", "", lines[i]))
                    i += 1
                out.append(f"<blockquote>{self.blocks(quoted)}</blockquote>")
            elif ITEM.match(line):
                i = self.list_block(lines, i, out)
            else:
                text = [line.strip()]
                i += 1
                while i < len(lines) and lines[i].strip() and not interrupts(lines[i]):
                    text.append(lines[i].strip())
                    i += 1
                out.append(f"<p>{inline(chr(10).join(text), self.link)}</p>")
        return "\n".join(out)

    def heading(self, level: int, title: str) -> str:
        """A heading with its id (remembered for the page's contents) and a "#" link to itself."""
        ident = slug(title, self.used)
        self.headings.append((level, ident, plain(title)))
        mark = f'<a class="anchor" href="#{ident}" aria-label="Link to this section">#</a>' if level > 1 else ""
        return f'<h{level} id="{ident}">{inline(title, self.link)}{mark}</h{level}>'

    def code_block(self, lines: list[str], i: int, out: list[str]) -> int:
        """A fenced code block, shown exactly as written."""
        match = FENCE.match(lines[i])
        assert match is not None
        fence, language = match.group(1), match.group(2)
        body: list[str] = []
        i += 1
        while i < len(lines) and not (lines[i].strip().startswith(fence)
                                      and set(lines[i].strip()) == {fence[0]}):
            body.append(lines[i])
            i += 1
        kind = f' class="language-{escape(language)}"' if language else ""
        out.append(f"<pre><code{kind}>{escape(chr(10).join(body))}</code></pre>")
        return i + 1

    def table(self, lines: list[str], i: int, out: list[str]) -> int:
        """A table: a header row, the |---| rule (which also sets each column's alignment), rows."""
        def cells(row: str) -> list[str]:
            row = row.strip().removeprefix("|")
            if row.endswith("|") and not row.endswith("\\|"):
                row = row[:-1]
            return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row)]

        head = cells(lines[i])
        aligns = []
        for rule in cells(lines[i + 1]):
            aligns.append(' class="al-c"' if rule.startswith(":") and rule.endswith(":")
                          else ' class="al-r"' if rule.endswith(":") else "")
        aligns += [""] * (len(head) - len(aligns))
        rows = []
        i += 2
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            values = cells(lines[i]) + [""] * len(head)
            rows.append("<tr>" + "".join(f"<td{aligns[n]}>{inline(values[n], self.link)}</td>"
                                         for n in range(len(head))) + "</tr>")
            i += 1
        header = "".join(f"<th{aligns[n]}>{inline(cell, self.link)}</th>" for n, cell in enumerate(head))
        out.append(f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead>'
                   f"<tbody>{''.join(rows)}</tbody></table></div>")
        return i

    def list_block(self, lines: list[str], i: int, out: list[str]) -> int:
        """A list. An item owns the lines indented under it (nested lists, code); a blank line
        between items makes the list "loose" (each item a paragraph)."""
        first = ITEM.match(lines[i])
        assert first is not None
        base, ordered = len(first.group(1)), first.group(2)[0].isdigit()
        items: list[list[str]] = []
        loose = False
        while i < len(lines):
            match = ITEM.match(lines[i])
            if not match or len(match.group(1)) != base or match.group(2)[0].isdigit() != ordered:
                break
            content = match.start(3)
            item = [match.group(3)]
            i += 1
            while i < len(lines):
                line = lines[i]
                if not line.strip():
                    after = i + 1
                    while after < len(lines) and not lines[after].strip():
                        after += 1
                    if after < len(lines) and indent_of(lines[after]) >= content:
                        item += [""] * (after - i)
                        loose = True
                        i = after
                        continue
                    break
                if indent_of(line) >= content:
                    item.append(line[content:])
                elif indent_of(line) > base and not ITEM.match(line):
                    item.append(line.strip())
                elif indent_of(line) <= base and not starts_block(line):
                    item.append(line.strip())            # a "lazy" continuation line
                else:
                    break
                i += 1
            items.append(item)
            after = i
            while after < len(lines) and not lines[after].strip():
                after += 1
            following = ITEM.match(lines[after]) if after < len(lines) else None
            if after > i and following and len(following.group(1)) == base:
                loose = True
                i = after
        parts = []
        for item in items:
            body = self.blocks(item)
            if not loose:
                body = re.sub(r"^<p>(.*?)</p>", r"\1", body, count=1, flags=re.S)
            parts.append(f"<li>{body}</li>")
        tag = "ol" if ordered else "ul"
        start = int(first.group(2)[:-1]) if ordered else 1
        opening = f'<ol start="{start}">' if ordered and start != 1 else f"<{tag}>"
        out.append(opening + "".join(parts) + f"</{tag}>")
        return i


def page_title(markdown: str) -> tuple[str, str]:
    """(title, one line) from a page's first heading: "`localagent/fetch.py` — downloads you can
    trust" gives ("fetch.py", "downloads you can trust")."""
    first = next((line for line in markdown.splitlines() if line.startswith("# ")), "# Page")
    title, _, rest = first[2:].partition(" — ")
    name = plain(title)
    return (name.rstrip("/").rsplit("/", 1)[-1] if "`" in title else name), plain(rest)


def describe(markdown: str) -> str:
    """The first real paragraph as plain text, at most 155 characters: the page's description."""
    for block in re.split(r"\n\s*\n", markdown):
        text = block.strip()
        if text and not starts_block(text.splitlines()[0]):
            words = plain(" ".join(text.split()))
            return words if len(words) <= 155 else words[:152].rsplit(" ", 1)[0] + "..."
    return "localagent documentation"


def doc_order(stems: list[str]) -> list[tuple[str, list[str]]]:
    """The documentation's groups, each with its pages in order; pages not named in a group
    are added under "More", so a new page can never go missing."""
    present = set(stems)
    groups = [("Guides", [stem for stem in GUIDES if stem in present])]
    groups += [(area, [stem for stem in names if stem in present]) for area, names in AREAS.items()]
    named = {stem for _, members in groups for stem in members}
    groups.append(("More", sorted(present - named)))
    return [(title, members) for title, members in groups if members]


def fill(template: str, values: dict[str, str]) -> str:
    """Put the values into a template's {{name}} places; a name without a value is an error."""
    missing = sorted(set(re.findall(r"{{(\w+)}}", template)) - set(values))
    if missing:
        raise SystemExit(f"the template needs values for: {', '.join(missing)}")
    return re.sub(r"{{(\w+)}}", lambda m: values[m.group(1)], template)


def size_text(size: int) -> str:
    """A file size the way people read it: "120 KB", "35.7 MB"."""
    return f"{size / 1e3:.0f} KB" if size < 1e6 else f"{size / 1e6:.1f} MB"


def canonical(site_url: str, path: str) -> str:
    """The <link rel="canonical"> tag for a page, when the site's address is known."""
    return f'<link rel="canonical" href="{escape(site_url + path)}">' if site_url else ""


def render_docs(out: Path, values: dict[str, str]) -> list[str]:
    """Every docs/*.md page as docs/<name>.html, plus docs/index.html. Returns the page names."""
    pages = {path.stem: path.read_text(encoding="utf-8") for path in sorted(DOCS.glob("*.md"))}
    titles = {stem: page_title(text) for stem, text in pages.items()}
    groups = doc_order(list(pages))
    order = [stem for _, members in groups for stem in members]

    def link(target: str) -> str | None:
        if target.startswith(("http://", "https://", "#")):
            return target
        path, _, part = target.partition("#")
        name = Path(path).name
        if name.endswith(".md") and name[:-3] in pages:
            return name[:-3] + ".html" + (f"#{part}" if part else "")
        return None                                     # a file that is not on the site

    def label(stem: str) -> str:
        return GUIDES[stem][0] if stem in GUIDES else titles[stem][0]

    def blurb(stem: str) -> str:
        return GUIDES[stem][1] if stem in GUIDES else titles[stem][1]

    def nav(current: str) -> str:
        parts = []
        for title, members in groups:
            entries = []
            for stem in members:
                here = ' aria-current="page"' if stem == current else ""
                entries.append(f'<li><a href="{stem}.html"{here}>{escape(label(stem))}</a></li>')
            parts.append(f"<h2>{escape(title)}</h2><ul>{''.join(entries)}</ul>")
        return "\n".join(parts)

    def pager(number: int) -> str:
        links = ""
        if number > 0:
            back = order[number - 1]
            links += f'<a class="prev" href="{back}.html"><small>Previous</small>{escape(label(back))}</a>'
        if number + 1 < len(order):
            ahead = order[number + 1]
            links += f'<a class="next" href="{ahead}.html"><small>Next</small>{escape(label(ahead))}</a>'
        return links

    template = (WEBSITE / "page.html").read_text(encoding="utf-8")
    (out / "docs").mkdir(parents=True, exist_ok=True)
    for number, stem in enumerate(order):
        renderer = Renderer(link)
        body = renderer.render(pages[stem])
        sections = [(ident, text) for level, ident, text in renderer.headings if level == 2]
        if len(sections) >= 4:                          # a long page gets its contents first
            entries = "".join(f'<li><a href="#{ident}">{escape(text)}</a></li>' for ident, text in sections)
            box = f'<nav class="toc" aria-label="On this page"><strong>On this page</strong><ul>{entries}</ul></nav>'
            body = body.replace("</h1>", "</h1>\n" + box, 1)
        page = fill(template, {**values, "title": escape(label(stem)), "nav": nav(stem), "content": body,
                               "pager": pager(number), "description": escape(describe(pages[stem])),
                               "canonical": canonical(values["site_url"], f"docs/{stem}.html")})
        (out / "docs" / f"{stem}.html").write_text(page, encoding="utf-8")
    cards = []
    for title, members in groups:
        entries = "".join(f'<li><a href="{stem}.html">{escape(label(stem))}<span>{escape(blurb(stem))}</span>'
                          "</a></li>" for stem in members)
        cards.append(f'<section class="group"><h2>{escape(title)}</h2><ul>{entries}</ul></section>')
    intro = ("<h1>Documentation</h1><p>Everything about localagent: guides for using it, and a page for every "
             "file of its code that explains each line — written so that a beginner can follow it.</p>")
    index = fill(template, {**values, "title": "Documentation", "nav": nav("index"), "pager": "",
                            "content": f'<div class="doc-index">{intro}{"".join(cards)}</div>',
                            "description": "Guides and line-by-line code documentation for localagent.",
                            "canonical": canonical(values["site_url"], "docs/index.html")})
    (out / "docs" / "index.html").write_text(index, encoding="utf-8")
    return order


def build(out: Path, dist: Path, site_url: str = "", downloads_url: str = "") -> list[str]:
    """Write the whole site into ``out`` (a previous build there is replaced). Returns the
    rendered documentation pages."""
    if site_url and not site_url.endswith("/"):
        site_url += "/"
    if downloads_url and not downloads_url.endswith("/"):
        downloads_url += "/"
    zip_file = dist / f"localagent-{__version__}-windows-x64.zip"
    wheel = dist / f"localagent-{__version__}-py3-none-any.whl"
    sums = dist / "SHA256SUMS.txt"
    missing = [path.name for path in (zip_file, wheel, sums) if not path.is_file()]
    if missing:
        raise SystemExit(f"missing in {dist}: {', '.join(missing)} - run tools/build_release.py first")
    if out.exists():
        if any(out.iterdir()) and not (out / "index.html").is_file():
            raise SystemExit(f"{out} is not empty and is not a site built before: choose another --out")
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    for name in STATIC:
        shutil.copy2(WEBSITE / name, out / name)
    for name in ASSETS:
        shutil.copy2(ROOT / "assets" / name, out / "assets" / name)
    if not downloads_url:
        (out / "downloads").mkdir()
        for path in (zip_file, wheel, sums):
            shutil.copy2(path, out / "downloads" / path.name)
    values = {"version": __version__, "zip_name": zip_file.name, "zip_size": size_text(zip_file.stat().st_size),
              "wheel_name": wheel.name, "wheel_size": size_text(wheel.stat().st_size),
              "downloads": escape(downloads_url or "downloads/"), "site_url": escape(site_url)}
    pages = render_docs(out, values)
    home = fill((WEBSITE / "index.html").read_text(encoding="utf-8"),
                {**values, "canonical": canonical(site_url, "")})
    (out / "index.html").write_text(home, encoding="utf-8")
    robots = "User-agent: *\nAllow: /\n"
    if site_url:
        addresses = ["", "docs/index.html"] + [f"docs/{stem}.html" for stem in pages]
        entries = "".join(f"<url><loc>{escape(site_url + address)}</loc></url>" for address in addresses)
        (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
                                         f'"http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n',
                                         encoding="utf-8")
        robots += f"Sitemap: {site_url}sitemap.xml\n"
    (out / "robots.txt").write_text(robots, encoding="utf-8")
    return pages


class PageCheck(HTMLParser):
    """Reads one page: every link and source address, every id, and whether each tag that was
    opened is closed again, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []
        self.ids: set[str] = set()
        self.open: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.targets.append(value)
            if name == "id" and value:
                self.ids.add(value)
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.open.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if self.open and self.open[-1] == tag:
            self.open.pop()
            return
        self.problems.append(f"line {self.getpos()[0]}: </{tag}> closes "
                             f"<{self.open[-1] if self.open else 'nothing'}>")
        if tag in self.open:                            # recover, so one slip is reported once
            while self.open.pop() != tag:
                pass


def check_site(out: Path, private: list[str]) -> list[str]:
    """Everything that would be wrong with the site: unfinished templates, unbalanced tags, links
    to files or sections that do not exist, and any of the ``private`` words on any page."""
    out = out.resolve()
    problems: list[str] = []
    pages: dict[Path, PageCheck] = {}
    for path in sorted(out.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        name = path.relative_to(out).as_posix()
        if "{{" in text:
            problems.append(f"{name}: a {{{{placeholder}}}} was not filled in")
        check = PageCheck()
        check.feed(text)
        check.close()
        problems += [f"{name}: {problem}" for problem in check.problems]
        if check.open:
            problems.append(f"{name}: never closed: {', '.join(check.open)}")
        pages[path] = check
    for path, check in pages.items():
        name = path.relative_to(out).as_posix()
        for target in check.targets:
            if target.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            address, _, part = target.partition("#")
            if address.startswith("/"):
                file = out / address.lstrip("/")
            else:
                file = (path.parent / address) if address else path
            if address.endswith("/") or file.is_dir():
                file = file / "index.html"
            file = file.resolve()
            if not file.is_file():
                problems.append(f"{name}: link to a missing file: {target}")
            elif part and file in pages and part not in pages[file].ids:
                problems.append(f"{name}: link to a missing section: {target}")
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".css", ".js", ".txt", ".xml", ".svg", ""):
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for word in private:
                if word.lower() in text:
                    problems.append(f"{path.relative_to(out).as_posix()}: shows '{word}'")
    return problems


def private_words() -> list[str]:
    """The repository owner's name as it appears in its GitHub address, also with spaces for "-":
    the site must never show it. Empty when this folder has no GitHub remote."""
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    found = re.search(r"github\.com[/:]([^/]+)/", remote)
    if not found:
        return []
    owner = found.group(1)
    return sorted({owner, owner.replace("-", " ")})


def serve(out: Path, port: int) -> None:
    """Show the site at http://127.0.0.1:<port>/ until Ctrl+C (only this computer can see it)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(SimpleHTTPRequestHandler, directory=str(out)))
    print(f"the site is at http://127.0.0.1:{port}/  (Ctrl+C stops)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str]) -> int:
    """Build the site, check it, and say where it is."""
    parser = argparse.ArgumentParser(description="Build the localagent website into one folder.")
    parser.add_argument("--out", type=Path, default=ROOT / "site", help="where to write it (default: site/)")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="where the release files are")
    parser.add_argument("--url", default="", help="the site's own address, for canonical links and sitemap.xml")
    parser.add_argument("--downloads-url", default="", help="serve the downloads from this address instead")
    parser.add_argument("--serve", action="store_true", help="show the site at http://127.0.0.1:PORT/ afterwards")
    parser.add_argument("--port", type=int, default=8000, help="the port for --serve (default 8000)")
    args = parser.parse_args(argv)
    for address in (args.url, args.downloads_url):
        if address and urlsplit(address).scheme not in ("http", "https"):
            raise SystemExit(f"{address}: an address must start with https:// (or http://)")
    pages = build(args.out, args.dist, args.url, args.downloads_url)
    problems = check_site(args.out, private_words())
    if problems:
        print("SITE PROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    files = [path for path in args.out.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    print(f"site OK: {len(files)} files, {size_text(total)}, {len(pages)} documentation pages -> {args.out}")
    if args.serve:
        serve(args.out, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
