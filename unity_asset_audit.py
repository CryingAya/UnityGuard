#!/usr/bin/env python3
"""UnityGuard entry point.

    python unity_asset_audit.py <unity_project_or_assets_dir> [--out DIR]
                                [--format md,json,html] [--fast]
                                [--fail-on critical] [-q]

This tool is READ-ONLY: it never writes inside the audited project.
Only stdlib is used, so it runs on any Python 3.9+ without pip install.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unityguard.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
