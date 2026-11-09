#!/usr/bin/env python3
"""Initialize database schema."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db import init_db
from env_config import load_env


def main() -> int:
    load_env()
    init_db()
    print("DB initialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
