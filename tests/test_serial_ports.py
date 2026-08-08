from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import patch

from app.serial_ports import discover_serial_ports, serial_port_available


@dataclass
class FakePort:
    device: str
    description: str
    manufacturer: str | None = None
    vid: int | None = None
    pid: int | None = None


class SerialPortDiscoveryTests(unittest.TestCase):
    def test_accepts_platform_native_port_without_filesystem_node(self) -> None:
        port = FakePort("COM7", "USB Serial Port", "Adapter Vendor", 0x1234, 0x5678)
        with patch("app.serial_ports._listed_ports", return_value=[port]):
            self.assertTrue(serial_port_available("COM7"))
            self.assertFalse(serial_port_available("COM8"))

    @patch("app.serial_ports.glob.glob", return_value=[])
    def test_discovery_is_adapter_agnostic(self, _glob) -> None:
        ports = [
            FakePort("COM7", "Generic USB Bridge", "Adapter Vendor", 0x1234, 0x5678),
            FakePort("/dev/cu.usbserial-test", "Serial Adapter"),
        ]
        with patch("app.serial_ports._listed_ports", return_value=ports):
            found = discover_serial_ports()

        self.assertEqual([item["device"] for item in found], ["/dev/cu.usbserial-test", "COM7"])
        self.assertEqual(found[1]["manufacturer"], "Adapter Vendor")
        self.assertEqual(found[1]["vid"], "1234")
        self.assertNotIn("waveshare_hint", found[1])


if __name__ == "__main__":
    unittest.main()
