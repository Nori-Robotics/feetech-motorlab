from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

DEFAULT_BAUDRATE = 1_000_000

# Feetech SMS/STS protocol-0 register map shared by the supported STS3215,
# STS3250, and STS3095 models.
CONTROL_TABLE: dict[str, tuple[int, int]] = {
    "Model_Number": (3, 2),
    "ID": (5, 1),
    "Baud_Rate": (6, 1),
    "Return_Delay_Time": (7, 1),
    "Response_Status_Level": (8, 1),
    "Min_Position_Limit": (9, 2),
    "Max_Position_Limit": (11, 2),
    "Max_Temperature_Limit": (13, 1),
    "Max_Voltage_Limit": (14, 1),
    "Min_Voltage_Limit": (15, 1),
    "Max_Torque_Limit": (16, 2),
    "Phase": (18, 1),
    "Unloading_Condition": (19, 1),
    "LED_Alarm_Condition": (20, 1),
    "P_Coefficient": (21, 1),
    "D_Coefficient": (22, 1),
    "I_Coefficient": (23, 1),
    "Minimum_Startup_Force": (24, 2),
    "CW_Dead_Zone": (26, 1),
    "CCW_Dead_Zone": (27, 1),
    "Protection_Current": (28, 2),
    "Angular_Resolution": (30, 1),
    "Homing_Offset": (31, 2),
    "Operating_Mode": (33, 1),
    "Protective_Torque": (34, 1),
    "Protection_Time": (35, 1),
    "Overload_Torque": (36, 1),
    "Torque_Enable": (40, 1),
    "Acceleration": (41, 1),
    "Goal_Position": (42, 2),
    "Goal_Time": (44, 2),
    "Goal_Velocity": (46, 2),
    "Torque_Limit": (48, 2),
    "Lock": (55, 1),
    "Present_Position": (56, 2),
    "Present_Velocity": (58, 2),
    "Present_Load": (60, 2),
    "Present_Voltage": (62, 1),
    "Present_Temperature": (63, 1),
    "Status": (65, 1),
    "Moving": (66, 1),
    "Present_Current": (69, 2),
}

BAUDRATE_VALUES = {
    1_000_000: 0,
    500_000: 1,
    250_000: 2,
    128_000: 3,
    115_200: 4,
    57_600: 5,
    38_400: 6,
    19_200: 7,
}

SIGNED_REGISTERS = {
    "Homing_Offset": 11,
    "Goal_Velocity": 15,
    "Present_Velocity": 15,
}


class MotorNormMode(str, Enum):
    """Compatibility values retained for MotorLab's motor descriptions."""

    RANGE_M100_100 = "range_m100_100"
    DEGREES = "degrees"


@dataclass(frozen=True)
class Motor:
    id: int
    model: str
    norm_mode: MotorNormMode


def _encode_sign_magnitude(value: int, sign_bit: int) -> int:
    magnitude = abs(int(value))
    maximum = (1 << sign_bit) - 1
    if magnitude > maximum:
        raise ValueError(f"Magnitude {magnitude} exceeds {maximum}")
    return magnitude | ((1 << sign_bit) if value < 0 else 0)


def _decode_sign_magnitude(value: int, sign_bit: int) -> int:
    sign_mask = 1 << sign_bit
    magnitude = int(value) & (sign_mask - 1)
    return -magnitude if int(value) & sign_mask else magnitude


