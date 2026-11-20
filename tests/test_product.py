"""Tests for the product commands: verified, resumable downloads, safe unpacking, the model
catalog and `localagent setup` / `localagent models` (no real download, no model needed)."""
import contextlib
import hashlib
import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from localagent import cli, models
from localagent.config import Config
from localagent.fetch import DownloadError, download, extract_archive
from localagent.settings import save_user_env

DATA = bytes(range(256)) * 4096                  # 1 MB of test content
DATA_SHA = hashlib.sha256(DATA).hexdigest()


class RangeServer:
    """A file server that understands Range requests (or ignores them), and can stop early."""

    def __init__(self, data: bytes, honour_range: bool = True, cut_at: int | None = None) -> None:
        outer = self
        self.ranges: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (name fixed by http.server)
                asked = self.headers.get("Range")
                outer.ranges.append(asked)
                body, status = data, 200
                if asked and honour_range:
                    body, status = data[int(asked.split("=")[1].split("-")[0]):], 206
                if cut_at is not None:
                    body = body[:cut_at]                 # the connection "breaks" early
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/file.bin"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class DownloadTests(unittest.TestCase):
    def serve(self, **kw) -> RangeServer:
        server = RangeServer(DATA, **kw)
        self.addCleanup(server.close)
        return server

    def test_a_download_is_verified_and_kept_once(self):
        server = self.serve()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "m.gguf"
            seen: list[int] = []
            download(server.url, target, DATA_SHA, len(DATA), lambda done, _total: seen.append(done))
            self.assertEqual(target.read_bytes(), DATA)
            self.assertEqual(seen[-1], len(DATA))
            download(server.url, target, DATA_SHA, len(DATA))
            self.assertEqual(len(server.ranges), 1)                # already there: no second fetch

    def test_a_wrong_checksum_deletes_the_file(self):
        server = self.serve()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "m.gguf"
            with self.assertRaisesRegex(DownloadError, "does not match its published checksum"):
                download(server.url, target, "0" * 64, len(DATA))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_an_interrupted_download_continues_where_it_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "m.gguf"
            broken = self.serve(cut_at=300_000)
            with self.assertRaisesRegex(DownloadError, "run the same command again"):
                download(broken.url, target, DATA_SHA, len(DATA))
            self.assertEqual(target.with_name("m.gguf.part").stat().st_size, 300_000)
            good = self.serve()
            download(good.url, target, DATA_SHA, len(DATA))
            self.assertEqual(good.ranges, ["bytes=300000-"])        # only the rest was asked for
            self.assertEqual(target.read_bytes(), DATA)

    def test_a_server_ignoring_the_range_sends_everything_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "m.gguf"
            target.with_name("m.gguf.part").write_bytes(DATA[:1000])
            download(self.serve(honour_range=False).url, target, DATA_SHA, len(DATA))
            self.assertEqual(target.read_bytes(), DATA)

    def test_archives_are_unpacked_only_inside_their_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good, evil = root / "good.zip", root / "evil.zip"
            with zipfile.ZipFile(good, "w") as bundle:
                bundle.writestr("build/llama-server.exe", b"MZ")
            with zipfile.ZipFile(evil, "w") as bundle:
                bundle.writestr("../outside.txt", b"x")
            extract_archive(good, root / "out")
            self.assertTrue((root / "out" / "build" / "llama-server.exe").is_file())
            with self.assertRaisesRegex(DownloadError, "would land outside"):
                extract_archive(evil, root / "out2")
            self.assertFalse((root / "outside.txt").exists())
            tgz = root / "build.tar.gz"
            with tarfile.open(tgz, "w:gz") as bundle:
                info = tarfile.TarInfo("bin/llama-server")
                info.size = 2
                bundle.addfile(info, io.BytesIO(b"\x7fE"))
            extract_archive(tgz, root / "out3")
            self.assertTrue((root / "out3" / "bin" / "llama-server").is_file())


