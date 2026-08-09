from __future__ import annotations

import unittest

from app.main import websocket_origin_matches_host


class WebSocketOriginTests(unittest.TestCase):
    def test_matching_browser_origin_is_allowed(self) -> None:
        self.assertTrue(websocket_origin_matches_host("http://127.0.0.1:8765", "127.0.0.1:8765"))
        self.assertTrue(websocket_origin_matches_host("https://motorlab.local", "motorlab.local"))

    def test_cross_origin_browser_is_rejected(self) -> None:
        self.assertFalse(websocket_origin_matches_host("https://example.net", "127.0.0.1:8765"))
        self.assertFalse(websocket_origin_matches_host("null", "127.0.0.1:8765"))

    def test_non_browser_client_without_origin_is_allowed(self) -> None:
        self.assertTrue(websocket_origin_matches_host(None, "127.0.0.1:8765"))


if __name__ == "__main__":
    unittest.main()
