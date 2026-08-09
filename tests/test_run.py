from __future__ import annotations

import unittest

from run import host_is_loopback, host_is_unspecified, require_remote_opt_in


class LauncherSafetyTests(unittest.TestCase):
    def test_loopback_hosts_do_not_require_remote_opt_in(self) -> None:
        for host in ("127.0.0.1", "127.4.3.2", "::1", "localhost"):
            with self.subTest(host=host):
                self.assertTrue(host_is_loopback(host))
                require_remote_opt_in(host, False)

    def test_non_loopback_hosts_require_explicit_opt_in(self) -> None:
        for host in ("0.0.0.0", "::", "192.168.1.20", "motorlab.local"):
            with self.subTest(host=host):
                self.assertFalse(host_is_loopback(host))
                with self.assertRaisesRegex(SystemExit, "Refusing to expose"):
                    require_remote_opt_in(host, False)
                require_remote_opt_in(host, True)

    def test_unspecified_host_detection(self) -> None:
        self.assertTrue(host_is_unspecified("0.0.0.0"))
        self.assertTrue(host_is_unspecified("::"))
        self.assertFalse(host_is_unspecified("127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
