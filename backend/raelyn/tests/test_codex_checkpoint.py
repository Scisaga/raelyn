from __future__ import annotations

import json
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

from raelyn.services.codex_checkpoint import (
    checkpoint_key,
    clear_checkpoints,
    load_checkpoint,
    save_checkpoint,
)


class CodexCheckpointFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="raelyn-codex-checkpoint-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "checkpoints"
        self.job_id = uuid.uuid4()
        self.key = checkpoint_key("只处理这段原文", {"model": "example", "effort": "low"}, "json")
        self.result: dict[str, Any] = {
            "text": '{"完成":true}',
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15, "call_count": 1},
            "meta": {"provider": "codex", "model": "example", "done": True, "done_reason": "stop", "reasoning_effort": "low"},
        }

    def test_real_file_round_trip_preserves_usage_and_marks_cache_hit(self) -> None:
        save_checkpoint(self.job_id, self.key, self.result, root=self.root)
        loaded = load_checkpoint(self.job_id, self.key, root=self.root)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["text"], self.result["text"])
        self.assertEqual(loaded["usage"], self.result["usage"])
        self.assertEqual(loaded["meta"]["done_reason"], "stop")
        self.assertTrue(loaded["meta"]["cached_checkpoint"])
        path = self.root / str(self.job_id) / f"{self.key}.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_exact_key_includes_prompt_model_effort_service_tier_and_response_format(self) -> None:
        options = {"model": "example", "effort": "low", "service_tier": "fast", "force": True}
        original = checkpoint_key("原文", options, "json")
        self.assertEqual(original, checkpoint_key("原文", dict(reversed(list(options.items()))), "json"))
        self.assertNotEqual(original, checkpoint_key("另一段原文", options, "json"))
        self.assertNotEqual(original, checkpoint_key("原文", {**options, "model": "other"}, "json"))
        self.assertNotEqual(original, checkpoint_key("原文", {**options, "effort": "high"}, "json"))
        self.assertNotEqual(original, checkpoint_key("原文", {**options, "service_tier": "default"}, "json"))
        self.assertNotEqual(original, checkpoint_key("原文", {**options, "force": False}, "json"))
        self.assertNotEqual(original, checkpoint_key("原文", options, {"type": "object"}))

    def test_identical_input_does_not_cross_job_boundaries(self) -> None:
        save_checkpoint(self.job_id, self.key, self.result, root=self.root)
        self.assertIsNone(load_checkpoint(uuid.uuid4(), self.key, root=self.root))
        self.assertIsNone(load_checkpoint(self.job_id, "0" * 64, root=self.root))

    def test_request_and_authentication_fields_are_never_saved(self) -> None:
        private = "PRIVATE_PROMPT_AND_AUTHENTICATION"
        result = {
            **self.result,
            "prompt": private, "authorization": private,
            "usage": {**self.result["usage"], "token": private},
            "meta": {**self.result["meta"], "prompt": private, "headers": private},
        }
        save_checkpoint(self.job_id, self.key, result, root=self.root)
        path = self.root / str(self.job_id) / f"{self.key}.json"
        self.assertNotIn(private, path.read_text(encoding="utf-8"))

    def test_failed_save_propagates_and_keeps_existing_completed_response(self) -> None:
        save_checkpoint(self.job_id, self.key, self.result, root=self.root)
        invalid = {**self.result, "usage": {"input_tokens": object()}}
        with self.assertRaises(TypeError):
            save_checkpoint(self.job_id, self.key, invalid, root=self.root)
        loaded = load_checkpoint(self.job_id, self.key, root=self.root)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["text"], self.result["text"])

    def test_real_filesystem_error_propagates(self) -> None:
        self.root.mkdir()
        (self.root / str(self.job_id)).write_text("路径被普通文件占用", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            save_checkpoint(self.job_id, self.key, self.result, root=self.root)

    def test_clear_removes_only_one_job_and_is_idempotent(self) -> None:
        other_job = uuid.uuid4()
        save_checkpoint(self.job_id, self.key, self.result, root=self.root)
        save_checkpoint(other_job, self.key, self.result, root=self.root)
        clear_checkpoints(self.job_id, root=self.root)
        clear_checkpoints(self.job_id, root=self.root)
        self.assertFalse((self.root / str(self.job_id)).exists())
        self.assertIsNotNone(load_checkpoint(other_job, self.key, root=self.root))

    def test_corrupt_checkpoint_is_not_silently_treated_as_a_cache_miss(self) -> None:
        save_checkpoint(self.job_id, self.key, self.result, root=self.root)
        path = self.root / str(self.job_id) / f"{self.key}.json"
        path.write_text("损坏的 JSON", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            load_checkpoint(self.job_id, self.key, root=self.root)

    def test_non_digest_key_cannot_escape_job_directory(self) -> None:
        with self.assertRaises(ValueError):
            save_checkpoint(self.job_id, "../../unexpected", self.result, root=self.root)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    # PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_codex_checkpoint
    unittest.main()
