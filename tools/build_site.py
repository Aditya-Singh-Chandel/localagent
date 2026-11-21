"""Build the website: one self-contained folder to put on any web host.

Usage (from the project folder, after tools/build_release.py has made the download files):
    python tools/build_site.py                          # writes site/ and checks it
    python tools/build_site.py --url https://example.org/          # + canonical links, sitemap.xml
    python tools/build_site.py --downloads-url https://files.example.org/v0.6.0/
    python tools/build_site.py --serve                  # build, then show it at http://127.0.0.1:8000

site/index.html            the home page: the session player, what it does, the models, questions
site/download.html         every file with its size and checksum, how to check it, release notes
site/setup.html            the setup builder: your choices turned into exact steps
site/docs/*.html           the user guides and every page of docs/, with search, menu and contents
site/changelog.html, privacy.html, license.html, 404.html
site/downloads/            the release files and SHA256SUMS.txt (not with --downloads-url)
site/css, js, fonts, assets, search-index.json, site.webmanifest, robots.txt, _headers (and sitemap.xml)

The site loads nothing from other sites and never links to the source repository. The build checks
every page: tags, links, sections, the security policy (no inline scripts or styles), its JSON
files - and refuses to finish if a page shows the repository owner's name.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import unittest
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from localagent import __version__  # noqa: E402  (after the path is set)
from localagent.models import CATALOG  # noqa: E402
from localagent.providers import LOCAL_SERVERS, PROVIDERS  # noqa: E402

WEBSITE, DOCS = ROOT / "website", ROOT / "docs"
STATIC_FOLDERS = ("css", "js", "fonts")
ASSETS = ("logo.svg", "social.png", "favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png")
SECTIONS = (                              # the documentation menu: (section, ((page, title, one line), ...))
    ("Get started", (("getting-started", "Get started", "install, set up, and finish a first task"),
                     ("choosing-a-model", "Choose a model", "free, your own key, or on your computer"),
                     ("troubleshooting", "Troubleshooting", "the usual problems and their fixes"))),
    ("Using localagent", (("05-user-manual", "User manual", "every command, flag, setting and tool"),
                          ("01-setup-and-models", "Online and offline", "every service, local model and mode"),
                          ("06-run-and-verify", "Run and verify", "every way to run it, and how to check it"),
                          ("04-start-from-zero", "Start from source", "every step from an empty computer"))),
    ("How it is built", (("00-overview", "How it works", "the architecture, one request through the code"),
                         ("02-roadmap", "Roadmap", "what is built, and what could come next"),
                         ("03-build-log", "Build log", "every decision and measurement, and why"))),
)
AREAS = {                                 # the code reference, grouped as the overview groups it
    "The agent": ("agent", "context", "tools", "verify", "sandbox", "runners", "checkpoint", "session",
                  "meter"),
    "Models": ("providers", "llm", "openai_compat", "backends", "resilient", "ratelimit", "localserver",
               "models", "fetch"),
    "Commands and settings": ("cli", "wizard", "doctor", "config", "settings", "server", "web", "mcp",
                              "package"),
    "Building and testing": ("tests", "evals", "build_docs", "build_release", "build_site", "check_commit",
                             "docker"),
}
LOCAL_HOW = {                             # the setup builder: how each local server is started
    "Ollama": ("Install Ollama from ollama.com, then download a model once:", "ollama pull qwen3:8b", "qwen3:8b"),
    "LM Studio": ("In LM Studio, download a model (for example Qwen3 8B) and start its local server on port 1234.",
                  "", "qwen/qwen3-8b"),
    "llama.cpp": ("Start llama.cpp's server with your model on port 8080:",
                  "llama-server -m your-model.gguf --port 8080 --jinja -c 8192", "your-model"),
    "vLLM": ("Start vLLM's OpenAI-compatible server on port 8000:", "vllm serve Qwen/Qwen3-8B --port 8000",
             "Qwen/Qwen3-8B"),
    "Jan": ("In Jan, start the local API server (port 1337) with the model you want.", "", "your-model"),
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
SITE_PAGE = re.compile(r"(\.\./)?[\w-]+\.html(#[\w-]+)?")
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
    """The documentation menu: each section with its pages in order, then the code reference, one
    group per area. A page that no group names lands in "More", so a new page never goes missing."""
    present = set(stems)
    groups = [(section, [stem for stem, _, _ in members if stem in present]) for section, members in SECTIONS]
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
    """A file size the way people read it: "120 KB", "22.8 MB"."""
    return f"{size / 1e3:.0f} KB" if size < 1e6 else f"{size / 1e6:.1f} MB"


def canonical(site_url: str, path: str) -> str:
    """The <link rel="canonical"> tag for a page, when the site's address is known."""
    return f'<link rel="canonical" href="{escape(site_url + path)}">' if site_url else ""


