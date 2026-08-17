"""Integration tests for --pretty task output.

Why: Coding agents review kanban task bodies and responses directly in CLI output.
Escaped newline sequences in body/agent_response hide structure and make task context
hard to read, so these tests cover the CLI parser, storage/search integration, and
output formatter together for the commands agents use most.
"""

import io
import json
import os
from copy import deepcopy
from unittest.mock import patch

import pytest

from kanban.cli import ExitCode, TaskCLI
from kanban.config import Config
from kanban.storage import TaskStorage


@pytest.fixture
def kanban_env(tmp_path):
    tasks_dir = tmp_path / ".juno_task" / "tasks"
    tasks_dir.mkdir(parents=True)

    config_data = deepcopy(Config.DEFAULT_CONFIG)
    config_data["storage"]["base_path"] = str(tasks_dir)

    config_path = str(tasks_dir / "config.json")
    with open(config_path, "w") as f:
        json.dump(config_data, f)

    config = Config(config_path=config_path)
    storage = TaskStorage(config)

    old_root = os.environ.get("JUNO_TASK_ROOT")
    os.environ["JUNO_TASK_ROOT"] = str(tmp_path)

    yield storage

    if old_root is not None:
        os.environ["JUNO_TASK_ROOT"] = old_root
    else:
        os.environ.pop("JUNO_TASK_ROOT", None)


def _run_cli(args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
        result = TaskCLI().run(args)
    return result, stdout.getvalue(), stderr.getvalue()


def test_pretty_help_is_available_on_target_commands():
    """Help must document command-local --pretty so users can place it after commands."""
    for command in ("get", "list", "search", "ready"):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), pytest.raises(SystemExit) as exc_info:
            TaskCLI().run([command, "--help"])
        assert exc_info.value.code == 0
        assert "--pretty" in stdout.getvalue()
        assert "multiline" in stdout.getvalue()


def test_get_pretty_renders_escaped_body_and_response_as_multiline(kanban_env):
    task = kanban_env.create_task(
        body="Line one\\nLine two",
        status="done",
        agent_response="Fixed A\\nFixed B",
    )

    result, stdout, stderr = _run_cli(["get", task.id, "--pretty"])

    assert result == ExitCode.SUCCESS
    assert stderr == ""
    assert "body:\n  Line one\n  Line two" in stdout
    assert "agent_response:\n  Fixed A\n  Fixed B" in stdout
    assert "\\n" not in stdout


def test_list_search_and_ready_pretty_render_multiline_fields(kanban_env):
    kanban_env.create_task(
        body="Readable body\\nsecond line",
        status="todo",
        agent_response="Response one\\nResponse two",
        feature_tags=["pretty"],
    )

    for command_args in (
        ["list", "--tag", "pretty", "--limit", "1", "--pretty"],
        ["search", "--tag", "pretty", "--limit", "1", "--pretty"],
        ["ready", "--tag", "pretty", "--limit", "1", "--pretty"],
    ):
        result, stdout, stderr = _run_cli(command_args)

        assert result == ExitCode.SUCCESS
        assert "body:\n  Readable body\n  second line" in stdout
        assert "agent_response:\n  Response one\n  Response two" in stdout
        assert "\\n" not in stdout
