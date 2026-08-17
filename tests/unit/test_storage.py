"""Core persistence API tests for the sole production Markdown backend."""
import json
import os
from copy import deepcopy

import pytest

from kanban.config import Config
from kanban.models import Task
from kanban.storage import TaskStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.delenv("JUNO_TASK_ROOT", raising=False)
    tasks = tmp_path / ".juno_task/tasks"
    tasks.mkdir(parents=True)
    config = deepcopy(Config.DEFAULT_CONFIG)
    config["storage"]["base_path"] = str(tasks)
    path = tasks / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return TaskStorage(Config(str(path)))


@pytest.fixture
def sample_task():
    return Task(id="Ab1Cd2", body="Test task", status="todo", feature_tags=["test"])


def test_initializes_markdown_backend(storage):
    assert os.path.isdir(storage.base_path)
    assert storage.file_pattern == "*/*.md"
    assert storage.get_files() == []


def test_write_read_unicode_and_stable_path(storage, sample_task):
    sample_task.body = "日本語 🚀\n# heading"
    storage.write_task(sample_task)
    assert storage.get_files()[0].endswith("/ab/Ab1Cd2.md")
    assert list(storage.read_all_tasks())[0]["body"] == sample_task.body
    assert storage.find_task_file(sample_task.id).endswith(".md")


def test_runtime_rejects_ndjson_write(storage, sample_task):
    with pytest.raises(ValueError, match="import-only"):
        storage.write_task(sample_task, filepath=str(storage.tasks_root / "backlog.ndjson"))


def test_create_find_update_delete(storage):
    task = storage.create_task(id="Ab1Cd2", body="created", status="todo")
    assert storage.find_task(task.id)["body"] == "created"
    assert storage.update_task(task.id, {"status": "done", "agent_response": "ok"})
    assert storage.find_task(task.id)["status"] == "done"
    assert storage.delete_task(task.id)
    assert storage.find_task(task.id) is None
    assert storage.delete_task(task.id) is False


def test_queries_and_file_info(storage):
    tasks = [
        Task(id="Ab1Cd2", body="Backlog", status="backlog"),
        Task(id="Xy9Za8", body="Todo", status="todo", feature_tags=["urgent"]),
        Task(id="Qr7St6", body="Done", status="done", agent_response="ok", commit_hash="abc1234"),
    ]
    for task in tasks:
        storage.write_task(task)
    assert storage.count_tasks() == 3
    assert len(storage.get_tasks_by_status("todo")) == 1
    assert len(storage.get_open_tasks()) == 2
    assert storage.get_tasks_with_tag("urgent")[0]["id"] == "Xy9Za8"
    assert storage.get_tasks_with_commit("abc1234")[0]["id"] == "Qr7St6"
    assert len(storage.get_recent_tasks(2)) == 2
    assert all(item["task_count"] == 1 for item in storage.get_file_info())


def test_invalid_markdown_read_reports_or_raises(storage):
    path = storage.tasks_root / "Ab/Ab1Cd2.md"
    path.parent.mkdir(parents=True)
    path.write_text("invalid", encoding="utf-8")
    assert list(storage.read_tasks(str(path), skip_errors=True)) == []
    with pytest.raises(ValueError, match="Parse error"):
        list(storage.read_tasks(str(path), skip_errors=False))