def json_script(data: object) -> str:
    """Data for a page's script, safe inside <script type="application/json">: no "</" can end the
    element early, and no "{{" can look like a template place."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("{{", "{\\u007b")


def file_info(path: Path) -> dict[str, str]:
    """A download file's name, its size as people read it, and its SHA-256 checksum."""
    return {"name": path.name, "size": size_text(path.stat().st_size),
            "sha": hashlib.sha256(path.read_bytes()).hexdigest()}


def release_date(changelog: str) -> str:
    """The date of this version's section of CHANGELOG.md ("## 0.6.0 — 21 November 2026")."""
    found = re.search(rf"^## {re.escape(__version__)} — (.+)$", changelog, re.M)
    return found.group(1).strip() if found else "not yet released"


def release_section(changelog: str) -> str:
    """This version's part of CHANGELOG.md, without its heading."""
    found = re.search(rf"^## {re.escape(__version__)} .*?$(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    return found.group(1).strip() if found else ""


def test_count() -> int:
    """How many automated tests there are, counted exactly as the test runner counts them."""
    return unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT)).countTestCases()


def read(name: str) -> str:
    """A file of the website's source folder."""
    return (WEBSITE / name).read_text(encoding="utf-8")


def page(layout: str, values: dict[str, str], body: str, *, root: str, title: str, description: str,
         path: str | None, current: str = "", head: str = "") -> str:
    """A whole page: the shared frame around ``body``, with this page's title, description and
    address. ``current`` marks the page's own entry in the top menu."""
    site_url = values["site_url"]
    whole = fill(layout, {**values, "root": root, "title": escape(title), "description": escape(description),
                          "canonical": canonical(site_url, path) if path is not None else "",
                          "og_image": (site_url or root) + "assets/social.png", "head_extra": head, "body": body})
    if current:
        whole = whole.replace(f'data-page="{current}"', f'data-page="{current}" aria-current="page"')
    return whole


def make_link(pages: dict[str, str], prefix: str) -> Link:
    """The link rule for pages ``prefix`` away from the docs folder ("" inside it, "docs/" from the
    top): outside addresses and #sections stay, the site's own pages stay, a documentation page
    becomes its .html, and anything else is not on the site (None)."""
    def link(target: str) -> str | None:
        if target.startswith(("http://", "https://", "#", "mailto:")) or SITE_PAGE.fullmatch(target):
            return target
        path, _, part = target.partition("#")
        name = Path(path).name
        if name.endswith(".md") and name[:-3] in pages:
            return prefix + name[:-3] + ".html" + (f"#{part}" if part else "")
        return None
    return link


def session_lines(lines: list[list[str]]) -> str:
    """One recorded session as HTML lines, the way the player draws them - so the page shows a
    complete session even without JavaScript."""
    rows = []
    for kind, text, *extra in lines:
        if kind in ("user", "cmd"):
            prompt = "you&gt; " if kind == "user" else "$ "
            rows.append(f'<div><span class="prompt">{prompt}</span><span class="{kind}">{escape(text)}</span></div>')
        elif kind == "tool":
            rows.append(f'<div><span class="tool">{escape(text)}</span>'
                        f'<span class="args">{escape(extra[0] if extra else "")}</span></div>')
        else:
            rows.append(f'<div class="{kind}">{escape(text)}</div>')
    return "".join(rows)


def home_body(values: dict[str, str]) -> str:
    """The home page: the session player (first session shown complete), the numbers, the rest."""
    sessions = json.loads(read("sessions.json"))
    first = sessions[0]
    tabs = "".join(f'<button type="button" role="tab" aria-selected="{"true" if number == 0 else "false"}" '
                   f'data-session="{escape(session["id"])}">{escape(session["tab"])}</button>'
                   for number, session in enumerate(sessions))
    return fill(read("home.html"), {**values, "session_tabs": tabs, "session_lines": session_lines(first["lines"]),
                                    "session_summary": escape(first["summary"]),
                                    "session_folder": escape(first["folder"]), "sessions_json": json_script(sessions),
                                    "test_count": str(test_count()), "provider_count": f"{len(PROVIDERS) - 1}+",
                                    "server_count": str(len(LOCAL_SERVERS))})


