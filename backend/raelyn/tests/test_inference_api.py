"""使用真实路由与隔离 SQLite 验证 AI 接入配置，不调用推理服务。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.tests.test_codex_inference import run_isolated_inference_case


_API_SETUP = """
import asyncio
import httpx
from fastapi import FastAPI
from raelyn.api.config_api import router

app = FastAPI()
app.include_router(router, prefix="/api")

async def request(method, path, payload=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", trust_env=False) as client:
        return await client.request(method, path, json=payload)

def api(method, path, payload=None):
    return asyncio.run(request(method, path, payload))
"""


class InferenceConfigApiTests(unittest.TestCase):
    def run_api_case(self, script: str) -> None:
        run_isolated_inference_case(self, _API_SETUP + script)

    def test_get_inference_config_defaults_to_local_and_reports_codex_settings(self) -> None:
        self.run_api_case("""
response = api("GET", "/api/config/inference")
assert response.status_code == 200
data = response.json()
assert data["mode"] == "local"
assert data["local_llm_provider"] == "local"
assert data["local_llm_provider_source"] == "env"
assert data["codex"]["model"] == ""
assert data["codex"]["timeout_seconds"] == 43
""")

    def test_put_inference_config_switches_to_codex_and_back_through_real_route(self) -> None:
        self.run_api_case("""
response = api("PUT", "/api/config/inference", {"mode": "local", "local_llm_provider": "codex"})
assert response.status_code == 200, response.text
saved = response.json()
assert saved["ok"] is True
assert saved["data"]["local_llm_provider"] == "codex"
assert saved["data"]["local_llm_provider_source"] == "app_config"
assert api("GET", "/api/config/inference").json()["local_llm_provider"] == "codex"
with SessionLocal() as session:
    assert session.get(AppConfig, "local_llm_provider").value == {"value": "codex"}
    assert get_effective_llm_config(session).provider == "codex"
    assert get_effective_asr_config(session).provider == "local"
response = api("PUT", "/api/config/inference", {"mode": "local", "local_llm_provider": "local"})
assert response.status_code == 200, response.text
assert response.json()["data"]["local_llm_provider"] == "local"
assert api("GET", "/api/config/inference").json()["local_llm_provider"] == "local"
with SessionLocal() as session:
    assert get_effective_llm_config(session).provider == "local"
    assert get_effective_llm_config(session).url == "http://127.0.0.1:11434/api/generate"
    assert get_effective_asr_config(session).provider == "local"
""")

    def test_put_without_local_provider_preserves_saved_selection(self) -> None:
        self.run_api_case("""
assert api("PUT", "/api/config/inference", {"mode": "local", "local_llm_provider": "codex"}).status_code == 200
response = api("PUT", "/api/config/inference", {"mode": "local"})
assert response.status_code == 200, response.text
assert response.json()["data"]["local_llm_provider"] == "codex"
assert api("GET", "/api/config/inference").json()["local_llm_provider"] == "codex"
""")

    def test_invalid_provider_is_rejected_without_overwriting_saved_selection(self) -> None:
        self.run_api_case("""
assert api("PUT", "/api/config/inference", {"mode": "local", "local_llm_provider": "codex"}).status_code == 200
for path, payload in [
    ("/api/config/inference", {"mode": "local", "local_llm_provider": "unknown"}),
    ("/api/config/local_llm_provider", {"value": {"value": "unknown"}}),
]:
    response = api("PUT", path, payload)
    assert response.status_code in (400, 422), response.text
assert api("GET", "/api/config/inference").json()["local_llm_provider"] == "codex"
""")

    def test_volcengine_secrets_are_masked_preserved_and_independent_of_local_provider(self) -> None:
        self.run_api_case("""
# 合成凭据只用于隔离配置库的脱敏与保留验证，不发送给任何服务。
secrets = {"api_key": "test-ark-secret", "asr_app_key": "test-app-secret", "asr_access_key": "test-access-secret"}
response = api("PUT", "/api/config/inference", {
    "mode": "volcengine", "local_llm_provider": "codex",
    "volcengine": {**secrets, "llm_model": "test-hosted-model", "asr_model": "bigmodel"},
})
assert response.status_code == 200, response.text
data = response.json()["data"]
assert data["mode"] == "volcengine"
assert data["local_llm_provider"] == "codex"
for name, secret in secrets.items():
    assert data["volcengine"][name + "_present"] is True
    assert secret not in response.text
    assert data["volcengine"][name + "_masked"].endswith(secret[-4:])
response = api("PUT", "/api/config/inference", {
    "mode": "volcengine", "volcengine": {"llm_model": "test-draft-model", **dict.fromkeys(secrets, "")},
})
assert response.status_code == 200, response.text
assert response.json()["data"]["volcengine"]["llm_model"] == "test-draft-model"
assert response.json()["data"]["local_llm_provider"] == "codex"
with SessionLocal() as session:
    stored = session.get(AppConfig, "volcengine_inference_config").value
    assert all(stored[name] == secret for name, secret in secrets.items())
    assert get_effective_llm_config(session).provider == "volcengine_ark"
    assert get_effective_asr_config(session).provider == "volcengine_speech"
response = api("PUT", "/api/config/inference", {"mode": "local"})
assert response.status_code == 200, response.text
assert response.json()["data"]["local_llm_provider"] == "codex"
with SessionLocal() as session:
    assert get_effective_llm_config(session).provider == "codex"
    assert get_effective_asr_config(session).provider == "local"
for response in (api("GET", "/api/config"), api("GET", "/api/config/inference")):
    assert response.status_code == 200
    assert all(secret not in response.text for secret in secrets.values())
""")


if __name__ == "__main__":
    unittest.main()
