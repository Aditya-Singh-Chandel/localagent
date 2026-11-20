"""Tests for Gemini support (rate limits, retries, fallback) and the Docker sandbox."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from localagent.backends import make_backend, model_limits
from localagent.cli import model_label
from localagent.config import Config
from localagent.doctor import run_doctor
from localagent.llm import LLMError, Reply
from localagent.openai_compat import OpenAICompatClient, to_openai
from localagent.ratelimit import QuotaExhausted, RateLimiter, pacific_day
from localagent.resilient import Entry, FallbackBackend, estimate_tokens, server_delay
from localagent.runners import DockerRunner, LocalRunner, docker_available, sandbox_image_ready
from localagent.tools import ToolBox

from .test_phase2 import FakeServer


class FakeClock:
    """A clock the test moves by hand; sleeping just advances it."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class RateLimiterTests(unittest.TestCase):
    def test_requests_per_minute_wait(self):
        clock = FakeClock()
        limiter = RateLimiter("m", rpm=2, tpm=10_000, rpd=100, clock=clock, sleep=clock.sleep)
        for _ in range(3):
            limiter.before_call(10)
        self.assertEqual(len(clock.slept), 1)                # third call had to wait
        self.assertAlmostEqual(sum(clock.slept), 60.0)

    def test_tokens_per_minute_wait_and_too_big_request(self):
        clock = FakeClock()
        limiter = RateLimiter("m", rpm=100, tpm=1000, rpd=100, clock=clock, sleep=clock.sleep)
        limiter.before_call(600)
        limiter.after_call(600)
        limiter.before_call(600)                             # 600 + 600 > 1000: waits
        self.assertTrue(clock.slept)
        with self.assertRaises(LLMError):
            limiter.before_call(5000)                        # can never fit in one minute

    def test_daily_quota_persists_and_resets_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.json"
            clock = FakeClock()
            first = RateLimiter("m", rpm=100, tpm=10_000, rpd=2, usage_file=usage, clock=clock)
            first.before_call(1)
            first.after_call(7)
            first.before_call(1)
            again = RateLimiter("m", rpm=100, tpm=10_000, rpd=2, usage_file=usage, clock=clock)
            self.assertTrue(again.exhausted())               # counted on disk, survives restart
            with self.assertRaises(QuotaExhausted):
                again.before_call(1)
            self.assertIn("2/2 requests, 7 tokens", again.status())
            other = RateLimiter("x", rpm=100, tpm=10_000, rpd=2, usage_file=usage, clock=clock)
            self.assertIn("0/2 requests", other.status())    # each model has its own counter
            clock.now += 86_400                              # next day
            self.assertFalse(again.exhausted())

    def test_mark_exhausted_and_pacific_day(self):
        limiter = RateLimiter("m", rpm=10, tpm=10_000, rpd=5)
        limiter.mark_exhausted()
        self.assertTrue(limiter.exhausted())
        self.assertEqual(pacific_day(0.0), "1969-12-31")     # midnight UTC is 4 pm in California


