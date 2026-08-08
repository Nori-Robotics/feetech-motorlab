from __future__ import annotations

import csv
import json
import math
import os
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .serial_ports import discover_serial_ports, serial_port_available


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CAPTURES_FILE = DATA_DIR / "captured_positions.json"
RECORDINGS_DIR = DATA_DIR / "recordings"

DEFAULT_BAUDRATE = 1_000_000
DEFAULT_VELOCITY_LIMIT = 600
DEFAULT_ACCELERATION = 40
ENCODER_CENTER_CONFIRMATION = "CENTER_ENCODER_2048"
PERSISTENT_CONFIG_CONFIRMATION = "WRITE_EEPROM"
GROUP_PERSISTENT_CONFIG_CONFIRMATION = "WRITE_ALL_EEPROM"
CURRENT_SCALE_A_PER_LSB = 0.0065
KGFCM_TO_NM = 0.0980665
CONTROL_MODES = {
    0: "Angle servo",
    1: "Closed-loop speed",
    2: "Open-loop speed",
    3: "Relative step",
}
SUPPORTED_MODELS: dict[int, dict[str, Any]] = {
    777: {
        "model": "STS3215",
        "sdk_model": "sts3215",
        "variant": "ST-3215-C018 · 12 V",
        "stall_torque_kgcm": 30,
        "rated_torque_kgcm": 10,
        "stall_current_a": 2.7,
        "torque_constant_kgcm_per_a": 11.0,
        "operating_voltage": "4–14 V",
        "operating_temp": "−10–60 °C",
        "datasheet": "/docs/datasheets/STS3215-12V-ST-3215-C018.pdf",
    },
    2825: {
        "model": "STS3250",
        "sdk_model": "sts3250",
        "variant": "ST-3250-C001 · 12 V",
        "stall_torque_kgcm": 50,
        "rated_torque_kgcm": 16,
        "stall_current_a": 4.2,
        "torque_constant_kgcm_per_a": 11.0,
        "operating_voltage": "6–12 V",
        "operating_temp": "−20–60 °C",
        "datasheet": "/docs/datasheets/STS3250-12V-ST-3250-C001.pdf",
    },
    2569: {
        "model": "STS3095",
        "sdk_model": "sts_series",
        "variant": "12 V · SMS/STS protocol profile",
        "stall_torque_kgcm": None,
        "rated_torque_kgcm": None,
        "stall_current_a": None,
        "torque_constant_kgcm_per_a": None,
        "operating_voltage": "12 V nominal",
        "operating_temp": "See model datasheet",
        "datasheet": None,
    },
}

STATUS_BITS = {
    0x01: "Voltage",
    0x02: "Angle sensor",
    0x04: "Over-temperature",
    0x08: "Over-current",
    0x20: "Overload",
}

PERSISTENT_PARAMETERS: dict[str, tuple[str, int, int]] = {
    "p_gain": ("P_Coefficient", 0, 254),
    "d_gain": ("D_Coefficient", 0, 254),
    "i_gain": ("I_Coefficient", 0, 254),
    "min_position": ("Min_Position_Limit", 0, 4095),
    "max_position": ("Max_Position_Limit", 0, 4095),
    "max_temperature": ("Max_Temperature_Limit", 40, 85),
    "min_voltage": ("Min_Voltage_Limit", 40, 140),
    "max_voltage": ("Max_Voltage_Limit", 40, 140),
}


class ControlError(RuntimeError):
    """An expected, user-facing controller error."""


@dataclass
class MotorState:
    id: int
    model_number: int
    model: str
    sdk_model: str
    variant: str
    datasheet: str | None
    stall_torque_kgcm: float | None
    rated_torque_kgcm: float | None
    stall_current_a: float | None
    torque_constant_kgcm_per_a: float | None
    operating_voltage: str
    operating_temp: str
    torque_enabled: bool = False
    online: bool = True
    position: int = 2048
    degrees: float = 180.0
    target_position: int = 2048
    velocity_raw: int = 0
    velocity_rpm_estimate: float = 0.0
    load_raw: int = 0
    load_percent: float = 0.0
    voltage: float = 12.0
    temperature: int = 25
    current_raw: int = 0
    moving: bool = False
    status: int = 0
    status_labels: list[str] = field(default_factory=list)
    acceleration: int = DEFAULT_ACCELERATION
    velocity_limit: int = DEFAULT_VELOCITY_LIMIT
    torque_limit: int = 500
    operating_mode: int = 0
    mode_command: int = 0
    config: dict[str, int] = field(default_factory=dict)
    last_seen: str | None = None
    communication_errors: int = 0

    def as_dict(self, selected: bool = False) -> dict[str, Any]:
        value = deepcopy(self.__dict__)
        current_a = abs(self.current_raw) * CURRENT_SCALE_A_PER_LSB
        value["current_a"] = round(current_a, 4)
        value["current_scale_a_per_lsb"] = CURRENT_SCALE_A_PER_LSB
        if self.rated_torque_kgcm is None or not self.stall_torque_kgcm:
            value["rated_torque_limit_raw"] = None
        else:
            rated_limit = round(1000 * self.rated_torque_kgcm / self.stall_torque_kgcm)
            value["rated_torque_limit_raw"] = max(0, min(1000, rated_limit))
        if self.torque_constant_kgcm_per_a is None:
            value["estimated_torque_kgcm"] = None
            value["estimated_torque_nm"] = None
        else:
            torque_kgcm = current_a * self.torque_constant_kgcm_per_a
            value["estimated_torque_kgcm"] = round(torque_kgcm, 3)
            value["estimated_torque_nm"] = round(torque_kgcm * KGFCM_TO_NM, 4)
        value["operating_mode_name"] = CONTROL_MODES.get(self.operating_mode, "Unknown mode")
        value["selected"] = selected
        return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _decode_sign_magnitude(value: int, sign_bit: int) -> int:
    sign_mask = 1 << sign_bit
    magnitude_mask = sign_mask - 1
    return -(value & magnitude_mask) if value & sign_mask else value & magnitude_mask


def _encode_sign_magnitude(value: int, sign_bit: int) -> int:
    magnitude = abs(int(value))
    maximum = (1 << sign_bit) - 1
    if magnitude > maximum:
        raise ValueError(f"Magnitude {magnitude} exceeds {maximum}")
    return magnitude | ((1 << sign_bit) if value < 0 else 0)


def _decode_load_effort(value: int) -> tuple[int, float]:
    """Return Feetech Present_Load as an unsigned effort magnitude and percent."""
    magnitude = abs(_decode_sign_magnitude(int(value), 10))
    return magnitude, min(100.0, magnitude * 100.0 / 1000.0)


def _word(data: list[int], offset: int) -> int:
    return int(data[offset]) | (int(data[offset + 1]) << 8)


