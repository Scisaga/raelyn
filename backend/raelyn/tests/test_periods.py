from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.periods import local_date, period_bounds_utc, period_end_inclusive, period_start


class PeriodsTests(unittest.TestCase):
    def test_period_start_handles_day_week_month(self) -> None:
        target = date(2026, 3, 11)

        self.assertEqual(period_start(target, "day"), date(2026, 3, 11))
        self.assertEqual(period_start(target, "week"), date(2026, 3, 9))
        self.assertEqual(period_start(target, "month"), date(2026, 3, 1))

    def test_period_end_inclusive_handles_day_week_month(self) -> None:
        self.assertEqual(period_end_inclusive(date(2026, 3, 11), "day"), date(2026, 3, 11))
        self.assertEqual(period_end_inclusive(date(2026, 3, 9), "week"), date(2026, 3, 15))
        self.assertEqual(period_end_inclusive(date(2026, 2, 1), "month"), date(2026, 2, 28))

    def test_period_bounds_utc_uses_configured_timezone(self) -> None:
        with patch("raelyn.services.periods.settings.timezone", "Asia/Shanghai"):
            start, end = period_bounds_utc(date(2026, 3, 10), "day")

        self.assertEqual(start, datetime(2026, 3, 9, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 3, 10, 16, 0, tzinfo=timezone.utc))

    def test_local_date_converts_using_configured_timezone(self) -> None:
        ts = datetime(2026, 3, 9, 16, 30, tzinfo=timezone.utc)

        with patch("raelyn.services.periods.settings.timezone", "Asia/Shanghai"):
            result = local_date(ts)

        self.assertEqual(result, date(2026, 3, 10))


if __name__ == "__main__":
    unittest.main()
