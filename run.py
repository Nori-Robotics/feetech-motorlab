#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import threading
import webbrowser


def ensure_environment() -> None:
    try:
        import fastapi  # noqa: F401
        import scservo_sdk  # noqa: F401
        import uvicorn  # noqa: F401

        return
    except ImportError:
        pass

    raise SystemExit(
        "Missing runtime dependencies. Activate the project environment and install requirements.txt."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NORI MotorLab web interface.")
    parser.add_argument("--host", default="127.0.0.1", help="Listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Acknowledge the risk of binding the unauthenticated interface beyond loopback.",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use simulated STS3215, STS3250, and STS3095 servos.",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the dashboard automatically."
    )
    return parser.parse_args()


def host_is_loopback(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def host_is_unspecified(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_unspecified
    except ValueError:
        return False


def require_remote_opt_in(host: str, allow_remote: bool) -> None:
    if not host_is_loopback(host) and not allow_remote:
        raise SystemExit(
            "Refusing to expose an unauthenticated hardware-control interface. "
            "Use --allow-remote only on a trusted, isolated network."
        )


def main() -> int:
    ensure_environment()
    import uvicorn

    from app.main import create_app

    args = parse_args()
    require_remote_opt_in(args.host, args.allow_remote)
    url_host = "127.0.0.1" if host_is_unspecified(args.host) else args.host
    url = f"http://{url_host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()
    mode = "SIMULATION" if args.simulate else "HARDWARE"
    print(f"NORI MotorLab · {mode}")
    print(f"Open {url}")
    if not args.simulate:
        print(
            "Hardware starts disconnected; discovery is read-only and connection never enables torque."
        )
    uvicorn.run(
        create_app(simulation=args.simulate), host=args.host, port=args.port, log_level="warning"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