def choices(name: str, options: list[tuple[str, str, str]], checked: str) -> str:
    """Radio cards for the setup builder: (value, title, small print) each; ``checked`` is chosen."""
    cards = []
    for value, title, note in options:
        ident = f"{name}-{value}"
        mark = " checked" if value == checked else ""
        cards.append(f'<div class="choice"><input type="radio" name="{name}" id="{ident}" value="{escape(value)}"'
                     f'{mark}><label for="{ident}"><strong>{escape(title)}</strong><small>{escape(note)}</small>'
                     "</label></div>")
    return "".join(cards)


def server_id(title: str) -> str:
    """A local server's short name: "LM Studio" -> "lmstudio", "llama.cpp" -> "llamacpp"."""
    return re.sub(r"[^a-z]", "", title.lower())


def memory_label(memory: int, smallest_other: int) -> str:
    """How a model's memory threshold reads: 14 -> "16 GB or more"; 0 -> "Less than 8 GB"."""
    common = next(size for size in (4, 8, 16, 32, 64, 128) if size >= max(memory, smallest_other))
    return f"{common} GB or more" if memory else f"Less than {common} GB"


def setup_body(values: dict[str, str]) -> str:
    """The setup builder: its choices and the data its script turns into steps - all from the
    program's own tables (providers.py, models.py), so the page cannot drift from the program."""
    online = [{"id": provider.name, "title": provider.title[0].upper() + provider.title[1:], "note": provider.note,
               "env": provider.key_env, "page": provider.key_page,
               "model": provider.suggested[0] if provider.suggested else "", "menu": number,
               "menu_title": provider.title} for number, provider in enumerate(PROVIDERS, 1)]
    local: list[dict[str, str]] = [{"id": "download", "title": "A free model, downloaded for you",
                                    "menu_label": "download a free model for me"}]
    for server in LOCAL_SERVERS:
        how, command, model = LOCAL_HOW[server.title]
        local.append({"id": server_id(server.title), "title": server.title, "kind": server.kind,
                      "base": server.base if server.kind == "openai" else "", "model": model, "how": how,
                      "command": command, "menu_label": f"{server.title} at {server.base}"})
    local.append({"id": "remote", "title": "A server on another computer", "kind": "openai",
                  "base": "http://192.168.1.20:8080/v1", "model": "your-model", "command": "",
                  "how": "Start the model server on that computer so that it listens on the network (llama.cpp: "
                         "add --host 0.0.0.0), and note its address.",
                  "menu_label": "a model server at another address"})
    models = [{"name": model.name, "size": round(model.size / 1e9, 1), "memory": model.memory_gb} for model in CATALOG]
    data = {"zip": values["zip_name"], "zip_size": values["zip_size"], "wheel": values["wheel_name"],
            "skip_number": len(PROVIDERS) + 1, "online": online, "local": local, "models": models}
    online_cards = choices("online", [(provider.name, provider.title[0].upper() + provider.title[1:], provider.note)
                                     for provider in PROVIDERS]
                           + [("none", "No online model", "work offline only")], "gemini")
    offline_cards = choices("offline", [("download", "A free model for me", "downloaded and started for you")]
                            + [(server_id(server.title), server.title, f"already running, port {urlsplit(server.base).port}")
                               for server in LOCAL_SERVERS]
                            + [("remote", "Another computer", "a server on your network"),
                               ("none", "No offline model", "online only")], "download")
    thresholds = [model.memory_gb for model in CATALOG if model.memory_gb]
    ram_cards = choices("ram", [(str(model.memory_gb), memory_label(model.memory_gb, min(thresholds)),
                                 f"{model.name} · {round(model.size / 1e9, 1)} GB download") for model in CATALOG],
                        str(CATALOG[0].memory_gb))
    return fill(read("setup.html"), {**values, "online_choices": online_cards, "offline_choices": offline_cards,
                                     "ram_choices": ram_cards, "setup_json": json_script(data)})


