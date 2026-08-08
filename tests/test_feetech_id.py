from __future__ import annotations

import unittest

from app.feetech_id import (
    DEFAULT_BAUDRATE,
    FeetechApi,
    MOTOR_PROFILES,
    Response,
    TargetPlan,
    program_and_verify,
    require_one_supported_response,
    validate_motor_id,
)


class TargetPlanTests(unittest.TestCase):
    def test_single_id_repeats_forever(self) -> None:
        plan = TargetPlan.single(5)
        self.assertEqual(plan.target, 5)
        self.assertTrue(plan.advance())
        self.assertEqual(plan.target, 5)

    def test_sequence_is_inclusive_and_stops_at_end(self) -> None:
        plan = TargetPlan.sequential(3, 5, loop=False)
        targets = [plan.target]
        while plan.advance():
            targets.append(plan.target)
        self.assertEqual(targets, [3, 4, 5])

    def test_loop_wraps_from_end_to_start(self) -> None:
        plan = TargetPlan.sequential(6, 7, loop=True)
        targets = [plan.target]
        for _ in range(4):
            self.assertTrue(plan.advance())
            targets.append(plan.target)
        self.assertEqual(targets, [6, 7, 6, 7, 6])

    def test_validates_protocol_id_range(self) -> None:
        self.assertEqual(validate_motor_id(1, "target ID"), 1)
        self.assertEqual(validate_motor_id(253, "target ID"), 253)
        with self.assertRaisesRegex(ValueError, "1–253"):
            validate_motor_id(254, "target ID")


class DiscoveryGuardTests(unittest.TestCase):
    def test_requires_exactly_one_supported_motor(self) -> None:
        one = Response("test-port", 1_000_000, 1, MOTOR_PROFILES["sts3215"].model_number)
        response, profile = require_one_supported_response([one], "auto")
        self.assertEqual(response, one)
        self.assertEqual(profile.name, "sts3215")

        with self.assertRaisesRegex(RuntimeError, "multiple responders"):
            require_one_supported_response(
                [one, Response("test-port", 1_000_000, 2, 777)], "auto"
            )
        with self.assertRaisesRegex(RuntimeError, "unsupported model"):
            require_one_supported_response(
                [Response("test-port", 1_000_000, 1, 9999)], "auto"
            )
        with self.assertRaisesRegex(RuntimeError, "expected STS3250"):
            require_one_supported_response([one], "sts3250")


class GuardedWriteFlowTests(unittest.TestCase):
    class FakePortHandler:
        is_open = True

        def closePort(self) -> None:
            self.is_open = False

    class FakeMotor:
        def __init__(self, *, id, model, norm_mode) -> None:
            self.id = id
            self.model = model
            self.norm_mode = norm_mode

    class FakeNormMode:
        RANGE_M100_100 = "range"

    class FakeBus:
        devices = {1: 777}
        setup_calls = []

        def __init__(self, port, motors) -> None:
            self.port = port
            self.motors = motors
            self.port_handler = GuardedWriteFlowTests.FakePortHandler()

        def _connect(self, handshake=False) -> None:
            self.port_handler.is_open = True

        def set_baudrate(self, baudrate) -> None:
            self.baudrate = baudrate

        def broadcast_ping(self, **_kwargs):
            return self.devices.copy()

        def ping(self, motor_id, **_kwargs):
            return self.devices[motor_id]

        def setup_motor(self, motor_name, *, initial_baudrate, initial_id) -> None:
            target_id = self.motors[motor_name].id
            self.setup_calls.append((initial_baudrate, initial_id, target_id))
            self.devices[target_id] = self.devices.pop(initial_id)

    def setUp(self) -> None:
        self.FakeBus.devices = {1: 777}
        self.FakeBus.setup_calls = []
        self.api = FeetechApi(self.FakeBus, self.FakeMotor, self.FakeNormMode)
        self.profile = MOTOR_PROFILES["sts3215"]

    def test_write_ping_and_final_scan_verify_new_id(self) -> None:
        verified = program_and_verify(
            self.api,
            Response("test-port", DEFAULT_BAUDRATE, 1, 777),
            self.profile,
            7,
        )
        self.assertEqual(verified.motor_id, 7)
        self.assertEqual(self.FakeBus.devices, {7: 777})
        self.assertEqual(self.FakeBus.setup_calls, [(DEFAULT_BAUDRATE, 1, 7)])

    def test_existing_target_is_verified_without_rewrite(self) -> None:
        verified = program_and_verify(
            self.api,
            Response("test-port", DEFAULT_BAUDRATE, 1, 777),
            self.profile,
            1,
        )
        self.assertEqual(verified.motor_id, 1)
        self.assertEqual(self.FakeBus.setup_calls, [])


if __name__ == "__main__":
    unittest.main()
