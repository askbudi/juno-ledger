"""Tests for kanban.config module.

Why: Config controls validation rules, storage paths, and workflow behavior.
Broken config = broken everything downstream (validation, storage, CLI).
The deep-merge and auto-create logic is especially tricky to get right.
"""

import json
import os
import pytest
from pathlib import Path
from kanban.config import Config, ConfigError, init_config


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temp directory with .juno_task/tasks/ structure."""
    tasks_dir = tmp_path / ".juno_task" / "tasks"
    tasks_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def config_path(tmp_config_dir):
    """Path to config.json within the temp structure."""
    return str(tmp_config_dir / ".juno_task" / "tasks" / "config.json")


class TestConfigDefaults:
    """Default configuration loading."""

    def test_default_config_has_required_sections(self):
        assert "status_workflow" in Config.DEFAULT_CONFIG
        assert "feature_tags" in Config.DEFAULT_CONFIG
        assert "storage" in Config.DEFAULT_CONFIG
        assert "search" in Config.DEFAULT_CONFIG
        assert "version" in Config.DEFAULT_CONFIG

    def test_default_status_values(self):
        assert Config.DEFAULT_CONFIG["status_workflow"]["values"] == [
            "backlog", "todo", "in_progress", "done", "archive"
        ]

    def test_default_status_is_backlog(self):
        assert Config.DEFAULT_CONFIG["status_workflow"]["default"] == "backlog"

    def test_transitions_not_enforced_by_default(self):
        assert Config.DEFAULT_CONFIG["status_workflow"]["enforce_transitions"] is False


class TestConfigLoading:
    """Config file loading with auto-create."""

    def test_auto_creates_config_file(self, config_path):
        config = Config(config_path=config_path, auto_create=True)
        assert os.path.exists(config_path)
        assert config.config["version"] == "1.0"

    def test_loads_existing_config(self, config_path):
        custom = Config.DEFAULT_CONFIG.copy()
        custom["version"] = "2.0"
        with open(config_path, "w") as f:
            json.dump(custom, f)

        config = Config(config_path=config_path)
        assert config.config["version"] == "2.0"

    def test_invalid_json_raises(self, config_path):
        with open(config_path, "w") as f:
            f.write("{not valid json}")

        with pytest.raises(ConfigError, match="Invalid JSON"):
            Config(config_path=config_path)


class TestConfigValidation:
    """Config.validate() checks."""

    def test_valid_default_config(self, config_path):
        config = Config(config_path=config_path)
        is_valid, error = config.validate()
        assert is_valid is True

    def test_missing_version_falls_back_to_default(self, config_path):
        """Local config without version still works because deep-merge adds it from defaults."""
        local = {"status_workflow": {"default": "backlog"}}
        with open(config_path, "w") as f:
            json.dump(local, f)

        config = Config(config_path=config_path)
        assert config.config["version"] == "1.0"  # From defaults

    def test_bad_version_format_fails(self, config_path):
        bad = Config.DEFAULT_CONFIG.copy()
        bad["version"] = "not.a.version.format"
        with open(config_path, "w") as f:
            json.dump(bad, f)

        with pytest.raises(ConfigError, match="version"):
            Config(config_path=config_path)

    def test_empty_status_values_fails(self, config_path):
        bad = Config.DEFAULT_CONFIG.copy()
        bad["status_workflow"] = {**bad["status_workflow"], "values": []}
        with open(config_path, "w") as f:
            json.dump(bad, f)

        with pytest.raises(ConfigError):
            Config(config_path=config_path)

    def test_default_not_in_values_fails(self, config_path):
        bad = Config.DEFAULT_CONFIG.copy()
        bad["status_workflow"] = {
            **bad["status_workflow"],
            "values": ["open", "closed"],
            "default": "nonexistent",
        }
        with open(config_path, "w") as f:
            json.dump(bad, f)

        with pytest.raises(ConfigError):
            Config(config_path=config_path)


class TestConfigDeepMerge:
    """Config._deep_merge() behavior."""

    def test_simple_override(self, config_path):
        config = Config(config_path=config_path)
        result = config._deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_merge(self, config_path):
        config = Config(config_path=config_path)
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        result = config._deep_merge(base, override)
        assert result["outer"] == {"a": 1, "b": 3, "c": 4}

    def test_new_keys_added(self, config_path):
        config = Config(config_path=config_path)
        result = config._deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}


class TestConfigProperties:
    """Convenience property accessors."""

    def test_status_values(self, config_path):
        config = Config(config_path=config_path)
        assert "todo" in config.status_values

    def test_default_status(self, config_path):
        config = Config(config_path=config_path)
        assert config.default_status == "backlog"

    def test_enforce_transitions(self, config_path):
        config = Config(config_path=config_path)
        assert config.enforce_transitions is False

    def test_storage_base_path(self, config_path):
        config = Config(config_path=config_path)
        assert config.storage_base_path.endswith(".juno_task/tasks")

    def test_default_file(self, config_path):
        config = Config(config_path=config_path)
        assert config.default_file == ""

    def test_default_limit(self, config_path):
        config = Config(config_path=config_path)
        assert config.default_limit == 5

    def test_use_ripgrep(self, config_path):
        config = Config(config_path=config_path)
        assert config.use_ripgrep is True

    def test_max_tags_per_task(self, config_path):
        config = Config(config_path=config_path)
        assert config.max_tags_per_task == 20


class TestConfigSave:
    """Config.save() persistence."""

    def test_save_creates_file(self, config_path):
        config = Config(config_path=config_path)
        os.remove(config_path)
        config.save()
        assert os.path.exists(config_path)

    def test_save_roundtrip(self, config_path):
        config = Config(config_path=config_path)
        config.config["version"] = "3.0"
        config.save()

        with open(config_path, "r") as f:
            saved = json.load(f)
        assert saved["version"] == "3.0"


class TestConfigHelp:
    """Help text generation."""

    def test_generate_help_output(self, config_path):
        config = Config(config_path=config_path)
        help_text = config.generate_help_output()
        assert "backlog" in help_text
        assert "todo" in help_text
        assert "Status Values" in help_text

    def test_get_help_text(self, config_path):
        config = Config(config_path=config_path)
        status_help = config.get_help_text("status")
        assert len(status_help) > 0

    def test_get_error_message(self, config_path):
        config = Config(config_path=config_path)
        msg = config.get_error_message("invalid_status", status="bad", allowed_values="a, b")
        assert "bad" in msg


class TestInitConfig:
    """init_config() standalone function."""

    def test_creates_file(self, tmp_path):
        path = str(tmp_path / ".juno_task" / "tasks" / "config.json")
        init_config(path=path)
        assert os.path.exists(path)

    def test_does_not_overwrite_without_force(self, tmp_path):
        path = str(tmp_path / ".juno_task" / "tasks" / "config.json")
        init_config(path=path)

        # Modify file
        with open(path, "r") as f:
            original = json.load(f)
        original["version"] = "99.0"
        with open(path, "w") as f:
            json.dump(original, f)

        init_config(path=path)  # Should NOT overwrite
        with open(path, "r") as f:
            after = json.load(f)
        assert after["version"] == "99.0"

    def test_overwrites_with_force(self, tmp_path):
        path = str(tmp_path / ".juno_task" / "tasks" / "config.json")
        init_config(path=path)

        with open(path, "r") as f:
            original = json.load(f)
        original["version"] = "99.0"
        with open(path, "w") as f:
            json.dump(original, f)

        init_config(path=path, force=True)
        with open(path, "r") as f:
            after = json.load(f)
        assert after["version"] == "1.0"


class TestJunoTaskRootEnv:
    """JUNO_TASK_ROOT environment variable support.

    Why: Coding agents may run from different directories within a project.
    Without JUNO_TASK_ROOT, juno-kanban resolves .juno_task relative to PWD,
    scattering ndjson files across the filesystem. JUNO_TASK_ROOT pins all
    config and storage operations to a fixed project root.
    """

    def test_find_config_uses_env_root(self, tmp_path, monkeypatch):
        """_find_config() should look for config at JUNO_TASK_ROOT first."""
        # Create config at the env root
        root = tmp_path / "project"
        tasks_dir = root / ".juno_task" / "tasks"
        tasks_dir.mkdir(parents=True)
        config_file = tasks_dir / "config.json"
        json.dump(Config.DEFAULT_CONFIG, config_file.open("w"))

        monkeypatch.setenv("JUNO_TASK_ROOT", str(root))
        # Even if CWD is somewhere else, config should come from env root
        monkeypatch.chdir(tmp_path)

        config = Config()
        assert config.config_path == str(config_file)

    def test_find_config_env_root_auto_creates(self, tmp_path, monkeypatch):
        """When JUNO_TASK_ROOT points to dir without config, auto-create there."""
        root = tmp_path / "project"
        root.mkdir()
        monkeypatch.setenv("JUNO_TASK_ROOT", str(root))
        monkeypatch.chdir(tmp_path)

        config = Config()
        expected = str(root / ".juno_task" / "tasks" / "config.json")
        assert config.config_path == expected
        assert os.path.exists(expected)

    def test_storage_base_path_resolves_from_env_root(self, tmp_path, monkeypatch):
        """storage_base_path should resolve relative paths from JUNO_TASK_ROOT."""
        root = tmp_path / "project"
        root.mkdir()
        monkeypatch.setenv("JUNO_TASK_ROOT", str(root))
        monkeypatch.chdir(tmp_path)

        config = Config()
        expected = os.path.join(str(root), ".juno_task", "tasks")
        assert config.storage_base_path == expected

    def test_storage_base_path_absolute_ignores_env(self, tmp_path, monkeypatch):
        """Absolute base_path in config is used as-is regardless of JUNO_TASK_ROOT."""
        root = tmp_path / "project"
        tasks_dir = root / ".juno_task" / "tasks"
        tasks_dir.mkdir(parents=True)

        custom_config = Config.DEFAULT_CONFIG.copy()
        custom_config["storage"] = {**custom_config["storage"], "base_path": "/absolute/path"}
        config_file = tasks_dir / "config.json"
        json.dump(custom_config, config_file.open("w"))

        monkeypatch.setenv("JUNO_TASK_ROOT", str(root))
        config = Config()
        assert config.storage_base_path == "/absolute/path"

    def test_env_root_takes_priority_over_upward_search(self, tmp_path, monkeypatch):
        """JUNO_TASK_ROOT should win over upward directory search."""
        # Create config in parent dir (would be found by upward search)
        parent_tasks = tmp_path / ".juno_task" / "tasks"
        parent_tasks.mkdir(parents=True)
        json.dump(Config.DEFAULT_CONFIG, (parent_tasks / "config.json").open("w"))

        # Create config at env root (different location)
        env_root = tmp_path / "env_project"
        env_tasks = env_root / ".juno_task" / "tasks"
        env_tasks.mkdir(parents=True)
        json.dump(Config.DEFAULT_CONFIG, (env_tasks / "config.json").open("w"))

        monkeypatch.setenv("JUNO_TASK_ROOT", str(env_root))
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)  # Would find parent's config via upward search

        config = Config()
        assert config.config_path == str(env_tasks / "config.json")

    def test_without_env_root_uses_cwd_search(self, tmp_path, monkeypatch):
        """Without JUNO_TASK_ROOT, falls back to normal upward search from CWD."""
        tasks_dir = tmp_path / ".juno_task" / "tasks"
        tasks_dir.mkdir(parents=True)
        config_file = tasks_dir / "config.json"
        json.dump(Config.DEFAULT_CONFIG, config_file.open("w"))

        monkeypatch.delenv("JUNO_TASK_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)

        config = Config()
        assert config.config_path == str(config_file)

    def test_storage_base_path_without_env_resolves_from_config(self, config_path, monkeypatch, tmp_path):
        """Without JUNO_TASK_ROOT, storage_base_path resolves from config file location."""
        monkeypatch.delenv("JUNO_TASK_ROOT", raising=False)
        config = Config(config_path=config_path)
        # config_path is at <root>/.juno_task/tasks/config.json
        # storage_base_path should resolve to <root>/.juno_task/tasks (absolute)
        expected = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(config_path)))), ".juno_task", "tasks")
        assert config.storage_base_path == expected
