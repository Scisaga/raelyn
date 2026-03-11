from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterator

from dateutil import tz

from raelyn.config import settings


_ALLOWED_GRANULARITIES = {"day", "week", "month"}


def normalize_granularity(value: str | None, *, default: str = "day") -> str:
    granularity = str(value or default).strip().lower() or default
    if granularity not in _ALLOWED_GRANULARITIES:
        raise ValueError("invalid granularity (day/week/month)")
    return granularity


def month_add_one(d: date) -> date:
    y = int(d.year)
    m = int(d.month)
    if m >= 12:
        return date(y + 1, 1, 1)
    return date(y, m + 1, 1)


def period_start(d: date, granularity: str) -> date:
    g = normalize_granularity(granularity)
    if g == "day":
        return d
    if g == "week":
        return d - timedelta(days=d.weekday())
    return date(d.year, d.month, 1)


def period_end_inclusive(period_start_value: date, granularity: str) -> date:
    g = normalize_granularity(granularity)
    if g == "day":
        return period_start_value
    if g == "week":
        return period_start_value + timedelta(days=6)
    return month_add_one(period_start_value) - timedelta(days=1)


def period_bounds_utc(period_start_value: date, granularity: str, *, timezone_name: str | None = None) -> tuple[datetime, datetime]:
    g = normalize_granularity(granularity)
    tzinfo = tz.gettz(timezone_name or settings.timezone) or tz.tzlocal()
    start = datetime.combine(period_start_value, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
    if g == "day":
        return start, start + timedelta(days=1)
    if g == "week":
        return start, start + timedelta(days=7)
    end_date = month_add_one(period_start_value)
    end = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=tzinfo).astimezone(tz.tzutc())
    return start, end


def day_bounds_utc(d: date, *, timezone_name: str | None = None) -> tuple[datetime, datetime]:
    return period_bounds_utc(d, "day", timezone_name=timezone_name)


def local_date(ts: Any | None, *, timezone_name: str | None = None) -> date | None:
    if not ts:
        return None
    try:
        tzinfo = tz.gettz(timezone_name or settings.timezone) or tz.tzlocal()
        return ts.astimezone(tzinfo).date()
    except Exception:
        try:
            return ts.date()
        except Exception:
            return None


def iter_period_starts(start: date, end: date, granularity: str) -> Iterator[date]:
    g = normalize_granularity(granularity)
    current = period_start(start, g)
    stop = period_start(end, g)
    while current <= stop:
        yield current
        if g == "day":
            current = current + timedelta(days=1)
        elif g == "week":
            current = current + timedelta(days=7)
        else:
            current = month_add_one(current)
