# Feetech MotorLab

A browser-based bench tool for testing and characterizing 12 V Feetech
STS3215, STS3250, and STS3095 servos. MotorLab provides multi-servo telemetry,
individual and bus-wide motion controls, live tuning, recording, encoder
centering, EEPROM configuration, and a guarded motor-ID station.

The application does not depend on a particular workstation, serial path, or
USB bridge. It enumerates serial ports through pyserial on Linux, macOS, and
Windows and works with compatible half-duplex Feetech/SCServo bus adapters.
The Waveshare Bus Servo Adapter (A) is one tested option, not a software
requirement.

## Safety first

This is a development tool, not a certified safety system. Secure the servo,
keep mechanisms clear, use a current-limited external supply matched to the
servo voltage, and retain a physical power disconnect. Never power a servo
from a computer's USB port unless the adapter manufacturer explicitly supports
that load.

Connection and scan are read-only with respect to motor configuration and do
not enable torque. Motion requires an explicit torque-arm action. EEPROM and
encoder-center operations require torque off, an explicit confirmation, and a
readback. Disconnect and emergency stop attempt to release every motor.

## Requirements

- Python 3.10–3.12
- A compatible Feetech half-duplex TTL serial adapter
- An external supply appropriate for the connected servos
- STS3215, STS3250, or STS3095 servos using the supported SMS/STS register map

