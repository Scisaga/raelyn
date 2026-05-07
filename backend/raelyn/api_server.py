from __future__ import annotations

import logging
import re

import uvicorn

from raelyn.services.log_timestamps import install_if_needed

_SENSITIVE_QUERY_RE = re.compile(r"([?&](?:token|access_token|api_key)=)[^&\s\"]+")


def _redact_sensitive_log_value(value):
    if not isinstance(value, str):
        return value
    return _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", value)


class _SensitiveQueryLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_sensitive_log_value(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_sensitive_log_value(value) for key, value in record.args.items()}
        return True


def _install_sensitive_query_log_filter() -> None:
    log_filter = _SensitiveQueryLogFilter()
    for logger_name in ["uvicorn.error", "uvicorn.access"]:
        logger = logging.getLogger(logger_name)
        if any(isinstance(item, _SensitiveQueryLogFilter) for item in logger.filters):
            continue
        logger.addFilter(log_filter)


def main() -> None:
    install_if_needed()
    _install_sensitive_query_log_filter()
    uvicorn.run("raelyn.main:app", host="0.0.0.0", port=8000, reload=False, access_log=False)


if __name__ == "__main__":
    main()
