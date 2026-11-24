#!/usr/bin/env python3
"""UnityGuard GUI launcher.

    python unityguard_gui.py

Same engine as the command line tool, wrapped in a small window.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unityguard.gui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
