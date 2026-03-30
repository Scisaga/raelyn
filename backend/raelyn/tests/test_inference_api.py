from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.config_api import InferenceSettingsPayload
from raelyn.api.config_api import put_inference_config
from raelyn.api.config_api import test_inference_config
from raelyn.models import AppConfig
from raelyn.services.inference import VOLCENGINE_INFERENCE_CONFIG_KEY


class _FakeSession:
    def __init__(self, *items: AppConfig):
        self.items = {item.key: item for item in items}

    def get(self, _model, key):
        return self.items.get(key)

    def add(self, item):
        self.items[item.key] = item


@contextmanager
def _session_scope(session: _FakeSession):
    yield session


class InferenceConfigApiTests(unittest.TestCase):
    def test_put_inference_config_persists_mode_and_returns_masked_status(self) -> None:
        session = _FakeSession()
        payload = InferenceSettingsPayload(
            mode="volcengine",
            volcengine={
                "api_key": "ark-secret",
                "llm_model": "doubao-seed",
                "asr_model": "bigmodel",
                "asr_app_key": "app-secret",
                "asr_access_key": "access-secret",
            },
        )

        with patch("raelyn.api.config_api.session_scope", lambda: _session_scope(session)):
            result = put_inference_config(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(session.items["inference_mode"].value["value"], "volcengine")
        self.assertEqual(session.items[VOLCENGINE_INFERENCE_CONFIG_KEY].value["api_key"], "ark-secret")
        self.assertTrue(result["data"]["volcengine"]["api_key_present"])
        self.assertNotEqual(result["data"]["volcengine"]["api_key_masked"], "ark-secret")

    def test_test_inference_config_uses_existing_secrets_when_input_blank(self) -> None:
        session = _FakeSession(
            AppConfig(
                key=VOLCENGINE_INFERENCE_CONFIG_KEY,
                value={
                    "api_key": "stored-ark",
                    "llm_model": "stored-model",
                    "asr_model": "bigmodel",
                    "asr_app_key": "stored-app",
                    "asr_access_key": "stored-access",
                },
            )
        )
        payload = InferenceSettingsPayload(mode="volcengine", volcengine={"api_key": "", "llm_model": "draft-model"})

        with (
            patch("raelyn.api.config_api.session_scope", lambda: _session_scope(session)),
            patch("raelyn.api.config_api.test_llm_connection", return_value={"ok": True, "error": None}) as llm_test,
            patch("raelyn.api.config_api.test_asr_connection", return_value={"ok": True, "error": None}) as asr_test,
        ):
            result = test_inference_config(payload)

        self.assertTrue(result["ok"])
        self.assertTrue(result["volcengine"]["api_key_present"])
        self.assertTrue(result["volcengine"]["asr_app_key_present"])
        self.assertTrue(result["volcengine"]["asr_access_key_present"])
        llm_cfg = llm_test.call_args.args[0]
        asr_cfg = asr_test.call_args.args[0]
        self.assertEqual(llm_cfg.api_key, "stored-ark")
        self.assertEqual(llm_cfg.model, "draft-model")
        self.assertEqual(asr_cfg.app_key, "stored-app")
        self.assertEqual(asr_cfg.access_key, "stored-access")


if __name__ == "__main__":
    unittest.main()
