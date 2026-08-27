#!/usr/bin/env python3
"""Repository-local bootstrap for the ``peridigm-preprocess`` CLI."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from peridigm_preprocess.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
