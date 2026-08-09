from __future__ import annotations

import unittest

from app.feetech_bus import FeetechMotorsBus, Motor, MotorNormMode


class LocalFeetechBusTests(unittest.TestCase):
    def test_signed_goal_velocity_is_encoded_and_decoded_locally(self) -> None:
        bus = object.__new__(FeetechMotorsBus)
        bus.motors = {"joint": Motor(7, "sts3215", MotorNormMode.DEGREES)}
        writes: list[tuple[int, int, int, int, int]] = []
        bus._write_raw = lambda address, length, motor_id, value, num_retry=0: writes.append(
            (address, length, motor_id, value, num_retry)
        )

        bus.write("Goal_Velocity", "joint", -3073, normalize=False, num_retry=2)
        self.assertEqual(writes, [(46, 2, 7, 0x8C01, 2)])

        bus._read_raw = lambda address, length, motor_id, num_retry=0: 0x8007
        self.assertEqual(
            bus.read("Present_Velocity", "joint", normalize=False, num_retry=1),
            -7,
        )

    def test_setup_motor_uses_guarded_register_order(self) -> None:
        bus = object.__new__(FeetechMotorsBus)
        bus.motors = {"motor": Motor(9, "sts3215", MotorNormMode.RANGE_M100_100)}
        bus.port_handler = type("Port", (), {"is_open": True})()
        baudrates: list[int] = []
        writes: list[tuple[int, int, int, int, int]] = []
        bus.set_baudrate = baudrates.append
        bus._write_raw = lambda address, length, motor_id, value, num_retry=0: writes.append(
            (address, length, motor_id, value, num_retry)
        )

        bus.setup_motor("motor", initial_baudrate=500_000, initial_id=3)

        self.assertEqual(baudrates, [500_000, 1_000_000])
        self.assertEqual(
            writes,
            [
                (40, 1, 3, 0, 2),
                (55, 1, 3, 0, 2),
                (5, 1, 3, 9, 2),
                (6, 1, 9, 0, 2),
            ],
        )


if __name__ == "__main__":
    unittest.main()