class CatalogTests(unittest.TestCase):
    def test_the_suggestion_follows_the_memory(self):
        self.assertEqual(models.recommend(15.7).name, "qwen3-8b")
        self.assertEqual(models.recommend(7.8).name, "qwen3-4b")
        self.assertEqual(models.recommend(4.0).name, "qwen3-1.7b")
        self.assertEqual(models.recommend(None).name, "qwen3-4b")
        self.assertEqual(models.catalog_entry(" QWEN3-4B ").file, "Qwen3-4B-Q4_K_M.gguf")
        self.assertIsNone(models.catalog_entry("gpt-9"))
        memory = models.total_memory_gb()
        self.assertTrue(memory is None or memory > 0.5)
        self.assertTrue(all(len(m.sha256) == 64 and m.url.endswith(m.file) for m in models.CATALOG))

    def test_the_newest_llama_build_with_a_checksum_is_chosen(self):
        def asset(tag, variant, digest="sha256:" + "a" * 64):
            name = f"llama-{tag}-bin-{variant}.zip"
            return {"name": name, "size": 10, "browser_download_url": f"https://x/{name}", "digest": digest}
        releases = [{"tag_name": "v1.0.0", "assets": [{"name": "nightly-tag.txt"}]},     # placeholder
                    {"tag_name": "b2", "assets": [asset("b2", "win-vulkan-x64", digest="")]},
                    {"tag_name": "b1", "assets": [asset("b1", "win-cpu-x64"), asset("b1", "win-vulkan-x64")]}]
        with mock.patch.object(models, "get_json", return_value=releases):
            name, url, size, sha = models.find_llama_release(("win-vulkan-x64", "win-cpu-x64"))
            self.assertEqual((name, sha), ("llama-b1-bin-win-vulkan-x64.zip", "a" * 64))
            with self.assertRaisesRegex(DownloadError, "no llama.cpp build"):
                models.find_llama_release(("macos-arm64",))

    def test_llama_cpp_is_installed_from_a_verified_archive(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as bundle:
            bundle.writestr("llama-server.exe", b"MZ fake program")
        data = payload.getvalue()
        server = RangeServer(data)
        self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(models, "HOME", Path(tmp)), \
                mock.patch.object(models, "find_llama_release",
                                  return_value=("llama-b9-bin-win-cpu-x64.zip", server.url, len(data),
                                                hashlib.sha256(data).hexdigest())):
            folder = models.install_llama(lambda _d, _t: None)
            self.assertEqual(folder, Path(tmp) / "llama" / "llama-b9-bin-win-cpu-x64")
            self.assertTrue((folder / "llama-server.exe").is_file())
            self.assertEqual(list((Path(tmp) / "llama" / "downloads").iterdir()), [])   # zip deleted

    def test_a_model_goes_to_the_models_folder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(models, "HOME", Path(tmp)), \
                mock.patch.object(models, "download", return_value=Path("x")) as fetched:
            model = models.CATALOG[1]
            models.install_model(model, lambda _d, _t: None)
            url, target, sha, size, _progress = fetched.call_args.args
            self.assertEqual((url, target, sha, size),
                             (model.url, Path(tmp) / "models" / model.file, model.sha256, model.size))


class SetupTests(unittest.TestCase):
    def test_models_list_and_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "models").mkdir()
            gguf = home / "models" / "Qwen3-4B-Q4_K_M.gguf"
            gguf.write_bytes(b"GGUF")
            settings = home / "settings.json"
            said: list[str] = []
            with mock.patch.object(models, "HOME", home), \
                    mock.patch.object(models, "CHECKOUT", home / "none"), \
                    mock.patch.object(models, "find_model_file", return_value=gguf), \
                    mock.patch.dict(os.environ, {"LOCALAGENT_MODEL_FILE": ""}):
                self.assertEqual(models.run_models([], Config(), said.append, settings), 0)
                self.assertTrue(any(line.startswith("  * ") and "Qwen3-4B" in line for line in said))
                self.assertEqual(models.run_models(["use", "qwen3-4b"], Config(), said.append, settings), 0)
                self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["env"]
                                 ["LOCALAGENT_MODEL_FILE"], str(gguf.resolve()))
                self.assertEqual(models.run_models(["use", "qwen3-8b"], Config(), said.append, settings), 2)
                self.assertEqual(models.run_models(["download", "nope"], Config(), said.append, settings), 2)
                self.assertEqual(models.run_models(["frobnicate"], Config(), said.append, settings), 2)

    def test_saving_a_setting_keeps_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"permissions": {"allow": ["run_tests"]}}', encoding="utf-8")
            save_user_env("A", "1", path)
            save_user_env("B", "2", path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, {"permissions": {"allow": ["run_tests"]}, "env": {"A": "1", "B": "2"}})
            save_user_env("A", None, path)                    # None forgets a setting
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["env"], {"B": "2"})
            if os.name != "nt":                               # it can hold keys: yours alone
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_the_commands_are_reached_from_the_command_line(self):
        with mock.patch.object(cli, "run_models", return_value=0) as listed, \
                mock.patch.object(cli, "run_setup", return_value=0) as guided:
            self.assertEqual(cli.main(["models", "use", "qwen3-4b"]), 0)
            self.assertEqual(listed.call_args.args[0], ["use", "qwen3-4b"])
            self.assertEqual(cli.main(["setup"]), 0)
            self.assertTrue(guided.called)
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertIn("localagent setup", out.getvalue())


if __name__ == "__main__":
    unittest.main()
