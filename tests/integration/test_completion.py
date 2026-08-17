"""Integration tests for shell completion support.

Why: Completion is an operator UX feature, but regressions are easy to miss because
normal task CRUD tests do not exercise completion-only code paths. These tests lock
both implementation behavior (`__complete` suggestions) and user-facing script output
so tab-completion flows remain stable across CLI refactors.
"""

import io
from pathlib import Path
from unittest.mock import patch

from kanban.cli import TaskCLI, ExitCode


class TestCompletionCommand:
    """Verify completion script generation and internal candidate suggestions."""

    def test_completion_bash_script_does_not_initialize_project_config(self, tmp_path, monkeypatch):
        """completion command should print script without creating .juno_task/config artifacts."""
        monkeypatch.chdir(tmp_path)

        cli = TaskCLI()
        stdout = io.StringIO()
        with patch('sys.stdout', stdout), patch('sys.stderr', io.StringIO()):
            result = cli.run(['completion', 'bash'])

        assert result == ExitCode.SUCCESS
        output = stdout.getvalue()
        assert '__complete --index "$COMP_CWORD"' in output
        assert 'complete -o default -F' in output
        assert 'juno-kanban juno-feedback kanban-juno' in output
        assert not (Path(tmp_path) / '.juno_task').exists()

    def test_internal_complete_suggests_top_level_commands(self):
        """Top-level completion should suggest matching command names."""
        cli = TaskCLI()
        stdout = io.StringIO()

        with patch('sys.stdout', stdout), patch('sys.stderr', io.StringIO()):
            result = cli.run(['__complete', '--index', '1', '--', 'juno-kanban', 'c'])

        assert result == ExitCode.SUCCESS
        suggestions = stdout.getvalue().strip().splitlines()
        assert 'create' in suggestions
        assert 'completion' in suggestions

    def test_internal_complete_suggests_mark_status_values(self):
        """`mark` first positional argument should suggest workflow statuses."""
        cli = TaskCLI()
        stdout = io.StringIO()

        with patch('sys.stdout', stdout), patch('sys.stderr', io.StringIO()):
            result = cli.run(['__complete', '--index', '2', '--', 'juno-kanban', 'mark', 'd'])

        assert result == ExitCode.SUCCESS
        suggestions = stdout.getvalue().strip().splitlines()
        assert 'done' in suggestions

    def test_internal_complete_suggests_choice_values_for_option_arguments(self):
        """Options with choices should suggest those choices (e.g. list --sort)."""
        cli = TaskCLI()
        stdout = io.StringIO()

        with patch('sys.stdout', stdout), patch('sys.stderr', io.StringIO()):
            result = cli.run(['__complete', '--index', '3', '--', 'juno-kanban', 'list', '--sort', 'd'])

        assert result == ExitCode.SUCCESS
        suggestions = stdout.getvalue().strip().splitlines()
        assert 'desc' in suggestions
