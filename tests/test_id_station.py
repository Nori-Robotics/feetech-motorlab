from __future__ import annotations

import time
import unittest

from app.id_station import (
    ID_STATION_CONFIRMATION,
    IdStation,
    IdStationConfig,
    IdStationError,
)


def wait_for(station: IdStation, predicate, timeout: float = 1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = station.snapshot()
        if predicate(snapshot):
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"Timed out waiting for ID station: {station.snapshot()}")


class IdStationSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.station = IdStation(
            simulation=True,
            poll_interval=0.01,
            stable_scans=2,
            unplug_scans=2,
        )

    def tearDown(self) -> None:
        self.station.close()

    def start(self, **overrides):
        values = {
            "mode": "single",
            "port": "SIMULATED_ID_STATION",
            "target_id": 5,
            "start_id": 1,
            "end_id": 7,
            "loop": False,
        }
        values.update(overrides)
        return self.station.start(
            IdStationConfig(**values), ID_STATION_CONFIRMATION
        )

    def test_single_mode_repeats_after_verified_unplug(self) -> None:
        self.start(target_id=5)
        self.station.simulate_motor(present=True, motor_id=1, model_number=777)
        first = wait_for(
            self.station,
            lambda state: state["completed"] == 1 and state["phase"] == "wait_unplug",
        )
        self.assertEqual(first["last_assignment"]["to_id"], 5)
        self.assertEqual(first["target_id"], 5)

        self.station.simulate_motor(present=False)
        wait_for(self.station, lambda state: state["phase"] == "waiting")
        self.station.simulate_motor(present=True, motor_id=3, model_number=2825)
        second = wait_for(self.station, lambda state: state["completed"] == 2)
        self.assertEqual(second["last_assignment"]["to_id"], 5)
        self.assertEqual(second["last_assignment"]["model"], "sts3250")

    def test_sequential_mode_stops_after_inclusive_end(self) -> None:
        self.start(mode="sequential", start_id=2, end_id=3)
        self.station.simulate_motor(present=True, motor_id=1, model_number=777)
        wait_for(
            self.station,
            lambda state: state["completed"] == 1 and state["phase"] == "wait_unplug",
        )
        self.station.simulate_motor(present=False)
        waiting = wait_for(self.station, lambda state: state["phase"] == "waiting")
        self.assertEqual(waiting["target_id"], 3)

        self.station.simulate_motor(present=True, motor_id=1, model_number=2569)
        complete = wait_for(self.station, lambda state: state["phase"] == "complete")
        self.assertFalse(complete["active"])
        self.assertEqual(complete["completed"], 2)
        self.assertEqual(complete["last_assignment"]["to_id"], 3)

    def test_loop_wraps_to_configured_start(self) -> None:
        self.start(mode="sequential", start_id=4, end_id=5, loop=True)
        self.station.simulate_motor(present=True, motor_id=1, model_number=777)
        wait_for(self.station, lambda state: state["completed"] == 1)
        self.station.simulate_motor(present=False)
        waiting_five = wait_for(self.station, lambda state: state["phase"] == "waiting")
        self.assertEqual(waiting_five["target_id"], 5)

        self.station.simulate_motor(present=True, motor_id=1, model_number=777)
        wait_for(self.station, lambda state: state["completed"] == 2)
        self.station.simulate_motor(present=False)
        wrapped = wait_for(self.station, lambda state: state["phase"] == "waiting")
        self.assertEqual(wrapped["target_id"], 4)
        self.assertTrue(wrapped["active"])

    def test_confirmation_and_range_guards(self) -> None:
        config = IdStationConfig(
            mode="sequential",
            port="SIMULATED_ID_STATION",
            start_id=8,
            end_id=2,
        )
        with self.assertRaisesRegex(IdStationError, "requires START_ID_STATION"):
            self.station.start(config, "no")
        with self.assertRaisesRegex(IdStationError, "less than or equal"):
            self.station.start(config, ID_STATION_CONFIRMATION)


if __name__ == "__main__":
    unittest.main()