def _safe_filename(label: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", label.strip()).strip("-")
    return clean[:48] or "session"


class BenchController:
    """Thread-safe hardware/simulation controller for one Feetech servo bus."""

    poll_interval = 0.08

    def __init__(self, *, simulation: bool = False, data_dir: Path = DATA_DIR):
        self.simulation = simulation
        self.data_dir = Path(data_dir)
        self.captures_file = self.data_dir / "captured_positions.json"
        self.recordings_dir = self.data_dir / "recordings"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._bus: Any | None = None
        self._connected = False
        self._port: str | None = None
        self._baudrate = DEFAULT_BAUDRATE
        self._selected_id: int | None = None
        self._motors: dict[int, MotorState] = {}
        self._message = "Simulation ready" if simulation else "Connect a serial bus adapter"
        self._message_level = "info"
        self._captures = self._load_captures()
        self._recording_file: Any | None = None
        self._recording_writer: csv.DictWriter | None = None
        self._recording_path: Path | None = None
        self._recording_started: str | None = None
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="servo-poll")
        self._thread.start()
        if simulation:
            self.connect("SIMULATED_FEETECH_BUS", DEFAULT_BAUDRATE)

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def close(self) -> None:
        self._stop.set()
        try:
            self.disconnect()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def list_ports(self) -> list[dict[str, Any]]:
        if self.simulation:
            return [
                {
                    "device": "SIMULATED_FEETECH_BUS",
                    "real_device": "SIMULATED_FEETECH_BUS",
                    "label": "Simulated Feetech bus",
                    "manufacturer": "MotorLab",
                    "vid": None,
                    "pid": None,
                }
            ]
        return discover_serial_ports()

    def profiles(self) -> list[dict[str, Any]]:
        return [
            {"model_number": model_number, **deepcopy(profile)}
            for model_number, profile in SUPPORTED_MODELS.items()
        ]

    def scan(self, port: str, baudrate: int = DEFAULT_BAUDRATE) -> list[dict[str, Any]]:
        if baudrate not in (19_200, 38_400, 57_600, 115_200, 128_000, 250_000, 500_000, 1_000_000):
            raise ControlError("Unsupported STS baud rate")
        if self.simulation:
            return [self._motor_description(mid, number) for mid, number in ((1, 777), (2, 2825), (3, 2569))]
        if not serial_port_available(port):
            raise ControlError(f"Serial port does not exist: {port}")
        with self._lock:
            if self._connected:
                raise ControlError("Disconnect the active bus before scanning another port")
            FeetechMotorsBus, _, _ = self._import_feetech()
            bus = FeetechMotorsBus(port, {})
            try:
                bus._connect(handshake=False)
                bus.set_baudrate(baudrate)
                found = bus.broadcast_ping(num_retry=1, raise_on_error=False) or {}
            except Exception as exc:
                raise ControlError(f"Bus scan failed: {self._compact_error(exc)}") from exc
            finally:
                handler = getattr(bus, "port_handler", None)
                if handler is not None and getattr(handler, "is_open", False):
                    handler.closePort()
            return [self._motor_description(mid, model) for mid, model in sorted(found.items())]

    def connect(self, port: str, baudrate: int = DEFAULT_BAUDRATE) -> dict[str, Any]:
        with self._lock:
            if self._connected:
                self._disconnect_locked()

            if self.simulation:
                found = {1: 777, 2: 2825, 3: 2569}
                self._bus = None
            else:
                descriptions = self.scan(port, baudrate)
                found = {
                    int(item["id"]): int(item["model_number"])
                    for item in descriptions
                    if item["supported"]
                }
                if not found:
                    raise ControlError("No supported STS3215, STS3250, or STS3095 servo responded")
                FeetechMotorsBus, Motor, MotorNormMode = self._import_feetech()
                motors = {
                    self._motor_name(mid): Motor(mid, SUPPORTED_MODELS[number]["sdk_model"], MotorNormMode.DEGREES)
                    for mid, number in found.items()
                }
                bus = FeetechMotorsBus(port, motors)
                try:
                    bus.connect(handshake=False)
                    bus.set_baudrate(baudrate)
                    for motor_id, expected in found.items():
                        actual = bus.ping(motor_id, num_retry=1, raise_on_error=True)
                        if actual != expected:
                            raise ControlError(
                                f"ID {motor_id} changed model response: expected {expected}, received {actual}"
                            )
                except Exception:
                    if getattr(bus, "is_connected", False):
                        bus.port_handler.closePort()
                    raise
                self._bus = bus

            self._port = port
            self._baudrate = baudrate
            self._motors = {
                mid: self._new_motor_state(mid, number) for mid, number in sorted(found.items())
            }
            self._selected_id = next(iter(self._motors), None)
            self._connected = True

            if self.simulation:
                for motor in self._motors.values():
                    motor.config = self._default_config()
            else:
                for motor in self._motors.values():
                    self._refresh_motor_locked(motor)
                    self._read_config_locked(motor)
                    motor.target_position = motor.position

            armed_ids = [motor.id for motor in self._motors.values() if motor.torque_enabled]
            if armed_ids:
                self._set_message(
                    "Connected without issuing motion, but torque was already enabled on "
                    + ", ".join(f"ID {motor_id}" for motor_id in armed_ids),
                    "warning",
                )
            else:
                self._set_message(
                    f"Connected to {len(self._motors)} supported servo{'s' if len(self._motors) != 1 else ''}; torque is off",
                    "success",
                )
            return self.snapshot()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._disconnect_locked()
            return self.snapshot()

    def _disconnect_locked(self) -> None:
        self._stop_recording_locked()
        if self._bus is not None:
            try:
                for motor in self._motors.values():
                    try:
                        self._bus.write("Torque_Enable", self._motor_name(motor.id), 0, normalize=False, num_retry=1)
                        motor.torque_enabled = False
                    except Exception:
                        pass
                if getattr(self._bus, "is_connected", False):
                    self._bus.port_handler.closePort()
            finally:
                self._bus = None
        self._connected = False
        self._motors = {}
        self._selected_id = None
        self._port = None
        self._set_message("Disconnected safely", "info")

    def select_motor(self, motor_id: int) -> dict[str, Any]:
        with self._lock:
            self._require_motor(motor_id)
            self._selected_id = motor_id
            return self.snapshot()

    def set_operating_mode(self, motor_id: int, mode: int) -> dict[str, Any]:
        with self._lock:
            motor = self._require_motor(motor_id)
            mode = int(mode)
            if mode not in CONTROL_MODES:
                raise ControlError("Operating mode must be 0, 1, 2, or 3")
            if motor.torque_enabled:
                raise ControlError("Release torque before changing the operating mode")
            if mode == motor.operating_mode:
                self._set_message(
                    f"ID {motor_id} is already in {CONTROL_MODES[mode]} mode", "info"
                )
                return motor.as_dict(selected=motor_id == self._selected_id)

            self._refresh_motor_locked(motor)

            if not self.simulation:
                name = self._motor_name(motor_id)
                self._bus.write("Torque_Enable", name, 0, normalize=False, num_retry=2)
                # Seed every non-position command register at zero before the persistent mode write.
                self._bus.write("Goal_Velocity", name, 0, normalize=False, num_retry=2)
                self._bus.write("Goal_Time", name, 0, normalize=False, num_retry=2)
                self._bus.write("Lock", name, 0, normalize=False, num_retry=2)
                try:
                    self._bus.write("Operating_Mode", name, mode, normalize=False, num_retry=2)
                    actual = int(
                        self._bus.read("Operating_Mode", name, normalize=False, num_retry=2)
                    )
                    if actual != mode:
                        raise ControlError(
                            f"Mode verification failed: wrote {mode}, read {actual}"
                        )
                finally:
                    self._bus.write("Lock", name, 1, normalize=False, num_retry=2)

            motor.operating_mode = mode
            motor.mode_command = 0
            motor.velocity_raw = 0
            motor.moving = False
            motor.target_position = motor.position
            self._set_message(
                f"ID {motor_id} switched to {CONTROL_MODES[mode]}; torque remains released",
                "success",
            )
            return motor.as_dict(selected=motor_id == self._selected_id)

    def set_torque(self, motor_id: int, enabled: bool) -> dict[str, Any]:
        with self._lock:
            motor = self._require_motor(motor_id)
            if enabled:
                if motor.operating_mode not in CONTROL_MODES:
                    raise ControlError(f"ID {motor_id} reported an unknown operating mode")
                self._refresh_motor_locked(motor)
                if not self.simulation:
                    name = self._motor_name(motor_id)
                    if motor.operating_mode == 0:
                        self._bus.write(
                            "Goal_Position", name, motor.position, normalize=False, num_retry=1
                        )
                    elif motor.operating_mode == 1:
                        self._bus.write("Goal_Velocity", name, 0, normalize=False, num_retry=1)
                    elif motor.operating_mode == 2:
                        self._bus.write("Goal_Time", name, 0, normalize=False, num_retry=1)
                    else:
                        self._bus.write("Goal_Position", name, 0, normalize=False, num_retry=1)
                    self._bus.enable_torque(name, num_retry=1)
                motor.target_position = motor.position
                motor.mode_command = 0
                motor.torque_enabled = True
                self._set_message(
                    f"ID {motor_id} armed in {CONTROL_MODES[motor.operating_mode]} mode with a zero-motion command",
                    "warning",
                )
            else:
                if not self.simulation:
                    name = self._motor_name(motor_id)
                    if motor.operating_mode == 1:
                        self._bus.write("Goal_Velocity", name, 0, normalize=False, num_retry=1)
                    elif motor.operating_mode == 2:
                        self._bus.write("Goal_Time", name, 0, normalize=False, num_retry=1)
                    self._bus.disable_torque(name, num_retry=1)
                motor.torque_enabled = False
                motor.mode_command = 0
                motor.velocity_raw = 0
                motor.moving = False
                self._set_message(f"Torque released on ID {motor_id}", "success")
            return motor.as_dict(selected=motor_id == self._selected_id)

    def emergency_stop(self) -> dict[str, Any]:
        with self._lock:
            errors: list[str] = []
            for motor in self._motors.values():
                if not self.simulation and self._bus is not None:
                    try:
                        self._bus.write("Torque_Enable", self._motor_name(motor.id), 0, normalize=False, num_retry=1)
                    except Exception as exc:
                        errors.append(f"ID {motor.id}: {self._compact_error(exc)}")
                motor.torque_enabled = False
                motor.target_position = motor.position
                motor.mode_command = 0
                motor.velocity_raw = 0
                motor.moving = False
            self._set_message("Emergency stop: torque disabled on all servos", "danger")
            if errors:
                raise ControlError("Emergency stop sent, but acknowledgements failed: " + "; ".join(errors))
            return self.snapshot()

    def set_all_torque(self, enabled: bool) -> dict[str, Any]:
        """Arm or release the entire connected chain with zero-motion seeding."""
        with self._lock:
            if not self._connected or not self._motors:
                raise ControlError("No servo bus is connected")
            enabled = bool(enabled)
            if enabled:
                incompatible = [motor.id for motor in self._motors.values() if motor.operating_mode != 0]
                if incompatible:
                    raise ControlError(
                        "Group angle control requires mode 0 on every motor; switch ID(s) "
                        + ", ".join(str(motor_id) for motor_id in incompatible)
                    )

                newly_armed: list[int] = []
                try:
                    for motor in self._motors.values():
                        if motor.torque_enabled:
                            continue
                        self.set_torque(motor.id, True)
                        newly_armed.append(motor.id)
                except Exception as exc:
                    for motor_id in newly_armed:
                        try:
                            self.set_torque(motor_id, False)
                        except Exception:
                            pass
                    if isinstance(exc, ControlError):
                        raise
                    raise ControlError(f"Could not arm the full chain: {self._compact_error(exc)}") from exc
                self._set_message(
                    f"Armed {len(self._motors)} motors in angle mode with zero-motion seeds",
                    "warning",
                )
            else:
                errors: list[str] = []
                for motor in self._motors.values():
                    try:
                        self.set_torque(motor.id, False)
                    except Exception as exc:
                        errors.append(f"ID {motor.id}: {self._compact_error(exc)}")
                self._set_message("Group torque released on all motors", "success")
                if errors:
                    raise ControlError(
                        "Group release was attempted on every motor, but acknowledgements failed: "
                        + "; ".join(errors)
                    )
            return self.snapshot()

    def command_all_positions(
        self,
        position: int,
        *,
        velocity: int,
        acceleration: int,
        torque_limit: int,
    ) -> dict[str, Any]:
        """Send one validated angle target and one set of limits to every motor."""
        with self._lock:
            if not self._connected or not self._motors:
                raise ControlError("No servo bus is connected")
            position = int(position)
            velocity = int(velocity)
            acceleration = int(acceleration)
            torque_limit = int(torque_limit)
            if not 0 <= position <= 4095:
                raise ControlError("Group position must be 0–4095")
            if not 0 <= velocity <= 3073:
                raise ControlError("Group velocity must be 0–3073")
            if not 0 <= acceleration <= 254:
                raise ControlError("Group acceleration must be 0–254")
            if not 0 <= torque_limit <= 1000:
                raise ControlError("Group torque limit must be 0–1000")

            offline = [motor.id for motor in self._motors.values() if not motor.online]
            unarmed = [motor.id for motor in self._motors.values() if not motor.torque_enabled]
            incompatible = [motor.id for motor in self._motors.values() if motor.operating_mode != 0]
            outside_limits = [
                motor.id
                for motor in self._motors.values()
                if not int(motor.config.get("min_position", 0))
                <= position
                <= int(motor.config.get("max_position", 4095))
            ]
            if offline:
                raise ControlError("Group command blocked by offline ID(s): " + ", ".join(map(str, offline)))
            if unarmed:
                raise ControlError("Arm every motor before a group command; unarmed ID(s): " + ", ".join(map(str, unarmed)))
            if incompatible:
                raise ControlError("Group position requires mode 0 on ID(s): " + ", ".join(map(str, incompatible)))
            if outside_limits:
                raise ControlError(
                    f"Target {position} is outside configured travel for ID(s): "
                    + ", ".join(map(str, outside_limits))
                )

            if not self.simulation:
                # Stage common limits first so no goal positions are sent if setup fails.
                for motor in self._motors.values():
                    name = self._motor_name(motor.id)
                    self._bus.write("Acceleration", name, acceleration, normalize=False, num_retry=1)
                    self._bus.write("Goal_Velocity", name, velocity, normalize=False, num_retry=1)
                    self._bus.write("Torque_Limit", name, torque_limit, normalize=False, num_retry=1)
                for motor in self._motors.values():
                    self._bus.write(
                        "Goal_Position",
                        self._motor_name(motor.id),
                        position,
                        normalize=False,
                        num_retry=1,
                    )

            for motor in self._motors.values():
                motor.target_position = position
                motor.velocity_limit = velocity
                motor.acceleration = acceleration
                motor.torque_limit = torque_limit
            self._set_message(
                f"Group command sent to {len(self._motors)} motors: {position} raw ({position * 360 / 4095:.1f}°)",
                "warning",
            )
            return self.snapshot()

    def jog_all_positions(
        self,
        delta: int,
        *,
        velocity: int,
        acceleration: int,
        torque_limit: int,
    ) -> dict[str, Any]:
        """Shift every angle-mode motor by the same delta after a full preflight."""
        with self._lock:
            if not self._connected or not self._motors:
                raise ControlError("No servo bus is connected")
            delta = int(delta)
            velocity = int(velocity)
            acceleration = int(acceleration)
            torque_limit = int(torque_limit)
            if delta == 0 or not -2048 <= delta <= 2048:
                raise ControlError(
                    "Group jog delta must be −2048 to 2048 raw steps and cannot be zero"
                )
            if not 0 <= velocity <= 3073:
                raise ControlError("Group velocity must be 0–3073")
            if not 0 <= acceleration <= 254:
                raise ControlError("Group acceleration must be 0–254")
            if not 0 <= torque_limit <= 1000:
                raise ControlError("Group torque limit must be 0–1000")

            offline = [motor.id for motor in self._motors.values() if not motor.online]
            unarmed = [
                motor.id for motor in self._motors.values() if not motor.torque_enabled
            ]
            incompatible = [
                motor.id for motor in self._motors.values() if motor.operating_mode != 0
            ]
            targets = {
                motor.id: int(motor.target_position) + delta
                for motor in self._motors.values()
            }
            outside_limits = [
                motor.id
                for motor in self._motors.values()
                if not int(motor.config.get("min_position", 0))
                <= targets[motor.id]
                <= int(motor.config.get("max_position", 4095))
            ]
            if offline:
                raise ControlError(
                    "Group jog blocked by offline ID(s): "
                    + ", ".join(map(str, offline))
                )
            if unarmed:
                raise ControlError(
                    "Arm every motor before a group jog; unarmed ID(s): "
                    + ", ".join(map(str, unarmed))
                )
            if incompatible:
                raise ControlError(
                    "Group jogging requires mode 0 on ID(s): "
                    + ", ".join(map(str, incompatible))
                )
            if outside_limits:
                raise ControlError(
                    "Group jog would exceed configured travel for ID(s): "
                    + ", ".join(map(str, outside_limits))
                )

            if not self.simulation:
                # Preflight every target before staging limits or sending any position.
                for motor in self._motors.values():
                    name = self._motor_name(motor.id)
                    self._bus.write(
                        "Acceleration", name, acceleration, normalize=False, num_retry=1
                    )
                    self._bus.write(
                        "Goal_Velocity", name, velocity, normalize=False, num_retry=1
                    )
                    self._bus.write(
                        "Torque_Limit", name, torque_limit, normalize=False, num_retry=1
                    )
                for motor in self._motors.values():
                    self._bus.write(
                        "Goal_Position",
                        self._motor_name(motor.id),
                        targets[motor.id],
                        normalize=False,
                        num_retry=1,
                    )

            for motor in self._motors.values():
                motor.target_position = targets[motor.id]
                motor.velocity_limit = velocity
                motor.acceleration = acceleration
                motor.torque_limit = torque_limit
            minimum_target = min(targets.values())
            maximum_target = max(targets.values())
            target_summary = (
                str(minimum_target)
                if minimum_target == maximum_target
                else f"{minimum_target}–{maximum_target}"
            )
            self._set_message(
                f"Group jogged {len(self._motors)} motors {delta:+d} raw "
                f"({delta * 360 / 4095:+.1f}°); targets {target_summary}",
                "warning",
            )
            return self.snapshot()

    def center_encoder_position(
        self, motor_id: int, confirmation: str
    ) -> dict[str, Any]:
        """Use Feetech's one-key midpoint command and verify 2048 with torque off."""
        with self._lock:
            if confirmation != ENCODER_CENTER_CONFIRMATION:
                raise ControlError(
                    "Encoder centering requires the exact confirmation "
                    f"{ENCODER_CENTER_CONFIRMATION}"
                )
            motor = self._require_motor(motor_id)
            self._preflight_encoder_center_locked(motor)
            before, after = self._center_encoder_locked(motor)
            self._set_message(
                f"Verified encoder center on ID {motor_id}: {before} → {after} raw; torque is off",
                "success",
            )
            return motor.as_dict(selected=motor_id == self._selected_id)

    def center_all_encoder_positions(self, confirmation: str) -> dict[str, Any]:
        """Center every encoder only after the complete bus passes preflight."""
        with self._lock:
            if confirmation != ENCODER_CENTER_CONFIRMATION:
                raise ControlError(
                    "Bus encoder centering requires the exact confirmation "
                    f"{ENCODER_CENTER_CONFIRMATION}"
                )
            if not self._connected or not self._motors:
                raise ControlError("No servo bus is connected")

            for motor in self._motors.values():
                self._preflight_encoder_center_locked(motor)

            centered: list[int] = []
            readings: list[str] = []
            for motor in self._motors.values():
                try:
                    before, after = self._center_encoder_locked(motor)
                except Exception as exc:
                    completed = ", ".join(map(str, centered)) or "none"
                    self._set_message(
                        f"Bus center stopped at ID {motor.id}; previously completed IDs: {completed}",
                        "danger",
                    )
                    if isinstance(exc, ControlError):
                        detail = str(exc)
                    else:
                        detail = self._compact_error(exc)
                    raise ControlError(
                        f"Bus center stopped at ID {motor.id}: {detail}. "
                        f"Previously completed ID(s): {completed}. The current ID may "
                        "also have accepted the calibration command; inspect its live readback"
                    ) from exc
                centered.append(motor.id)
                readings.append(f"ID {motor.id} {before}→{after}")

            self._set_message(
                "Verified all encoder centers at 2048 with torque off: "
                + "; ".join(readings),
                "success",
            )
            return self.snapshot()

    def _preflight_encoder_center_locked(self, motor: MotorState) -> None:
        if not motor.online:
            raise ControlError(f"ID {motor.id} is offline")
        if motor.operating_mode != 0:
            raise ControlError(
                f"ID {motor.id} must be in angle-servo mode before encoder centering"
            )
        if motor.torque_enabled:
            raise ControlError(
                f"Release torque on ID {motor.id} before encoder centering"
            )
        if self.simulation:
            return

        name = self._motor_name(motor.id)
        try:
            torque_state = int(
                self._bus.read(
                    "Torque_Enable", name, normalize=False, num_retry=2
                )
            )
            mode = int(
                self._bus.read(
                    "Operating_Mode", name, normalize=False, num_retry=2
                )
            )
        except Exception as exc:
            raise ControlError(
                f"Could not verify torque and mode on ID {motor.id}: "
                f"{self._compact_error(exc)}"
            ) from exc
        motor.torque_enabled = torque_state != 0
        motor.operating_mode = mode
        if torque_state != 0:
            raise ControlError(
                f"ID {motor.id} reports Torque_Enable={torque_state}; release torque first"
            )
        if mode != 0:
            raise ControlError(
                f"ID {motor.id} reports operating mode {mode}; switch to angle-servo mode first"
            )

    def _center_encoder_locked(self, motor: MotorState) -> tuple[int, int]:
        before = int(motor.position)
        if self.simulation:
            motor.position = 2048
        else:
            name = self._motor_name(motor.id)
            try:
                before = int(
                    self._bus.read(
                        "Present_Position", name, normalize=False, num_retry=2
                    )
                )
                if before != 2048:
                    # Feetech STS one-key midpoint calibration: address 40 value 128.
                    self._bus.write(
                        "Torque_Enable", name, 128, normalize=False, num_retry=2
                    )
                    time.sleep(0.08)
                self._bus.write(
                    "Torque_Enable", name, 0, normalize=False, num_retry=2
                )
                after = int(
                    self._bus.read(
                        "Present_Position", name, normalize=False, num_retry=2
                    )
                )
                torque_state = int(
                    self._bus.read(
                        "Torque_Enable", name, normalize=False, num_retry=2
                    )
                )
                mode = int(
                    self._bus.read(
                        "Operating_Mode", name, normalize=False, num_retry=2
                    )
                )
                if mode != 0:
                    self._bus.write("Lock", name, 0, normalize=False, num_retry=2)
                    try:
                        self._bus.write(
                            "Operating_Mode", name, 0, normalize=False, num_retry=2
                        )
                        mode = int(
                            self._bus.read(
                                "Operating_Mode",
                                name,
                                normalize=False,
                                num_retry=2,
                            )
                        )
                    finally:
                        self._bus.write(
                            "Lock", name, 1, normalize=False, num_retry=2
                        )
                else:
                    self._bus.write(
                        "Lock", name, 1, normalize=False, num_retry=2
                    )
            except Exception as exc:
                try:
                    self._bus.write(
                        "Torque_Enable", name, 0, normalize=False, num_retry=2
                    )
                except Exception as stop_exc:
                    motor.torque_enabled = True
                    raise ControlError(
                        f"ID {motor.id} center command failed and torque-off could not be verified: "
                        f"{self._compact_error(stop_exc)}"
                    ) from exc
                raise ControlError(
                    f"ID {motor.id} center command could not be completed and verified: "
                    f"{self._compact_error(exc)}. Its encoder value may already have changed; "
                    "inspect the live readback"
                ) from exc
            if torque_state != 0:
                motor.torque_enabled = True
                raise ControlError(
                    f"ID {motor.id} centered but Torque_Enable read back {torque_state}, not 0"
                )
            if mode != 0:
                motor.operating_mode = mode
                raise ControlError(
                    f"ID {motor.id} centered but operating mode changed to {mode}"
                )
            if after != 2048:
                raise ControlError(
                    f"ID {motor.id} center verification failed: expected 2048, read {after}"
                )
            motor.position = after

        motor.degrees = motor.position * 360.0 / 4095.0
        motor.target_position = motor.position
        motor.mode_command = 0
        motor.velocity_raw = 0
        motor.moving = False
        motor.torque_enabled = False
        motor.last_seen = _utc_now()
        return before, motor.position

    def command_position(
        self,
        motor_id: int,
        position: int,
        velocity: int | None = None,
        acceleration: int | None = None,
        torque_limit: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            motor = self._require_motor(motor_id)
            if not motor.torque_enabled:
                raise ControlError("Arm torque before commanding motion")
            if motor.operating_mode != 0:
                raise ControlError("Position commands are only allowed in operating mode 0")

            config_min = int(motor.config.get("min_position", 0))
            config_max = int(motor.config.get("max_position", 4095))
            position = int(position)
            if not config_min <= position <= config_max:
                raise ControlError(f"Position must be inside configured limits {config_min}–{config_max}")
            velocity = motor.velocity_limit if velocity is None else int(velocity)
            acceleration = motor.acceleration if acceleration is None else int(acceleration)
            torque_limit = motor.torque_limit if torque_limit is None else int(torque_limit)
            if not 0 <= velocity <= 3073:
                raise ControlError("Velocity must be 0–3073 raw units")
            if not 0 <= acceleration <= 254:
                raise ControlError("Acceleration must be 0–254")
            if not 0 <= torque_limit <= 1000:
                raise ControlError("Torque limit must be 0–1000")

            if not self.simulation:
                name = self._motor_name(motor_id)
                self._bus.write("Acceleration", name, acceleration, normalize=False, num_retry=1)
                self._bus.write("Goal_Velocity", name, velocity, normalize=False, num_retry=1)
                self._bus.write("Torque_Limit", name, torque_limit, normalize=False, num_retry=1)
                self._bus.write("Goal_Position", name, position, normalize=False, num_retry=1)
            motor.target_position = position
            motor.velocity_limit = velocity
            motor.acceleration = acceleration
            motor.torque_limit = torque_limit
            self._set_message(f"ID {motor_id} moving to {position} ({position * 360 / 4095:.1f}°)", "success")
            return motor.as_dict(selected=motor_id == self._selected_id)

    def jog_position(
        self,
        motor_id: int,
        delta: int,
        *,
        velocity: int,
        acceleration: int,
        torque_limit: int,
    ) -> dict[str, Any]:
        """Atomically move an angle-mode motor relative to its latest commanded target."""
        with self._lock:
            motor = self._require_motor(motor_id)
            if not motor.torque_enabled:
                raise ControlError("Arm torque before jogging")
            if motor.operating_mode != 0:
                raise ControlError("Jogging is only allowed in operating mode 0")
            delta = int(delta)
            if delta == 0 or not -2048 <= delta <= 2048:
                raise ControlError("Jog delta must be −2048 to 2048 raw steps and cannot be zero")

            minimum = int(motor.config.get("min_position", 0))
            maximum = int(motor.config.get("max_position", 4095))
            base = int(motor.target_position)
            target = max(minimum, min(maximum, base + delta))
            if target == base:
                boundary = "minimum" if delta < 0 else "maximum"
                raise ControlError(f"Jog stopped at configured {boundary} {target}")

            result = self.command_position(
                motor_id,
                target,
                velocity=velocity,
                acceleration=acceleration,
                torque_limit=torque_limit,
            )
            moved = target - base
            self._set_message(
                f"ID {motor_id} jogged {moved:+d} raw to {target} ({target * 360 / 4095:.1f}°)",
                "success",
            )
            return result

    def command_mode(
        self,
        motor_id: int,
        value: int,
        *,
        velocity: int,
        acceleration: int,
        torque_limit: int,
    ) -> dict[str, Any]:
        """Issue a mode-specific non-position command with explicit signed ranges."""
        with self._lock:
            motor = self._require_motor(motor_id)
            if not motor.torque_enabled:
                raise ControlError("Arm torque before commanding motion")
            if motor.operating_mode == 0:
                raise ControlError("Use the position command in angle-servo mode")
            if motor.operating_mode not in CONTROL_MODES:
                raise ControlError("The servo reported an unknown operating mode")

            value = int(value)
            velocity = int(velocity)
            acceleration = int(acceleration)
            torque_limit = int(torque_limit)
            if not 0 <= velocity <= 3073:
                raise ControlError("Velocity must be 0–3073 raw units")
            if not 0 <= acceleration <= 254:
                raise ControlError("Acceleration must be 0–254")
            if not 0 <= torque_limit <= 1000:
                raise ControlError("Torque limit must be 0–1000")

            name = self._motor_name(motor_id)
            if motor.operating_mode == 1:
                if not -3073 <= value <= 3073:
                    raise ControlError("Closed-loop speed must be −3073 to 3073")
                if not self.simulation:
                    self._bus.write("Acceleration", name, acceleration, normalize=False, num_retry=1)
                    self._bus.write("Torque_Limit", name, torque_limit, normalize=False, num_retry=1)
                    self._bus.write("Goal_Velocity", name, value, normalize=False, num_retry=1)
                message = f"ID {motor_id} closed-loop speed set to {value}"
            elif motor.operating_mode == 2:
                if not -1000 <= value <= 1000:
                    raise ControlError("Open-loop drive must be −1000 to 1000")
                raw_drive = _encode_sign_magnitude(value, 10)
                if not self.simulation:
                    self._bus.write("Acceleration", name, acceleration, normalize=False, num_retry=1)
                    self._bus.write("Torque_Limit", name, torque_limit, normalize=False, num_retry=1)
                    self._bus.write("Goal_Time", name, raw_drive, normalize=False, num_retry=1)
                message = f"ID {motor_id} open-loop drive set to {value}"
            else:
                if not -4095 <= value <= 4095:
                    raise ControlError("Relative step must be −4095 to 4095")
                raw_step = _encode_sign_magnitude(value, 15)
                if not self.simulation:
                    self._bus.write("Acceleration", name, acceleration, normalize=False, num_retry=1)
                    self._bus.write("Goal_Velocity", name, velocity, normalize=False, num_retry=1)
                    self._bus.write("Torque_Limit", name, torque_limit, normalize=False, num_retry=1)
                    self._bus.write("Goal_Position", name, raw_step, normalize=False, num_retry=1)
                motor.target_position = max(0, min(4095, motor.position + value))
                message = f"ID {motor_id} relative step set to {value}"

            motor.mode_command = value
            motor.acceleration = acceleration
            motor.torque_limit = torque_limit
            if motor.operating_mode == 3:
                motor.velocity_limit = velocity
            self._set_message(message, "success")
            return motor.as_dict(selected=motor_id == self._selected_id)

    def apply_live_parameters(
        self,
        motor_id: int,
        *,
        velocity: int,
        acceleration: int,
        torque_limit: int,
    ) -> dict[str, Any]:
        with self._lock:
            motor = self._require_motor(motor_id)
            velocity, acceleration, torque_limit = int(velocity), int(acceleration), int(torque_limit)
            if not 0 <= velocity <= 3073:
                raise ControlError("Velocity must be 0–3073 raw units")
            if not 0 <= acceleration <= 254:
                raise ControlError("Acceleration must be 0–254")
            if not 0 <= torque_limit <= 1000:
                raise ControlError("Torque limit must be 0–1000")
            if not self.simulation:
                name = self._motor_name(motor_id)
                self._bus.write("Acceleration", name, acceleration, normalize=False, num_retry=1)
                if motor.operating_mode in (0, 3):
                    self._bus.write("Goal_Velocity", name, velocity, normalize=False, num_retry=1)
                self._bus.write("Torque_Limit", name, torque_limit, normalize=False, num_retry=1)
            motor.velocity_limit = velocity
            motor.acceleration = acceleration
            motor.torque_limit = torque_limit
            self._set_message(f"Live motion limits applied to ID {motor_id}", "success")
            return motor.as_dict(selected=motor_id == self._selected_id)

    def write_persistent_config(
        self, motor_id: int, values: dict[str, int], confirmation: str
    ) -> dict[str, Any]:
        with self._lock:
            motor = self._require_motor(motor_id)
            if motor.torque_enabled:
                raise ControlError("Release torque before writing persistent EEPROM settings")
            if confirmation != PERSISTENT_CONFIG_CONFIRMATION:
                raise ControlError(
                    "Persistent writes require the exact confirmation "
                    f"{PERSISTENT_CONFIG_CONFIRMATION}"
                )
            clean = self._validate_persistent_values_locked(motor, values)
            self._write_persistent_values_locked(motor, clean)
            self._set_message(f"Verified persistent configuration on ID {motor_id}", "success")
            return deepcopy(motor.config)

    def write_all_persistent_config(
        self, values: dict[str, int], confirmation: str
    ) -> dict[str, Any]:
        """Write shared EEPROM values only after every motor passes preflight."""
        with self._lock:
            if confirmation != GROUP_PERSISTENT_CONFIG_CONFIRMATION:
                raise ControlError(
                    "Bus persistent writes require the exact confirmation "
                    f"{GROUP_PERSISTENT_CONFIG_CONFIRMATION}"
                )
            if not self._connected or not self._motors:
                raise ControlError("No servo bus is connected")

            motors = sorted(self._motors.values(), key=lambda motor: motor.id)
            offline = [motor.id for motor in motors if not motor.online]
            armed = [motor.id for motor in motors if motor.torque_enabled]
            if offline:
                raise ControlError(
                    "Bus EEPROM write blocked by offline ID(s): "
                    + ", ".join(map(str, offline))
                )
            if armed:
                raise ControlError(
                    "Release torque before the bus EEPROM write; armed ID(s): "
                    + ", ".join(map(str, armed))
                )

            # Validate the shared values against every motor's current config
            # before the first device is unlocked or written.
            plans = [
                (motor, self._validate_persistent_values_locked(motor, values))
                for motor in motors
            ]

            completed: list[int] = []
            for motor, clean in plans:
                try:
                    self._write_persistent_values_locked(motor, clean)
                    completed.append(motor.id)
                except Exception as exc:
                    completed_label = ", ".join(map(str, completed)) or "none"
                    self._set_message(
                        f"Bus EEPROM write stopped at ID {motor.id}; verified earlier IDs: {completed_label}",
                        "danger",
                    )
                    detail = str(exc) if isinstance(exc, ControlError) else self._compact_error(exc)
                    raise ControlError(
                        f"Bus EEPROM write stopped at ID {motor.id}: {detail}. "
                        f"Earlier verified IDs: {completed_label}; the current ID may be partially updated."
                    ) from exc

            fields = ", ".join(clean.keys())
            self._set_message(
                f"Verified {fields} on all {len(motors)} motors", "success"
            )
            return self.snapshot()

    def _validate_persistent_values_locked(
        self, motor: MotorState, values: dict[str, int]
    ) -> dict[str, int]:
        unknown = set(values) - set(PERSISTENT_PARAMETERS)
        if unknown:
            raise ControlError(
                f"Unsupported persistent parameter(s): {', '.join(sorted(unknown))}"
            )
        if not values:
            raise ControlError("No persistent settings were supplied")

        clean: dict[str, int] = {}
        for key, supplied in values.items():
            _, minimum, maximum = PERSISTENT_PARAMETERS[key]
            try:
                value = int(supplied)
            except (TypeError, ValueError) as exc:
                raise ControlError(f"{key} must be an integer") from exc
            if not minimum <= value <= maximum:
                raise ControlError(f"{key} must be {minimum}–{maximum}")
            clean[key] = value

        merged = {**motor.config, **clean}
        if int(merged.get("min_position", 0)) >= int(
            merged.get("max_position", 4095)
        ):
            raise ControlError(
                f"Minimum position must be lower than maximum position on ID {motor.id}"
            )
        if int(merged.get("min_voltage", 40)) >= int(
            merged.get("max_voltage", 140)
        ):
            raise ControlError(
                f"Minimum voltage must be lower than maximum voltage on ID {motor.id}"
            )
        return clean

    def _write_persistent_values_locked(
        self, motor: MotorState, clean: dict[str, int]
    ) -> None:
        if not self.simulation:
            name = self._motor_name(motor.id)
            self._bus.write("Lock", name, 0, normalize=False, num_retry=2)
            try:
                for key, value in clean.items():
                    register = PERSISTENT_PARAMETERS[key][0]
                    self._bus.write(register, name, value, normalize=False, num_retry=2)
                    actual = int(
                        self._bus.read(register, name, normalize=False, num_retry=2)
                    )
                    if actual != value:
                        raise ControlError(
                            f"Verification failed for {key}: wrote {value}, read {actual}"
                        )
            finally:
                self._bus.write("Lock", name, 1, normalize=False, num_retry=2)
        motor.config.update(clean)

    def capture_position(self, motor_id: int, name: str) -> dict[str, Any]:
        with self._lock:
            motor = self._require_motor(motor_id)
            self._refresh_motor_locked(motor)
            name = name.strip()[:60]
            if not name:
                raise ControlError("Give the captured position a name")
            capture = {
                "capture_id": f"{motor.model.lower()}-{motor.id}-{time.time_ns()}",
                "name": name,
                "motor_id": motor.id,
                "model": motor.model,
                "model_number": motor.model_number,
                "position": motor.position,
                "degrees": round(motor.degrees, 3),
                "created_at": _utc_now(),
            }
            self._captures.append(capture)
            self._save_captures_locked()
            self._set_message(f"Captured “{name}” at {motor.position}", "success")
            return deepcopy(capture)

    def delete_capture(self, capture_id: str) -> dict[str, Any]:
        with self._lock:
            before = len(self._captures)
            self._captures = [item for item in self._captures if item.get("capture_id") != capture_id]
            if len(self._captures) == before:
                raise ControlError("Captured position was not found")
            self._save_captures_locked()
            self._set_message("Captured position removed", "info")
            return self.snapshot()

    def move_to_capture(self, motor_id: int, capture_id: str) -> dict[str, Any]:
        with self._lock:
            capture = next((item for item in self._captures if item.get("capture_id") == capture_id), None)
            if capture is None:
                raise ControlError("Captured position was not found")
            motor = self._require_motor(motor_id)
            if capture.get("model_number") != motor.model_number:
                raise ControlError("This capture belongs to a different servo model")
            return self.command_position(motor_id, int(capture["position"]))

    def start_recording(self, label: str = "session") -> dict[str, Any]:
        with self._lock:
            if not self._connected:
                raise ControlError("Connect a servo before recording")
            if self._recording_file is not None:
                raise ControlError("A recording is already active")
            self.recordings_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.recordings_dir / f"{timestamp}-{_safe_filename(label)}.csv"
            self._recording_file = path.open("w", newline="", encoding="utf-8")
            fields = [
                "timestamp_utc", "elapsed_s", "motor_id", "model", "position_raw",
                "position_deg", "target_raw", "velocity_raw", "velocity_rpm_estimate",
                "load_raw", "load_percent", "voltage_v", "temperature_c", "current_raw",
                "current_a", "torque_constant_kgcm_per_a", "estimated_torque_kgcm",
                "estimated_torque_nm", "operating_mode", "mode_command", "moving",
                "status", "torque_enabled",
            ]
            self._recording_writer = csv.DictWriter(self._recording_file, fieldnames=fields)
            self._recording_writer.writeheader()
            self._recording_file.flush()
            self._recording_path = path
            self._recording_started = _utc_now()
            self._recording_monotonic = time.monotonic()
            self._set_message(f"Recording {path.name}", "danger")
            return self._recording_state_locked()

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            result = self._recording_state_locked()
            self._stop_recording_locked()
            self._set_message("Recording saved", "success")
            return result

    def list_recordings(self) -> list[dict[str, Any]]:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "download": f"/api/recordings/{path.name}",
            }
            for path in sorted(self.recordings_dir.glob("*.csv"), reverse=True)
        ]

    def recording_path(self, name: str) -> Path:
        if Path(name).name != name or not name.endswith(".csv"):
            raise ControlError("Invalid recording name")
        path = self.recordings_dir / name
        if not path.is_file():
            raise ControlError("Recording not found")
        return path

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            captures = []
            if self._selected_id in self._motors:
                selected = self._motors[self._selected_id]
                captures = [
                    deepcopy(item)
                    for item in self._captures
                    if item.get("motor_id") == selected.id
                    and item.get("model_number") == selected.model_number
                ]
            return {
                "mode": "simulation" if self.simulation else "hardware",
                "connected": self._connected,
                "port": self._port,
                "baudrate": self._baudrate,
                "selected_id": self._selected_id,
                "motors": [
                    motor.as_dict(selected=motor.id == self._selected_id)
                    for motor in self._motors.values()
                ],
                "captures": captures,
                "recording": self._recording_state_locked(),
                "message": self._message,
                "message_level": self._message_level,
                "server_time": _utc_now(),
            }

    def _poll_loop(self) -> None:
        previous = time.monotonic()
        while not self._stop.wait(self.poll_interval):
            now = time.monotonic()
            dt = min(0.25, now - previous)
            previous = now
            with self._lock:
                if not self._connected:
                    continue
                for motor in self._motors.values():
                    try:
                        if self.simulation:
                            self._step_simulation_locked(motor, dt)
                        else:
                            self._refresh_motor_locked(motor)
                        self._write_record_row_locked(motor)
                    except Exception as exc:
                        motor.communication_errors += 1
                        motor.online = False
                        if motor.communication_errors == 1:
                            self._set_message(
                                f"Telemetry read failed on ID {motor.id}: {self._compact_error(exc)}",
                                "danger",
                            )

    def _step_simulation_locked(self, motor: MotorState, dt: float) -> None:
        previous = motor.position
        if motor.torque_enabled:
            if motor.operating_mode in (0, 3):
                delta = motor.target_position - motor.position
                max_step = max(1, int(max(20, motor.velocity_limit * 0.65) * dt))
                step = max(-max_step, min(max_step, delta))
                motor.position += step
                motor.velocity_raw = int(step / max(dt, 0.001) / 50)
                motor.load_raw = min(950, int(abs(delta) * 0.08 + abs(step) * 0.7))
                if motor.operating_mode == 3 and step == 0:
                    motor.mode_command = 0
            elif motor.operating_mode == 1:
                motor.velocity_raw = motor.mode_command
                step = int(motor.mode_command * 0.65 * dt)
                motor.position = (motor.position + step) % 4096
                motor.load_raw = min(850, int(abs(motor.mode_command) * 0.08))
            else:
                motor.velocity_raw = int(motor.mode_command * 2.6)
                step = int(motor.mode_command * 1.7 * dt)
                motor.position = (motor.position + step) % 4096
                motor.load_raw = min(950, int(abs(motor.mode_command) * 0.7))
            motor.moving = step != 0
            stall_current = motor.stall_current_a or 3.0
            simulated_amps = 0.03 + stall_current * motor.load_raw / 1000.0
            motor.current_raw = max(0, round(simulated_amps / CURRENT_SCALE_A_PER_LSB))
            motor.temperature = min(68, max(24, motor.temperature + (1 if motor.load_raw > 700 else 0)))
        else:
            motor.velocity_raw = 0
            motor.load_raw = 0
            motor.current_raw = 0
            motor.moving = False
            motor.temperature = max(24, motor.temperature - (1 if motor.temperature > 25 else 0))
        motor.position = max(0, min(4095, motor.position))
        motor.degrees = motor.position * 360.0 / 4095.0
        motor.velocity_rpm_estimate = motor.velocity_raw * 0.732
        motor.load_raw, motor.load_percent = _decode_load_effort(motor.load_raw)
        motor.voltage = round(12.0 + math.sin(time.monotonic() / 3 + motor.id) * 0.08, 2)
        motor.status = 0
        motor.status_labels = []
        motor.online = True
        motor.last_seen = _utc_now()
        if previous == motor.position and motor.position == motor.target_position:
            motor.moving = False

    def _refresh_motor_locked(self, motor: MotorState) -> None:
        if self.simulation:
            return
        if self._bus is None:
            raise ControlError("Hardware bus is not connected")
        data, comm, error = self._bus.packet_handler.readTxRx(
            self._bus.port_handler, motor.id, 56, 15
        )
        if not self._bus._is_comm_success(comm):
            raise ConnectionError(self._bus.packet_handler.getTxRxResult(comm))
        if error:
            motor.status = int(error)
        if len(data) < 15:
            raise ConnectionError(f"Short telemetry packet ({len(data)} bytes)")
        motor.position = _word(data, 0)
        motor.degrees = motor.position * 360.0 / 4095.0
        motor.velocity_raw = _decode_sign_magnitude(_word(data, 2), 15)
        motor.velocity_rpm_estimate = motor.velocity_raw * 0.732
        motor.load_raw, motor.load_percent = _decode_load_effort(_word(data, 4))
        motor.voltage = int(data[6]) / 10.0
        motor.temperature = int(data[7])
        motor.status = int(data[9])
        motor.moving = bool(data[10])
        motor.current_raw = _word(data, 13)
        motor.status_labels = [label for bit, label in STATUS_BITS.items() if motor.status & bit]
        motor.online = True
        motor.communication_errors = 0
        motor.last_seen = _utc_now()

    def _read_config_locked(self, motor: MotorState) -> None:
        if self.simulation:
            motor.config = self._default_config()
            return
        name = self._motor_name(motor.id)
        for key, (register, _, _) in PERSISTENT_PARAMETERS.items():
            try:
                motor.config[key] = int(self._bus.read(register, name, normalize=False, num_retry=1))
            except Exception:
                continue
        try:
            motor.operating_mode = int(
                self._bus.read("Operating_Mode", name, normalize=False, num_retry=1)
            )
        except Exception:
            motor.operating_mode = -1
        for attr, register in (
            ("acceleration", "Acceleration"),
            ("velocity_limit", "Goal_Velocity"),
            ("torque_limit", "Torque_Limit"),
        ):
            try:
                value = int(self._bus.read(register, name, normalize=False, num_retry=1))
                if value == 0 and attr in ("acceleration", "velocity_limit"):
                    continue
                setattr(motor, attr, value)
            except Exception:
                pass
        try:
            motor.torque_enabled = bool(
                self._bus.read("Torque_Enable", name, normalize=False, num_retry=1)
            )
        except Exception:
            motor.torque_enabled = False

    def _write_record_row_locked(self, motor: MotorState) -> None:
        if self._recording_writer is None or self._recording_file is None:
            return
        telemetry = motor.as_dict()
        self._recording_writer.writerow(
            {
                "timestamp_utc": _utc_now(),
                "elapsed_s": f"{time.monotonic() - self._recording_monotonic:.4f}",
                "motor_id": motor.id,
                "model": motor.model,
                "position_raw": motor.position,
                "position_deg": f"{motor.degrees:.4f}",
                "target_raw": motor.target_position,
                "velocity_raw": motor.velocity_raw,
                "velocity_rpm_estimate": f"{motor.velocity_rpm_estimate:.4f}",
                "load_raw": motor.load_raw,
                "load_percent": f"{motor.load_percent:.3f}",
                "voltage_v": f"{motor.voltage:.2f}",
                "temperature_c": motor.temperature,
                "current_raw": motor.current_raw,
                "current_a": f"{telemetry['current_a']:.4f}",
                "torque_constant_kgcm_per_a": motor.torque_constant_kgcm_per_a,
                "estimated_torque_kgcm": telemetry["estimated_torque_kgcm"],
                "estimated_torque_nm": telemetry["estimated_torque_nm"],
                "operating_mode": motor.operating_mode,
                "mode_command": motor.mode_command,
                "moving": int(motor.moving),
                "status": motor.status,
                "torque_enabled": int(motor.torque_enabled),
            }
        )
        self._recording_file.flush()

    def _stop_recording_locked(self) -> None:
        if self._recording_file is not None:
            self._recording_file.flush()
            self._recording_file.close()
        self._recording_file = None
        self._recording_writer = None
        self._recording_path = None
        self._recording_started = None

    def _recording_state_locked(self) -> dict[str, Any]:
        active = self._recording_file is not None
        return {
            "active": active,
            "name": self._recording_path.name if self._recording_path else None,
            "started_at": self._recording_started,
            "download": f"/api/recordings/{self._recording_path.name}" if self._recording_path else None,
        }

    def _load_captures(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.captures_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_captures_locked(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.captures_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._captures, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.captures_file)

    def _new_motor_state(self, motor_id: int, model_number: int) -> MotorState:
        profile = SUPPORTED_MODELS[model_number]
        return MotorState(
            id=motor_id,
            model_number=model_number,
            model=profile["model"],
            sdk_model=profile["sdk_model"],
            variant=profile["variant"],
            datasheet=profile["datasheet"],
            stall_torque_kgcm=profile["stall_torque_kgcm"],
            rated_torque_kgcm=profile["rated_torque_kgcm"],
            stall_current_a=profile["stall_current_a"],
            torque_constant_kgcm_per_a=profile["torque_constant_kgcm_per_a"],
            operating_voltage=profile["operating_voltage"],
            operating_temp=profile["operating_temp"],
        )

    def _motor_description(self, motor_id: int, model_number: int) -> dict[str, Any]:
        profile = SUPPORTED_MODELS.get(model_number)
        return {
            "id": motor_id,
            "model_number": model_number,
            "model": profile["model"] if profile else "UNKNOWN",
            "variant": profile["variant"] if profile else "Unsupported model",
            "supported": profile is not None,
        }

    def _require_motor(self, motor_id: int) -> MotorState:
        if not self._connected:
            raise ControlError("No servo bus is connected")
        try:
            return self._motors[int(motor_id)]
        except (KeyError, ValueError) as exc:
            raise ControlError(f"Servo ID {motor_id} is not on the connected bus") from exc

    def _set_message(self, message: str, level: str) -> None:
        self._message = message
        self._message_level = level

    @staticmethod
    def _motor_name(motor_id: int) -> str:
        return f"servo_{motor_id}"

    @staticmethod
    def _default_config() -> dict[str, int]:
        return {
            "p_gain": 32,
            "d_gain": 0,
            "i_gain": 0,
            "min_position": 0,
            "max_position": 4095,
            "max_temperature": 70,
            "min_voltage": 40,
            "max_voltage": 140,
        }

    @staticmethod
    def _compact_error(exc: BaseException) -> str:
        text = " ".join(str(exc).split())
        return text or exc.__class__.__name__

    @staticmethod
    def _import_feetech():
        try:
            from lerobot.motors.feetech import FeetechMotorsBus
            from lerobot.motors.motors_bus import Motor, MotorNormMode
        except Exception as exc:
            raise ControlError(
                "LeRobot Feetech support is unavailable. Start with ./run.py so it can select the local lerobot environment."
            ) from exc
        return FeetechMotorsBus, Motor, MotorNormMode
