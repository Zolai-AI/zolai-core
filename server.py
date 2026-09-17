"""Minimal Zolai API server entrypoint.

A single-file server that can be launched with ``python server.py``.
Configures host/port via environment variables and enables CORS
for server deployment.

Usage:
    python server.py                          # defaults: 0.0.0.0:8000
    ZOLAI_PORT=9000 python server.py          # custom port
    python server.py --host 127.0.0.1 --port 3000
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Zolai API Server")
    parser.add_argument(
        "--host",
        default=os.environ.get("ZOLAI_HOST", "0.0.0.0"),
        help="Bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ZOLAI_PORT", "8000")),
        help="Bind port (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Ensure zolai-core is importable
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.info(
        "Starting Zolai API server on %s:%d (workers=%d)",
        args.host,
        args.port,
        args.workers,
    )

    uvicorn.run(
        "zolai.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
