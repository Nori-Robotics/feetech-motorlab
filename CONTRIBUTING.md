# Contributing

Thank you for improving Feetech MotorLab. Keep changes portable across hosts,
serial adapters, and motor IDs: never commit absolute user paths, fixed serial
device names, or assumptions about how many motors are connected.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py --simulate
```

Run the hardware-free test suite before submitting a change:

```bash
python -m unittest discover -s tests -v
```

Hardware changes should also be checked on a current-limited bench. Start with
read-only discovery, guard every persistent write, and verify changes by ping,
register readback, or a final bus scan. Document the adapter, servo model,
firmware or SDK version, and exact verification performed.

Keep runtime recordings, captured positions, virtual environments, editor
settings, and locally selected devices out of commits. Do not add a new motor
profile until its model number, control table, units, voltage variant, and
safety limits have authoritative documentation or repeatable measurements.
