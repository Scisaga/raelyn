from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from raelyn.config import settings
from raelyn.services.codex import (
    _child_environment,
    _model_defaults,
    _parse_events,
    _run_process,
    _safe_cli_error,
    codex_health,
    codex_model_name,
    codex_test_connection,
)


class CodexModelConfigTests(unittest.TestCase):
    def test_explicit_model_overrides_real_toml_defaults(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-codex-config-") as directory:
            config = Path(directory) / "config.toml"
            config.write_text('model = "configured-model"\n', encoding="utf-8")
            self.assertEqual(
                codex_model_name(model=" explicit-model ", config_path=config), "explicit-model"
            )

    def test_model_change_is_read_without_reusing_previous_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-codex-config-") as directory:
            config = Path(directory) / "config.toml"
            config.write_text('model = "first-model"\n', encoding="utf-8")
            self.assertEqual(codex_model_name(config_path=config), "first-model")
            config.write_text('model = "second-model"\n', encoding="utf-8")
            self.assertEqual(codex_model_name(config_path=config), "second-model")

    def test_missing_model_uses_alias_and_only_model_parameters_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-codex-config-") as directory:
            config = Path(directory) / "config.toml"
            self.assertEqual(codex_model_name(config_path=config), "codex-default")
            config.write_text(
                'model_reasoning_effort = "low"\n'
                'service_tier = "fast"\n'
                '[mcp_servers.example]\ncommand = "must-not-load"\n',
                encoding="utf-8",
            )
            self.assertEqual(codex_model_name(config_path=config), "codex-default")
            self.assertEqual(
                _model_defaults(config_path=config),
                {"model_reasoning_effort": "low", "service_tier": "fast"},
            )


class CodexProcessTests(unittest.TestCase):
    def test_proxy_is_explicit_and_blank_configuration_stays_direct(self) -> None:
        proxy_keys = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
        direct_environment = _child_environment(proxy="")
        self.assertFalse(proxy_keys.intersection(key.upper() for key in direct_environment))
        self.assertNotIn("NO_PROXY", {key.upper() for key in direct_environment})
        explicit_proxy = "http://configured-proxy.invalid:3128"
        proxy_environment = _child_environment(proxy=explicit_proxy)
        self.assertEqual(
            {key: value for key, value in proxy_environment.items() if key in proxy_keys},
            {key: explicit_proxy for key in proxy_keys},
        )
        self.assertNotIn("NO_PROXY", {key.upper() for key in proxy_environment})

    def test_real_child_receives_stdin_and_only_configured_proxy_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-codex-test-") as directory:
            result = _run_process(
                [
                    sys.executable, "-c",
                    "import json,os,sys; payload=json.load(sys.stdin); "
                    "expected=payload['proxy'].strip(); "
                    "matches=all(os.environ.get(key)==(expected or None) "
                    "for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')); "
                    "print(json.dumps({'text':payload['text'], 'keys':list(os.environ), "
                    "'proxy_matches':matches}))",
                ],
                cwd=directory,
                timeout_seconds=10,
                prompt=json.dumps({"text": "验证中文输入传递", "proxy": settings.ytdlp_proxy}),
            )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["text"], "验证中文输入传递")
        self.assertTrue(payload["proxy_matches"], "子进程代理只能来自 YTDLP_PROXY")
        forbidden = {"NO_PROXY", "OPENAI_API_KEY", "CODEX_API_KEY"}
        self.assertFalse(forbidden.intersection(key.upper() for key in payload["keys"]))
        self.assertNotIn("DATABASE_URL", payload["keys"])
        self.assertEqual(_child_environment(proxy="").get("CODEX_HOME"), os.environ.get("CODEX_HOME"))

    def test_timeout_stops_real_parent_and_descendant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="raelyn-codex-timeout-") as directory:
            marker = Path(directory) / "survived.txt"
            child_pid = Path(directory) / "child.pid"
            descendant = (
                "import time; from pathlib import Path; time.sleep(1); "
                f"Path({str(marker)!r}).write_text('unexpected')"
            )
            parent = (
                "import subprocess,sys,time; from pathlib import Path; "
                f"child=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
                f"Path({str(child_pid)!r}).write_text(str(child.pid)); time.sleep(30)"
            )
            with self.assertRaises(httpx.TimeoutException):
                _run_process(
                    [sys.executable, "-c", parent], cwd=directory, timeout_seconds=0.5, prompt=""
                )
            self.assertTrue(child_pid.is_file(), "真实子进程应已启动后再验证超时回收")
            time.sleep(1)
            self.assertFalse(marker.exists(), "超时后后代进程不应继续执行")

    def test_failure_text_never_copies_stderr(self) -> None:
        secret = "PRIVATE_TRANSCRIPT_AND_ACCESS_TOKEN"
        message = _safe_cli_error("", f"error sending request Authorization: {secret}")
        self.assertIn("网络请求失败", message)
        self.assertNotIn(secret, message)

    def test_event_usage_matches_documented_cli_contract(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "example"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "完成"}},
            {"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3}},
        ]
        usage = _parse_events("\n".join(json.dumps(event) for event in events))
        self.assertEqual(usage, {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11, "call_count": 1})

    def test_tool_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "工具事件"):
            _parse_events(json.dumps({"type": "item.started", "item": {"type": "command_execution"}}))

    def test_failed_turn_is_not_returned_as_success(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "网络请求失败"):
            _parse_events(json.dumps({"type": "turn.failed", "error": {"message": "stream disconnected"}}))

    def test_error_item_is_reported_as_failure_instead_of_tool_use(self) -> None:
        events = [
            {"type": "item.completed", "item": {"type": "error", "message": "error sending request"}},
            {"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3}},
        ]
        with self.assertRaisesRegex(RuntimeError, "网络请求失败"):
            _parse_events("\n".join(json.dumps(event) for event in events))


class CodexInstalledCliTests(unittest.TestCase):
    def test_health_uses_real_installed_cli_without_generation(self) -> None:
        command = os.environ.get("CODEX_COMMAND", "codex")
        if not shutil.which(command):
            self.skipTest("需要安装 Codex CLI，并在服务运行账号下执行 codex login")
        status = codex_health(command=command)
        self.assertTrue(status["installed"])
        self.assertIn("authenticated", status)
        self.assertNotIn("generation_verified", status)
        self.assertNotIn("usage", status)

    @unittest.skipUnless(
        os.environ.get("RUN_CODEX_INTEGRATION") == "1",
        "需要 Codex CLI 已登录且网络可用（可配置 YTDLP_PROXY）；设置 RUN_CODEX_INTEGRATION=1 执行真实结构化生成",
    )
    def test_connection_uses_real_structured_generation(self) -> None:
        status = codex_test_connection(
            command=os.environ.get("CODEX_COMMAND", "codex"),
            model=os.environ.get("CODEX_MODEL", ""),
            timeout_seconds=60,
        )
        self.assertTrue(status["ok"], status.get("error"))
        self.assertTrue(status["generation_verified"])
        self.assertGreater(status["usage"]["total_tokens"], 0)


if __name__ == "__main__":
    # PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_codex
    unittest.main()
