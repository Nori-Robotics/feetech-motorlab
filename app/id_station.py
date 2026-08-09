from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .feetech_bus import FeetechMotorsBus, Motor, MotorNormMode
from .feetech_id import (
    DEFAULT_BAUDRATE,
    FeetechApi,
    Response,
    TargetPlan,
    program_and_verify,
    require_one_supported_response,
    scan_once,
    validate_motor_id,
)
from .serial_ports import serial_port_available

ID_STATION_CONFIRMATION = "START_ID_STATION"
SUPPORTED_MODELS = {"auto", "sts3215", "sts3095", "sts3250"}


class IdStationError(RuntimeError):
    """An expected, user-facing ID-station error."""


@dataclass(frozen=True)
class IdStationConfig:
    mode: str
    port: str
    baudrate: int = DEFAULT_BAUDRATE
    model: str = "auto"
    target_id: int = 1
    start_id: int = 1
    end_id: int = 7
    loop: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class IdStation:
    """Background plug/program/unplug state machine for one serial adapter."""

    def __init__(
        self,
        *,
        simulation: bool = False,
        poll_interval: float = 0.35,
        stable_scans: int = 2,
        unplug_scans: int = 2,
    ) -> None:
        self.simulation = simulation
        self.poll_interval = poll_interval
        self.stable_scans = stable_scans
        self.unplug_scans = unplug_scans
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._api: FeetechApi | None = None
        self._config: IdStationConfig | None = None
        self._plan: TargetPlan | None = None
        self._active = False
        self._phase = "idle"
        self._message = "Configure a session, then start the ID station."
        self._completed = 0
        self._detected: dict[str, Any] | None = None
        self._last_assignment: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._event_number = 0
        self._sim_motor: Response | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            config = deepcopy(self._config.__dict__) if self._config else None
            return {
                "active": self._active,
                "phase": self._phase,
                "message": self._message,
                "completed": self._completed,
                "target_id": self._plan.target if self._plan else None,
                "detected": deepcopy(self._detected),
                "last_assignment": deepcopy(self._last_assignment),
                "config": config,
                "events": deepcopy(self._events),
                "simulation": self.simulation,
                "sim_motor_present": self._sim_motor is not None,
            }

    def start(self, config: IdStationConfig, confirmation: str) -> dict[str, Any]:
        if confirmation != ID_STATION_CONFIRMATION:
            raise IdStationError(f"Starting the ID station requires {ID_STATION_CONFIRMATION}")
        config = self._validate_config(config)
        api = self._load_api() if not self.simulation else None
        with self._lock:
            if self._active or (self._thread and self._thread.is_alive()):
                raise IdStationError("The ID station is already running")
            self._api = api
            self._config = config
            self._plan = (
                TargetPlan.single(config.target_id)
                if config.mode == "single"
                else TargetPlan.sequential(config.start_id, config.end_id, config.loop)
            )
            self._stop = threading.Event()
            self._active = True
            self._phase = "waiting"
            self._message = f"Waiting for one motor to assign ID {self._plan.target}."
            self._completed = 0
            self._detected = None
            self._last_assignment = None
            self._events = []
            self._event_number = 0
            self._sim_motor = None
            self._add_event_locked(
                "info",
                f"Session started on {config.port}; next target ID {self._plan.target}.",
            )
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="feetech-id-station",
            )
            self._thread.start()
            return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            if not thread or not thread.is_alive():
                self._active = False
                if self._phase not in {"complete", "error"}:
                    self._phase = "stopped"
                    self._message = "ID station stopped."
                return self.snapshot()
            self._stop.set()
            self._phase = "stopping"
            self._message = "Stopping after the current bus transaction."
        thread.join(timeout=3.0)
        return self.snapshot()

    def close(self) -> None:
        self.stop()

    def simulate_motor(
        self, *, present: bool, motor_id: int = 1, model_number: int = 777
    ) -> dict[str, Any]:
        if not self.simulation:
            raise IdStationError("Simulated plug controls are unavailable in hardware mode")
        with self._lock:
            if not self._active:
                raise IdStationError("Start the ID station before simulating a motor")
            if present:
                validate_motor_id(int(motor_id), "motor ID")
                self._sim_motor = Response(
                    self._config.port,
                    self._config.baudrate,
                    int(motor_id),
                    int(model_number),
                )
                self._add_event_locked("info", f"Simulated motor plugged in at ID {motor_id}.")
            else:
                self._sim_motor = None
                self._add_event_locked("info", "Simulated motor unplugged.")
            return self.snapshot()

    def _validate_config(self, config: IdStationConfig) -> IdStationConfig:
        if config.mode not in {"single", "sequential"}:
            raise IdStationError("ID-station mode must be single or sequential")
        if config.model not in SUPPORTED_MODELS:
            raise IdStationError("Unsupported model filter")
        if config.baudrate <= 0:
            raise IdStationError("Baud rate must be positive")
        if not config.port:
            raise IdStationError("Select a serial adapter")
        if not self.simulation and not serial_port_available(config.port):
            raise IdStationError(f"Serial port does not exist: {config.port}")
        try:
            target = validate_motor_id(int(config.target_id), "target ID")
            start = validate_motor_id(int(config.start_id), "start ID")
            end = validate_motor_id(int(config.end_id), "end ID")
        except ValueError as exc:
            raise IdStationError(str(exc)) from exc
        if config.mode == "sequential" and start > end:
            raise IdStationError("Start ID must be less than or equal to end ID")
        return IdStationConfig(
            mode=config.mode,
            port=config.port,
            baudrate=int(config.baudrate),
            model=config.model,
            target_id=target,
            start_id=start,
            end_id=end,
            loop=bool(config.loop),
        )

    @staticmethod
    def _load_api() -> FeetechApi:
        return FeetechApi(FeetechMotorsBus, Motor, MotorNormMode)

    def _scan(self, baudrate: int) -> list[Response]:
        with self._lock:
            if self.simulation:
                return [self._sim_motor] if self._sim_motor is not None else []
            api = self._api
            port = self._config.port
        return scan_once(api, port, baudrate)

    def _program(self, response: Response, profile: Any, target_id: int) -> Response:
        if self.simulation:
            verified = Response(
                response.port,
                DEFAULT_BAUDRATE if response.motor_id != target_id else response.baudrate,
                target_id,
                response.model_number,
            )
            with self._lock:
                self._sim_motor = verified
            return verified
        return program_and_verify(
            self._api,
            response,
            profile,
            target_id,
            emit=lambda message: self._event("info", message),
        )

    def _run(self) -> None:
        stable_key: tuple[int, int] | None = None
        stable_count = 0
        try:
            while not self._stop.is_set():
                config = self._config
                plan = self._plan
                try:
                    responses = self._scan(config.baudrate)
                    response, profile = require_one_supported_response(responses, config.model)
                except RuntimeError as exc:
                    stable_key = None
                    stable_count = 0
                    self._set_waiting_message(f"Waiting for one supported motor: {exc}")
                    self._stop.wait(self.poll_interval)
                    continue
                except Exception as exc:
                    raise IdStationError(f"Adapter scan failed: {self._compact(exc)}") from exc

                key = (response.motor_id, response.model_number)
                stable_count = stable_count + 1 if key == stable_key else 1
                stable_key = key
                with self._lock:
                    self._phase = "confirming"
                    self._detected = {
                        "id": response.motor_id,
                        "model_number": response.model_number,
                        "model": profile.name,
                        "baudrate": response.baudrate,
                        "stable_scans": stable_count,
                        "required_scans": self.stable_scans,
                    }
                    self._message = (
                        f"Confirming {profile.name.upper()} ID {response.motor_id}: "
                        f"scan {stable_count}/{self.stable_scans}."
                    )
                if stable_count < self.stable_scans:
                    self._stop.wait(self.poll_interval)
                    continue

                target_id = plan.target
                with self._lock:
                    self._phase = "programming"
                    self._message = (
                        f"Programming {profile.name.upper()} ID {response.motor_id} → {target_id}."
                    )
                    self._add_event_locked("warning", self._message)
                verified = self._program(response, profile, target_id)
                with self._lock:
                    self._completed += 1
                    self._last_assignment = {
                        "number": self._completed,
                        "from_id": response.motor_id,
                        "to_id": verified.motor_id,
                        "model": profile.name,
                        "model_number": profile.model_number,
                        "baudrate": verified.baudrate,
                        "verified_at": _now(),
                    }
                    self._detected = deepcopy(self._last_assignment)
                    self._add_event_locked(
                        "success",
                        f"Verified motor {self._completed}: {profile.name.upper()} ID {verified.motor_id} at {verified.baudrate} baud; torque off.",
                    )

                if not plan.advance():
                    with self._lock:
                        self._phase = "complete"
                        self._active = False
                        self._message = f"Sequence complete: IDs {plan.start}–{plan.end} verified."
                        self._add_event_locked("success", self._message)
                    return

                with self._lock:
                    self._phase = "wait_unplug"
                    self._message = f"ID {verified.motor_id} verified. Unplug it before target ID {plan.target}."
                empty_scans = 0
                while not self._stop.is_set() and empty_scans < self.unplug_scans:
                    try:
                        present = self._scan(verified.baudrate)
                    except Exception as exc:
                        raise IdStationError(f"Unplug check failed: {self._compact(exc)}") from exc
                    empty_scans = 0 if present else empty_scans + 1
                    if empty_scans < self.unplug_scans:
                        self._stop.wait(self.poll_interval)
                if self._stop.is_set():
                    break
                with self._lock:
                    self._detected = None
                    self._phase = "waiting"
                    self._message = f"Motor removed. Waiting to assign ID {plan.target}."
                    self._add_event_locked("info", self._message)
                stable_key = None
                stable_count = 0
        except Exception as exc:
            with self._lock:
                self._phase = "error"
                self._active = False
                self._message = self._compact(exc)
                self._add_event_locked("danger", self._message)
            return
        finally:
            with self._lock:
                if self._stop.is_set() and self._phase not in {"complete", "error"}:
                    self._phase = "stopped"
                    self._active = False
                    self._message = "ID station stopped; no further motors will be programmed."
                    self._add_event_locked("info", self._message)

    def _set_waiting_message(self, message: str) -> None:
        with self._lock:
            self._phase = "waiting"
            self._detected = None
            self._message = message

    def _event(self, level: str, message: str) -> None:
        with self._lock:
            self._add_event_locked(level, message)

    def _add_event_locked(self, level: str, message: str) -> None:
        self._event_number += 1
        self._events.append(
            {
                "number": self._event_number,
                "timestamp": _now(),
                "level": level,
                "message": message,
            }
        )
        self._events = self._events[-80:]

    @staticmethod
    def _compact(exc: Exception) -> str:
        text = str(exc).strip().replace("\n", " ")
        return text or exc.__class__.__name__
