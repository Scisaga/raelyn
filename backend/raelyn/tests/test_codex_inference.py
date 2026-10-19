"""隔离进程验证 Codex 配置解析；不启动 CLI，不访问推理服务或业务库。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]

_DATABASE_SETUP = """
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from raelyn.db import SessionLocal, engine
from raelyn.models import AppConfig
from raelyn.services.inference import (
    build_effective_asr_config_from_payload, build_effective_llm_config_from_payload,
    get_effective_asr_config, get_effective_llm_config, get_local_llm_provider,
    preserve_existing_volcengine_secrets, save_local_llm_provider,
)

assert engine.url.get_backend_name() == "sqlite"
metadata = MetaData()
table = AppConfig.__table__.to_metadata(metadata)
table.c.value.type = JSON()
metadata.create_all(engine)
assert isinstance(AppConfig.__table__.c.value.type, JSONB)
"""


def run_isolated_inference_case(
    case: unittest.TestCase,
    script: str,
    *,
    environment: dict[str, str] | None = None,
) -> None:
    """进程导入前固定临时库；临时工作目录阻止读取开发者的 .env。"""

    with tempfile.TemporaryDirectory(prefix="raelyn-inference-test-") as directory:
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(_BACKEND_DIR),
            "PYTHONOPTIMIZE": "0",
            "DATABASE_URL": f"sqlite:///{Path(directory) / 'config.sqlite'}",
            "LOCAL_LLM_PROVIDER": "local",
            "LLM_URL": "http://127.0.0.1:11434/api/generate",
            "LLM_MODEL": "test-local-model",
            "LLM_API_KEY": "",
            "LLM_HEADERS_JSON": "",
            "ASR_URL": "http://127.0.0.1:8001",
            "ASR_MODEL": "test-local-asr",
            # 只检查实际可执行文件的可发现性，不执行此命令。
            "CODEX_COMMAND": sys.executable,
            "CODEX_MODEL": "",
            "CODEX_TIMEOUT_SECONDS": "43",
            "VOLCENGINE_LLM_URL": "https://ark.example/api/v3/chat/completions",
            "VOLCENGINE_LLM_MODEL": "",
            "VOLCENGINE_LLM_API_KEY": "",
            "VOLCENGINE_ASR_URL": "https://speech.example/api/v3/auc",
            "VOLCENGINE_ASR_APP_KEY": "",
            "VOLCENGINE_ASR_ACCESS_KEY": "",
        })
        env.update(environment or {})
        result = subprocess.run(
            [sys.executable, "-c", _DATABASE_SETUP + textwrap.dedent(script)],
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        case.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CodexInferenceConfigTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_CODEX_INTEGRATION") == "1",
        "设置 RUN_CODEX_INTEGRATION=1 验证真实 Codex 请求的低推理档位",
    )
    def test_real_llm_request_respects_disabled_deep_thinking(self) -> None:
        from raelyn.config import settings

        command = shutil.which(settings.codex_command)
        if not command:
            self.skipTest("需要服务运行账号已安装并登录 Codex CLI")
        run_isolated_inference_case(self, """
import json
from raelyn.services.llm import llm_generate

response = llm_generate(
    prompt='连接测试：只返回 {"ok":true}。',
    think=False,
    response_format={
        "type": "object", "properties": {"ok": {"type": "boolean", "enum": [True]}},
        "required": ["ok"], "additionalProperties": False,
    },
)
assert json.loads(response["text"]) == {"ok": True}
assert response["meta"]["provider"] == "codex"
assert response["meta"]["reasoning_effort"] == "low"
assert response["usage"]["total_tokens"] > 0
""", environment={
            "LOCAL_LLM_PROVIDER": "codex", "CODEX_COMMAND": command,
            "YTDLP_PROXY": settings.ytdlp_proxy,
            # 只验证传输与推理档位；临时库不写业务额度状态。
            "CODEX_DAILY_WEEKLY_LIMIT_PERCENT": "0",
        })

    def test_default_local_config_does_not_change_http_or_asr_settings(self) -> None:
        run_isolated_inference_case(self, """
with SessionLocal() as session:
    assert get_local_llm_provider(session) == ("local", "env")
    llm = get_effective_llm_config(session)
    asr = get_effective_asr_config(session)
assert llm.provider == "local"
assert llm.model == "test-local-model"
assert llm.url == "http://127.0.0.1:11434/api/generate"
assert asr.provider == "local"
assert asr.model == "test-local-asr"
assert asr.url == "http://127.0.0.1:8001"
""")

    def test_env_codex_has_independent_nonempty_model_and_timeout(self) -> None:
        run_isolated_inference_case(self, """
with SessionLocal() as session:
    assert get_local_llm_provider(session) == ("codex", "env")
    llm = get_effective_llm_config(session)
    asr = get_effective_asr_config(session)
