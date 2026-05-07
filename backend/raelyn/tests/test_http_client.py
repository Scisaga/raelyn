from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.http_client import httpx_client


class HttpClientTests(unittest.TestCase):
    def test_httpx_client_ignores_environment_proxy_without_explicit_proxy(self) -> None:
        with patch("raelyn.services.http_client.httpx.Client") as client:
            httpx_client()

        self.assertEqual(client.call_args.kwargs["trust_env"], False)
        self.assertNotIn("proxy", client.call_args.kwargs)
        self.assertNotIn("proxies", client.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
