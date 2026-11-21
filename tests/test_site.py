"""Tests for the website builder (tools/build_site.py): the Markdown it renders, the checks it
runs, and a whole site built from the real docs, checked, and served over HTTP like a visitor
would see it. Nothing is uploaded anywhere; the download files are small stand-ins."""
import hashlib
import importlib.util
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
assert _SPEC is not None and _SPEC.loader is not None
site = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(site)
VERSION = site.__version__


def link(target):
    """The link rule of a tiny site with two pages, cli and tools."""
    if target.startswith(("http", "#")):
        return target
    name = target.rsplit("/", 1)[-1]
    return name[:-3] + ".html" if name in ("cli.md", "tools.md") else None


class Quiet(SimpleHTTPRequestHandler):
    """The standard file server without a log line per request."""

    def log_message(self, *_args):
        pass


def fake_dist(folder: Path) -> Path:
    """Small stand-ins for the three release files."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"localagent-{VERSION}-windows-x64.zip").write_bytes(b"PK zip " * 300)
    (folder / f"localagent-{VERSION}-py3-none-any.whl").write_bytes(b"PK wheel")
    (folder / "SHA256SUMS.txt").write_text("abc  file\n", encoding="utf-8")
    return folder


class InlineTests(unittest.TestCase):
    def test_code_links_and_emphasis(self):
        out = site.inline("See `cli.md`, **bold** and *it* [the `tools` page](tools.md)", link)
        self.assertEqual(out, 'See <a href="cli.html"><code>cli.md</code></a>, <strong>bold</strong> and '
                              '<em>it</em> <a href="tools.html">the <code>tools</code> page</a>')

    def test_nothing_becomes_a_tag_or_a_placeholder(self):
        out = site.inline("<script>x</script> & {{name}} `a<b`", link)
        self.assertEqual(out, "&lt;script&gt;x&lt;/script&gt; &amp; &#123;&#123;name}} <code>a&lt;b</code>")

    def test_words_with_marks_stay_as_they_are(self):
        self.assertEqual(site.inline("snake_case_name 17*3 a \\| b", link), "snake_case_name 17*3 a | b")

    def test_addresses(self):
        out = site.inline("Go to https://example.org/a?b=1&c=2. Or <https://x.org>, (https://y.org/z)", link)
        self.assertEqual(out, 'Go to <a href="https://example.org/a?b=1&amp;c=2">https://example.org/a?b=1&amp;c=2'
                              '</a>. Or <a href="https://x.org">https://x.org</a>, (<a href="https://y.org/z">'
                              "https://y.org/z</a>)")
        self.assertEqual(site.inline("[the license](../LICENSE)", link), "the license")   # not on the site


class BlockTests(unittest.TestCase):
    def render(self, text):
        renderer = site.Renderer(link)
        return renderer.render(text), renderer.headings

    def test_headings_get_unique_ids(self):
        out, headings = self.render("# Title\n## `read_secret`\n## Same\n## Same")
        self.assertIn('<h1 id="title">Title</h1>', out)
        self.assertIn('<h2 id="read_secret"><code>read_secret</code><a class="anchor" href="#read_secret"', out)
        self.assertEqual([ident for _, ident, _ in headings], ["title", "read_secret", "same", "same-2"])

    def test_paragraphs_lists_and_quotes(self):
        out, _ = self.render("Text\n2026. still text\n\n3. three\n   more\n4. four\n   - nested\n\n> quoted")
        self.assertIn("<p>Text\n2026. still text</p>", out)
        self.assertIn('<ol start="3"><li>three\nmore</li><li>four\n<ul><li>nested</li></ul></li></ol>', out)
        self.assertIn("<blockquote><p>quoted</p></blockquote>", out)
        loose, _ = self.render("- one\n\n- two")
        self.assertEqual(loose, "<ul><li><p>one</p></li><li><p>two</p></li></ul>")

    def test_tables_and_code(self):
        out, _ = self.render("| A | B | C |\n|---|:-:|--:|\n| `x\\|y` | 2 |\n\n```python\nif a < b:\n```\n---")
        self.assertIn('<th>A</th><th class="al-c">B</th><th class="al-r">C</th>', out)
        self.assertIn('<td><code>x|y</code></td><td class="al-c">2</td><td class="al-r"></td>', out)
        self.assertIn('<pre><code class="language-python">if a &lt; b:</code></pre>', out)
        self.assertTrue(out.endswith("<hr>"))


class PageTests(unittest.TestCase):
    def test_titles_descriptions_order_and_templates(self):
        self.assertEqual(site.page_title("# `localagent/fetch.py` — downloads you can trust\n"),
                         ("fetch.py", "downloads you can trust"))
        self.assertEqual(site.page_title("# `tests/` — proving it works")[0], "tests")
        self.assertEqual(site.page_title("# Roadmap — what comes next")[0], "Roadmap")
        long = "# T\n\n" + "word " * 60
        self.assertTrue(site.describe(long).endswith("...") and len(site.describe(long)) <= 155)
        groups = dict(site.doc_order(["cli", "00-overview", "getting-started", "brand-new"]))
        self.assertEqual(groups, {"Get started": ["getting-started"], "How it is built": ["00-overview"],
                                  "Commands and settings": ["cli"], "More": ["brand-new"]})
        self.assertEqual(site.fill("<b>{{a}}</b>", {"a": "{{b}}"}), "<b>{{b}}</b>")    # values are not re-read
        with self.assertRaises(SystemExit):
            site.fill("{{a}} {{nmae}}", {"a": "1"})
        self.assertEqual((site.size_text(101_403), site.size_text(22_788_740)), ("101 KB", "22.8 MB"))


class DataTests(unittest.TestCase):
    def test_data_inside_a_page_cannot_break_it(self):
        text = site.json_script({"a": "</script><b>", "b": "{{x}}"})
        self.assertNotIn("</", text)
        self.assertNotIn("{{", text)
        self.assertEqual(json.loads(text), {"a": "</script><b>", "b": "{{x}}"})   # still the same data
        lines = site.session_lines([["user", "fix <it>"], ["tool", "-> run ", "{}"], ["ok", "done"]])
        self.assertEqual(lines, '<div><span class="prompt">you&gt; </span><span class="user">fix &lt;it&gt;</span>'
                                '</div><div><span class="tool">-&gt; run </span><span class="args">&#123;}</span>'
                                '</div><div class="ok">done</div>')

    def test_release_facts_come_from_the_files(self):
        changelog = f"# Changelog\n\n## {VERSION} — 21 November 2026\n\n### Added\n- a thing\n\n## 0.0.1 — 1 May\n- old\n"
        self.assertEqual(site.release_date(changelog), "21 November 2026")
        self.assertEqual(site.release_section(changelog), "### Added\n- a thing")
        self.assertEqual(site.release_date("# nothing"), "not yet released")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.zip"
            path.write_bytes(b"abc")
            self.assertEqual(site.file_info(path), {"name": "f.zip", "size": "0 KB", "sha":
                             "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"})

    def test_the_setup_builder_follows_the_program(self):
        self.assertEqual((site.server_id("LM Studio"), site.server_id("llama.cpp")), ("lmstudio", "llamacpp"))
        self.assertEqual([site.memory_label(14, 7), site.memory_label(7, 7), site.memory_label(0, 7)],
                         ["16 GB or more", "8 GB or more", "Less than 8 GB"])
        body = site.setup_body({"zip_name": "z.zip", "zip_size": "1 MB", "wheel_name": "w.whl", "version": VERSION})
        data = json.loads(re.search(r'id="setup-data">(.*?)</script>', body, re.S).group(1))
        self.assertEqual([item["id"] for item in data["online"]][:2], ["gemini", "openai"])
        self.assertEqual(data["skip_number"], len(data["online"]) + 1)          # the setup's "skip" line
        self.assertIn("ollama", [item["id"] for item in data["local"]])
        self.assertIn('id="online-gemini" value="gemini" checked', body)
        link = site.make_link({"cli": ""}, "docs/")
        self.assertEqual([link("cli.md#x"), link("../download.html"), link("README.md"), link("https://a.b")],
                         ["docs/cli.html#x", "../download.html", None, "https://a.b"])

    def test_search_entries_follow_the_headings(self):
        body = ('<h1 id="t">T</h1><p>Intro.</p><h2 id="one">One<a class="anchor" href="#one">#</a></h2>'
                "<p>Words about one.</p><pre><code>hidden code</code></pre><h3 id=\"two\">Two</h3><p>More.</p>")
        entries = site.search_entries(body, "Page", "docs/p.html", "About the page.", True)
        self.assertEqual(entries, [{"p": "Page", "h": "", "u": "docs/p.html", "x": "About the page.", "g": 1},
                                   {"p": "Page", "h": "One", "u": "docs/p.html#one", "x": "Words about one.", "g": 1},
                                   {"p": "Page", "h": "Two", "u": "docs/p.html#two", "x": "More.", "g": 1}])


class SiteTests(unittest.TestCase):
    def build(self, out, dist, **addresses):
        with mock.patch.object(site, "test_count", return_value=42):     # counted for real when the site is built
            return site.build(out, dist, **addresses)

    def test_the_real_site_is_built_checked_and_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist, out = fake_dist(Path(tmp) / "dist"), Path(tmp) / "site"
            pages = self.build(out, dist, site_url="https://example.test")
            self.assertEqual(site.check_site(out, site.private_words()), [])   # never shows the owner
            for name in ("index.html", "download.html", "setup.html", "changelog.html", "privacy.html", "license.html",
                         "404.html", "docs/index.html", "docs/getting-started.html", "docs/providers.html"):
                self.assertIn(name, pages)
            self.assertIn("https://example.test/docs/cli.html", (out / "sitemap.xml").read_text(encoding="utf-8"))
            self.assertIn("Sitemap: https://example.test/sitemap.xml", (out / "robots.txt").read_text(encoding="utf-8"))
            index = json.loads((out / "search-index.json").read_text(encoding="utf-8"))
            self.assertTrue(any(entry["u"] == "docs/getting-started.html" for entry in index))
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(out)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}/"
            try:
                home = urllib.request.urlopen(base, timeout=10).read().decode("utf-8")
                download = urllib.request.urlopen(base + "download.html", timeout=10).read().decode("utf-8")
                doc = urllib.request.urlopen(base + "docs/wizard.html", timeout=10).read().decode("utf-8")
                zipped = urllib.request.urlopen(base + f"downloads/localagent-{VERSION}-windows-x64.zip",
                                                timeout=10).read()
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(base + "docs/nothing.html", timeout=10)
                missing.exception.close()
            finally:
                server.shutdown()
                server.server_close()
        self.assertIn('<link rel="canonical" href="https://example.test/">', home)
        self.assertIn("<strong>42</strong>", home)                            # the number of tests
        self.assertIn('id="sessions-data"', home)
        self.assertIn(f'href="downloads/localagent-{VERSION}-windows-x64.zip" download', download)
        self.assertIn(hashlib.sha256(b"PK zip " * 300).hexdigest(), download)  # its checksum is shown
        self.assertIn('aria-current="page">wizard.py</a>', doc)
        self.assertEqual(zipped, b"PK zip " * 300)

    def test_downloads_can_live_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist, out = fake_dist(Path(tmp) / "dist"), Path(tmp) / "site"
            self.build(out, dist, downloads_url="https://files.example.test/v1")
            download = (out / "download.html").read_text(encoding="utf-8")
            self.assertFalse((out / "downloads").exists())
            self.assertIn(f'href="https://files.example.test/v1/localagent-{VERSION}-windows-x64.zip"', download)
            self.assertEqual(site.check_site(out, []), [])

    def test_problems_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "index.html").write_text('<div><a href="gone.html">x</a><a href="#nowhere">y</a> {{x}}</p>'
                                            '<p style="color:red" onclick="go()">z</p><script>alert(1)</script>'
                                            '<script type="application/json">{}</script>', encoding="utf-8")
            (out / "notes.txt").write_text("made by Jane Doe", encoding="utf-8")
            (out / "data.json").write_text("{broken", encoding="utf-8")
            problems = "\n".join(site.check_site(out, ["Jane-Doe", "Jane Doe"]))
        for expected in ("was not filled in", "</p> closes <div>", "never closed: div", "missing file: gone.html",
                         "missing section: #nowhere", "notes.txt: shows 'Jane Doe'", "blocks inline styles",
                         "blocks inline event handlers", "an inline <script>", "data.json: not valid JSON"):
            self.assertIn(expected, problems)
        self.assertEqual(problems.count("inline <script>"), 1)                # JSON data is allowed

    def test_a_folder_that_is_not_a_site_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist, out = fake_dist(Path(tmp) / "dist"), Path(tmp) / "mine"
            out.mkdir()
            (out / "thesis.docx").write_bytes(b"precious")
            with self.assertRaises(SystemExit):
                self.build(out, dist)
            self.assertEqual((out / "thesis.docx").read_bytes(), b"precious")
            with self.assertRaises(SystemExit):                          # no release files yet
                self.build(Path(tmp) / "new", Path(tmp) / "empty")

    def test_the_owner_is_read_from_the_remote(self):
        for address in ("https://github.com/Some-Owner/repo.git", "git@github.com:Some-Owner/repo.git"):
            answer = mock.Mock(stdout=address + "\n")
            with mock.patch.object(site.subprocess, "run", return_value=answer):
                self.assertEqual(site.private_words(), ["Some Owner", "Some-Owner"])
        with mock.patch.object(site.subprocess, "run", return_value=mock.Mock(stdout="https://example.org/x\n")):
            self.assertEqual(site.private_words(), [])


if __name__ == "__main__":
    unittest.main()
