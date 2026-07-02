from __future__ import annotations

import io
import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from botocore.exceptions import ClientError

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import config
from raelyn.api.assets import _stream_asset
from raelyn.api.system import system_status
from raelyn.services.s3 import ObjectStreamResult, _format_http_last_modified


@contextmanager
def _fake_session_scope():
    yield object()


class AssetDeliveryTests(unittest.TestCase):
    def test_system_status_exposes_startup_probe_asset_delivery(self) -> None:
        with (
            patch.object(config.settings, "asset_direct_probe_url", ""),
            patch.object(config.settings, "asset_direct_probe_timeout_ms", 1000),
            patch.object(config.settings, "asset_proxy_base_path", "/api/assets"),
            patch.object(config.settings, "asset_presign_enabled", True),
            patch.object(config.settings, "s3_endpoint", "http://minio:9000"),
            patch("raelyn.api.system.session_scope", _fake_session_scope),
            patch("raelyn.api.system.get_pause", return_value={"paused": False}),
            patch("raelyn.api.system.get_provider_pauses", return_value={}),
        ):
            payload = system_status()

        self.assertEqual(payload["asset_delivery"]["strategy"], "startup_probe")
        self.assertEqual(payload["asset_delivery"]["direct_probe_url"], "http://minio:9000/")
        self.assertEqual(payload["asset_delivery"]["direct_probe_timeout_ms"], 1000)
        self.assertEqual(payload["asset_delivery"]["proxy_base_path"], "/api/assets")
        self.assertTrue(payload["asset_delivery"]["presign_enabled"])

    def test_system_status_exposes_proxy_strategy_when_presign_disabled(self) -> None:
        with (
            patch.object(config.settings, "asset_direct_probe_url", ""),
            patch.object(config.settings, "asset_direct_probe_timeout_ms", 1000),
            patch.object(config.settings, "asset_proxy_base_path", "/api/assets"),
            patch.object(config.settings, "asset_presign_enabled", False),
            patch.object(config.settings, "s3_endpoint", "http://minio:9000"),
            patch("raelyn.api.system.session_scope", _fake_session_scope),
            patch("raelyn.api.system.get_pause", return_value={"paused": False}),
            patch("raelyn.api.system.get_provider_pauses", return_value={}),
        ):
            payload = system_status()

        self.assertEqual(payload["asset_delivery"]["strategy"], "proxy")
        self.assertEqual(payload["asset_delivery"]["direct_probe_url"], "")
        self.assertEqual(payload["asset_delivery"]["proxy_base_path"], "/api/assets")
        self.assertFalse(payload["asset_delivery"]["presign_enabled"])

    def test_stream_asset_returns_range_headers(self) -> None:
        asset = SimpleNamespace(id=uuid.uuid4(), s3_bucket="raelyn", s3_key="demo.txt")
        stream = ObjectStreamResult(
            body=io.BytesIO(b"hello"),
            content_length=5,
            content_type="text/plain",
            content_range="bytes 0-4/5",
            etag='"etag"',
            last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
        )
        with patch("raelyn.api.assets.s3_get_object_stream", return_value=stream):
            response = _stream_asset(asset, byte_range="bytes=0-4", as_attachment=False)

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(response.headers.get("Content-Range"), "bytes 0-4/5")
        self.assertEqual(response.headers.get("Content-Length"), "5")
        self.assertEqual(response.media_type, "text/plain")

    def test_stream_asset_raises_416_for_invalid_range(self) -> None:
        asset = SimpleNamespace(id=uuid.uuid4(), s3_bucket="raelyn", s3_key="demo.txt")
        err = ClientError(
            {"Error": {"Code": "InvalidRange"}, "ResponseMetadata": {"HTTPStatusCode": 416}},
            "GetObject",
        )
        with patch("raelyn.api.assets.s3_get_object_stream", side_effect=err):
            with self.assertRaises(Exception) as ctx:
                _stream_asset(asset, byte_range="bytes=10-20", as_attachment=False)

        self.assertEqual(getattr(ctx.exception, "status_code", None), 416)

    def test_format_http_last_modified_normalizes_non_utc_timezone(self) -> None:
        value = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(_format_http_last_modified(value), "Mon, 01 Jan 2024 00:00:00 GMT")


if __name__ == "__main__":
    unittest.main()