class SectionText(HTMLParser):
    """Reads a rendered page and collects the text under each h2 and h3 heading - code blocks and
    the "#" links left out: the snippets of the search index."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[list[str]] = [["", "", ""]]      # [id, heading, text]; the first: the intro
        self.in_heading = False
        self.in_code = 0
        self.in_anchor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag in ("h2", "h3"):
            self.sections.append([found.get("id") or "", "", ""])
            self.in_heading = True
        elif tag == "pre":
            self.in_code += 1
        elif tag == "a" and found.get("class") == "anchor":
            self.in_anchor = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h2", "h3"):
            self.in_heading = False
        elif tag == "pre":
            self.in_code -= 1
        elif tag == "a":
            self.in_anchor = False

    def handle_data(self, data: str) -> None:
        if not self.in_code and not self.in_anchor:
            self.sections[-1][1 if self.in_heading else 2] += data


def search_entries(body: str, title: str, url: str, description: str, guide: bool) -> list[dict]:
    """The search index's entries for one page: the page itself, then each of its sections."""
    reader = SectionText()
    reader.feed(body)
    entries: list[dict] = [{"p": title, "h": "", "u": url, "x": description, **({"g": 1} if guide else {})}]
    for ident, heading, text in reader.sections[1:]:
        if ident and heading.strip():
            snippet = " ".join(text.split())
            entries.append({"p": title, "h": " ".join(heading.split()), "u": f"{url}#{ident}",
                            "x": snippet[:140] + ("…" if len(snippet) > 140 else ""), **({"g": 1} if guide else {})})
    return entries


def render_docs(pages: dict[str, str], layout: str, values: dict[str, str], write: Callable[[str, str], None],
                index: list[dict]) -> list[str]:
    """Every documentation page as docs/<name>.html, and docs/index.html. Returns the pages in
    menu order; their search entries go into ``index``."""
    groups = doc_order(list(pages))
    order = [stem for _, members in groups for stem in members]
    titles = {stem: page_title(text) for stem, text in pages.items()}
    menu = {stem: (title, line) for _, members in SECTIONS for stem, title, line in members}
    section_of = {stem: title for title, members in groups for stem in members}
    link = make_link(pages, "")
    template = read("docs.html")

    def label(stem: str) -> str:
        return menu[stem][0] if stem in menu else titles[stem][0]

    def blurb(stem: str) -> str:
        return menu[stem][1] if stem in menu else titles[stem][1]

    def sidebar(current: str) -> str:
        parts, reference = [], False
        for title, members in groups:
            if title in AREAS:
                if not reference:
                    parts.append("<h2>Code reference</h2>")
                    reference = True
                parts.append(f'<p class="sub">{escape(title)}</p>')
            else:
                parts.append(f"<h2>{escape(title)}</h2>")
            entries = []
            for stem in members:
                here = ' aria-current="page"' if stem == current else ""
                entries.append(f'<li><a href="{stem}.html"{here}>{escape(label(stem))}</a></li>')
            parts.append(f"<ul>{''.join(entries)}</ul>")
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

    for number, stem in enumerate(order):
        renderer = Renderer(link)
        body = renderer.render(pages[stem])
        sections = [(ident, text) for level, ident, text in renderer.headings if level == 2]
        toc = ""
        if len(sections) >= 3:
            entries = "".join(f'<li><a href="#{ident}">{escape(text)}</a></li>' for ident, text in sections)
            toc = f'<aside class="toc" aria-label="On this page"><strong>On this page</strong><ul>{entries}</ul></aside>'
        middle = "" if section_of[stem] == label(stem) else f"<li>{escape(section_of[stem])}</li>"
        crumbs = (f'<li><a href="index.html">Docs</a></li>{middle}'
                  f'<li><span aria-current="page">{escape(label(stem))}</span></li>')
        inner = fill(template, {"layout_class": "" if toc else " no-toc", "sidebar": sidebar(stem),
                                "breadcrumbs": crumbs, "content": body, "pager": pager(number), "toc": toc})
        description = describe(pages[stem])
        write(f"docs/{stem}.html", page(layout, values, inner, root="../", title=f"{label(stem)} — localagent docs",
                                        description=description, path=f"docs/{stem}.html", current="docs"))
        guide = section_of[stem] in ("Get started", "Using localagent")
        index.extend(search_entries(body, label(stem), f"docs/{stem}.html", description, guide))
    cards = ['<h1>Documentation</h1><p class="lead-text">Guides for using localagent, and a page for every file of '
             "its code that explains each line — written so that a beginner can follow it.</p>"
             '<p><button class="btn btn-secondary" type="button" data-search-open>Search the documentation</button></p>']
    reference = False
    for title, members in groups:
        entries = "".join(f'<li><a href="{stem}.html"><strong>{escape(label(stem))}</strong>'
                          f"<span>{escape(blurb(stem))}</span></a></li>" for stem in members)
        if title in AREAS:
            if not reference:
                cards.append('<h2 id="code-reference">Code reference</h2><p>Every file of the program, explained line '
                             "by line.</p>")
                reference = True
            cards.append(f"<h3>{escape(title)}</h3>")
        else:
            cards.append(f'<h2 id="{slug(title, set())}">{escape(title)}</h2>')
        cards.append(f'<ul class="doc-cards">{entries}</ul>')
    inner = fill(template, {"layout_class": " no-toc", "sidebar": sidebar("index"), "pager": "",
                            "breadcrumbs": '<li><span aria-current="page">Docs</span></li>',
                            "content": f'<div class="doc-index">{"".join(cards)}</div>', "toc": ""})
    write("docs/index.html", page(layout, values, inner, root="../", title="Documentation — localagent",
                                  description="Guides for using localagent, and line-by-line documentation of its code.",
                                  path="docs/index.html", current="docs"))
    return order


