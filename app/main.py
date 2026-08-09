from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .controller import DEFAULT_BAUDRATE, ROOT, BenchController, ControlError
from .id_station import IdStation, IdStationConfig, IdStationError

LOGGER = logging.getLogger(__name__)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self' ws: wss:; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data:; "
        "object-src 'none'; script-src 'self'; style-src 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}


def websocket_origin_matches_host(origin: str | None, host: str | None) -> bool:
    """Allow browser WebSockets only from the page that served MotorLab."""
    if origin is None:
        return True
    if not host:
        return False
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == host.casefold()


class ConnectionRequest(BaseModel):
    port: str
    baudrate: int = DEFAULT_BAUDRATE


class TorqueRequest(BaseModel):
    enabled: bool


class ModeRequest(BaseModel):
    mode: int = Field(ge=0, le=3)


class PositionRequest(BaseModel):
    position: int = Field(ge=0, le=4095)
    velocity: int | None = Field(default=None, ge=0, le=3073)
    acceleration: int | None = Field(default=None, ge=0, le=254)
    torque_limit: int | None = Field(default=None, ge=0, le=1000)


class LiveParametersRequest(BaseModel):
    velocity: int = Field(ge=0, le=3073)
    acceleration: int = Field(ge=0, le=254)
    torque_limit: int = Field(ge=0, le=1000)


class ModeCommandRequest(BaseModel):
    value: int = Field(ge=-4095, le=4095)
    velocity: int = Field(ge=0, le=3073)
    acceleration: int = Field(ge=0, le=254)
    torque_limit: int = Field(ge=0, le=1000)


class JogRequest(BaseModel):
    delta: int = Field(ge=-2048, le=2048)
    velocity: int = Field(ge=0, le=3073)
    acceleration: int = Field(ge=0, le=254)
    torque_limit: int = Field(ge=0, le=1000)


class GroupPositionRequest(BaseModel):
    position: int = Field(ge=0, le=4095)
    velocity: int = Field(ge=0, le=3073)
    acceleration: int = Field(ge=0, le=254)
    torque_limit: int = Field(ge=0, le=1000)


class PersistentConfigRequest(BaseModel):
    values: dict[str, int]
    confirmation: str


class EncoderCenterRequest(BaseModel):
    confirmation: str


class CaptureRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class RecordingRequest(BaseModel):
    label: str = Field(default="session", max_length=60)


class IdStationStartRequest(BaseModel):
    mode: str
    port: str
    baudrate: int = Field(default=DEFAULT_BAUDRATE, gt=0)
    model: str = "auto"
    target_id: int = Field(default=1, ge=1, le=253)
    start_id: int = Field(default=1, ge=1, le=253)
    end_id: int = Field(default=7, ge=1, le=253)
    loop: bool = False
    confirmation: str


class IdStationSimulateMotorRequest(BaseModel):
    present: bool
    motor_id: int = Field(default=1, ge=1, le=253)
    model_number: int = 777