For the Waveshare Bus Servo Adapter (A), use its USB-control jumper position
(B) and external servo power as described in the
[manufacturer guide](https://www.waveshare.com/wiki/Bus_Servo_Adapter_(A)).
For other adapters, follow their direction-control, logic-level, grounding,
and power instructions.

## Install and run

Create an isolated environment from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

On Windows, activate with `.venv\Scripts\activate` before running the same
`pip` and `python run.py` commands.

MotorLab opens at <http://127.0.0.1:8765>. To exercise the full interface
without attached hardware:

```bash
python run.py --simulate
```

Useful launcher options:

```text
--host ADDRESS      Listen address; defaults to 127.0.0.1
--port PORT         HTTP port; defaults to 8765
--no-browser        Do not open a browser automatically
--simulate          Use the built-in three-motor simulator
```

Binding to `0.0.0.0` exposes a hardware-control interface to the local network.
MotorLab has no authentication layer, so only do this on a trusted, isolated
network. If dependencies live in a separate Python installation, set
`FEETECH_PYTHON` to that interpreter before launching; no machine-specific
fallback path is built into the application.

## Connect a bus

1. Wire the adapter, shared ground, external supply, and servos with power off.
2. Power the servo bus and connect the adapter to the host.
3. Select the enumerated serial port and the current motor baud rate. Linux
   `/dev/serial/by-id/...` aliases are shown first when available because they
   remain stable across reconnects; macOS device names and Windows COM ports
   are discovered without configuration.
4. Use **Scan** for read-only discovery, then **Connect bus**. The default STS
   baud rate is `1,000,000`.

MotorLab accepts every supported responder on the selected chain and rejects
unsupported models from control. It never assumes a fixed port, motor ID, or
number of motors.

## Dashboard and controls

The bus dashboard shows chain health, online/armed/moving counts, current,
estimated torque, temperature, voltage, fault states, and per-motor telemetry.
Click a motor to open its control page.

- **Bus control:** arm/release all motors, command one shared angle target,
  apply shared live limits, jog every motor by the same relative amount,
  center all encoders, and write selected EEPROM values across the chain.
- **Motor control:** switch operating mode, command position/speed/drive/step,
  type or slide live limits, jog in angle mode, capture and recall a position,
  center the encoder, and edit persistent parameters.
- **Sliders:** `0%`, `25%`, `50%`, `75%`, and `100%` markers are clickable.
  Velocity, acceleration, torque limit, and jog step can also be typed.
- **Recording:** the red control records each motor's position, target,
  velocity, effort, voltage, temperature, current, estimated torque, status,
  and torque state to CSV under `data/recordings/`.

Velocity and acceleration use nonzero UI defaults (`600` and `40`) when a
device reports zeroed command registers. Bus-wide jogs use the limits currently
shown on the bus dashboard; individual jogs use that motor page's values. A
group jog preflights every target and rejects the whole command if any motor
would cross its configured travel limits.

### Operating modes

| Mode | Control |
| --- | --- |
| 0 | Angle servo / absolute position |
| 1 | Closed-loop signed velocity |
| 2 | Open-loop signed drive |
| 3 | Relative step |

Mode changes require torque off and are read back. Torque arming seeds a
zero-motion command appropriate to the active mode before enabling output.

### Telemetry units

- Position is the 12-bit encoder value (`0–4095`) and its degree conversion.
- Velocity is the signed register value plus a labeled estimate using the
  common SMS/STS `0.732 RPM/raw` conversion.
- Load/effort is the absolute magnitude of `Present_Load`. Feetech defines
  `1000 raw = 100%`; it is not calibrated torque.
- Current uses Feetech's SMS/STS `6.5 mA/LSB` scale and is displayed in amperes.
- STS3215 and STS3250 estimated torque uses the datasheet constant
  `11 kgf·cm/A` (`1.079 N·m/A`) multiplied by measured current. This is an
  estimate affected by gearbox loss, speed, voltage, and temperature.
- STS3095 torque estimation remains unavailable until a model-specific
  constant or physical calibration is verified.
- Voltage uses `0.1 V/LSB`; temperature is reported in degrees Celsius.

The torque-limit control is an output cap, not a current command. Its rated
marker is derived from rated torque divided by stall torque: STS3215
`10/30 → 333 raw` and STS3250 `16/50 → 320 raw`. There is no fixed
torque-limit-to-current conversion, so the UI shows measured live and session
peak current instead.

## Quick ID station

Open **Quick ID Station** from the left sidebar while the normal bus is
disconnected. The workflow is intentionally web-only; there is no separate
command-line ID utility to maintain or accidentally bypass the UI guards.

- **Single ID** assigns the configured ID to each motor connected one at a
  time.
- **Sequential ID** advances through a configurable inclusive start/end range.
- **Loop range** wraps from the end ID back to the start ID.

For every motor, the station requires two matching discovery scans, requires
exactly one supported responder, disables torque, writes the ID and verified
default baud, pings the new ID, performs a final exclusive scan, and then waits
for the motor to be physically unplugged. Normal Scan and Connect actions are
locked while the station owns the adapter.

ID changes are persistent EEPROM writes. Keep exactly one motor connected to
the selected adapter during this workflow.

## Persistent operations

The advanced EEPROM editor covers PID gains, travel limits, temperature limit,
and voltage limits. Each write unlocks EEPROM, writes one value, reads it back,
relocks the device, and leaves torque off. Bus-wide writes validate the full
chain before beginning, but a communication failure can still leave earlier
motors successfully changed.

**Set here = 2048** invokes Feetech's one-key midpoint command, forces torque
back off, and verifies `Present_Position = 2048`. This changes the coordinate
meaning of saved positions and existing travel limits.

## Data and repository layout

```text
app/                 FastAPI service, controller, serial discovery, ID workflow
static/              Browser interface and local visual assets
tests/               Hardware-free simulation and unit tests
docs/datasheets/     Referenced vendor datasheets and source notes
data/recordings/     Runtime CSV output; recordings are ignored by Git
run.py               Portable application launcher
```

Captured positions and recordings are runtime data and are not committed.
There are no checked-in host paths, serial-port selections, motor IDs, or local
environment settings.

## Tests

Tests use fakes and simulation only; they never access a serial port or enable
real motor torque.

```bash
python -m unittest discover -s tests -v
```

## Documentation and sources

- [Local datasheet index and source provenance](docs/datasheets/README.md)
- [Feetech official Python SDK](https://github.com/ftservo/FTServo_Python)
- [Feetech STS3215 modes and current units](https://www.feetechrc.com/20210430-56680.html)
- [Waveshare Bus Servo Adapter (A) guide](https://www.waveshare.com/wiki/Bus_Servo_Adapter_(A))

The repository is MIT licensed. Vendor datasheets and NORI marks remain the
property of their respective owners; see [NOTICE](NOTICE).
