"""Tests for scripts/bump_version.py.

Why: release versioning is a safety-critical workflow. If bumping starts from a
stale local value instead of the latest published value, maintainers can
accidentally attempt a rollback version (for example 1.30.0 after 1.32.0 exists),
which breaks publish flow and creates confusing release state.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
SPEC = importlib.util.spec_from_file_location("bump_version_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
bump_version_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bump_version_script)

VersionBumper = bump_version_script.VersionBumper


def make_bumper(tmp_path: Path, version: str) -> VersionBumper:
    setup_file = tmp_path / "setup.py"
    setup_file.write_text("# test setup file\n", encoding="utf-8")

    init_file = tmp_path / "src" / "kanban" / "__init__.py"
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")

    return VersionBumper(setup_file)


def test_resolve_bump_baseline_prefers_published_version_when_local_is_behind(tmp_path: Path):
    bumper = make_bumper(tmp_path, "1.29.0")

    with patch.object(bumper, "get_latest_published_version", return_value="1.32.0"):
        baseline, local, published = bumper.resolve_bump_baseline_version()

    assert local == "1.29.0"
    assert published == "1.32.0"
    assert baseline == "1.32.0"
    assert bumper.bump_version("minor", current_version=baseline) == "1.33.0"


def test_resolve_bump_baseline_falls_back_to_local_when_pypi_unavailable(tmp_path: Path):
    bumper = make_bumper(tmp_path, "1.29.0")

    with patch.object(bumper, "get_latest_published_version", return_value=None):
        baseline, local, published = bumper.resolve_bump_baseline_version()

    assert local == "1.29.0"
    assert published is None
    assert baseline == "1.29.0"
    assert bumper.bump_version("minor", current_version=baseline) == "1.30.0"


def test_get_latest_published_version_returns_none_for_invalid_pypi_version(tmp_path: Path):
    bumper = make_bumper(tmp_path, "1.29.0")

    fake_response = contextlib.nullcontext(io.StringIO('{"info": {"version": "latest"}}'))
    with patch.object(bump_version_script.urllib.request, "urlopen", return_value=fake_response):
        assert bumper.get_latest_published_version() is None


def test_get_latest_published_version_returns_none_for_network_error(tmp_path: Path):
    bumper = make_bumper(tmp_path, "1.29.0")

    with patch.object(
        bump_version_script.urllib.request,
        "urlopen",
        side_effect=bump_version_script.urllib.error.URLError("offline"),
    ):
        assert bumper.get_latest_published_version() is None
