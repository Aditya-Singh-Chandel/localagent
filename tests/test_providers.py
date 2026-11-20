"""Tests for choosing who answers: online services with your own key, model servers you run
yourself, and the guided setup that offers them all. A small local server plays the services;
nothing here needs the internet, a key or a model."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from localagent import cli, providers, wizard
from localagent.backends import make_backend, make_hosted
from localagent.config import Config, online_problem
from localagent.doctor import run_doctor
from localagent.llm import LLMError
from localagent.localserver import LocalModel, describe_local, running_local
from localagent.openai_compat import OpenAICompatClient
from localagent.providers import (PROVIDERS, LocalServer, check_key, find_provider, key_headers,
                                  pick_local_model, pick_model, scan_local_servers)
from localagent.resilient import FallbackBackend

USAGE = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}


def clean_env(extra: dict | None = None) -> dict:
    """This process's environment without any model setting or key, plus ``extra``."""
    kept = {name: value for name, value in os.environ.items()
            if not name.startswith(("LOCALAGENT_", "OLLAMA_")) and not name.endswith("_API_KEY")}
    kept.update(extra or {})
    return kept


class FakeService:
    """An OpenAI-compatible service on a free local port (with ``ollama``: Ollama's model list
    too). It records every request; ``key`` makes it check the key; ``refuse`` names request
    fields it answers HTTP 400 to, like a model that only allows its default temperature."""

    def __init__(self, models=("m1", "m2"), key="", refuse=(), ollama=None):
        self.requests: list[tuple[str, dict, dict]] = []
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send(self, status, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def heard(self, body):
                headers = {name.lower(): value for name, value in self.headers.items()}
                service.requests.append((self.path, headers, body))
                return headers

            def do_GET(self):
                headers = self.heard({})
                if self.path == "/api/tags" and ollama is not None:
                    return self.send(200, {"models": [{"name": name} for name in ollama]})
                if key and headers.get("authorization") != f"Bearer {key}":
                    return self.send(401, {"error": {"message": "invalid key"}})
                if self.path == "/v1/models" and models is not None:
                    return self.send(200, {"data": [{"id": name} for name in models]})
                self.send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.heard(body)
                for name in refuse:
                    if name in body:
                        return self.send(400, {"error": {"message": f"Unsupported parameter: '{name}'"}})
                if body.get("stream"):
                    chunks = [{"choices": [{"delta": {"content": "hi"}}]}, {"choices": [], "usage": USAGE}]
                    text = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
                    return self.send(200, text.encode("utf-8"), "text/event-stream")
                self.send(200, {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                                "usage": USAGE})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.root = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.base = self.root + "/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ProviderTests(unittest.TestCase):
    def test_the_table_is_complete(self):
        names = [provider.name for provider in PROVIDERS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[0], "gemini")                       # the free one comes first
        for provider in PROVIDERS:
            self.assertTrue(provider.key_env and provider.title and provider.note)
            if provider.name != "custom":
                self.assertTrue(provider.base.startswith("https://"), provider.name)
                self.assertTrue(provider.key_page.startswith("https://"), provider.name)
        self.assertEqual(find_provider(" OpenAI ").name, "openai")
        self.assertIsNone(find_provider("nope"))

    def test_keys_travel_in_the_headers_each_service_expects(self):
        anthropic = find_provider("anthropic")
        self.assertEqual(key_headers(anthropic, "k"), {"Authorization": "Bearer k", "x-api-key": "k",
                                                       "anthropic-version": "2023-06-01"})
        self.assertEqual(key_headers(find_provider("openai"), "k"), {"Authorization": "Bearer k"})
        self.assertEqual(key_headers(find_provider("custom"), ""), {})

    def test_good_models_are_suggested(self):
        openai = find_provider("openai")
        self.assertEqual(pick_model(openai, ["whisper-1", "gpt-4.1", "gpt-5-mini"]), "gpt-5-mini")
        dated = pick_model(find_provider("anthropic"), ["claude-3-haiku", "claude-sonnet-4-5-20250929"])
        self.assertEqual(dated, "claude-sonnet-4-5-20250929")
        self.assertEqual(pick_model(openai, ["text-embedding-3-small", "my-model"]), "my-model")
        self.assertEqual(pick_model(openai, []), "")
        self.assertEqual(pick_local_model(["nomic-embed-text", "llama3.1:8b", "qwen3:8b"]), "qwen3:8b")
        self.assertEqual(pick_local_model(["qwen3:8b", "qwen2.5-coder:7b"]), "qwen2.5-coder:7b")
        self.assertEqual(pick_local_model(["phi4"]), "phi4")

    def test_keys_are_checked_by_listing_models(self):
        service = FakeService(models=("a", "b"), key="good")
        try:
            self.assertEqual(check_key(service.base, {"Authorization": "Bearer good"}), ("ok", ["a", "b"], ""))
            state, offered, detail = check_key(service.base, {"Authorization": "Bearer bad"})
            self.assertEqual((state, offered), ("rejected", []))
            self.assertIn("HTTP 401", detail)
        finally:
            service.close()
        self.assertEqual(check_key(service.base, {})[0], "unreachable")     # nothing answers now

    def test_local_servers_are_found_with_their_models(self):
        ollama = FakeService(models=None, ollama=["qwen3:8b"])
        studio = FakeService(models=("qwen/qwen3-8b",))
        try:
            mine = (LocalServer("Ollama", ollama.root, "ollama"),
                    LocalServer("LM Studio", studio.base, "openai"),
                    LocalServer("again", studio.base + "/", "openai"),        # tried only once
                    LocalServer("nothing", "http://127.0.0.1:9/v1", "openai"))
            with mock.patch.object(providers, "LOCAL_SERVERS", ()):
                found = scan_local_servers(mine)
        finally:
            ollama.close()
            studio.close()
        self.assertEqual([(server.title, models) for server, models in found],
                         [("Ollama", ["qwen3:8b"]), ("LM Studio", ["qwen/qwen3-8b"])])


class ClientTests(unittest.TestCase):
    def client(self, service, provider_name, stream=True):
        config = replace(Config(), api_base=service.base, api_key="k", model="m1", stream=stream)
        return OpenAICompatClient(config, find_provider(provider_name))

    def test_a_service_gets_its_headers_and_answer_length(self):
        service = FakeService()
        try:
            reply = self.client(service, "anthropic").chat([{"role": "user", "content": "hello"}], [])
        finally:
            service.close()
        path, headers, body = service.requests[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual((headers["x-api-key"], headers["anthropic-version"]), ("k", "2023-06-01"))
        self.assertEqual(body["max_tokens"], 16384)
        self.assertEqual((reply.message["content"], reply.prompt_tokens), ("hi", 5))

    def test_a_refused_field_is_left_out_from_then_on(self):
        service = FakeService(refuse=("temperature",))
        try:
            client = self.client(service, "openai", stream=False)
            self.assertEqual(client.chat([{"role": "user", "content": "a"}], []).message["content"], "hi")
            self.assertEqual(client.dropped, {"temperature"})
            client.chat([{"role": "user", "content": "b"}], [])
        finally:
            service.close()
        posts = [body for path, _headers, body in service.requests if path.endswith("/completions")]
        self.assertEqual(len(posts), 3)                  # refused, sent again, then right at once
        self.assertNotIn("temperature", posts[2])

    def test_other_refusals_are_reported(self):
        service = FakeService(refuse=("messages",))      # not an optional field: nothing to drop
        try:
            with self.assertRaises(LLMError) as caught:
                self.client(service, "openai").chat([{"role": "user", "content": "a"}], [])
        finally:
            service.close()
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(len(service.requests), 1)


class OnlineConfigTests(unittest.TestCase):
    def test_the_online_service_comes_from_the_environment(self):
        env = clean_env({"LOCALAGENT_PROVIDER": "OpenAI", "OPENAI_API_KEY": "sk",
                         "LOCALAGENT_PROVIDER_MODEL": "gpt-5-mini"})
        with mock.patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        self.assertEqual((config.provider, config.provider_key, config.provider_model, config.provider_base),
                         ("openai", "sk", "gpt-5-mini", "https://api.openai.com/v1"))
        with mock.patch.dict(os.environ, {**env, "LOCALAGENT_PROVIDER_BASE": "https://proxy/v1/"}, clear=True):
            self.assertEqual(Config.from_env().provider_base, "https://proxy/v1")
        with mock.patch.dict(os.environ, clean_env({"LOCALAGENT_PROVIDER": "nope"}), clear=True):
            with self.assertRaisesRegex(ValueError, "unknown provider 'nope'"):
                Config.from_env()
        with mock.patch.dict(os.environ, clean_env({"LOCALAGENT_LOCAL_SERVER": "lmstudio"}), clear=True):
            with self.assertRaisesRegex(ValueError, "LOCALAGENT_LOCAL_SERVER"):
                Config.from_env()

    def test_what_is_missing_is_said(self):
        self.assertIn("GEMINI_API_KEY", online_problem(Config()))
        openai = replace(Config(), provider="openai", provider_base="https://api.openai.com/v1")
        self.assertIn("OPENAI_API_KEY", online_problem(openai))
        self.assertIn("no OpenAI model is chosen", online_problem(replace(openai, provider_key="k")))
        self.assertEqual(online_problem(replace(openai, provider_key="k", provider_model="m")), "")
        custom = replace(Config(), provider="custom", provider_base="", provider_model="m")
        self.assertIn("address", online_problem(custom))
        self.assertEqual(online_problem(replace(custom, provider_base="http://box/v1")), "")  # no key needed


class HostedModeTests(unittest.TestCase):
    def test_another_service_answers_with_the_local_model_as_backup(self):
        config = replace(Config(), provider="openai", provider_model="gpt-5-mini",
                         provider_base="https://api.openai.com/v1", provider_key="k")
        with mock.patch.object(cli, "server_up", return_value=False), \
                mock.patch.object(cli, "ollama_up", return_value=False), \
                mock.patch.object(cli, "can_start", return_value=True), \
                mock.patch.object(cli, "describe_local", return_value="a local model"):
            auto, notes = cli.choose_mode(config, "auto")
            online, _ = cli.choose_mode(config, "online")
        self.assertEqual((auto.backend, auto.fallback_backend), ("hosted", "local"))
        self.assertEqual(notes[0], "mode auto: OpenAI gpt-5-mini answers (OPENAI_API_KEY is set)")
        self.assertEqual((online.backend, online.fallback_backend), ("hosted", ""))
        with mock.patch.object(cli, "find_model_file", return_value=Path("Qwen3-8B.gguf")):
            self.assertEqual(cli.model_label(auto), "openai:gpt-5-mini > local:Qwen3-8B")
        chain = make_hosted(auto)
        self.assertIsInstance(chain, FallbackBackend)
        self.assertEqual([entry.name for entry in chain._entries], ["openai:gpt-5-mini", "local"])
        self.assertEqual(chain._entries[0].backend._provider.name, "openai")
        self.assertIsNotNone(chain._probe)
        with self.assertRaisesRegex(ValueError, "no OpenAI model is chosen"):
            make_hosted(replace(auto, provider_model=""))

    def test_the_provider_flag(self):
        with mock.patch.dict(os.environ, clean_env({"OPENROUTER_API_KEY": "or"}), clear=True):
            config = cli.build_config(cli.build_parser().parse_args(
                ["--provider", "openrouter:qwen/qwen3-coder:free"]))
            with self.assertRaisesRegex(ValueError, "unknown provider 'nope'"):
                cli.build_config(cli.build_parser().parse_args(["--provider", "nope"]))
        self.assertEqual((config.provider, config.provider_model, config.provider_key, config.provider_base),
                         ("openrouter", "qwen/qwen3-coder:free", "or", "https://openrouter.ai/api/v1"))
        saved = clean_env({"LOCALAGENT_PROVIDER": "groq", "LOCALAGENT_PROVIDER_MODEL": "m"})
        with mock.patch.dict(os.environ, saved, clear=True):
            kept = cli.build_config(cli.build_parser().parse_args(["--provider", "groq"]))
        self.assertEqual(kept.provider_model, "m")        # the same service keeps its saved model

    def test_a_chosen_local_server_is_trusted(self):
        config = replace(Config(), local_server="ollama", model="qwen3:8b")
        with mock.patch.object(cli, "server_up") as probed:
            self.assertEqual(cli.local_kind(config), "ollama")
        probed.assert_not_called()                       # checked when it is used, not before
        with mock.patch("localagent.localserver.ollama_up", return_value=False), \
                mock.patch("localagent.localserver.server_up", return_value=True):
            self.assertEqual(running_local(config), "")  # the server at api_base does not count
            self.assertIn("not running now", describe_local(config))
        local = make_backend(replace(config, backend="local"))
        self.assertIsInstance(local, LocalModel)
        self.assertEqual(local._kind, "ollama")

    def test_doctor_checks_another_service(self):
        service = FakeService(models=("models/m1",), key="k")        # named the way Gemini does
        config = replace(Config(), backend="hosted", mode="online", provider="openai", provider_model="m1",
                         provider_base=service.base, provider_key="k")
        try:
            healthy, lines = run_doctor(config)
            missing = run_doctor(replace(config, provider_model="m9"))
            refused = run_doctor(replace(config, provider_key="wrong"))
        finally:
            service.close()
        self.assertTrue(healthy)
        self.assertIn("ok    model m1", lines)
        self.assertTrue(any("key accepted" in line for line in lines))
        self.assertFalse(missing[0])
        self.assertTrue(any(line.startswith("FAIL  model m9 is not offered") for line in missing[1]))
        self.assertTrue(any("refused the key (HTTP 401)" in line for line in refused[1]))

    def test_the_first_start_offers_the_setup(self):
        args = cli.build_parser().parse_args([])
        nothing = replace(Config(), gemini_api_key="")
        terminal = mock.Mock(**{"stdin.isatty.return_value": True, "stdout.isatty.return_value": True})
        with mock.patch.object(cli, "sys", terminal), \
                mock.patch.object(cli, "local_kind", return_value=""), \
                mock.patch.object(cli, "run_subcommand", return_value=0) as setup, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(cli.guided_first_run(nothing, args, read=lambda _q: ""))
            setup.assert_called_once_with("setup", [])
            self.assertFalse(cli.guided_first_run(nothing, args, read=lambda _q: "n"))
            self.assertFalse(cli.guided_first_run(replace(nothing, gemini_api_key="k"), args,
                                                  read=lambda _q: ""))      # online works already
            scripted = cli.build_parser().parse_args(["-p", "hi"])
            self.assertFalse(cli.guided_first_run(nothing, scripted, read=lambda _q: ""))
        self.assertEqual(setup.call_count, 1)


class WizardTests(unittest.TestCase):
    def run_setup(self, answers=(), secrets=(), env=None, found=(), check=("ok", ["m1"], ""),
                  llama=None, model=None, saved_before=None):
        """Run the guided setup with typed ``answers`` (input ends after them), pasted
        ``secrets``, local servers ``found``, and every key check answering ``check``.
        Returns (exit code, everything said, the saved "env", the check_key mock)."""
        said: list[str] = []
        replies, keys = iter(answers), iter(secrets)

        def ask(_question):
            try:
                return next(replies)
            except StopIteration:
                raise EOFError from None

        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            if saved_before is not None:
                settings.write_text(json.dumps({"env": saved_before}), encoding="utf-8")
            state = {"llama": llama, "model": model}

            def fetch_llama(_progress):
                state["llama"] = Path(tmp) / "llama-server"
                return Path(tmp)

            def fetch_model(pick, _progress):
                state["model"] = Path(tmp) / pick.file
                return state["model"]

            with mock.patch.dict(os.environ, clean_env(env), clear=True), \
                    mock.patch.object(wizard, "check_key", return_value=check) as checked, \
                    mock.patch.object(wizard, "find_llama_server", side_effect=lambda _c: state["llama"]), \
                    mock.patch.object(wizard, "find_model_file", side_effect=lambda _c: state["model"]), \
                    mock.patch.object(wizard, "install_llama", side_effect=fetch_llama), \
                    mock.patch.object(wizard, "install_model", side_effect=fetch_model), \
                    mock.patch.object(wizard, "running_local", return_value=""), \
                    mock.patch.object(wizard, "can_start", return_value=False), \
                    mock.patch.object(wizard, "total_memory_gb", return_value=16.0), \
                    mock.patch.object(wizard, "docker_available", return_value=False):
                code = wizard.run_setup(Config.from_env(), ask=ask, secret=lambda _q: next(keys, ""),
                                        say=said.append, settings_file=settings,
                                        progress=lambda _d, _t: None, scan=lambda _own: list(found))
            saved = json.loads(settings.read_text(encoding="utf-8")).get("env", {}) \
                if settings.exists() else None
        return code, "\n".join(said), saved, checked

    def test_a_free_gemini_key_is_checked_and_saved(self):
        code, said, saved, _ = self.run_setup(answers=("", "3"), secrets=("good-key",))
        self.assertEqual(code, 0)
        self.assertEqual(saved, {"GEMINI_API_KEY": "good-key", "LOCALAGENT_PROVIDER": "gemini"})
        self.assertIn("Ready (online only)", said)

    def test_a_key_already_set_is_found_and_a_good_model_suggested(self):
        code, said, saved, checked = self.run_setup(
            answers=("", "", "3"), env={"OPENAI_API_KEY": "sk"},
            check=("ok", ["gpt-4.1", "gpt-5-mini", "whisper-1"], ""))
        self.assertEqual(code, 0)
        self.assertIn("[key found]", said)                # offered first, as the default
        self.assertEqual(saved, {"LOCALAGENT_PROVIDER": "openai", "LOCALAGENT_PROVIDER_MODEL": "gpt-5-mini"})
        self.assertEqual(checked.call_args.args, ("https://api.openai.com/v1", {"Authorization": "Bearer sk"}))

    def test_a_refused_key_saves_nothing(self):
        code, said, saved, _ = self.run_setup(answers=("5", "3"), secrets=("bad",),
                                              check=("rejected", [], "answered HTTP 401"))
        self.assertEqual((code, saved), (1, None))
        self.assertIn("Groq refused the key", said)
        self.assertIn("No model is ready yet", said)

    def test_any_openai_compatible_address(self):
        code, _, saved, checked = self.run_setup(
            answers=("8", "http://box.test:9000/v1/", "", "3"), check=("ok", ["embed-x", "big-model"], ""))
        self.assertEqual(code, 0)
        self.assertEqual(saved, {"LOCALAGENT_PROVIDER": "custom", "LOCALAGENT_PROVIDER_MODEL": "big-model",
                                 "LOCALAGENT_PROVIDER_BASE": "http://box.test:9000/v1"})
        self.assertEqual(checked.call_args.args, ("http://box.test:9000/v1", {}))   # no key: none sent

    def test_a_running_ollama_is_offered_first(self):
        ollama = LocalServer("Ollama", Config.host, "ollama")
        code, said, saved, _ = self.run_setup(answers=("9", "", ""),
                                              found=[(ollama, ["nomic-embed-text", "llama3.1:8b", "qwen3:8b"])])
        self.assertEqual(code, 0)
        self.assertEqual(saved, {"LOCALAGENT_LOCAL_SERVER": "ollama", "LOCALAGENT_MODEL": "qwen3:8b"})
        self.assertIn("Ready (offline only)", said)

    def test_lm_studio_keeps_its_address(self):
        studio = LocalServer("LM Studio", "http://127.0.0.1:1234/v1", "openai")
        _, _, saved, _ = self.run_setup(answers=("9", "1", ""), found=[(studio, ["qwen/qwen3-8b"])])
        self.assertEqual(saved, {"LOCALAGENT_LOCAL_SERVER": "openai", "LOCALAGENT_MODEL": "qwen/qwen3-8b",
                                 "LOCALAGENT_API_BASE": "http://127.0.0.1:1234/v1"})

    def test_the_free_model_is_downloaded_and_used(self):
        code, said, saved, _ = self.run_setup(
            answers=("9", "1", "y", "y"),
            saved_before={"LOCALAGENT_API_BASE": "http://127.0.0.1:1234/v1", "LOCALAGENT_MODEL": "old"})
        self.assertEqual(code, 0)
        self.assertIn("llama.cpp installed in", said)
        self.assertIn("model saved", said)
        self.assertEqual(saved, {"LOCALAGENT_LOCAL_SERVER": "openai"})   # the old server is forgotten

    def test_another_address_is_tried_as_ollama_then_openai(self):
        def models_at(server, timeout):
            return None if server.kind == "ollama" else ["remote-model"]
        with mock.patch.object(wizard, "server_models", side_effect=models_at):
            _, _, saved, _ = self.run_setup(answers=("9", "2", "192.168.1.20:8080/v1", ""))
        self.assertEqual(saved, {"LOCALAGENT_LOCAL_SERVER": "openai", "LOCALAGENT_MODEL": "remote-model",
                                 "LOCALAGENT_API_BASE": "http://192.168.1.20:8080/v1"})

    def test_ending_input_changes_nothing(self):
        code, said, saved, _ = self.run_setup()
        self.assertEqual((code, saved), (1, None))
        self.assertIn("skipped", said)

    def test_a_wrong_number_is_asked_again(self):
        code, said, _, _ = self.run_setup(answers=("42", "9", "3"))
        self.assertIn("please type a number from 1 to 9", said)
        self.assertEqual(code, 1)

    def test_answers_piped_in_by_a_script_work(self):
        code, said, _, _ = self.run_setup(answers=("﻿9", "3"))     # Windows PowerShell's mark
        self.assertNotIn("please type a number", said)
        self.assertEqual(code, 1)
        with mock.patch.object(wizard.sys, "stdin", io.StringIO("piped-key\n")), \
                mock.patch.object(wizard.getpass, "getpass") as hidden, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.read_secret("key? "), "piped-key")
        hidden.assert_not_called()                    # it would wait for the keyboard forever

    def test_an_environment_variable_that_wins_is_pointed_out(self):
        _, said, saved, _ = self.run_setup(answers=("", "", "3"),
                                           env={"LOCALAGENT_PROVIDER": "gemini", "GROQ_API_KEY": "g"},
                                           check=("ok", ["llama-3.3-70b-versatile"], ""))
        self.assertEqual(saved["LOCALAGENT_PROVIDER"], "groq")
        self.assertIn("LOCALAGENT_PROVIDER is also set in your environment", said)


if __name__ == "__main__":
    unittest.main()