def article(layout: str, values: dict[str, str], *, name: str, eyebrow: str, heading: str, intro: str,
            content: str, current: str = "") -> str:
    """A simple page with a title band: the changelog, privacy, the license."""
    body = fill(read("article.html"), {"eyebrow": escape(eyebrow), "heading": escape(heading),
                                       "intro": escape(intro), "content": content})
    return page(layout, values, body, root="", title=f"{heading} — localagent", description=intro,
                path=name, current=current)


def build(out: Path, dist: Path, site_url: str = "", downloads_url: str = "") -> list[str]:
    """Write the whole site into ``out`` (a previous build there is replaced). Returns every page
    written."""
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
    out.mkdir(parents=True)
    for folder in STATIC_FOLDERS:
        shutil.copytree(WEBSITE / folder, out / folder)
    shutil.copy2(WEBSITE / "_headers", out / "_headers")
    (out / "assets").mkdir()
    for name in ASSETS:
        shutil.copy2(ROOT / "assets" / name, out / "assets" / name)
    if not downloads_url:
        (out / "downloads").mkdir()
        for path in (zip_file, wheel, sums):
            shutil.copy2(path, out / "downloads" / path.name)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    zipped, packaged = file_info(zip_file), file_info(wheel)
    values = {"version": __version__, "site_url": escape(site_url), "downloads": escape(downloads_url or "downloads/"),
              "zip_name": zipped["name"], "zip_size": zipped["size"], "zip_sha": zipped["sha"],
              "wheel_name": packaged["name"], "wheel_size": packaged["size"], "wheel_sha": packaged["sha"],
              "release_date": escape(release_date(changelog))}
    layout = read("layout.html")
    written: list[str] = []

    def write(path: str, text: str) -> None:
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_text(text, encoding="utf-8")
        written.append(path)

    pages = {path.stem: path.read_text(encoding="utf-8") for path in sorted((WEBSITE / "guide").glob("*.md"))}
    pages.update({path.stem: path.read_text(encoding="utf-8") for path in sorted(DOCS.glob("*.md"))})
    index: list[dict] = []
    render_docs(pages, layout, values, write, index)
    top = make_link(pages, "docs/")
    write("index.html", page(layout, values, home_body(values), root="", path="", title="localagent — the coding agent "
                             "that keeps working offline", description="A free coding agent for your terminal: it edits, "
                             "runs your tests and checks every change - with a free online model, your own key, or a "
                             "model on your own computer."))
    notes = Renderer(top).render(release_section(changelog))
    write("download.html", page(layout, values, fill(read("download.html"), {**values, "release_notes": notes}),
                                root="", path="download.html", current="download",
                                title=f"Download localagent {__version__}",
                                description="Download localagent for Windows, macOS and Linux - every file with its "
                                            "size and checksum."))
    write("setup.html", page(layout, values, setup_body(values), root="", path="setup.html", current="setup",
                             title="Setup builder — localagent", description="Choose your computer and your models, "
                             "and get the exact steps to set localagent up."))
    history = changelog.split("\n", 1)[1]                  # without its "# Changelog" title
    write("changelog.html", article(layout, values, name="changelog.html", eyebrow="Changelog", current="changelog",
                                    heading="What's new", intro="Every version of localagent and what changed in it.",
                                    content=Renderer(top).render(history)))
    write("privacy.html", article(layout, values, name="privacy.html", eyebrow="Privacy", heading="Privacy",
                                  intro="What this website and the localagent program do with your data: as little "
                                        "as possible.", content=Renderer(top).render(read("pages/privacy.md"))))
    terms = "\n".join(line for line in (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()
                      if not line.startswith("Copyright")).strip()   # the notice itself travels in each download
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").replace("# ", "## ", 1)
    write("license.html", article(layout, values, name="license.html", eyebrow="License", heading="License",
                                  intro="localagent is free and open source under the MIT License.",
                                  content='<h2 id="mit-license">MIT License</h2><p>The terms below apply to '
                                          "localagent. Its copyright notice is in the <code>LICENSE</code> file that "
                                          f"comes with every download.</p><pre><code>{escape(terms)}</code></pre>"
                                          f"{Renderer(top).render(notices)}"))
    write("404.html", page(layout, values, fill(read("notfound.html"), {"root": "/"}), root="/", path=None,
                           title="Page not found — localagent", description="This page does not exist.",
                           head='<meta name="robots" content="noindex">'))
    index += [{"p": "Download", "h": "", "u": "download.html", "x": "Every file with its size and checksum.", "g": 1},
              {"p": "Setup builder", "h": "", "u": "setup.html", "x": "Your choices turned into exact steps.", "g": 1},
              {"p": "Changelog", "h": "", "u": "changelog.html", "x": "What changed in every version.", "g": 1},
              {"p": "Privacy", "h": "", "u": "privacy.html", "x": "What happens with your data.", "g": 1},
              {"p": "License", "h": "", "u": "license.html", "x": "MIT, and the third-party software.", "g": 1}]
    (out / "search-index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")
    manifest = {"name": "localagent", "short_name": "localagent", "start_url": "./index.html", "scope": "./",
                "display": "browser", "background_color": "#050914", "theme_color": "#050914",
                "description": "A coding agent for your terminal that keeps working offline.",
                "icons": [{"src": "assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "assets/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
    (out / "site.webmanifest").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    robots = "User-agent: *\nAllow: /\n"
    if site_url:
        entries = "".join(f"<url><loc>{escape(site_url + path)}</loc></url>"
                          for path in written if path != "404.html")
        (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
                                         f'"http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n',
                                         encoding="utf-8")
        robots += f"Sitemap: {site_url}sitemap.xml\n"
    (out / "robots.txt").write_text(robots, encoding="utf-8")
    return written


class PageCheck(HTMLParser):
    """Reads one page: every link and source address, every id, whether each tag that was opened
    is closed again in order, and anything the security policy would block (inline scripts,
    inline styles, event handlers)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []
        self.ids: set[str] = set()
        self.open: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        where = f"line {self.getpos()[0]}"
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.targets.append(value)
            if name == "id" and value:
                self.ids.add(value)
            if name == "style" or name.startswith("on"):
                self.problems.append(f"{where}: <{tag} {name}=...> - the security policy blocks inline "
                                     f"{'styles' if name == 'style' else 'event handlers'}")
        if tag == "script" and not found.get("src") and found.get("type") != "application/json":
            self.problems.append(f"{where}: an inline <script> - the security policy blocks it")
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
    """Everything that would be wrong with the site: unfinished templates, unbalanced tags, what
    the security policy blocks, links to files or sections that do not exist, broken JSON, and
    any of the ``private`` words on any page."""
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
    for path in sorted([*out.rglob("*.json"), *out.rglob("*.webmanifest")]):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{path.relative_to(out).as_posix()}: not valid JSON ({exc})")
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".css", ".js", ".json", ".webmanifest", ".txt", ".xml",
                                              ".svg", ""):
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
    print(f"site OK: {len(files)} files, {size_text(total)}, {len(pages)} pages -> {args.out}")
    if args.serve:
        serve(args.out, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
