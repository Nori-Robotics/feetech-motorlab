#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def ensure_environment() -> None:
    try:
        import fastapi  # noqa: F401
        import lerobot  # noqa: F401
        import scservo_sdk  # noqa: F401
        import uvicorn  # noqa: F401
        return
    except ImportError:
        pass

    candidates = [os.environ.get("FEETECH_PYTHON")]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        if Path(candidate).resolve() == Path(sys.executable).resolve():
            continue
        os.execv(candidate, [candidate, str(Path(__file__).resolve()), *sys.argv[1:]])

    raise SystemExit(
        "Missing runtime dependencies. Install requirements.txt in an environment "
        "with LeRobot Feetech support, or set FEETECH_PYTHON=/path/to/python."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Feetech MotorLab web interface."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use simulated STS3215, STS3250, and STS3095 servos.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard automatically.")
    return parser.parse_args()


def main() -> int:
    ensure_environment()
    import uvicorn

    from app.main import create_app

    args = parse_args()
    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{url_host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()
    mode = "SIMULATION" if args.simulate else "HARDWARE"
    print(f"Feetech MotorLab · {mode}")
    print(f"Open {url}")
    if not args.simulate:
        print("Hardware starts disconnected; discovery is read-only and connection never enables torque.")
    uvicorn.run(create_app(simulation=args.simulate), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
