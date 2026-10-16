from __future__ import annotations

import sys
import types
import unittest

try:
    import requests as _requests  # noqa: F401
except ModuleNotFoundError:
    fake_requests = types.ModuleType("requests")

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

    fake_requests.Session = FakeSession  # type: ignore[attr-defined]
    fake_requests.RequestException = OSError  # type: ignore[attr-defined]
    sys.modules["requests"] = fake_requests

from cakra_iot.api import ApiClient, ApiError


class FakeResponse:
    ok = False
    status_code = 401

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "success": False,
            "message": "Device token panel tidak valid atau sudah dicabut.",
        }


class ApiClientTest(unittest.TestCase):
    def test_backend_error_keeps_status_and_message(self) -> None:
        client = ApiClient("http://192.168.196.196:4000", "invalid")
        client.session.request = lambda *args, **kwargs: FakeResponse()  # type: ignore[method-assign]
        with self.assertRaises(ApiError) as raised:
            client.bootstrap()
        self.assertEqual(raised.exception.status_code, 401)
        self.assertIn("tidak valid", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
