from __future__ import annotations

import json
import os
import unittest

from raelyn.services.codex_account import _sanitize_rate_limits, read_codex_rate_limits


class CodexRateLimitParsingTests(unittest.TestCase):
    def test_actual_subscription_shape_has_weekly_primary_without_short_window(self) -> None:
        # 复用 2026-10-16 本机真实只读查询的无敏感信息字段。
        bucket = {
            "limitId": "codex", "planType": "pro",
            "primary": {"usedPercent": 15, "windowDurationMins": 10080, "resetsAt": 1792650852},
            "secondary": None,
            "credits": {"hasCredits": True, "unlimited": False},
        }
        result = _sanitize_rate_limits({
            "rateLimits": bucket,
            "rateLimitsByLimitId": {"codex": bucket},
            "rateLimitResetCredits": {"availableCount": 3},
        })
        self.assertEqual(result["weekly"], {
            "used_percent": 15, "window_minutes": 10080, "resets_at": 1792650852,
        })
        self.assertIs(type(result["weekly"]["used_percent"]), int)
        self.assertIsNone(result["short_window"])
        self.assertEqual(result["credits"], {"has_credits": True, "unlimited": False})
        self.assertEqual(result["reset_credits_available_count"], 3)

    def test_documented_single_bucket_compatibility_keeps_fractional_percent(self) -> None:
        result = _sanitize_rate_limits({"rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 25.5, "windowDurationMins": 300, "resetsAt": 1792650852},
            "secondary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": 1792650853},
        }})
        self.assertEqual(result["weekly"]["used_percent"], 42)
        self.assertEqual(result["short_window"]["used_percent"], 25.5)
        self.assertIsNone(result["credits"])

    def test_unknown_bucket_does_not_masquerade_as_codex_weekly_budget(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "可识别"):
            _sanitize_rate_limits({"rateLimitsByLimitId": {"codex_other": {
                "primary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 1792650852},
            }}})

    def test_missing_weekly_window_is_explicit(self) -> None:
        result = _sanitize_rate_limits({"rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1792650852},
        }})
        self.assertIsNone(result["weekly"])

    def test_invalid_usage_fails_without_including_response_data(self) -> None:
        private_value = "PRIVATE_RESPONSE_FIELD"
        with self.assertRaises(RuntimeError) as caught:
            _sanitize_rate_limits({"rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": private_value, "windowDurationMins": 10080, "resetsAt": 1792650852},
            }})
        self.assertNotIn(private_value, str(caught.exception))

    def test_only_allowlisted_account_fields_are_returned(self) -> None:
        private_value = "PRIVATE_RESPONSE_FIELD"
        result = _sanitize_rate_limits({"rateLimits": {
            "limitId": "codex", "email": private_value, "accessToken": private_value,
            "primary": {
                "usedPercent": 15, "windowDurationMins": 10080, "resetsAt": 1792650852,
                "unexpected": private_value,
            },
            "credits": {"hasCredits": True, "unlimited": False, "balance": private_value},
        }})
        self.assertNotIn(private_value, json.dumps(result))


class CodexRateLimitLiveTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_CODEX_ACCOUNT_INTEGRATION") == "1",
        "设置 RUN_CODEX_ACCOUNT_INTEGRATION=1；只读查询已登录 Codex 的订阅额度，不运行生成",
    )
    def test_real_logged_in_cli_reports_subscription_windows(self) -> None:
        result = read_codex_rate_limits(timeout_seconds=30)
        self.assertEqual(result["limit_id"], "codex")
        self.assertIn("codex", result["buckets"])
        self.assertIn("weekly", result)


if __name__ == "__main__":
    # PYTHONPATH=backend ./.venv/bin/python -m unittest raelyn.tests.test_codex_account
    unittest.main()
