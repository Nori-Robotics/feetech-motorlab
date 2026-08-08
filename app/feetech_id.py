from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


DEFAULT_BAUDRATE = 1_000_000
MIN_MOTOR_ID = 1
MAX_MOTOR_ID = 253
MOTOR_NAME = "motor"


@dataclass(frozen=True)
class MotorProfile:
    name: str
    model_number: int
    lerobot_model: str


MOTOR_PROFILES = {
    "sts3215": MotorProfile("sts3215", 777, "sts3215"),
    # STS3095 uses the standard STS control table in LeRobot.
    "sts3095": MotorProfile("sts3095", 2569, "sts_series"),
    "sts3250": MotorProfile("sts3250", 2825, "sts3250"),
}
MODEL_NUMBER_TO_PROFILE = {
    profile.model_number: profile for profile in MOTOR_PROFILES.values()
}


@dataclass(frozen=True)
class Response:
    port: str
    baudrate: int
    motor_id: int
    model_number: int


@dataclass
class TargetPlan:
    """Return the target ID for each newly connected motor."""

    mode: str
    current: int
    start: int
    end: int
    loop: bool = False

    @classmethod
    def single(cls, target_id: int) -> "TargetPlan":
        return cls("single", target_id, target_id, target_id, True)

    @classmethod
    def sequential(cls, start: int, end: int, loop: bool) -> "TargetPlan":
        return cls("sequential", start, start, end, loop)

    @property
    def target(self) -> int:
        return self.current

    def advance(self) -> bool:
        """Advance after a verified motor; return False when the range is done."""
        if self.mode == "single":
            return True
        if self.current < self.end:
            self.current += 1
            return True
        if self.loop:
            self.current = self.start
            return True
        return False


@dataclass(frozen=True)
class FeetechApi:
    bus_type: Any
    motor_type: Any
    norm_mode_type: Any


def validate_motor_id(value: int, name: str) -> int:
    if not MIN_MOTOR_ID <= value <= MAX_MOTOR_ID:
        raise ValueError(f"{name} must be {MIN_MOTOR_ID}–{MAX_MOTOR_ID}")
    return value


def close_bus(bus: Any) -> None:
    handler = getattr(bus, "port_handler", None)
    if handler is not None and getattr(handler, "is_open", False):
        handler.closePort()


def scan_once(api: FeetechApi, port: str, baudrate: int) -> list[Response]:
    bus = api.bus_type(port, {})
    try:
        bus._connect(handshake=False)
        bus.set_baudrate(baudrate)
        found = bus.broadcast_ping(num_retry=1, raise_on_error=False) or {}
        return [
            Response(port, baudrate, int(motor_id), int(model_number))
            for motor_id, model_number in sorted(found.items())
        ]
    finally:
        close_bus(bus)


def describe_response(response: Response) -> str:
    profile = MODEL_NUMBER_TO_PROFILE.get(response.model_number)
    model = profile.name.upper() if profile else f"UNKNOWN MODEL {response.model_number}"
    return f"{model} ID {response.motor_id}"


def require_one_supported_response(
    responses: Sequence[Response], requested_model: str
) -> tuple[Response, MotorProfile]:
    if len(responses) != 1:
        if not responses:
            raise RuntimeError("no motor detected")
        ids = ", ".join(str(response.motor_id) for response in responses)
        raise RuntimeError(
            f"multiple responders detected (IDs {ids}); unplug all but one — no write issued"
        )
    response = responses[0]
    profile = MODEL_NUMBER_TO_PROFILE.get(response.model_number)
    if profile is None:
        raise RuntimeError(
            f"unsupported model number {response.model_number}; no write issued"
        )
    if requested_model != "auto" and profile.name != requested_model:
        raise RuntimeError(
            f"expected {requested_model.upper()}, found {profile.name.upper()}; no write issued"
        )
    return response, profile


def verify_ping(api: FeetechApi, response: Response, profile: MotorProfile) -> None:
    bus = api.bus_type(
        port=response.port,
        motors={
            MOTOR_NAME: api.motor_type(
                id=response.motor_id,
                model=profile.lerobot_model,
                norm_mode=api.norm_mode_type.RANGE_M100_100,
            )
        },
    )
    try:
        bus._connect(handshake=False)
        bus.set_baudrate(response.baudrate)
        actual = int(bus.ping(response.motor_id, num_retry=2, raise_on_error=True))
    finally:
        close_bus(bus)
    if actual != profile.model_number:
        raise RuntimeError(
            f"ping model mismatch: expected {profile.model_number}, received {actual}"
        )


def write_id(
    api: FeetechApi,
    response: Response,
    profile: MotorProfile,
    target_id: int,
) -> None:
    bus = api.bus_type(
        port=response.port,
        motors={
            MOTOR_NAME: api.motor_type(
                id=target_id,
                model=profile.lerobot_model,
                norm_mode=api.norm_mode_type.RANGE_M100_100,
            )
        },
    )
    try:
        bus.setup_motor(
            MOTOR_NAME,
            initial_baudrate=response.baudrate,
            initial_id=response.motor_id,
        )
        actual = int(bus.ping(target_id, num_retry=2, raise_on_error=True))
    finally:
        close_bus(bus)
    if actual != profile.model_number:
        raise RuntimeError(
            f"post-write ping model mismatch: expected {profile.model_number}, received {actual}"
        )


def program_and_verify(
    api: FeetechApi,
    response: Response,
    profile: MotorProfile,
    target_id: int,
    *,
    emit: Callable[[str], None] = print,
) -> Response:
    """Write a new ID when needed, then verify ping and an exclusive final scan."""
    validate_motor_id(target_id, "target ID")
    if response.motor_id == target_id:
        emit(f"ID already {target_id}; skipping EEPROM rewrite and verifying only.")
        verify_ping(api, response, profile)
        verification_baud = response.baudrate
    else:
        emit(f"Writing ID {response.motor_id} → {target_id}; torque will be disabled.")
        write_id(api, response, profile, target_id)
        verification_baud = DEFAULT_BAUDRATE

    post = scan_once(api, response.port, verification_baud)
    if len(post) != 1:
        seen = ", ".join(describe_response(item) for item in post) or "none"
        raise RuntimeError(
            f"final scan expected one motor at {verification_baud} baud; found {seen}"
        )
    verified = post[0]
    if verified.motor_id != target_id or verified.model_number != profile.model_number:
        raise RuntimeError(
            f"final scan mismatch: found {describe_response(verified)}, "
            f"expected {profile.name.upper()} ID {target_id}"
        )
    return verified
