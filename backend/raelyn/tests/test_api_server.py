from __future__ import annotations

import logging
import unittest

from raelyn.api_server import _SensitiveQueryLogFilter, _redact_sensitive_log_value


class ApiServerLogTests(unittest.TestCase):
    def test_redact_sensitive_query_values(self) -> None:
        value = "/api/ws/job_stats?interval_seconds=1&token=secret-value&window_hours=24"

        self.assertEqual(
            _redact_sensitive_log_value(value),
            "/api/ws/job_stats?interval_seconds=1&token=[REDACTED]&window_hours=24",
        )

    def test_filter_redacts_record_args(self) -> None:
        record = logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "WebSocket %s" [accepted]',
            args=("127.0.0.1:1", "/api/ws?token=secret"),
            exc_info=None,
        )

        self.assertTrue(_SensitiveQueryLogFilter().filter(record))
        self.assertEqual(record.args, ("127.0.0.1:1", "/api/ws?token=[REDACTED]"))


if __name__ == "__main__":
    unittest.main()
