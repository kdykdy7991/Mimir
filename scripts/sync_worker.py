#!/usr/bin/env python3
"""Run the durable Task 08 datasource sync queue."""

from __future__ import annotations

import argparse
import socket
import time
from uuid import uuid4

from src.application.composition import build_application_services
from src.connectors.runtime import build_sync_worker_pool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--config", default="./config/settings.yaml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    services = build_application_services(
        data_dir=args.data_dir, config_path=args.config,
    )
    owner = f"{socket.gethostname()}:{uuid4()}"
    pool = build_sync_worker_pool(
        services, data_dir=args.data_dir, owner=owner,
    )
    if args.once:
        pool.run_once("sync")
        return 0
    try:
        while True:
            if not pool.run_once("sync"):
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
