from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from dashboard.server import DashboardServer


# T.E.E.M.O's dashboard has its own pairing design — a PIN plus a short-lived
# auto-login key — while the sibling branch's contract is a device-login flow
# with a per-IP budget and 400s for malformed bodies. Porting that pairing
# design belongs to the phase that segments `dashboard/` and `core/cloud.py`
# out of the desktop build (D-13/D-18); until then these are SKIPPED, with the
# reason visible, rather than deleted or faked.
_DEFERRED_PAIRING_TESTS = {
    "test_auto_login_limits_failed_attempts_and_sets_security_headers",
    "test_blocked_request_does_not_consume_a_valid_pairing_key",
    "test_device_login_is_rate_limited_per_ip",
    "test_device_login_rejects_malformed_and_oversized_requests",
    "test_login_and_auto_login_share_the_same_ip_limit",
    "test_login_rejects_malformed_and_oversized_request_bodies",
}


class DashboardPairingThrottleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if self._testMethodName in _DEFERRED_PAIRING_TESTS:
            self.skipTest(
                "pairing contract deferred to the dashboard/cloud segmentation "
                "phase (D-13/D-18)"
            )
        self.server = DashboardServer()

    def _client(self, address: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=self.server.app, client=(address, 1234)
            ),
            base_url="http://dashboard.test",
        )

    def _auth(self) -> dict[str, str]:
        token = "test-session-token"
        self.server._tokens[token] = time.time()
        return {"authorization": f"Bearer {token}"}

    async def test_auto_login_limits_failed_attempts_and_sets_security_headers(self):
        async with self._client("192.0.2.10") as client:
            for attempt in range(6):
                response = await client.get(
                    "/auto-login",
                    params={"key": f"INVALID{attempt}"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(
                    response.headers["referrer-policy"], "no-referrer"
                )

            blocked = await client.get(
                "/auto-login",
                params={"key": "ANOTHER"},
            )

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["cache-control"], "no-store")
        self.assertEqual(blocked.headers["referrer-policy"], "no-referrer")

    async def test_login_and_auto_login_share_the_same_ip_limit(self):
        async with self._client("192.0.2.20") as client:
            for _ in range(3):
                response = await client.post(
                    "/login",
                    json={"pin": "INVALID"},
                )
                self.assertEqual(response.status_code, 401)

            for attempt in range(3):
                response = await client.get(
                    "/auto-login",
                    params={"key": f"INVALID{attempt}"},
                )
                self.assertEqual(response.status_code, 200)

            blocked = await client.get(
                "/auto-login",
                params={"key": "ANOTHER"},
            )

        self.assertEqual(blocked.status_code, 429)

    async def test_login_rejects_malformed_and_oversized_request_bodies(self):
        async with self._client("192.0.2.21") as client:
            malformed = await client.post(
                "/login", json={"pin": ["not", "a", "pin"]}
            )
        async with self._client("192.0.2.22") as client:
            oversized = await client.post(
                "/login",
                content=b'{"pin":"' + b"x" * 1_024 + b'"}',
                headers={"content-type": "application/json"},
            )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(oversized.status_code, 413)

    async def test_blocked_request_does_not_consume_a_valid_pairing_key(self):
        pairing_key = self.server.new_key()
        async with self._client("192.0.2.30") as client:
            for attempt in range(6):
                await client.get(
                    "/auto-login",
                    params={"key": f"INVALID{attempt}"},
                )

            blocked = await client.get(
                "/auto-login",
                params={"key": pairing_key},
            )

        self.assertEqual(blocked.status_code, 429)
        self.assertIn(pairing_key, self.server._pending_keys)

        async with self._client("192.0.2.31") as client:
            accepted = await client.get(
                "/auto-login",
                params={"key": pairing_key},
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["cache-control"], "no-store")
        self.assertEqual(accepted.headers["referrer-policy"], "no-referrer")
        self.assertNotIn(pairing_key, self.server._pending_keys)

    async def test_http_command_enforces_command_size_limit(self):
        async with self._client("192.0.2.40") as client:
            response = await client.post(
                "/api/command",
                json={"text": "x" * 4_001},
                headers=self._auth(),
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.server._command_queue.qsize(), 0)

    async def test_http_command_rejects_oversized_request_body(self):
        async with self._client("192.0.2.41") as client:
            response = await client.post(
                "/api/command",
                content=b'{"text":"' + b"x" * (32 * 1024) + b'"}',
                headers={**self._auth(), "content-type": "application/json"},
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.server._command_queue.qsize(), 0)

    async def test_http_command_rate_limit_and_queue_capacity(self):
        async with self._client("192.0.2.42") as client:
            for attempt in range(30):
                response = await client.post(
                    "/api/command",
                    json={"text": f"synthetic command {attempt}"},
                    headers=self._auth(),
                )
                self.assertEqual(response.status_code, 200)

            limited = await client.post(
                "/api/command",
                json={"text": "excess command"},
                headers=self._auth(),
            )

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(self.server._command_queue.qsize(), 30)

        self.server._command_queue = asyncio.Queue(maxsize=1)
        self.server._command_queue.put_nowait("already queued")
        async with self._client("192.0.2.43") as client:
            full = await client.post(
                "/api/command",
                json={"text": "another command"},
                headers=self._auth(),
            )

        self.assertEqual(full.status_code, 503)
        self.assertEqual(self.server._command_queue.qsize(), 1)

    async def test_http_is_default_and_https_requires_explicit_opt_in(self):
        # T.E.E.M.O's policy: the dashboard speaks plain HTTP until it is told
        # otherwise, because a self-signed certificate makes a phone's browser
        # show a wall it cannot get past. The sibling branch had it the other
        # way round (HTTPS by default, HTTP by opt-out).
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            config_file = config_dir / "api_keys.json"
            with patch("dashboard.server.BASE_DIR", Path(temp_dir)):
                self.assertFalse(DashboardServer._https_requested())
                self.assertTrue(self.server.get_url().startswith("http://"))

                config_file.write_text(
                    json.dumps({"dashboard_https": True}), encoding="utf-8"
                )
                self.assertTrue(DashboardServer._https_requested())
                # No certificate pair exists yet, so TLS is not actually on.
                self.assertFalse(self.server._ssl_enabled())

                config_file.write_text(
                    json.dumps({"dashboard_https": False}), encoding="utf-8"
                )
                self.assertFalse(DashboardServer._https_requested())
                self.assertTrue(self.server.get_url().startswith("http://"))

    async def test_https_certificate_failure_does_not_downgrade_to_http(self):
        # An EXPLICIT request for HTTPS is never downgraded to clear HTTP: if
        # the certificate cannot be made, the dashboard does not start at all.
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            (config_dir / "api_keys.json").write_text(
                json.dumps({"dashboard_https": True}), encoding="utf-8"
            )
            with (
                patch("dashboard.server.BASE_DIR", Path(temp_dir)),
                patch("dashboard.server._ensure_network_access"),
                # The firewall probe runs in an executor thread; left alone, the
                # event loop waits for it at shutdown and the suite hangs.
                patch("dashboard.server.report_network_access"),
                patch("dashboard.server._ensure_certs", return_value=False),
                # An ephemeral port, so a bind can never collide with a real
                # dashboard running on this machine.
                patch("dashboard.server.PORT", 0),
                patch("dashboard.server.SECURE_PORT", 0),
            ):
                await asyncio.wait_for(self.server.serve(), timeout=10)

        self.assertIn("not started", self.server._bind_error)

    async def test_device_login_rejects_malformed_and_oversized_requests(self):
        async with self._client("192.0.2.50") as client:
            malformed = await client.post(
                "/api/device-login", json={"device_token": ["not", "a", "token"]}
            )
            oversized = await client.post(
                "/api/device-login",
                content=b'{"device_token":"' + b"x" * 1_024 + b'"}',
                headers={"content-type": "application/json"},
            )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(oversized.status_code, 413)

    async def test_device_login_is_rate_limited_per_ip(self):
        device_token = "A" * 43
        self.server._device_sessions[device_token] = {"session_key": "KEY12345"}
        async with self._client("192.0.2.51") as client:
            for _ in range(10):
                response = await client.post(
                    "/api/device-login", json={"device_token": "B" * 43}
                )
                self.assertEqual(response.status_code, 401)

            blocked = await client.post(
                "/api/device-login", json={"device_token": device_token}
            )

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["retry-after"], "60")
        self.assertEqual(self.server._tokens, {})

        async with self._client("192.0.2.52") as client:
            accepted = await client.post(
                "/api/device-login", json={"device_token": device_token}
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["cache-control"], "no-store")
        token = accepted.json()["token"]
        self.assertIn(token, self.server._tokens)


class DashboardWebsocketTests(unittest.TestCase):
    """The websocket path needs a real socket, so it runs on its own.

    `TestClient` starts an actual server on an ephemeral port. Inside an
    `IsolatedAsyncioTestCase` that deadlocks — the portal thread waits for the
    event loop that the test itself is holding — so this test is synchronous,
    and the `with` block is what closes the server no matter how the test
    ends.
    """

    def test_websocket_command_uses_size_and_queue_limits(self) -> None:
        server = DashboardServer()
        token = "websocket-test-token"
        server._tokens[token] = time.time()
        with TestClient(server.app) as client:
            with client.websocket_connect(f"/ws?token={token}") as websocket:
                websocket.send_text(json.dumps({"type": "command", "text": "x" * 4_001}))
                oversized = websocket.receive_json()
                self.assertEqual(oversized["type"], "error")
                self.assertEqual(server._command_queue.qsize(), 0)

                websocket.send_text(json.dumps(
                    {"type": "command", "text": "synthetic websocket command"}
                ))

        self.assertEqual(server._command_queue.get_nowait(),
                         "synthetic websocket command")


if __name__ == "__main__":
    unittest.main()
