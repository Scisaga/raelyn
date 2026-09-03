from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from urllib.parse import unquote, urlsplit
import unittest

from raelyn.config import Settings


_MIB = 1024 * 1024


class _LocalS3Handler(BaseHTTPRequestHandler):
    """只实现本测试需要的对象 PUT/HEAD/GET，实际承接 boto3 HTTP 传输。"""

    protocol_version = "HTTP/1.1"
    objects: dict[str, bytes] = {}
    methods: list[str] = []

    def _object_path(self) -> str:
        return unquote(urlsplit(self.path).path)

    def _request_body(self) -> bytes:
        content_length = self.headers.get("Content-Length")
        if content_length is not None:
            return self.rfile.read(int(content_length))
        if str(self.headers.get("Transfer-Encoding") or "").lower() != "chunked":
            return b""
        chunks: list[bytes] = []
        while True:
            size_line = self.rfile.readline().split(b";", 1)[0].strip()
            size = int(size_line, 16)
            if size == 0:
                while self.rfile.readline() not in {b"\r\n", b"\n", b""}:
                    pass
                break
            chunks.append(self.rfile.read(size))
            self.rfile.read(2)
        return b"".join(chunks)

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 固定接口
        type(self).methods.append("PUT")
        type(self).objects[self._object_path()] = self._request_body()
        self.send_response(200)
        self.send_header("ETag", '"transfer-test-etag"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 固定接口
        type(self).methods.append("HEAD")
        data = type(self).objects.get(self._object_path())
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", '"transfer-test-etag"')
        self.send_header("Last-Modified", "Thu, 21 Aug 2026 00:00:00 GMT")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 固定接口
        type(self).methods.append("GET")
        data = type(self).objects.get(self._object_path())
        if data is None:
            self.send_error(404)
            return
        status = 200
        start = 0
        end = len(data) - 1
        byte_range = str(self.headers.get("Range") or "")
        if byte_range.startswith("bytes="):
            start_text, _, end_text = byte_range[6:].partition("-")
            start = int(start_text or 0)
            end = min(int(end_text) if end_text else end, end)
            status = 206
        payload = data[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", '"transfer-test-etag"')
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class S3TransferIntegrationTests(unittest.TestCase):
    def test_transfer_defaults_match_four_disk_production_budget(self) -> None:
        fields = Settings.model_fields

        self.assertEqual(fields["s3_transfer_max_concurrency"].default, 2)
        self.assertEqual(fields["s3_transfer_multipart_threshold_bytes"].default, 64 * _MIB)
        self.assertEqual(fields["s3_transfer_multipart_chunksize_bytes"].default, 64 * _MIB)

        configured = Settings(
            _env_file=None,
            S3_TRANSFER_MAX_CONCURRENCY=1,
            S3_TRANSFER_MULTIPART_THRESHOLD_BYTES=8 * _MIB,
            S3_TRANSFER_MULTIPART_CHUNKSIZE_BYTES=16 * _MIB,
        )
        self.assertEqual(configured.s3_transfer_max_concurrency, 1)
        self.assertEqual(configured.s3_transfer_multipart_threshold_bytes, 8 * _MIB)
        self.assertEqual(configured.s3_transfer_multipart_chunksize_bytes, 16 * _MIB)

    def test_boto_transfer_round_trip_uses_isolated_test_endpoint(self) -> None:
        handler = type(
            "IsolatedLocalS3Handler",
            (_LocalS3Handler,),
            {"objects": {}, "methods": []},
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with TemporaryDirectory() as tempdir:
                temp_path = Path(tempdir)
                source_path = temp_path / "source.bin"
                target_path = temp_path / "download" / "target.bin"
                source_bytes = (b"raelyn-s3-transfer\n" * 32768)[:512 * 1024]
                source_path.write_bytes(source_bytes)
                backend_root = Path(__file__).resolve().parents[2]
                environment = os.environ.copy()
                for name in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                ):
                    environment.pop(name, None)
                environment.update(
                    {
                        "AWS_EC2_METADATA_DISABLED": "true",
                        "NO_PROXY": "127.0.0.1,localhost",
                        "no_proxy": "127.0.0.1,localhost",
                        "S3_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                        "S3_ACCESS_KEY": "transfer-test-access",
                        "S3_SECRET_KEY": "transfer-test-secret",
                        "S3_REGION": "us-east-1",
                        "S3_USE_SSL": "false",
                        "S3_TRANSFER_MAX_CONCURRENCY": "1",
                        "S3_TRANSFER_MULTIPART_THRESHOLD_BYTES": str(5 * _MIB),
                        "S3_TRANSFER_MULTIPART_CHUNKSIZE_BYTES": str(5 * _MIB),
                    }
                )
                script = """
import json
from pathlib import Path
import sys

from raelyn.services.s3 import _transfer_config, s3_download_file, s3_upload_file

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
result = s3_upload_file(
    local_path=source_path,
    bucket="raelyn-transfer-config-test",
    key="round-trip/source.bin",
    content_type="application/octet-stream",
)
s3_download_file(
    bucket=result.bucket,
    key=result.key,
    local_path=target_path,
)
config = _transfer_config()
print(json.dumps({
    "size_bytes": result.size_bytes,
    "max_concurrency": config.max_concurrency,
    "multipart_threshold": config.multipart_threshold,
    "multipart_chunksize": config.multipart_chunksize,
}))
"""
                completed = subprocess.run(
                    [sys.executable, "-c", script, str(source_path), str(target_path)],
                    cwd=backend_root,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                payload = json.loads(completed.stdout)

                self.assertEqual(target_path.read_bytes(), source_bytes)
                self.assertEqual(payload["size_bytes"], len(source_bytes))
                self.assertEqual(payload["max_concurrency"], 1)
                self.assertEqual(payload["multipart_threshold"], 5 * _MIB)
                self.assertEqual(payload["multipart_chunksize"], 5 * _MIB)
                self.assertEqual(handler.methods, ["PUT", "HEAD", "GET"])
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
