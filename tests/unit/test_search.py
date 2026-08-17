"""Tests for kanban.search module.

Why: Search is the primary read path for all CLI commands (list, search, get).
The dual-backend (ripgrep + Python) architecture means bugs can lurk in one backend
while the other works. PythonSearch is the always-available fallback, so it MUST be
correct - it's the source of truth when ripgrep is unavailable.
"""

import json
import os
import pytest
from copy import deepcopy
from kanban.config import Config
from kanban.storage import TaskStorage
from kanban.search import (
    SearchFilters,
    PythonSearch,
    TaskSearch,
    sort_tasks_by_last_modified,
    sort_tasks_by_status_sequence,
    sort_tasks_with_status_priority,
)
from kanban.models import Task


@pytest.fixture
def storage_with_tasks(tmp_path):
    """Set up storage with a variety of test tasks."""
    tasks_dir = tmp_path / ".juno_task" / "tasks"
    tasks_dir.mkdir(parents=True)

    config_data = deepcopy(Config.DEFAULT_CONFIG)
    config_data["storage"]["base_path"] = str(tasks_dir)

    config_path = str(tasks_dir / "config.json")
    with open(config_path, "w") as f:
        json.dump(config_data, f)

    config = Config(config_path=config_path)
    storage = TaskStorage(config)

    # Create tasks with varied properties and controlled timestamps
    test_tasks = [
        Task(id="Ts1aB2", body="Fix login bug", status="todo",
             feature_tags=["bug", "frontend"],
             created_date="2026-02-15 10:00:00",
             last_modified="2026-02-15 10:00:00"),
        Task(id="Ts2cD4", body="Add dark mode", status="backlog",
             feature_tags=["feature", "frontend"],
             created_date="2026-02-16 10:00:00",
             last_modified="2026-02-16 10:00:00"),
        Task(id="Ts3eF6", body="Write API tests", status="in_progress",
             feature_tags=["test", "backend"],
             agent_response="",
             created_date="2026-02-17 10:00:00",
             last_modified="2026-02-17 10:00:00"),
        Task(id="Ts4gH8", body="Deploy to staging", status="done",
             feature_tags=["devops"],
             agent_response="Deployed successfully",
             commit_hash="abc1234",
             created_date="2026-02-18 10:00:00",
             last_modified="2026-02-18 10:00:00"),
        Task(id="Ts5iJ0", body="Archive old data", status="archive",
             feature_tags=["cleanup"],
             agent_response="Archived 500 records",
             created_date="2026-02-14 10:00:00",
             last_modified="2026-02-14 10:00:00"),
    ]

    for task in test_tasks:
        storage.write_task(task)

    return config, storage, test_tasks


class TestSearchFilters:
    """SearchFilters dataclass defaults."""

    def test_defaults(self):
        f = SearchFilters()
        assert f.id is None
        assert f.status is None
        assert f.tag is None
        assert f.limit == 5
        assert f.open_only is False
        assert f.case_sensitive is False

    def test_custom_values(self):
        f = SearchFilters(status="todo", tag="urgent", limit=10)
        assert f.status == "todo"
        assert f.tag == "urgent"
        assert f.limit == 10


