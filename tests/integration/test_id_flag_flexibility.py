"""Tests for flexible task ID handling across all commands.

Why: Coding agents frequently use different case variations of --ID (--id, --Id, --ID)
and may use either positional or flag-based ID arguments interchangeably. All commands
that accept a task ID must support both positional and flag forms, and all case variations
must work identically. This prevents frustrating "unrecognized arguments" errors that
break automated workflows.
"""

import io
import json
import os
import sys
import pytest
from copy import deepcopy
from unittest.mock import patch

from kanban.cli import TaskCLI, ExitCode
from kanban.config import Config
from kanban.storage import TaskStorage


@pytest.fixture
def kanban_env(tmp_path):
    """Set up a kanban environment with a test task for CLI testing."""
    tasks_dir = tmp_path / ".juno_task" / "tasks"
    tasks_dir.mkdir(parents=True)

    config_data = deepcopy(Config.DEFAULT_CONFIG)
    config_data["storage"]["base_path"] = str(tasks_dir)

    config_path = str(tasks_dir / "config.json")
    with open(config_path, "w") as f:
        json.dump(config_data, f)

    config = Config(config_path=config_path)
    storage = TaskStorage(config)

    # Create a test task
    task = storage.create_task(body="Test task for ID flag flexibility", status="backlog")

    # Set JUNO_TASK_ROOT so CLI finds the config
    old_root = os.environ.get("JUNO_TASK_ROOT")
    os.environ["JUNO_TASK_ROOT"] = str(tmp_path)

    yield config_path, tmp_path, storage, task.id

    if old_root is not None:
        os.environ["JUNO_TASK_ROOT"] = old_root
    else:
        os.environ.pop("JUNO_TASK_ROOT", None)


class TestGetIdFlexibility:
    """get command: both positional and flag-based ID, all case variations."""

    def test_get_positional(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['get', task_id])
        assert result == ExitCode.SUCCESS
        output = json.loads(stdout.getvalue())
        assert output[0]['id'] == task_id

    def test_get_flag_lowercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['get', '--id', task_id])
        assert result == ExitCode.SUCCESS
        output = json.loads(stdout.getvalue())
        assert output[0]['id'] == task_id

    def test_get_flag_uppercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['get', '--ID', task_id])
        assert result == ExitCode.SUCCESS
        output = json.loads(stdout.getvalue())
        assert output[0]['id'] == task_id

    def test_get_flag_mixed_case(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['get', '--Id', task_id])
        assert result == ExitCode.SUCCESS
        output = json.loads(stdout.getvalue())
        assert output[0]['id'] == task_id

    def test_get_no_id_error(self, kanban_env):
        cli = TaskCLI()
        stderr = io.StringIO()
        with patch('sys.stderr', stderr):
            result = cli.run(['get'])
        assert result == ExitCode.INVALID_USAGE


class TestUpdateIdFlexibility:
    """update command: both positional and flag-based ID, all case variations."""

    def test_update_positional(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['update', task_id, '--response', 'positional test'])
        assert result == ExitCode.SUCCESS

    def test_update_flag_lowercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['update', '--id', task_id, '--response', 'flag test'])
        assert result == ExitCode.SUCCESS

    def test_update_flag_uppercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['update', '--ID', task_id, '--response', 'flag test'])
        assert result == ExitCode.SUCCESS

    def test_update_flag_mixed_case(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['update', '--Id', task_id, '--response', 'flag test'])
        assert result == ExitCode.SUCCESS

    def test_update_no_id_error(self, kanban_env):
        cli = TaskCLI()
        stderr = io.StringIO()
        with patch('sys.stderr', stderr):
            result = cli.run(['update', '--response', 'no id'])
        assert result == ExitCode.INVALID_USAGE
        assert 'Task ID is required' in stderr.getvalue()


class TestArchiveIdFlexibility:
    """archive command: both positional and flag-based ID, all case variations."""

    def test_archive_positional(self, kanban_env):
        _, _, storage, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['archive', task_id])
        assert result == ExitCode.SUCCESS
        # Verify archived
        task = storage.find_task(task_id)
        assert task['status'] == 'archive'

    def test_archive_flag_lowercase(self, kanban_env):
        _, _, storage, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['archive', '--id', task_id])
        assert result == ExitCode.SUCCESS

    def test_archive_flag_uppercase(self, kanban_env):
        _, _, storage, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['archive', '--ID', task_id])
        assert result == ExitCode.SUCCESS

    def test_archive_flag_mixed_case(self, kanban_env):
        _, _, storage, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['archive', '--Id', task_id])
        assert result == ExitCode.SUCCESS

    def test_archive_no_id_error(self, kanban_env):
        cli = TaskCLI()
        stderr = io.StringIO()
        with patch('sys.stderr', stderr):
            result = cli.run(['archive'])
        assert result == ExitCode.INVALID_USAGE
        assert 'Task ID is required' in stderr.getvalue()


class TestMarkIdFlexibility:
    """mark command: both positional and flag-based ID, all case variations."""

    def test_mark_positional_id(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['mark', 'todo', task_id, '--response', 'positional test'])
        assert result == ExitCode.SUCCESS

    def test_mark_flag_lowercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['mark', 'todo', '--id', task_id, '--response', 'flag test'])
        assert result == ExitCode.SUCCESS

    def test_mark_flag_uppercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['mark', 'todo', '--ID', task_id, '--response', 'flag test'])
        assert result == ExitCode.SUCCESS

    def test_mark_flag_mixed_case(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['mark', 'todo', '--Id', task_id, '--response', 'flag test'])
        assert result == ExitCode.SUCCESS

    def test_mark_no_id_error(self, kanban_env):
        cli = TaskCLI()
        stderr = io.StringIO()
        with patch('sys.stderr', stderr):
            result = cli.run(['mark', 'todo', '--response', 'no id'])
        assert result == ExitCode.INVALID_USAGE
        assert 'Task ID is required' in stderr.getvalue()


class TestDepsIdFlexibility:
    """deps command: verify flag-based ID variations work."""

    def test_deps_show_positional(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['deps', task_id])
        assert result == ExitCode.SUCCESS

    def test_deps_show_flag_lowercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['deps', '--id', task_id])
        assert result == ExitCode.SUCCESS

    def test_deps_show_flag_uppercase(self, kanban_env):
        _, _, _, task_id = kanban_env
        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            result = cli.run(['deps', '--ID', task_id])
        assert result == ExitCode.SUCCESS
