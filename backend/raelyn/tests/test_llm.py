from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.inference import EffectiveLlmConfig
from raelyn.services.llm import llm_generate


class LlmServiceTests(unittest.TestCase):
    def test_llm_generate_ollama_json_format_and_options(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="test",
            url="http://llm.test/api/generate",
            model="qwen3.6:35b",
            api_key="",
            headers_json="",
            timeout_seconds=10,
            configured=True,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"response": '{"ok": true}', "prompt_eval_count": 3, "eval_count": 5}

        with patch("raelyn.services.llm.get_effective_llm_config", return_value=cfg):
            with patch("raelyn.services.llm.httpx.Client") as client:
                client.return_value.__enter__.return_value.post.return_value = response
                result = llm_generate(
                    prompt="ping",
                    think=False,
                    response_format="json",
                    options={"temperature": 0},
                )

        payload = client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen3.6:35b")
        self.assertEqual(payload["prompt"], "ping")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"], {"temperature": 0})
        self.assertEqual(result["text"], '{"ok": true}')
        self.assertEqual(result["usage"]["input_tokens"], 3)
        self.assertEqual(result["usage"]["output_tokens"], 5)

    def test_llm_generate_openai_chat_ignores_ollama_format_and_options(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="test",
            url="http://llm.test/v1/chat/completions",
            model="qwen2.5",
            api_key="",
            headers_json="",
            timeout_seconds=10,
            configured=True,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "pong"}}]}

        with patch("raelyn.services.llm.get_effective_llm_config", return_value=cfg):
            with patch("raelyn.services.llm.httpx.Client") as client:
                client.return_value.__enter__.return_value.post.return_value = response
                result = llm_generate(
                    prompt="ping",
                    response_format="json",
                    options={"temperature": 0},
                )

        payload = client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(payload, {"model": "qwen2.5", "messages": [{"role": "user", "content": "ping"}], "stream": False})
        self.assertNotIn("format", payload)
        self.assertNotIn("options", payload)
        self.assertEqual(result["text"], "pong")

    def test_llm_generate_ignores_environment_proxy(self) -> None:
        cfg = EffectiveLlmConfig(
            mode="local",
            provider="local",
            source="test",
            url="http://llm.test/v1/chat/completions",
            model="qwen2.5",
            api_key="",
            headers_json="",
            timeout_seconds=10,
            configured=True,
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "pong"}}]}

        with patch("raelyn.services.llm.get_effective_llm_config", return_value=cfg):
            with patch("raelyn.services.llm.httpx.Client") as client:
                client.return_value.__enter__.return_value.post.return_value = response
                result = llm_generate(prompt="ping")

        self.assertEqual(result["text"], "pong")
        self.assertEqual(client.call_args.kwargs["trust_env"], False)


if __name__ == "__main__":
    unittest.main()