class TestPythonSearchFiltering:
    """PythonSearch._matches_filters() - the core filter logic."""

    @pytest.fixture
    def python_search(self, storage_with_tasks):
        config, storage, _ = storage_with_tasks
        return PythonSearch(storage)

    def test_filter_by_id(self, python_search):
        filters = SearchFilters(id="Ts1aB2", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 1
        assert results[0]["id"] == "Ts1aB2"

    def test_filter_by_status_string(self, python_search):
        filters = SearchFilters(status="todo", limit=10)
        results = python_search.search_all(filters)
        assert all(r["status"] == "todo" for r in results)

    def test_filter_by_status_list(self, python_search):
        filters = SearchFilters(status=["todo", "backlog"], limit=10)
        results = python_search.search_all(filters)
        assert all(r["status"] in ["todo", "backlog"] for r in results)
        assert len(results) == 2

    def test_filter_by_tag_string(self, python_search):
        filters = SearchFilters(tag="frontend", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 2
        assert all("frontend" in r.get("feature_tags", []) for r in results)

    def test_filter_by_tag_list(self, python_search):
        filters = SearchFilters(tag=["bug", "test"], limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 2  # "Fix login bug" (bug) + "Write API tests" (test)

    def test_filter_by_exclude_tags(self, python_search):
        filters = SearchFilters(exclude_tags="frontend", limit=10)
        results = python_search.search_all(filters)
        assert all("frontend" not in r.get("feature_tags", []) for r in results)

    def test_filter_by_exclude_tags_list(self, python_search):
        filters = SearchFilters(exclude_tags=["frontend", "devops"], limit=10)
        results = python_search.search_all(filters)
        for r in results:
            tags = r.get("feature_tags", [])
            assert "frontend" not in tags
            assert "devops" not in tags

    def test_filter_by_commit_hash(self, python_search):
        filters = SearchFilters(commit_hash="abc1234", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 1
        assert results[0]["id"] == "Ts4gH8"

    def test_filter_by_body_text(self, python_search):
        filters = SearchFilters(body_text="login", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 1
        assert "login" in results[0]["body"].lower()

    def test_filter_by_body_text_case_insensitive(self, python_search):
        filters = SearchFilters(body_text="FIX LOGIN", case_sensitive=False, limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 1

    def test_filter_by_body_text_case_sensitive(self, python_search):
        filters = SearchFilters(body_text="FIX LOGIN", case_sensitive=True, limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 0

    def test_filter_by_response_text(self, python_search):
        filters = SearchFilters(response_text="deployed", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 1
        assert results[0]["id"] == "Ts4gH8"

    def test_filter_open_only(self, python_search):
        filters = SearchFilters(open_only=True, limit=10)
        results = python_search.search_all(filters)
        # Tasks with empty or no agent_response: Ts1aB2, Ts2cD4, Ts3eF6
        assert len(results) == 3
        for r in results:
            assert not r.get("agent_response", "").strip()

    def test_limit_respected(self, python_search):
        filters = SearchFilters(limit=2)
        results = python_search.search_all(filters)
        assert len(results) == 2

    def test_sort_order_asc(self, python_search):
        filters = SearchFilters(limit=10, sort_order='asc')
        results = python_search.search_all(filters)

        assert len(results) >= 2
        for i in range(len(results) - 1):
            assert results[i]['last_modified'] <= results[i + 1]['last_modified']

    def test_combined_filters(self, python_search):
        filters = SearchFilters(status="todo", tag="bug", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 1
        assert results[0]["id"] == "Ts1aB2"

    def test_no_matches(self, python_search):
        filters = SearchFilters(status="todo", tag="nonexistent", limit=10)
        results = python_search.search_all(filters)
        assert len(results) == 0


class TestPythonSearchPrioritized:
    """PythonSearch.search_all_prioritized() for list command sorting."""

    @pytest.fixture
    def python_search(self, storage_with_tasks):
        config, storage, _ = storage_with_tasks
        return PythonSearch(storage)

    def test_open_before_closed(self, python_search):
        """Open statuses (backlog, todo, in_progress) should appear before closed (done, archive)."""
        filters = SearchFilters(limit=10)
        results = python_search.search_all_prioritized(filters, sort_order='desc')

        open_statuses = {"backlog", "todo", "in_progress"}
        closed_statuses = {"done", "archive"}

        found_closed = False
        for r in results:
            if r["status"] in closed_statuses:
                found_closed = True
            if r["status"] in open_statuses and found_closed:
                pytest.fail("Open task appeared after closed task in prioritized sort")

    def test_sort_asc(self, python_search):
        """Ascending sort: oldest first within each group."""
        filters = SearchFilters(limit=10)
        results = python_search.search_all_prioritized(filters, sort_order='asc')

        open_results = [r for r in results if r["status"] in ["backlog", "todo", "in_progress"]]
        if len(open_results) >= 2:
            for i in range(len(open_results) - 1):
                assert open_results[i]["last_modified"] <= open_results[i + 1]["last_modified"]

    def test_sort_desc(self, python_search):
        """Descending sort: newest first within each group."""
        filters = SearchFilters(limit=10)
        results = python_search.search_all_prioritized(filters, sort_order='desc')

        open_results = [r for r in results if r["status"] in ["backlog", "todo", "in_progress"]]
        if len(open_results) >= 2:
            for i in range(len(open_results) - 1):
                assert open_results[i]["last_modified"] >= open_results[i + 1]["last_modified"]


class TestSortHelpers:
    """Shared sort helper behavior used across list/search/ready commands."""

    def test_sort_tasks_by_last_modified_uses_id_tie_breaker(self):
        tasks = [
            {"id": "Cc3Dd4", "last_modified": "2026-02-15 10:00:00"},
            {"id": "Aa1Bb2", "last_modified": "2026-02-15 10:00:00"},
        ]

        asc = sort_tasks_by_last_modified(tasks, 'asc')
        desc = sort_tasks_by_last_modified(tasks, 'desc')

        assert [task['id'] for task in asc] == ['Aa1Bb2', 'Cc3Dd4']
        assert [task['id'] for task in desc] == ['Cc3Dd4', 'Aa1Bb2']

    def test_sort_tasks_with_status_priority_keeps_open_before_closed(self):
        tasks = [
            {"id": "Dn1Xe2", "status": "done", "last_modified": "2026-02-18 10:00:00"},
            {"id": "Td1Xe2", "status": "todo", "last_modified": "2026-02-17 10:00:00"},
            {"id": "Ar1Xe2", "status": "archive", "last_modified": "2026-02-19 10:00:00"},
            {"id": "Bk1Xe2", "status": "backlog", "last_modified": "2026-02-16 10:00:00"},
        ]

        results = sort_tasks_with_status_priority(tasks, 'desc')

        assert [task['id'] for task in results] == ['Td1Xe2', 'Bk1Xe2', 'Ar1Xe2', 'Dn1Xe2']

    def test_sort_tasks_by_status_sequence_respects_explicit_status_order(self):
        tasks = [
            {"id": "StA111", "status": "todo", "last_modified": "2026-02-17 10:00:00"},
            {"id": "StA222", "status": "backlog", "last_modified": "2026-02-18 10:00:00"},
            {"id": "StA333", "status": "in_progress", "last_modified": "2026-02-16 10:00:00"},
            {"id": "StA444", "status": "done", "last_modified": "2026-02-19 10:00:00"},
        ]

        results = sort_tasks_by_status_sequence(tasks, ['backlog', 'in_progress'], 'desc')

        assert [task['id'] for task in results] == ['StA222', 'StA333', 'StA444', 'StA111']


class TestTaskSearch:
    """TaskSearch main interface (backend routing)."""

    @pytest.fixture
    def task_search(self, storage_with_tasks):
        config, storage, _ = storage_with_tasks
        return TaskSearch(config=config, storage=storage)

    def test_search_by_id(self, task_search):
        result = task_search.search_by_id("Ts1aB2")
        assert result is not None
        assert result["body"] == "Fix login bug"

    def test_search_by_id_not_found(self, task_search):
        result = task_search.search_by_id("Zz9xY1")
        assert result is None

    def test_search_by_status(self, task_search):
        results = task_search.search_by_status("todo")
        assert len(results) >= 1
        assert all(r["status"] == "todo" for r in results)

    def test_search_by_tag(self, task_search):
        results = task_search.search_by_tag("frontend")
        assert len(results) >= 1

    def test_search_open_tasks(self, task_search):
        results = task_search.search_open_tasks(limit=10)
        assert len(results) == 3

    def test_search_prioritized_list(self, task_search):
        results = task_search.search_prioritized_list(limit=10)
        assert len(results) == 5

    def test_get_info(self, task_search):
        info = task_search.get_info()
        assert "backend" in info
        assert "base_path" in info

    def test_search_with_combined_filters(self, task_search):
        filters = SearchFilters(status="todo", tag="bug", limit=10)
        results = task_search.search(filters)
        assert len(results) == 1
