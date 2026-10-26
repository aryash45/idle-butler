"""Local HTTP boundary tests."""

import http.client
import json
import threading
import unittest
from typing import Dict, Optional, Tuple

from idle_butler.server import HOST, MAX_BODY_BYTES, IdleButlerServer


class ServerTests(unittest.TestCase):
    """Test token, Host, and request-size enforcement."""

    def setUp(self) -> None:
        self.token = "test-token"
        self.server = IdleButlerServer(0, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self, path: str, headers: Optional[Dict[str, str]] = None, body: Optional[str] = None
    ) -> Tuple[int, bytes]:
        connection = http.client.HTTPConnection(HOST, self.port, timeout=3)
        connection.request("POST" if body is not None else "GET", path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_token_and_host_are_required(self) -> None:
        status, _ = self.request(f"/api/status?token={self.token}", {"Host": f"{HOST}:{self.port}"})
        self.assertEqual(status, 200)
        status, _ = self.request(f"/api/status?token=wrong", {"Host": f"{HOST}:{self.port}"})
        self.assertEqual(status, 403)
        status, _ = self.request(f"/api/status?token={self.token}", {"Host": f"127.0.0.2:{self.port}"})
        self.assertEqual(status, 403)

    def test_post_body_is_capped(self) -> None:
        body = json.dumps({"value": "x" * MAX_BODY_BYTES})
        status, payload = self.request(
            f"/api/settings?token={self.token}",
            {"Host": f"{HOST}:{self.port}", "Content-Type": "application/json"},
            body,
        )
        self.assertEqual(status, 400)
        self.assertIn(b"too large", payload)

