"""Pytest configuration for juno_kanban.

Why: Test runs inside the monorepo may resolve `kanban` from the active virtualenv
(site-packages) instead of this working tree's `src/kanban`. That hides local
changes and can produce false green/false red results.

This hook pins imports to the repository source under `src/` so tests always
validate the current implementation under development.
"""

from pathlib import Path
import sys

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
SRC_DIR_STR = str(SRC_DIR)

if SRC_DIR_STR not in sys.path:
    sys.path.insert(0, SRC_DIR_STR)
