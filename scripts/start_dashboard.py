#!/usr/bin/env python3
"""
Dashboard launch script (G1).

Wraps ``streamlit run`` with sane defaults so the user only
needs to remember one command. Pass-through to streamlit for
any extra args (port, browser, etc.).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DASHBOARD_APP = (
    Path(__file__).resolve().parent.parent
    / "src" / "observability" / "dashboard" / "app.py"
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(DASHBOARD_APP),
        *argv,
    ]
    print(f"[Dashboard] launching: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=os.getenv("DASHBOARD_CWD") or None)


if __name__ == "__main__":
    sys.exit(main())
