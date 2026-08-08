from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

from app.controller import (
    CURRENT_SCALE_A_PER_LSB,
    DEFAULT_ACCELERATION,
    DEFAULT_VELOCITY_LIMIT,
    ENCODER_CENTER_CONFIRMATION,
    KGFCM_TO_NM,
    BenchController,
    ControlError,
    _decode_load_effort,
)


class BenchControllerSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = BenchController(
            simulation=True, data_dir=Path(self.temporary.name)
        )

    def tearDown(self) -> None:
        self.controller.close()
        self.temporary.cleanup()

    def test_simulation_exposes_all_supported_models_with_torque_off(self) -> None:
        state = self.controller.snapshot()
        self.assertTrue(state["connected"])
        self.assertEqual([m["model"] for m in state["motors"]], ["STS3215", "STS3250", "STS3095"])
        self.assertTrue(all(not motor["torque_enabled"] for motor in state["motors"]))
        self.assertTrue(all(motor["velocity_limit"] == DEFAULT_VELOCITY_LIMIT for motor in state["motors"]))
        self.assertTrue(all(motor["acceleration"] == DEFAULT_ACCELERATION for motor in state["motors"]))
        self.assertEqual(
            [motor["rated_torque_limit_raw"] for motor in state["motors"]],
            [333, 320, None],
        )

    def test_zeroed_device_motion_registers_keep_nonzero_ui_defaults(self) -> None:
        class ZeroedMotionRegisters:
            @staticmethod
            def read(register, _name, **_kwargs):
                values = {
                    "Operating_Mode": 0,
                    "Acceleration": 0,
                    "Goal_Velocity": 0,
                    "Torque_Limit": 0,
                    "Torque_Enable": 0,
                }
                return values.get(register, 0)

        controller = BenchController(
            simulation=False, data_dir=Path(self.temporary.name) / "hardware-defaults"
        )
        try:
            motor = controller._new_motor_state(1, 777)
            controller._bus = ZeroedMotionRegisters()
            controller._read_config_locked(motor)
            self.assertEqual(motor.velocity_limit, DEFAULT_VELOCITY_LIMIT)
            self.assertEqual(motor.acceleration, DEFAULT_ACCELERATION)
            self.assertEqual(motor.torque_limit, 0)
        finally:
            controller.close()

    def test_motion_requires_torque_and_then_reaches_target(self) -> None:
        with self.assertRaisesRegex(ControlError, "Arm torque"):
            self.controller.command_position(1, 2500)
        self.controller.set_torque(1, True)
        self.controller.command_position(1, 2500, 900, 60, 450)
        time.sleep(0.65)
        motor = next(m for m in self.controller.snapshot()["motors"] if m["id"] == 1)
        self.assertGreater(motor["position"], 2048)
        self.assertEqual(motor["target_position"], 2500)
        self.assertEqual(motor["velocity_limit"], 900)

    def test_multi_motor_selection_and_atomic_jog_are_isolated(self) -> None:
        initial = self.controller.snapshot()
        self.assertEqual([motor["id"] for motor in initial["motors"]], [1, 2, 3])
        with self.assertRaisesRegex(ControlError, "Arm torque"):
            self.controller.jog_position(
                2, 57, velocity=600, acceleration=40, torque_limit=500
            )

        selected = self.controller.select_motor(2)
        self.assertEqual(selected["selected_id"], 2)
        self.controller.set_torque(2, True)
        first = self.controller.jog_position(
            2, 57, velocity=700, acceleration=45, torque_limit=550
        )
        second = self.controller.jog_position(
            2, 57, velocity=700, acceleration=45, torque_limit=550
        )
        self.assertEqual(first["target_position"], 2105)
        self.assertEqual(second["target_position"], 2162)

        snapshot = self.controller.snapshot()
        targets = {motor["id"]: motor["target_position"] for motor in snapshot["motors"]}
        self.assertEqual(targets, {1: 2048, 2: 2162, 3: 2048})

        with self.controller._lock:
            motor = self.controller._motors[2]
            motor.target_position = 4090
            motor.config["max_position"] = 4095
        clamped = self.controller.jog_position(
            2, 57, velocity=700, acceleration=45, torque_limit=550
        )
        self.assertEqual(clamped["target_position"], 4095)
        with self.assertRaisesRegex(ControlError, "configured maximum"):
            self.controller.jog_position(
                2, 57, velocity=700, acceleration=45, torque_limit=550
            )

    def test_group_angle_command_requires_and_updates_every_motor(self) -> None:
        with self.assertRaisesRegex(ControlError, "Arm every motor"):
            self.controller.command_all_positions(
                2400, velocity=800, acceleration=55, torque_limit=420
            )

        armed = self.controller.set_all_torque(True)
        self.assertTrue(all(motor["torque_enabled"] for motor in armed["motors"]))
        commanded = self.controller.command_all_positions(
            2400, velocity=800, acceleration=55, torque_limit=420
        )
        self.assertTrue(all(motor["target_position"] == 2400 for motor in commanded["motors"]))
        self.assertTrue(all(motor["velocity_limit"] == 800 for motor in commanded["motors"]))
        self.assertTrue(all(motor["acceleration"] == 55 for motor in commanded["motors"]))
        self.assertTrue(all(motor["torque_limit"] == 420 for motor in commanded["motors"]))

        released = self.controller.set_all_torque(False)
        self.assertTrue(all(not motor["torque_enabled"] for motor in released["motors"]))
        self.controller.set_operating_mode(3, 1)
        with self.assertRaisesRegex(ControlError, "requires mode 0"):
            self.controller.set_all_torque(True)
        self.assertTrue(
            all(not motor["torque_enabled"] for motor in self.controller.snapshot()["motors"])
        )

    def test_group_jog_preserves_offsets_and_preflights_every_limit(self) -> None:
        with self.assertRaisesRegex(ControlError, "Arm every motor"):
            self.controller.jog_all_positions(
                57, velocity=700, acceleration=45, torque_limit=550
            )

        self.controller.set_all_torque(True)
        for motor_id, target in ((1, 1800), (2, 2100), (3, 2400)):
            self.controller.command_position(
                motor_id,
                target,
                velocity=700,
                acceleration=45,
                torque_limit=550,
            )

        jogged = self.controller.jog_all_positions(
            57, velocity=800, acceleration=50, torque_limit=475
        )
        targets = {motor["id"]: motor["target_position"] for motor in jogged["motors"]}
        self.assertEqual(targets, {1: 1857, 2: 2157, 3: 2457})
        self.assertTrue(all(motor["velocity_limit"] == 800 for motor in jogged["motors"]))
        self.assertTrue(all(motor["acceleration"] == 50 for motor in jogged["motors"]))
        self.assertTrue(all(motor["torque_limit"] == 475 for motor in jogged["motors"]))

        with self.controller._lock:
            self.controller._motors[3].target_position = 4090
            self.controller._motors[3].config["max_position"] = 4095
        before = {
            motor["id"]: motor["target_position"]
            for motor in self.controller.snapshot()["motors"]
        }
        with self.assertRaisesRegex(ControlError, "configured travel"):
            self.controller.jog_all_positions(
                57, velocity=800, acceleration=50, torque_limit=475
            )
        after = {
            motor["id"]: motor["target_position"]
            for motor in self.controller.snapshot()["motors"]
        }
        self.assertEqual(after, before)

    def test_encoder_center_requires_confirmation_and_torque_off(self) -> None:
        with self.controller._lock:
            motor = self.controller._motors[1]
            motor.position = 1375
            motor.target_position = 1375

        with self.assertRaisesRegex(ControlError, "exact confirmation"):
            self.controller.center_encoder_position(1, "CENTER")

        centered = self.controller.center_encoder_position(
            1, ENCODER_CENTER_CONFIRMATION
        )
        self.assertEqual(centered["position"], 2048)
        self.assertEqual(centered["target_position"], 2048)
        self.assertFalse(centered["torque_enabled"])

        self.controller.set_torque(1, True)
        with self.assertRaisesRegex(ControlError, "Release torque"):
            self.controller.center_encoder_position(
                1, ENCODER_CENTER_CONFIRMATION
            )

    def test_hardware_center_uses_feetech_midpoint_command_and_readback(self) -> None:
        class CenterCommandBus:
            def __init__(self) -> None:
                self.position = 1360
                self.torque = 0
                self.mode = 0
                self.writes: list[tuple[str, int]] = []

            def read(self, register, _name, **_kwargs):
                return {
                    "Present_Position": self.position,
                    "Torque_Enable": self.torque,
                    "Operating_Mode": self.mode,
                }.get(register, 0)

            def write(self, register, _name, value, **_kwargs):
                self.writes.append((register, value))
                if register == "Torque_Enable":
                    self.torque = value
                    if value == 128:
                        self.position = 2048
                elif register == "Operating_Mode":
                    self.mode = value

        controller = BenchController(
            simulation=False,
            data_dir=Path(self.temporary.name) / "center-command",
        )
        bus = CenterCommandBus()
        try:
            with controller._lock:
                motor = controller._new_motor_state(1, 777)
                motor.position = bus.position
                motor.target_position = bus.position
                motor.config = controller._default_config()
                controller._bus = bus
                controller._motors = {1: motor}
                controller._selected_id = 1
                controller._connected = True

            centered = controller.center_encoder_position(
                1, ENCODER_CENTER_CONFIRMATION
            )
            self.assertEqual(centered["position"], 2048)
            self.assertEqual(
                bus.writes[:3],
                [
                    ("Torque_Enable", 128),
                    ("Torque_Enable", 0),
                    ("Lock", 1),
                ],
            )
            self.assertEqual(bus.torque, 0)
        finally:
            controller.close()

    def test_bus_encoder_center_preflights_before_changing_any_motor(self) -> None:
        with self.controller._lock:
            for motor, position in zip(
                self.controller._motors.values(), (900, 1700, 3200), strict=True
            ):
                motor.position = position
                motor.target_position = position
            self.controller._motors[2].torque_enabled = True

        before = [
            motor["position"] for motor in self.controller.snapshot()["motors"]
        ]
        with self.assertRaisesRegex(ControlError, "Release torque on ID 2"):
            self.controller.center_all_encoder_positions(
                ENCODER_CENTER_CONFIRMATION
            )
        self.assertEqual(
            [motor["position"] for motor in self.controller.snapshot()["motors"]],
            before,
        )

        with self.controller._lock:
            self.controller._motors[2].torque_enabled = False
        centered = self.controller.center_all_encoder_positions(
            ENCODER_CENTER_CONFIRMATION
        )
        self.assertTrue(all(motor["position"] == 2048 for motor in centered["motors"]))
        self.assertTrue(
            all(motor["target_position"] == 2048 for motor in centered["motors"])
        )
        self.assertTrue(
            all(not motor["torque_enabled"] for motor in centered["motors"])
        )

    def test_capture_persists_and_can_be_recalled(self) -> None:
        capture = self.controller.capture_position(1, "Bench zero")
        self.assertEqual(capture["position"], 2048)
        self.controller.set_torque(1, True)
        self.controller.command_position(1, 2600)
        time.sleep(0.2)
        result = self.controller.move_to_capture(1, capture["capture_id"])
        self.assertEqual(result["target_position"], 2048)
        stored = Path(self.temporary.name, "captured_positions.json")
        self.assertIn("Bench zero", stored.read_text(encoding="utf-8"))

    def test_modes_switch_with_torque_off_and_accept_contextual_commands(self) -> None:
        motor = self.controller.set_operating_mode(1, 1)
        self.assertEqual(motor["operating_mode"], 1)
        self.assertEqual(motor["operating_mode_name"], "Closed-loop speed")
        self.controller.set_torque(1, True)
        with self.assertRaisesRegex(ControlError, "Release torque"):
            self.controller.set_operating_mode(1, 2)
        commanded = self.controller.command_mode(
            1, 700, velocity=600, acceleration=45, torque_limit=500
        )
        self.assertEqual(commanded["mode_command"], 700)
        time.sleep(0.15)
        moving = next(m for m in self.controller.snapshot()["motors"] if m["id"] == 1)
        self.assertTrue(moving["moving"])
        self.assertGreater(moving["current_a"], 0)
        self.assertAlmostEqual(
            moving["current_a"], moving["current_raw"] * CURRENT_SCALE_A_PER_LSB, places=4
        )

        self.controller.set_torque(1, False)
        for mode, command in ((2, -450), (3, 256), (0, None)):
            changed = self.controller.set_operating_mode(1, mode)
            self.assertEqual(changed["operating_mode"], mode)
            if command is not None:
                self.controller.set_torque(1, True)
                issued = self.controller.command_mode(
                    1, command, velocity=500, acceleration=35, torque_limit=400
                )
                self.assertEqual(issued["mode_command"], command)
                self.controller.set_torque(1, False)

    def test_mode_command_ranges_are_enforced(self) -> None:
        limits = ((1, 3074, "Closed-loop"), (2, 1001, "Open-loop"), (3, 4096, "Relative"))
        for mode, command, message in limits:
            self.controller.set_operating_mode(1, mode)
            self.controller.set_torque(1, True)
            with self.assertRaisesRegex(ControlError, message):
                self.controller.command_mode(
                    1, command, velocity=600, acceleration=40, torque_limit=500
                )
            self.controller.set_torque(1, False)

    def test_load_effort_uses_absolute_magnitude_and_1000_full_scale(self) -> None:
        self.assertEqual(_decode_load_effort(500), (500, 50.0))
        self.assertEqual(_decode_load_effort(0x400 | 500), (500, 50.0))
        self.assertEqual(_decode_load_effort(1000), (1000, 100.0))
        self.assertEqual(_decode_load_effort(1023), (1023, 100.0))

    def test_current_based_torque_estimate_is_model_aware(self) -> None:
        with self.controller._lock:
            known_motor = self.controller._motors[1]
            unknown_motor = self.controller._motors[3]
            known_motor.current_raw = 100
            unknown_motor.current_raw = 100
            known = known_motor.as_dict()
            unknown = unknown_motor.as_dict()

        expected_current = 100 * CURRENT_SCALE_A_PER_LSB
        expected_kgcm = expected_current * 11.0
        self.assertAlmostEqual(known["current_a"], expected_current, places=4)
        self.assertAlmostEqual(known["estimated_torque_kgcm"], expected_kgcm, places=3)
        self.assertAlmostEqual(
            known["estimated_torque_nm"], expected_kgcm * KGFCM_TO_NM, places=4
        )
        self.assertIsNone(unknown["estimated_torque_kgcm"])
        self.assertIsNone(unknown["estimated_torque_nm"])

    def test_persistent_write_requires_torque_off_and_confirmation(self) -> None:
        with self.assertRaisesRegex(ControlError, "confirmation"):
            self.controller.write_persistent_config(1, {"p_gain": 40}, "no")
        config = self.controller.write_persistent_config(
            1, {"p_gain": 40, "min_position": 100, "max_position": 4000}, "WRITE_EEPROM"
        )
        self.assertEqual(config["p_gain"], 40)
        self.controller.set_torque(1, True)
        with self.assertRaisesRegex(ControlError, "Release torque"):
            self.controller.write_persistent_config(1, {"p_gain": 41}, "WRITE_EEPROM")

    def test_bus_persistent_write_preflights_and_updates_every_motor(self) -> None:
        before = {
            motor["id"]: motor["config"].copy()
            for motor in self.controller.snapshot()["motors"]
        }
        self.controller.set_torque(2, True)
        with self.assertRaisesRegex(ControlError, "armed ID\(s\): 2"):
            self.controller.write_all_persistent_config(
                {"p_gain": 44}, "WRITE_ALL_EEPROM"
            )
        self.assertEqual(
            {motor["id"]: motor["config"] for motor in self.controller.snapshot()["motors"]},
            before,
        )

        self.controller.set_torque(2, False)
        with self.controller._lock:
            self.controller._motors[3].config["max_position"] = 2000
        before_limits = {
            motor["id"]: motor["config"].copy()
            for motor in self.controller.snapshot()["motors"]
        }
        with self.assertRaisesRegex(ControlError, "ID 3"):
            self.controller.write_all_persistent_config(
                {"min_position": 2500}, "WRITE_ALL_EEPROM"
            )
        self.assertEqual(
            {motor["id"]: motor["config"] for motor in self.controller.snapshot()["motors"]},
            before_limits,
        )

        with self.controller._lock:
            self.controller._motors[3].config["max_position"] = 4095
        updated = self.controller.write_all_persistent_config(
            {"p_gain": 44, "max_temperature": 60}, "WRITE_ALL_EEPROM"
        )
        self.assertTrue(all(motor["config"]["p_gain"] == 44 for motor in updated["motors"]))
        self.assertTrue(
            all(motor["config"]["max_temperature"] == 60 for motor in updated["motors"])
        )

    def test_recording_writes_characterization_csv(self) -> None:
        recording = self.controller.start_recording("unit test")
        self.assertTrue(recording["active"])
        time.sleep(0.2)
        stopped = self.controller.stop_recording()
        path = Path(self.temporary.name, "recordings", stopped["name"])
        self.assertTrue(path.is_file())
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 0)
        self.assertEqual({row["model"] for row in rows}, {"STS3215", "STS3250", "STS3095"})
        self.assertIn("current_a", rows[0])
        self.assertIn("estimated_torque_kgcm", rows[0])
        self.assertIn("estimated_torque_nm", rows[0])
        self.assertIn("operating_mode", rows[0])

    def test_emergency_stop_releases_every_motor(self) -> None:
        for motor_id in (1, 2, 3):
            self.controller.set_torque(motor_id, True)
        state = self.controller.emergency_stop()
        self.assertTrue(all(not motor["torque_enabled"] for motor in state["motors"]))


if __name__ == "__main__":
    unittest.main()
