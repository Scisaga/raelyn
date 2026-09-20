from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import config
from raelyn.models import AppConfig
from raelyn.services.inference import EffectiveLlmConfig
from raelyn.services.inference import VOLCENGINE_INFERENCE_CONFIG_KEY
from raelyn.services.inference import build_effective_asr_config_from_payload
from raelyn.services.inference import build_effective_llm_config_from_payload
from raelyn.services.inference import check_llm_health
from raelyn.services.inference import get_effective_asr_config
from raelyn.services.inference import get_effective_llm_config
from raelyn.services.inference import preserve_existing_volcengine_secrets
from raelyn.services.inference import sanitize_config_value
from raelyn.services.inference import test_llm_connection


class _FakeSession:
    def __init__(self, *items: AppConfig):
        self.items = {item.key: item for item in items}

    def get(self, _model, key):
        return self.items.get(key)

    def add(self, item):
        self.items[item.key] = item


class InferenceServiceTests(unittest.TestCase):
    def test_effective_configs_prefer_app_config_in_volcengine_mode(self) -> None:
        session = _FakeSession(
            AppConfig(key="inference_mode", value={"value": "volcengine"}),
            AppConfig(
                key=VOLCENGINE_INFERENCE_CONFIG_KEY,
                value={
                    "api_key": "runtime-ark-key",
                    "llm_model": "doubao-seed",
                    "asr_model": "bigmodel",
                    "asr_app_key": "runtime-app",
                    "asr_access_key": "runtime-access",
                },
            ),
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(config.settings, "llm_url", "http://127.0.0.1:11434/api/generate"))
            stack.enter_context(patch.object(config.settings, "asr_url", "http://127.0.0.1:8001"))
            stack.enter_context(patch.object(config.settings, "volcengine_llm_url", "https://ark.example/api/v3/chat/completions"))
            stack.enter_context(patch.object(config.settings, "volcengine_asr_url", "https://speech.example/api/v3/auc"))
            llm_cfg = get_effective_llm_config(session)
            asr_cfg = get_effective_asr_config(session)

        self.assertEqual(llm_cfg.mode, "volcengine")
        self.assertEqual(llm_cfg.provider, "volcengine_ark")
        self.assertEqual(llm_cfg.source, "app_config")
        self.assertEqual(llm_cfg.url, "https://ark.example/api/v3/chat/completions")
        self.assertEqual(llm_cfg.model, "doubao-seed")
        self.assertEqual(llm_cfg.api_key, "runtime-ark-key")
        self.assertTrue(llm_cfg.configured)

        self.assertEqual(asr_cfg.mode, "volcengine")
        self.assertEqual(asr_cfg.provider, "volcengine_speech")
        self.assertEqual(asr_cfg.source, "app_config")
        self.assertEqual(asr_cfg.url, "https://speech.example/api/v3/auc")
        self.assertEqual(asr_cfg.app_key, "runtime-app")
        self.assertEqual(asr_cfg.access_key, "runtime-access")
        self.assertTrue(asr_cfg.configured)

    def test_build_effective_configs_from_local_payload_ignore_current_runtime_mode(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(patch.object(config.settings, "llm_url", "http://127.0.0.1:11434/api/generate"))
            stack.enter_context(patch.object(config.settings, "llm_model", "qwen2.5"))
            stack.enter_context(patch.object(config.settings, "asr_url", "http://127.0.0.1:8001"))
            stack.enter_context(patch.object(config.settings, "asr_model", "qwen3-asr"))
            llm_cfg = build_effective_llm_config_from_payload({"mode": "local"})
            asr_cfg = build_effective_asr_config_from_payload({"mode": "local"})

        self.assertEqual(llm_cfg.mode, "local")
        self.assertEqual(llm_cfg.provider, "local")
        self.assertEqual(llm_cfg.url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(asr_cfg.mode, "local")
        self.assertEqual(asr_cfg.provider, "local")
        self.assertEqual(asr_cfg.url, "http://127.0.0.1:8001")

    def test_preserve_existing_volcengine_secrets_keeps_stored_values_when_blank(self) -> None:
        session = _FakeSession(
            AppConfig(
                key=VOLCENGINE_INFERENCE_CONFIG_KEY,
                value={"api_key": "old-ark", "asr_app_key": "old-app", "asr_access_key": "old-access"},
            )
        )
        merged = preserve_existing_volcengine_secrets(
            {"api_key": "", "asr_app_key": "", "asr_access_key": "", "llm_model": "doubao-seed"},
            session=session,
        )

        self.assertEqual(merged["api_key"], "old-ark")
        self.assertEqual(merged["asr_app_key"], "old-app")
        self.assertEqual(merged["asr_access_key"], "old-access")
        self.assertEqual(merged["llm_model"], "doubao-seed")

    def test_sanitize_config_value_masks_volcengine_secrets(self) -> None:
        masked = sanitize_config_value(
            VOLCENGINE_INFERENCE_CONFIG_KEY,
            {"api_key": "abcdef123456", "asr_app_key": "app-secret", "asr_access_key": "access-secret"},  # gitleaks:allow -- 合成凭据仅用于验证脱敏
        )

        self.assertNotEqual(masked["api_key"], "abcdef123456")
        self.assertTrue(masked["api_key"].endswith("3456"))
        self.assertNotEqual(masked["asr_app_key"], "app-secret")
        self.assertNotEqual(masked["asr_access_key"], "access-secret")

    def test_check_llm_health_returns_error_for_invalid_headers_json(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="env",
            url="http://127.0.0.1:11434/api/generate",
            model="qwen2.5",
            api_key="",
            headers_json="{",
            timeout_seconds=10,
            configured=True,
        )

        result = check_llm_health(cfg)
        self.assertFalse(result["ok"])
        self.assertTrue(result["configured"])
        self.assertIn("headers_json", result["error"])

    def test_llm_health_ignores_environment_proxy(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="env",
            url="http://llm.test/v1/chat/completions",
            model="qwen2.5",
            api_key="",
            headers_json="",
            timeout_seconds=10,
            configured=True,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("raelyn.services.inference.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            result = check_llm_health(cfg)

        self.assertTrue(result["ok"])
        self.assertEqual(client.call_args.kwargs["trust_env"], False)

    def test_llm_connection_ignores_environment_proxy(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="env",
            url="http://llm.test/v1/chat/completions",
            model="qwen2.5",
            api_key="",
            headers_json="",
            timeout_seconds=10,
            configured=True,
        )
        health_response = Mock()
        health_response.raise_for_status.return_value = None
        completion_response = Mock()
        completion_response.raise_for_status.return_value = None
        completion_response.json.return_value = {"choices": [{"message": {"content": "pong"}}]}

        with patch("raelyn.services.inference.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = health_response
            client.return_value.__enter__.return_value.post.return_value = completion_response
            result = test_llm_connection(cfg)

        self.assertTrue(result["ok"])
        self.assertTrue(all(call.kwargs["trust_env"] is False for call in client.call_args_list))


if __name__ == "__main__":
    unittest.main()