def create_app(*, simulation: bool = False, data_dir: Path | None = None) -> FastAPI:
    controller = BenchController(simulation=simulation, data_dir=data_dir or ROOT / "data")
    id_station = IdStation(simulation=simulation)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await asyncio.to_thread(id_station.close)
        await asyncio.to_thread(controller.close)

    app = FastAPI(
        title="NORI MotorLab",
        version=__version__,
        description="Browser-based workspace for STS3215, STS3250, and STS3095 servos.",
        lifespan=lifespan,
        docs_url=None,
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.controller = controller
    app.state.id_station = id_station

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        if (
            request.url.path.startswith("/api/")
            and request.method not in SAFE_HTTP_METHODS
            and request.headers.get("x-motorlab-request") != "1"
        ):
            response = JSONResponse(
                status_code=403,
                content={"detail": "State-changing requests require the MotorLab request header"},
            )
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def combined_snapshot() -> dict[str, Any]:
        snapshot = controller.snapshot()
        snapshot["id_station"] = id_station.snapshot()
        return snapshot

    async def call(method: str, *args: Any, **kwargs: Any):
        try:
            function = getattr(controller, method)
            return await asyncio.to_thread(function, *args, **kwargs)
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Controller method %s failed", method)
            raise HTTPException(
                status_code=500,
                detail="Controller operation failed; see the server log for details",
            ) from exc

    async def call_id_station(method: str, *args: Any, **kwargs: Any):
        try:
            function = getattr(id_station, method)
            return await asyncio.to_thread(function, *args, **kwargs)
        except IdStationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("ID station method %s failed", method)
            raise HTTPException(
                status_code=500,
                detail="ID station operation failed; see the server log for details",
            ) from exc

    @app.get("/api/bootstrap")
    async def bootstrap():
        return {
            "state": combined_snapshot(),
            "ports": controller.list_ports(),
            "profiles": controller.profiles(),
            "recordings": controller.list_recordings(),
            "adapter": {
                "name": "Feetech-compatible half-duplex serial adapter",
                "default_baudrate": DEFAULT_BAUDRATE,
                "power": "Use an external current-limited supply matched to the servo voltage.",
                "jumper": "Configure the adapter for host serial/USB control per its documentation.",
                "documentation": None,
            },
        }

    @app.get("/api/ports")
    async def ports():
        return await call("list_ports")

    @app.post("/api/scan")
    async def scan(request: ConnectionRequest):
        if id_station.active:
            raise HTTPException(
                status_code=400, detail="Stop the ID station before scanning the normal bus"
            )
        return await call("scan", request.port, request.baudrate)

    @app.post("/api/connect")
    async def connect(request: ConnectionRequest):
        if id_station.active:
            raise HTTPException(
                status_code=400, detail="Stop the ID station before connecting the normal bus"
            )
        return await call("connect", request.port, request.baudrate)

    @app.post("/api/disconnect")
    async def disconnect():
        return await call("disconnect")

    @app.get("/api/id-station/status")
    async def id_station_status():
        return id_station.snapshot()

    @app.post("/api/id-station/start")
    async def start_id_station(request: IdStationStartRequest):
        if controller.connected:
            raise HTTPException(
                status_code=400,
                detail="Disconnect the normal servo bus before starting the ID station",
            )
        config = IdStationConfig(
            mode=request.mode,
            port=request.port,
            baudrate=request.baudrate,
            model=request.model,
            target_id=request.target_id,
            start_id=request.start_id,
            end_id=request.end_id,
            loop=request.loop,
        )
        return await call_id_station("start", config, request.confirmation)

    @app.post("/api/id-station/stop")
    async def stop_id_station():
        return await call_id_station("stop")

    @app.post("/api/id-station/simulate-motor")
    async def simulate_id_station_motor(request: IdStationSimulateMotorRequest):
        if not simulation:
            raise HTTPException(status_code=404, detail="Simulation controls are unavailable")
        return await call_id_station(
            "simulate_motor",
            present=request.present,
            motor_id=request.motor_id,
            model_number=request.model_number,
        )

    @app.post("/api/select/{motor_id}")
    async def select_motor(motor_id: int):
        return await call("select_motor", motor_id)

    @app.post("/api/motors/{motor_id}/torque")
    async def set_torque(motor_id: int, request: TorqueRequest):
        return await call("set_torque", motor_id, request.enabled)

    @app.post("/api/group/torque")
    async def set_group_torque(request: TorqueRequest):
        return await call("set_all_torque", request.enabled)

    @app.post("/api/group/position")
    async def command_group_position(request: GroupPositionRequest):
        return await call(
            "command_all_positions",
            request.position,
            velocity=request.velocity,
            acceleration=request.acceleration,
            torque_limit=request.torque_limit,
        )

    @app.post("/api/group/jog")
    async def jog_group(request: JogRequest):
        return await call(
            "jog_all_positions",
            request.delta,
            velocity=request.velocity,
            acceleration=request.acceleration,
            torque_limit=request.torque_limit,
        )

    @app.post("/api/group/center-encoders")
    async def center_group_encoders(request: EncoderCenterRequest):
        return await call("center_all_encoder_positions", request.confirmation)

    @app.post("/api/group/persistent-config")
    async def group_persistent_config(request: PersistentConfigRequest):
        return await call("write_all_persistent_config", request.values, request.confirmation)

    @app.post("/api/motors/{motor_id}/mode")
    async def set_operating_mode(motor_id: int, request: ModeRequest):
        return await call("set_operating_mode", motor_id, request.mode)

    @app.post("/api/motors/{motor_id}/position")
    async def command_position(motor_id: int, request: PositionRequest):
        return await call(
            "command_position",
            motor_id,
            request.position,
            request.velocity,
            request.acceleration,
            request.torque_limit,
        )

    @app.post("/api/motors/{motor_id}/live-parameters")
    async def live_parameters(motor_id: int, request: LiveParametersRequest):
        return await call(
            "apply_live_parameters",
            motor_id,
            velocity=request.velocity,
            acceleration=request.acceleration,
            torque_limit=request.torque_limit,
        )

    @app.post("/api/motors/{motor_id}/mode-command")
    async def mode_command(motor_id: int, request: ModeCommandRequest):
        return await call(
            "command_mode",
            motor_id,
            request.value,
            velocity=request.velocity,
            acceleration=request.acceleration,
            torque_limit=request.torque_limit,
        )

    @app.post("/api/motors/{motor_id}/jog")
    async def jog_motor(motor_id: int, request: JogRequest):
        return await call(
            "jog_position",
            motor_id,
            request.delta,
            velocity=request.velocity,
            acceleration=request.acceleration,
            torque_limit=request.torque_limit,
        )

    @app.post("/api/motors/{motor_id}/center-encoder")
    async def center_motor_encoder(motor_id: int, request: EncoderCenterRequest):
        return await call("center_encoder_position", motor_id, request.confirmation)

    @app.post("/api/motors/{motor_id}/persistent-config")
    async def persistent_config(motor_id: int, request: PersistentConfigRequest):
        return await call("write_persistent_config", motor_id, request.values, request.confirmation)

    @app.post("/api/motors/{motor_id}/captures")
    async def capture_position(motor_id: int, request: CaptureRequest):
        return await call("capture_position", motor_id, request.name)

    @app.post("/api/motors/{motor_id}/captures/{capture_id}/go")
    async def move_to_capture(motor_id: int, capture_id: str):
        return await call("move_to_capture", motor_id, capture_id)

    @app.delete("/api/captures/{capture_id}")
    async def delete_capture(capture_id: str):
        return await call("delete_capture", capture_id)

    @app.post("/api/emergency-stop")
    async def emergency_stop():
        return await call("emergency_stop")

    @app.post("/api/recording/start")
    async def start_recording(request: RecordingRequest):
        return await call("start_recording", request.label)

    @app.post("/api/recording/stop")
    async def stop_recording():
        return await call("stop_recording")

    @app.get("/api/recordings")
    async def recordings():
        return await call("list_recordings")

    @app.get("/api/recordings/{name}")
    async def recording(name: str):
        path = await call("recording_path", name)
        return FileResponse(path, media_type="text/csv", filename=path.name)

    @app.websocket("/ws")
    async def websocket_state(websocket: WebSocket):
        if not websocket_origin_matches_host(
            websocket.headers.get("origin"), websocket.headers.get("host")
        ):
            await websocket.close(code=1008, reason="WebSocket origin does not match Host")
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(combined_snapshot())
                await asyncio.sleep(0.08)
        except (WebSocketDisconnect, RuntimeError):
            return

    docs_directory = ROOT / "docs"
    static_directory = ROOT / "static"
    app.mount("/docs", StaticFiles(directory=docs_directory), name="documents")
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="static")
    return app
