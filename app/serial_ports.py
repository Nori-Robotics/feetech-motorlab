from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any


def _listed_ports() -> list[Any]:
    try:
        from serial.tools import list_ports

        return list(list_ports.comports())
    except Exception:
        return []


def serial_port_available(device: str) -> bool:
    """Accept filesystem device nodes and platform-native names such as COM3."""
    if not device:
        return False
    if os.path.exists(device):
        return True
    normalized = os.path.normcase(device)
    return any(
        os.path.normcase(str(getattr(port, "device", ""))) == normalized
        for port in _listed_ports()
    )


def discover_serial_ports() -> list[dict[str, Any]]:
    """Return serial ports on Linux, macOS, and Windows.

    Linux ``/dev/serial/by-id`` aliases are placed first because they remain
    stable across reconnects. Ports reported by pyserial cover macOS device
    names and Windows COM ports without assuming a particular USB bridge.
    """
    listed = _listed_ports()
    metadata: dict[str, Any] = {}
    for port in listed:
        device = str(getattr(port, "device", ""))
        if not device:
            continue
        metadata[device] = port
        if os.path.exists(device):
            metadata[os.path.realpath(device)] = port

    candidates = sorted(glob.glob("/dev/serial/by-id/*"))
    candidates.extend(
        sorted(
            str(getattr(port, "device", ""))
            for port in listed
            if getattr(port, "device", None)
        )
    )
    # These fallbacks keep discovery useful if pyserial cannot enumerate but
    # conventional Linux device nodes are present.
    candidates.extend(sorted(glob.glob("/dev/ttyACM*")))
    candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for device in candidates:
        exists = os.path.exists(device)
        real_device = os.path.realpath(device) if exists else device
        identity = os.path.normcase(real_device)
        if identity in seen or not serial_port_available(device):
            continue
        seen.add(identity)
        info = metadata.get(device) or metadata.get(real_device)
        description = getattr(info, "description", None) or Path(device).name or device
        manufacturer = getattr(info, "manufacturer", None)
        vid = getattr(info, "vid", None)
        pid = getattr(info, "pid", None)
        label = description
        if vid is not None or pid is not None:
            label = f"{label} · USB serial"
        result.append(
            {
                "device": device,
                "real_device": real_device,
                "label": label,
                "manufacturer": manufacturer,
                "vid": f"{vid:04x}" if vid is not None else None,
                "pid": f"{pid:04x}" if pid is not None else None,
            }
        )
    return result
