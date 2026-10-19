"""在真实隔离配置库验证抽取推理档位和缓存口径，不运行生成或比较内容质量。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from raelyn.tests.test_codex_inference import run_isolated_inference_case


class CodexEventEffortTests(unittest.TestCase):
    def test_default_and_allowed_efforts_use_real_settings_validation(self) -> None:
        run_isolated_inference_case(self, """
from raelyn.config import Settings

assert Settings.model_fields["codex_event_extraction_reasoning_effort"].default == "high"
for effort in ("low", "medium", "high", "xhigh", "max"):
    configured = Settings(_env_file=None, codex_event_extraction_reasoning_effort=effort)
    assert configured.codex_event_extraction_reasoning_effort == effort
""")

    def test_invalid_efforts_are_rejected_before_generation(self) -> None:
        run_isolated_inference_case(self, """
from pydantic import ValidationError
from raelyn.config import Settings

for effort in ("", "none", "minimal", "ultra", "HIGH"):
    try:
        Settings(_env_file=None, codex_event_extraction_reasoning_effort=effort)
    except ValidationError:
        pass
    else:
        raise AssertionError("不支持的推理档位必须在配置阶段被拒绝")
""")

    def test_codex_cache_versions_separate_efforts_and_leave_http_and_ollama_unchanged(self) -> None:
        observations = {}
        with tempfile.TemporaryDirectory(prefix="raelyn-codex-effort-spec-") as directory:
            for endpoint, url in (
                ("http", "http://127.0.0.1:9999/v1/chat/completions"),
                ("ollama", "http://127.0.0.1:11434/api/generate"),
            ):
                for effort in ("low", "high"):
                    with self.subTest(endpoint=endpoint, effort=effort):
                        output = Path(directory) / f"{endpoint}-{effort}.json"
                        run_isolated_inference_case(self, """
import json
import os
from pathlib import Path
from raelyn.config import settings
from raelyn.services.event_analysis import event_extraction_spec

with SessionLocal() as session:
    local = event_extraction_spec(session)
with SessionLocal.begin() as session:
    save_local_llm_provider(session, "codex")
with SessionLocal() as session:
    codex = event_extraction_spec(session)
with SessionLocal.begin() as session:
    save_local_llm_provider(session, "local")
with SessionLocal() as session:
    restored = event_extraction_spec(session)

assert local.model == codex.model == restored.model == "shared-test-model"
assert restored.prompt_version == local.prompt_version
assert ":codex-reasoning-" not in local.prompt_version
assert codex.prompt_version.endswith(":codex-reasoning-" + settings.codex_event_extraction_reasoning_effort)
Path(os.environ["RAELYN_CODEX_SPEC_OUTPUT"]).write_text(json.dumps({
    "local_version": local.prompt_version, "codex_version": codex.prompt_version,
}))
""", environment={
                            "LOCAL_LLM_PROVIDER": "local", "LLM_URL": url,
                            "LLM_MODEL": "shared-test-model", "CODEX_MODEL": "shared-test-model",
                            "CODEX_EVENT_EXTRACTION_REASONING_EFFORT": effort,
                            "RAELYN_CODEX_SPEC_OUTPUT": str(output),
                        })
                        observations[endpoint, effort] = json.loads(output.read_text())

        for endpoint in ("http", "ollama"):
            self.assertEqual(observations[endpoint, "low"]["local_version"], observations[endpoint, "high"]["local_version"])
            self.assertNotEqual(observations[endpoint, "low"]["codex_version"], observations[endpoint, "high"]["codex_version"])
        for effort in ("low", "high"):
            self.assertEqual(observations["http", effort]["codex_version"], observations["ollama", effort]["codex_version"])
            self.assertEqual(observations["http", effort]["codex_version"], observations["http", effort]["local_version"] + f":codex-reasoning-{effort}")


if __name__ == "__main__":
    # PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_codex_event_effort
    unittest.main()