class FeetechMotorsBus:
    """Small MotorLab-specific wrapper around ``feetech-servo-sdk``.

    It intentionally implements only the protocol-0 operations used by this
    application: discovery, ping, register reads/writes, torque state, and the
    guarded single-motor ID setup flow.
    """

    default_baudrate = DEFAULT_BAUDRATE

    def __init__(self, port: str, motors: dict[str, Motor]):
        try:
            import scservo_sdk as scs
        except ImportError as exc:  # pragma: no cover - exercised by launcher
            raise RuntimeError("feetech-servo-sdk is not installed") from exc

        self.scs = scs
        self.port = port
        self.motors = dict(motors)
        self.transaction_lock = threading.RLock()
        self.port_handler = scs.PortHandler(port)
        self.packet_handler = scs.PacketHandler(0)
        self._comm_success = scs.COMM_SUCCESS
        self._no_error = 0

        # The PyPI SDK's default timeout includes a fixed 32 ms latency twice,
        # which makes every missing motor painfully slow. This matches the
        # corrected upstream calculation while retaining a conservative tail.
        def set_packet_timeout(handler: Any, packet_length: int) -> None:
            handler.packet_start_time = handler.getCurrentTime()
            handler.packet_timeout = (
                handler.tx_time_per_byte * packet_length + handler.tx_time_per_byte * 3.0 + 50.0
            )

        self.port_handler.setPacketTimeout = set_packet_timeout.__get__(
            self.port_handler, type(self.port_handler)
        )

    @property
    def is_connected(self) -> bool:
        return bool(self.port_handler.is_open)

    def register_motor(self, name: str, motor: Motor) -> None:
        self.motors[name] = motor

    def unregister_motor(self, name: str) -> None:
        self.motors.pop(name, None)

    def connect(self, handshake: bool = True) -> None:
        with self.transaction_lock:
            if self.is_connected:
                raise ConnectionError(f"Serial port is already open: {self.port}")
            self._connect(handshake=handshake)

    def _connect(self, handshake: bool = True) -> None:
        with self.transaction_lock:
            try:
                if not self.port_handler.openPort():
                    raise OSError(f"Failed to open serial port {self.port}")
                if handshake:
                    for motor in self.motors.values():
                        if self.ping(motor.id, raise_on_error=True) is None:
                            raise ConnectionError(f"Motor ID {motor.id} did not respond")
            except Exception:
                if self.port_handler.is_open:
                    self.port_handler.closePort()
                raise

    def set_baudrate(self, baudrate: int) -> None:
        with self.transaction_lock:
            baudrate = int(baudrate)
            if self.port_handler.getBaudRate() == baudrate:
                return
            if not self.port_handler.setBaudRate(baudrate):
                raise RuntimeError(f"Unsupported or unavailable baud rate: {baudrate}")
            if self.port_handler.getBaudRate() != baudrate:
                raise RuntimeError(f"Failed to set baud rate: {baudrate}")

    def _motor_id(self, motor: str | int) -> int:
        if isinstance(motor, int):
            return motor
        try:
            return int(self.motors[motor].id)
        except KeyError as exc:
            raise KeyError(f"Unknown motor name: {motor}") from exc

    @staticmethod
    def _register(data_name: str) -> tuple[int, int]:
        try:
            return CONTROL_TABLE[data_name]
        except KeyError as exc:
            raise KeyError(f"Unknown Feetech register: {data_name}") from exc

    def _is_comm_success(self, comm: int) -> bool:
        return comm == self._comm_success

    def _raise_transaction_error(self, action: str, comm: int, error: int) -> None:
        if not self._is_comm_success(comm):
            raise ConnectionError(f"{action}: {self.packet_handler.getTxRxResult(comm)}")
        if error != self._no_error:
            raise RuntimeError(f"{action}: {self.packet_handler.getRxPacketError(error)}")

    def ping(
        self,
        motor: str | int,
        num_retry: int = 0,
        raise_on_error: bool = False,
    ) -> int | None:
        motor_id = self._motor_id(motor)
        with self.transaction_lock:
            model_number = 0
            comm = -1
            error = 0
            for _ in range(1 + num_retry):
                model_number, comm, error = self.packet_handler.ping(self.port_handler, motor_id)
                if self._is_comm_success(comm) and error == self._no_error:
                    return int(model_number)
            if raise_on_error:
                self._raise_transaction_error(f"Ping failed on ID {motor_id}", comm, error)
            return None

    def read(
        self,
        data_name: str,
        motor: str,
        *,
        normalize: bool = True,
        num_retry: int = 0,
    ) -> int:
        del normalize  # MotorLab always requests raw values.
        address, length = self._register(data_name)
        value = self._read_raw(address, length, self._motor_id(motor), num_retry)
        sign_bit = SIGNED_REGISTERS.get(data_name)
        return _decode_sign_magnitude(value, sign_bit) if sign_bit is not None else value

    def _read_raw(self, address: int, length: int, motor_id: int, num_retry: int = 0) -> int:
        with self.transaction_lock:
            read_functions = {
                1: self.packet_handler.read1ByteTxRx,
                2: self.packet_handler.read2ByteTxRx,
                4: self.packet_handler.read4ByteTxRx,
            }
            try:
                read_function = read_functions[length]
            except KeyError as exc:
                raise ValueError(f"Unsupported register width: {length}") from exc

            value = 0
            comm = -1
            error = 0
            for _ in range(1 + num_retry):
                value, comm, error = read_function(self.port_handler, int(motor_id), int(address))
                if self._is_comm_success(comm) and error == self._no_error:
                    return int(value)
            self._raise_transaction_error(
                f"Read failed at address {address} on ID {motor_id}", comm, error
            )
            raise AssertionError("unreachable")

    def write(
        self,
        data_name: str,
        motor: str,
        value: int,
        *,
        normalize: bool = True,
        num_retry: int = 0,
    ) -> None:
        del normalize  # MotorLab always supplies raw values.
        sign_bit = SIGNED_REGISTERS.get(data_name)
        encoded = (
            _encode_sign_magnitude(int(value), sign_bit) if sign_bit is not None else int(value)
        )
        address, length = self._register(data_name)
        self._write_raw(address, length, self._motor_id(motor), encoded, num_retry)

    def _write_raw(
        self,
        address: int,
        length: int,
        motor_id: int,
        value: int,
        num_retry: int = 0,
    ) -> None:
        with self.transaction_lock:
            maximum = (1 << (length * 8)) - 1
            if not 0 <= int(value) <= maximum:
                raise ValueError(f"Value {value} does not fit in a {length}-byte register")
            data = [(int(value) >> (8 * index)) & 0xFF for index in range(length)]
            comm = -1
            error = 0
            for _ in range(1 + num_retry):
                comm, error = self.packet_handler.writeTxRx(
                    self.port_handler, int(motor_id), int(address), length, data
                )
                if self._is_comm_success(comm) and error == self._no_error:
                    return
            self._raise_transaction_error(
                f"Write failed at address {address} on ID {motor_id}", comm, error
            )

    def disable_torque(self, motors: str | list[str] | None = None, num_retry: int = 0) -> None:
        for name in self._motor_names(motors):
            self.write("Torque_Enable", name, 0, normalize=False, num_retry=num_retry)
            self.write("Lock", name, 0, normalize=False, num_retry=num_retry)

    def enable_torque(self, motors: str | list[str] | None = None, num_retry: int = 0) -> None:
        for name in self._motor_names(motors):
            self.write("Torque_Enable", name, 1, normalize=False, num_retry=num_retry)
            self.write("Lock", name, 1, normalize=False, num_retry=num_retry)

    def _motor_names(self, motors: str | list[str] | None) -> list[str]:
        if motors is None:
            return list(self.motors)
        if isinstance(motors, str):
            return [motors]
        return list(motors)

    def setup_motor(
        self,
        motor: str,
        initial_baudrate: int | None = None,
        initial_id: int | None = None,
    ) -> None:
        """Disable torque, assign one motor's ID, and restore default baud."""
        if motor not in self.motors:
            raise KeyError(f"Unknown motor name: {motor}")
        if initial_baudrate is None or initial_id is None:
            raise ValueError("MotorLab setup requires the discovered baud rate and ID")
        if not self.is_connected:
            self._connect(handshake=False)

        target_id = int(self.motors[motor].id)
        self.set_baudrate(initial_baudrate)
        self._write_raw(*CONTROL_TABLE["Torque_Enable"], initial_id, 0, num_retry=2)
        self._write_raw(*CONTROL_TABLE["Lock"], initial_id, 0, num_retry=2)
        self._write_raw(*CONTROL_TABLE["ID"], initial_id, target_id, num_retry=2)
        self._write_raw(
            *CONTROL_TABLE["Baud_Rate"],
            target_id,
            BAUDRATE_VALUES[self.default_baudrate],
            num_retry=2,
        )
        self.set_baudrate(self.default_baudrate)

    def broadcast_ping(
        self, num_retry: int = 0, raise_on_error: bool = False
    ) -> dict[int, int] | None:
        with self.transaction_lock:
            comm = -1
            for _ in range(1 + num_retry):
                statuses, comm = self._broadcast_status_packets()
                if self._is_comm_success(comm):
                    models: dict[int, int] = {}
                    for motor_id in sorted(statuses):
                        try:
                            models[motor_id] = self._read_raw(3, 2, motor_id, num_retry=1)
                        except Exception:
                            if raise_on_error:
                                raise
                    return models
            if comm == self.scs.COMM_RX_TIMEOUT:
                return {}
            if raise_on_error:
                raise ConnectionError(self.packet_handler.getTxRxResult(comm))
            return None

    def _broadcast_status_packets(self) -> tuple[dict[int, int], int]:
        with self.transaction_lock:
            scs = self.scs
            status_length = 6
            max_motor_id = scs.BROADCAST_ID - 1
            wait_length = status_length * max_motor_id
            txpacket = [0] * status_length
            txpacket[scs.PKT_ID] = scs.BROADCAST_ID
            txpacket[scs.PKT_LENGTH] = 2
            txpacket[scs.PKT_INSTRUCTION] = scs.INST_PING

            comm = self.packet_handler.txPacket(self.port_handler, txpacket)
            if comm != scs.COMM_SUCCESS:
                self.port_handler.is_using = False
                return {}, comm

            byte_time_ms = (1000.0 / self.port_handler.getBaudRate()) * 10.0
            timeout_ms = (wait_length * byte_time_ms) + (3.0 * max_motor_id) + 16.0
            self.port_handler.setPacketTimeoutMillis(timeout_ms)

            received: list[int] = []
            while not self.port_handler.isPacketTimeout() and len(received) < wait_length:
                chunk = self.port_handler.readPort(wait_length - len(received))
                if chunk:
                    received.extend(chunk)
                else:
                    time.sleep(0.001)
            self.port_handler.is_using = False

            if not received:
                return {}, scs.COMM_RX_TIMEOUT

            statuses: dict[int, int] = {}
            while len(received) >= status_length:
                header = next(
                    (
                        index
                        for index in range(len(received) - 1)
                        if received[index] == 0xFF and received[index + 1] == 0xFF
                    ),
                    None,
                )
                if header is None:
                    break
                if header:
                    del received[:header]
                if len(received) < status_length:
                    break
                packet = received[:status_length]
                checksum = ~(sum(packet[2:-1])) & 0xFF
                if packet[-1] == checksum:
                    motor_id = int(packet[scs.PKT_ID])
                    if 0 <= motor_id < scs.BROADCAST_ID:
                        statuses[motor_id] = int(packet[scs.PKT_ERROR])
                    del received[:status_length]
                else:
                    del received[:2]

            return statuses, scs.COMM_SUCCESS if statuses else scs.COMM_RX_CORRUPT