class ScriptedBackend:
    """Returns or raises the given items in order, one per chat() call."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def chat(self, messages, tools, on_token=None):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def reply(text: str) -> Reply:
    return Reply({"role": "assistant", "content": text}, 10, 5, 0.1)


class FallbackTests(unittest.TestCase):
    def run_chain(self, *entries, retries=2):
        slept: list[float] = []
        backend = FallbackBackend(list(entries), retries, sleep=slept.append)
        return backend, slept

    def test_retries_busy_model_then_succeeds(self):
        first = ScriptedBackend([LLMError("busy", 503), reply("hi")])
        backend, slept = self.run_chain(Entry("a", first))
        self.assertEqual(backend.chat([], []).message["content"], "hi")
        self.assertEqual((first.calls, slept, backend.last_model), (2, [2.0], "a"))

    def test_server_retry_delay_is_respected(self):
        error = LLMError('HTTP 429: {"retryDelay": "17s"}', 429)
        self.assertEqual(server_delay(error), 17.0)
        first = ScriptedBackend([error, reply("ok")])
        backend, slept = self.run_chain(Entry("a", first))
        backend.chat([], [])
        self.assertEqual(slept, [18.0])

    def test_falls_back_after_retries_and_on_daily_quota(self):
        busy = ScriptedBackend([LLMError("x", 503)] * 3)
        daily = ScriptedBackend([LLMError("429 quota: GenerateRequestsPerDay exceeded", 429)])
        limiter = RateLimiter("b", rpm=100, tpm=10_000, rpd=10)
        local = ScriptedBackend([reply("local")])
        backend, _ = self.run_chain(Entry("a", busy), Entry("b", daily, limiter), Entry("c", local))
        self.assertEqual(backend.chat([], []).message["content"], "local")
        self.assertEqual(busy.calls, 3)                      # 1 try + 2 retries
        self.assertTrue(limiter.exhausted())                 # server said "per day"
        self.assertEqual(backend.last_model, "c")

    def test_exhausted_model_is_skipped_without_a_call(self):
        limiter = RateLimiter("a", rpm=100, tpm=10_000, rpd=1)
        limiter.mark_exhausted()
        skipped = ScriptedBackend([])
        backend, _ = self.run_chain(Entry("a", skipped, limiter), Entry("b", ScriptedBackend([reply("b")])))
        self.assertEqual(backend.chat([], []).message["content"], "b")
        self.assertEqual(skipped.calls, 0)
        self.assertEqual(len(backend.status()), 1)

    def test_bad_request_is_not_hidden_and_all_failing_explains(self):
        backend, _ = self.run_chain(Entry("a", ScriptedBackend([LLMError("bad", 400)])))
        with self.assertRaises(LLMError) as caught:
            backend.chat([], [])
        self.assertEqual(caught.exception.status, 400)
        down = ScriptedBackend([LLMError("no network")] * 3)
        backend, _ = self.run_chain(Entry("a", down))
        with self.assertRaisesRegex(LLMError, "every model failed"):
            backend.chat([], [])
        with self.assertRaises(ValueError):
            FallbackBackend([])

    def test_server_daily_limit_is_learned_and_announced(self):
        notices: list[str] = []
        error = LLMError('HTTP 429: {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", '
                         '"quotaValue": "20"}', 429)
        limiter = RateLimiter("a", rpm=100, tpm=10_000, rpd=1500)
        backend = FallbackBackend([Entry("a", ScriptedBackend([error]), limiter),
                                   Entry("b", ScriptedBackend([reply("b")]))],
                                  sleep=lambda _s: None, on_notice=notices.append)
        backend.chat([], [])
        self.assertTrue(limiter.exhausted())
        self.assertIn("1/20 requests", limiter.status())          # the real limit, not 1500
        self.assertIn("used up", limiter.status())
        self.assertTrue(any("Google's limit: 20/day" in n for n in notices))

    def test_per_model_limit_overrides(self):
        config = replace(Config(), gemini_limits=("m1=5/20", "m2 = 7/30"))
        self.assertEqual(model_limits(config, "m1"), (5, 20))
        self.assertEqual(model_limits(config, "m2"), (7, 30))
        self.assertEqual(model_limits(config, "other"), (config.rpm, config.rpd))
        with self.assertRaisesRegex(ValueError, "model=rpm/rpd"):
            model_limits(replace(config, gemini_limits=("m1=fast",)), "m1")

    def test_limiter_counts_tokens_of_successful_calls(self):
        limiter = RateLimiter("a", rpm=100, tpm=10_000, rpd=10)
        backend, _ = self.run_chain(Entry("a", ScriptedBackend([reply("x")]), limiter))
        backend.chat([{"role": "user", "content": "hello"}], [])
        self.assertIn("1/10 requests, 15 tokens", limiter.status())
        self.assertGreater(estimate_tokens([{"content": "x" * 400}], []), 90)


class RoutingTests(unittest.TestCase):
    def test_escalation_puts_strong_models_first_until_reset(self):
        cheap = ScriptedBackend([reply("cheap"), reply("cheap again")])
        strong = ScriptedBackend([reply("strong")])
        backend = FallbackBackend([Entry("cheap", cheap, RateLimiter("c", 100, 10_000, 100))],
                                  strong=[Entry("strong", strong, RateLimiter("s", 100, 10_000, 100))])
        self.assertEqual(backend.chat([], []).message["content"], "cheap")
        self.assertTrue(backend.escalate())
        self.assertFalse(backend.escalate())                 # already escalated
        self.assertEqual(backend.chat([], []).message["content"], "strong")
        backend.reset_route()
        self.assertEqual(backend.chat([], []).message["content"], "cheap again")
        self.assertEqual(len(backend.status()), 2)
        self.assertFalse(FallbackBackend([Entry("x", cheap)]).escalate())   # nothing to escalate to

    def test_strong_models_are_tried_after_cheap_ones_fail(self):
        limiter = RateLimiter("c", 100, 10_000, 1)
        limiter.mark_exhausted()
        backend = FallbackBackend([Entry("c", ScriptedBackend([]), limiter)],
                                  strong=[Entry("s", ScriptedBackend([reply("s")]))])
        self.assertEqual(backend.chat([], []).message["content"], "s")

    def test_missing_model_is_skipped_for_good(self):
        gone = ScriptedBackend([LLMError("HTTP 404: model no longer available", 404)])
        other = ScriptedBackend([reply("one"), reply("two")])
        notices: list[str] = []
        backend = FallbackBackend([Entry("gone", gone), Entry("other", other)],
                                  sleep=lambda _s: None, on_notice=notices.append)
        self.assertEqual(backend.chat([], []).message["content"], "one")
        self.assertEqual(backend.chat([], []).message["content"], "two")
        self.assertEqual(gone.calls, 1)                       # never asked again
        self.assertTrue(any("HTTP 404" in n for n in notices))

    def test_per_minute_limit_is_learned(self):
        limiter = RateLimiter("a", rpm=12, tpm=10_000, rpd=100)
        error = LLMError('HTTP 429: {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", '
                         '"quotaValue": "5"}', 429)
        backend = FallbackBackend([Entry("a", ScriptedBackend([error, reply("ok")]), limiter)],
                                  sleep=lambda _s: None)
        backend.chat([], [])
        self.assertEqual(limiter._rpm, 5)
        limiter.set_rpm(50)                                  # never raised above what we know
        self.assertEqual(limiter._rpm, 5)


class GeminiWireTests(unittest.TestCase):
    def test_thought_signature_round_trip_reasoning_and_hidden_tokens(self):
        body = {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
            "extra_content": {"google": {"thought_signature": "SIG"}}}]}}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 20, "total_tokens": 150}}
        server = FakeServer(200, json.dumps(body).encode())
        try:
            config = replace(Config(), api_base=server.url, reasoning_effort="low", stream=False)
            got = OpenAICompatClient(config).chat([{"role": "user", "content": "hi"}], [])
            self.assertEqual(server.received[0]["reasoning_effort"], "low")
        finally:
            server.close()
        call = got.message["tool_calls"][0]
        self.assertEqual(call["extra_content"]["google"]["thought_signature"], "SIG")
        self.assertEqual((got.prompt_tokens, got.completion_tokens), (60, 90))   # 70 hidden + 20
        history = [{"role": "assistant", "content": "", "tool_calls": [call]}]
        sent = to_openai(history, gemini=True)
        self.assertEqual(sent[0]["tool_calls"][0]["extra_content"], call["extra_content"])
        self.assertNotIn("extra_content", to_openai(history)[0]["tool_calls"][0])   # local: never

    def test_missing_signatures_get_placeholder_only_for_gemini(self):
        history = [{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "read_file", "arguments": {}}}]}]
        plain = to_openai(history)[0]["tool_calls"][0]
        self.assertNotIn("extra_content", plain)                  # local servers: unchanged
        filled = to_openai(history, gemini=True)[0]["tool_calls"][0]
        self.assertEqual(filled["extra_content"]["google"]["thought_signature"],
                         "skip_thought_signature_validator")

    def test_gemini_backend_needs_key_and_labels(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            make_backend(replace(Config(), backend="gemini"))
        config = replace(Config(), backend="gemini", gemini_api_key="k", gemini_models=("m1",),
                         gemini_strong_models=("m2",), fallback_backend="openai", model="q")
        self.assertIsInstance(make_backend(config), FallbackBackend)
        self.assertEqual(model_label(config), "gemini:m1 > m2 > openai:q")
        self.assertEqual(model_label(Config()), "ollama:qwen3:8b")
        with self.assertRaises(ValueError):
            make_backend(replace(config, fallback_backend="nope"))

    def test_doctor_reports_missing_key(self):
        healthy, lines = run_doctor(replace(Config(), backend="gemini", gemini_api_key=""))
        self.assertFalse(healthy)
        self.assertTrue(any("GEMINI_API_KEY" in line for line in lines))

    def test_doctor_treats_a_missing_backup_as_a_note(self):
        def fake_get(url, headers=None):
            if url.startswith(Config().gemini_base):
                return {"data": [{"id": "models/m1"}, {"id": "models/m2"}]}
            raise OSError("connection refused")             # the backup server is not running
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("localagent.doctor._get_json", side_effect=fake_get):
            config = replace(Config(), backend="gemini", gemini_api_key="k", gemini_models=("m1",),
                             gemini_strong_models=("m2",), fallback_backend="openai",
                             usage_file=Path(tmp) / "usage.json")
            healthy, lines = run_doctor(config)
        self.assertTrue(healthy)                             # Gemini works: the backup is optional
        self.assertFalse(any(line.startswith("FAIL") for line in lines))
        self.assertTrue(any(line.startswith("note  the offline backup") for line in lines))


class RunnerTests(unittest.TestCase):
    def test_local_runner_refuses_shell_and_toolbox_hides_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = LocalRunner(Config(), Path(tmp))
            self.assertEqual(runner.run("shell", ["ls"])[0], 126)
            box = ToolBox(replace(Config(), workspace=Path(tmp)), lambda _a: True)
            names = [spec["function"]["name"] for spec in box.specs()]
            self.assertNotIn("run_shell", names)
            box.close()

    def test_docker_toolbox_offers_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = ToolBox(replace(Config(), workspace=Path(tmp), sandbox="docker"), lambda _a: True)
            self.assertIn("run_shell", [s["function"]["name"] for s in box.specs()])
            self.assertIn("ERROR", box.call("run_shell", {"command": "  "}))
            box.close()                                      # never started: nothing to stop

    @unittest.skipUnless(docker_available() and sandbox_image_ready("localagent-sandbox:latest"),
                         "Docker is not running, or the sandbox image is not built")
    def test_docker_sandbox_isolation_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(Config(), workspace=Path(tmp), sandbox="docker", command_timeout=20)
            box = ToolBox(config, lambda _a: True)
            try:
                box.call("write_file", {"path": "hi.py", "content": "print('from container')\n"})
                self.assertIn("from container", box.call("run_python", {"path": "hi.py"}))
                who = box.call("run_shell", {"command": "id -u && pwd"}).splitlines()
                self.assertEqual(who[0], "exit code 0")
                self.assertNotEqual(who[1], "0")             # not root
                self.assertEqual(who[2], "/work")
                net = box.call("run_shell", {"command": "python -c \"import urllib.request as u; "
                                             "u.urlopen('http://example.com', timeout=5)\""})
                self.assertNotIn("exit code 0", net)         # no network by default
                slow = replace(config, command_timeout=2)
                runner = DockerRunner(slow, Path(tmp))
                code, _ = runner.run("shell", ["sleep 30"])
                runner.stop()
                self.assertEqual(code, 124)                  # timeout kills the command
            finally:
                box.close()


if __name__ == "__main__":
    unittest.main()
