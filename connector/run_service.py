#!/usr/bin/env python3
"""Launch / manage Windows service."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.windows_service import _service_main, run_headless

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        try:
            _service_main()
        except ImportError:
            run_headless()
    else:
        run_headless()