assert llm.mode == "local"
assert llm.provider == "codex"
assert llm.source == "env"
assert llm.url == "codex://local"
assert llm.model and llm.model != "test-local-model"
assert llm.timeout_seconds == 43
assert llm.configured is True
assert llm.api_key == "" and llm.headers_json == ""
assert asr.provider == "local"
assert asr.url == "http://127.0.0.1:8001"
""", environment={"LOCAL_LLM_PROVIDER": "codex"})

    def test_saved_local_provider_overrides_environment_and_switches_back(self) -> None:
        run_isolated_inference_case(self, """
with SessionLocal.begin() as session:
    save_local_llm_provider(session, "codex")
with SessionLocal() as session:
    assert get_local_llm_provider(session) == ("codex", "app_config")
    assert get_effective_llm_config(session).provider == "codex"
    assert get_effective_asr_config(session).provider == "local"
with SessionLocal.begin() as session:
    save_local_llm_provider(session, "local")
with SessionLocal() as session:
    assert get_local_llm_provider(session) == ("local", "app_config")
    assert get_effective_llm_config(session).provider == "local"
    assert get_effective_llm_config(session).source == "app_config"
""")

    def test_draft_provider_selection_does_not_modify_saved_config(self) -> None:
        run_isolated_inference_case(self, """
with SessionLocal.begin() as session:
    save_local_llm_provider(session, "codex")
llm = build_effective_llm_config_from_payload({"mode": "local", "local_llm_provider": "local"})
assert llm.provider == "local"
assert llm.source == "request"
llm = build_effective_llm_config_from_payload({"mode": "local"})
assert llm.provider == "codex"
assert llm.source == "app_config"
asr = build_effective_asr_config_from_payload({"mode": "local", "local_llm_provider": "codex"})
assert asr.provider == "local"
with SessionLocal() as session:
    assert get_local_llm_provider(session) == ("codex", "app_config")
try:
    build_effective_llm_config_from_payload({"mode": "local", "local_llm_provider": "unknown"})
except ValueError:
    pass
else:
    raise AssertionError("无效 provider 应被拒绝")
""")

    def test_codex_uses_configured_model_and_reports_missing_executable(self) -> None:
        run_isolated_inference_case(self, """
from raelyn.services.codex import codex_generate
from raelyn.services.inference import check_llm_health
from raelyn.services.llm import llm_enabled

llm = build_effective_llm_config_from_payload({"mode": "local", "local_llm_provider": "codex"})
assert llm.model == "test-codex-model"
assert llm.timeout_seconds == 43
assert llm.configured is True
assert llm.api_key == ""
with SessionLocal.begin() as session:
    save_local_llm_provider(session, "codex")
assert llm_enabled(), "已选择 Codex 时，缺少 CLI 不能把任务静默标为未配置并跳过"
health = check_llm_health(llm)
assert health["ok"] is False and health["installed"] is False
try:
    codex_generate(prompt="验证缺少 CLI 时明确失败", command="/missing-raelyn-test/codex")
except RuntimeError as exc:
    assert "未找到 Codex CLI" in str(exc)
else:
    raise AssertionError("缺少 CLI 时应失败并交由 worker 重试")
""", environment={"CODEX_MODEL": "test-codex-model", "CODEX_COMMAND": "/missing-raelyn-test/codex"})

    def test_volcengine_draft_preserves_saved_secrets_without_saving_draft(self) -> None:
        run_isolated_inference_case(self, """
# 合成凭据只在临时配置库中使用，不发起真实火山请求。
secrets = {"api_key": "test-stored-ark", "asr_app_key": "test-stored-app", "asr_access_key": "test-stored-access"}
with SessionLocal.begin() as session:
    session.add(AppConfig(key="volcengine_inference_config", value={
        **secrets, "llm_model": "test-saved-model", "asr_model": "bigmodel",
    }))
with SessionLocal() as session:
    merged = preserve_existing_volcengine_secrets({
        **dict.fromkeys(secrets, ""), "llm_model": "test-draft-model", "asr_model": "bigmodel",
    }, session=session)
    draft = {"mode": "volcengine", "local_llm_provider": "codex", "volcengine": merged}
    llm = build_effective_llm_config_from_payload(draft)
    asr = build_effective_asr_config_from_payload(draft)
    assert llm.provider == "volcengine_ark"
    assert llm.model == "test-draft-model"
    assert llm.api_key == secrets["api_key"]
    assert asr.provider == "volcengine_speech"
    assert asr.app_key == secrets["asr_app_key"]
    assert asr.access_key == secrets["asr_access_key"]
with SessionLocal() as session:
    assert session.get(AppConfig, "volcengine_inference_config").value["llm_model"] == "test-saved-model"
    assert get_local_llm_provider(session) == ("local", "env")
""")


if __name__ == "__main__":
    unittest.main()
