"""Integration tests for end-to-end kanban workflows.

Why: Unit tests validate individual modules in isolation. These integration tests verify
that models, storage, search, and config work together correctly through realistic
workflows. Bugs at module boundaries (e.g., Task -> Storage -> Search) are invisible
to unit tests but critical to catch.
"""

import json
import os
import pytest
from copy import deepcopy
from kanban.config import Config
from kanban.storage import TaskStorage
from kanban.search import TaskSearch, SearchFilters
from kanban.models import Task, parse_related_task_ids, parse_blocked_by_ids
from kanban.validators import ValidationError
from kanban.graph import DependencyGraph


@pytest.fixture
def kanban_env(tmp_path):
    """Set up a complete kanban environment (config + storage + search)."""
    tasks_dir = tmp_path / ".juno_task" / "tasks"
    tasks_dir.mkdir(parents=True)

    config_data = deepcopy(Config.DEFAULT_CONFIG)
    config_data["storage"]["base_path"] = str(tasks_dir)

    config_path = str(tasks_dir / "config.json")
    with open(config_path, "w") as f:
        json.dump(config_data, f)

    config = Config(config_path=config_path)
    storage = TaskStorage(config)
    search = TaskSearch(config=config, storage=storage)

    return config, storage, search


class TestTaskLifecycle:
    """Full task lifecycle: create -> update -> search -> delete."""

    def test_create_find_update_delete(self, kanban_env):
        config, storage, search = kanban_env

        # Create
        task = storage.create_task(body="Implement feature X", status="backlog",
                                   feature_tags=["feature"])
        assert task.id is not None

        # Find by ID
        found = search.search_by_id(task.id)
        assert found is not None
        assert found["body"] == "Implement feature X"

        # Update status
        storage.update_task(task.id, {"status": "in_progress"})
        found = search.search_by_id(task.id)
        assert found["status"] == "in_progress"

        # Add response
        storage.update_task(task.id, {"agent_response": "Feature implemented"})
        found = search.search_by_id(task.id)
        assert found["agent_response"] == "Feature implemented"

        # Delete
        result = storage.delete_task(task.id)
        assert result is True
        assert search.search_by_id(task.id) is None

    def test_status_workflow(self, kanban_env):
        """Task moves through full workflow: backlog -> todo -> in_progress -> done -> archive."""
        config, storage, search = kanban_env

        task = storage.create_task(body="Workflow task", status="backlog")

        for next_status in ["todo", "in_progress", "done", "archive"]:
            storage.update_task(task.id, {"status": next_status})
            found = search.search_by_id(task.id)
            assert found["status"] == next_status


class TestBulkOperations:
    """Multiple tasks with filtering and counting."""

    def test_create_and_filter_by_status(self, kanban_env):
        config, storage, search = kanban_env

        for i in range(3):
            storage.create_task(body=f"Backlog {i}", status="backlog")
        for i in range(2):
            storage.create_task(body=f"Todo {i}", status="todo")
        storage.create_task(body="Done task", status="done",
                            agent_response="Completed")

        # Count
        assert storage.count_tasks() == 6

        # Filter by status
        todos = search.search_by_status("todo", limit=10)
        assert len(todos) == 2

        backlogs = search.search_by_status("backlog", limit=10)
        assert len(backlogs) == 3

    def test_create_and_filter_by_tags(self, kanban_env):
        config, storage, search = kanban_env

        storage.create_task(body="Frontend bug", status="todo",
                            feature_tags=["bug", "frontend"])
        storage.create_task(body="Backend bug", status="todo",
                            feature_tags=["bug", "backend"])
        storage.create_task(body="Feature", status="todo",
                            feature_tags=["feature"])

        bug_tasks = search.search_by_tag("bug", limit=10)
        assert len(bug_tasks) == 2

        frontend_tasks = search.search_by_tag("frontend", limit=10)
        assert len(frontend_tasks) == 1


class TestRelatedTasks:
    """Inter-task linking with [task_id] format."""

    def test_parse_and_create_with_related(self, kanban_env):
        config, storage, search = kanban_env

        # Create first task
        task1 = storage.create_task(body="Parent task", status="todo")

        # Create second task with reference
        body = f"Child task [task_id]{task1.id}[/task_id]"
        related_ids = parse_related_task_ids(body)
        assert task1.id in related_ids

        task2 = storage.create_task(body=body, status="todo",
                                    related_tasks=related_ids)

        found = search.search_by_id(task2.id)
        assert found["related_tasks"] == [task1.id]


class TestDataIntegrity:
    """Data consistency across operations."""

    def test_update_does_not_corrupt_other_tasks(self, kanban_env):
        config, storage, search = kanban_env

        tasks = []
        for i in range(5):
            t = storage.create_task(body=f"Task {i}", status="todo")
            tasks.append(t)

        # Update middle task
        storage.update_task(tasks[2].id, {"status": "done", "agent_response": "Fixed"})

        # Verify all others unchanged
        for i, t in enumerate(tasks):
            found = search.search_by_id(t.id)
            assert found is not None
            if i == 2:
                assert found["status"] == "done"
            else:
                assert found["status"] == "todo"

    def test_delete_does_not_corrupt_other_tasks(self, kanban_env):
        config, storage, search = kanban_env

        tasks = []
        for i in range(5):
            t = storage.create_task(body=f"Task {i}", status="todo")
            tasks.append(t)

        # Delete middle task
        storage.delete_task(tasks[2].id)

        # Verify others still exist
        assert storage.count_tasks() == 4
        for i, t in enumerate(tasks):
            if i == 2:
                assert search.search_by_id(t.id) is None
            else:
                assert search.search_by_id(t.id) is not None

    def test_concurrent_writes_do_not_lose_data(self, kanban_env):
        """Write many tasks rapidly - file locking should prevent data loss."""
        config, storage, search = kanban_env

        task_ids = []
        for i in range(20):
            t = storage.create_task(body=f"Rapid task {i}", status="todo")
            task_ids.append(t.id)

        # All should be findable
        assert storage.count_tasks() == 20
        for tid in task_ids:
            assert search.search_by_id(tid) is not None


class TestValidationIntegration:
    """Validation errors propagate correctly through the stack."""

    def test_create_task_with_invalid_status(self, kanban_env):
        config, storage, search = kanban_env
        with pytest.raises(ValidationError):
            storage.create_task(body="Bad status", status="nonexistent")

    def test_update_task_with_invalid_status(self, kanban_env):
        config, storage, search = kanban_env
        task = storage.create_task(body="Good task", status="todo")

        with pytest.raises(ValidationError):
            storage.update_task(task.id, {"status": "nonexistent"})

        # Task should be unchanged
        found = search.search_by_id(task.id)
        assert found["status"] == "todo"

    def test_create_task_with_invalid_tags(self, kanban_env):
        config, storage, search = kanban_env
        with pytest.raises(ValidationError):
            storage.create_task(body="Bad tags", status="todo",
                                feature_tags=["has space"])


class TestSearchPrioritization:
    """Prioritized list sorting across the full stack."""

    def test_prioritized_list_open_before_closed(self, kanban_env):
        config, storage, search = kanban_env

        storage.create_task(body="Done", status="done", agent_response="x")
        storage.create_task(body="Todo", status="todo")
        storage.create_task(body="Backlog", status="backlog")
        storage.create_task(body="Archive", status="archive", agent_response="x")
        storage.create_task(body="InProgress", status="in_progress")

        results = search.search_prioritized_list(limit=10)

        open_statuses = {"backlog", "todo", "in_progress"}
        closed_statuses = {"done", "archive"}

        found_closed = False
        for r in results:
            if r["status"] in closed_statuses:
                found_closed = True
            if r["status"] in open_statuses and found_closed:
                pytest.fail("Open task appeared after closed task")


class TestDependencyWorkflows:
    """End-to-end tests for blocked_by dependency system through CLI components.

    Why: The dependency system spans models (parse_blocked_by_ids), graph (DependencyGraph),
    and CLI (create/update with --blocked-by). Integration tests verify these modules work
    together: body parsing feeds into validation, validation feeds into cycle detection,
    cycle detection prevents invalid state in storage.
    """

    def test_create_with_blocked_by(self, kanban_env):
        """Create a task with blocked_by and verify it persists."""
        config, storage, search = kanban_env

        blocker = storage.create_task(body="Blocker task", status="todo")
        dependent = storage.create_task(
            body="Dependent task", status="backlog", blocked_by=[blocker.id]
        )

        found = search.search_by_id(dependent.id)
        assert found is not None
        assert found["blocked_by"] == [blocker.id]

    def test_blocked_by_body_parsing_integration(self, kanban_env):
        """Body parsing with [blocked_by] tags integrates with task creation."""
        config, storage, search = kanban_env

        blocker = storage.create_task(body="Prereq task", status="todo")
        body = f"Needs [blocked_by]{blocker.id}[/blocked_by] done first"

        parsed = parse_blocked_by_ids(body)
        assert blocker.id in parsed

        dependent = storage.create_task(body=body, status="backlog", blocked_by=parsed)
        found = search.search_by_id(dependent.id)
        assert found["blocked_by"] == [blocker.id]

    def test_blocked_by_all_synonym_tags(self, kanban_env):
        """All 4 synonym tags parse correctly and merge/deduplicate."""
        config, storage, search = kanban_env

        t1 = storage.create_task(body="T1", status="todo")
        t2 = storage.create_task(body="T2", status="todo")

        body = (
            f"Needs [blocked_by]{t1.id}[/blocked_by] and "
            f"[block]{t2.id}[/block] and "
            f"[parent_task]{t1.id}[/parent_task]"  # duplicate t1
        )

        parsed = parse_blocked_by_ids(body)
        assert t1.id in parsed
        assert t2.id in parsed
        # Deduplication: t1 appears only once
        assert parsed.count(t1.id) == 1

    def test_update_blocked_by(self, kanban_env):
        """Update a task's blocked_by field and verify it persists."""
        config, storage, search = kanban_env

        blocker = storage.create_task(body="Blocker", status="todo")
        task = storage.create_task(body="Task", status="backlog")

        # Initially no blocked_by
        found = search.search_by_id(task.id)
        assert found.get("blocked_by") is None

        # Update
        storage.update_task(task.id, {"blocked_by": [blocker.id]})
        found = search.search_by_id(task.id)
        assert found["blocked_by"] == [blocker.id]

    def test_graph_ready_tasks_with_storage(self, kanban_env):
        """DependencyGraph correctly identifies ready tasks from persisted data."""
        config, storage, search = kanban_env

        t1 = storage.create_task(body="Task 1", status="done")
        t2 = storage.create_task(body="Task 2 blocked by T1", status="backlog",
                                  blocked_by=[t1.id])
        t3 = storage.create_task(body="Task 3 blocked by T2", status="backlog",
                                  blocked_by=[t2.id])

        # Load all tasks and build graph (read_tasks yields dicts)
        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)

        ready = graph.get_ready_tasks()
        assert t2.id in ready  # t1 is done, so t2 is ready
        assert t3.id not in ready  # t2 is not done, so t3 is blocked

    def test_cycle_detection_prevents_circular_deps(self, kanban_env):
        """Cycle detection catches A -> B -> A before it reaches storage."""
        config, storage, search = kanban_env

        t1 = storage.create_task(body="Task A", status="todo")
        t2 = storage.create_task(body="Task B blocked by A", status="backlog",
                                  blocked_by=[t1.id])

        # Build graph and check: would updating t1 to be blocked by t2 create a cycle?
        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        # Simulate the proposed update
        for t in all_tasks:
            if t['id'] == t1.id:
                t['blocked_by'] = [t2.id]

        graph = DependencyGraph(all_tasks)
        cycle = graph.detect_cycle(t2.id, t1.id)
        assert cycle is not None, "Should detect cycle: A -> B -> A"

    def test_self_dependency_rejected(self, kanban_env):
        """Self-dependency is rejected by cycle detection."""
        config, storage, search = kanban_env

        task = storage.create_task(body="Task", status="todo")

        all_tasks = [{'id': task.id, 'status': 'todo', 'blocked_by': [task.id]}]
        graph = DependencyGraph(all_tasks)
        cycle = graph.detect_cycle(task.id, task.id)
        assert cycle is not None

    def test_clear_blocked_by(self, kanban_env):
        """Setting blocked_by to empty list clears dependencies."""
        config, storage, search = kanban_env

        blocker = storage.create_task(body="Blocker", status="todo")
        task = storage.create_task(body="Task", status="backlog",
                                    blocked_by=[blocker.id])

        found = search.search_by_id(task.id)
        assert found["blocked_by"] == [blocker.id]

        # Clear blocked_by
        storage.update_task(task.id, {"blocked_by": []})
        found = search.search_by_id(task.id)
        assert found["blocked_by"] == []

    def test_backward_compat_tasks_without_blocked_by(self, kanban_env):
        """Old tasks without blocked_by field still load and work."""
        config, storage, search = kanban_env

        # Create task normally (will have blocked_by=None)
        task = storage.create_task(body="Old task", status="todo")
        found = search.search_by_id(task.id)
        assert found.get("blocked_by") is None

        # Can still update other fields
        storage.update_task(task.id, {"status": "in_progress"})
        found = search.search_by_id(task.id)
        assert found["status"] == "in_progress"
        assert found.get("blocked_by") is None


class TestDependencyQueryCommands:
    """Integration tests for deps, ready, and order CLI commands.

    Why: These commands integrate the DependencyGraph engine with storage/search
    through the CLI layer. Testing through TaskCLI.run() verifies argument parsing,
    graph construction from persisted data, and output formatting all work together.
    """

    def test_ready_returns_unblocked_tasks(self, kanban_env):
        """ready command returns tasks whose blockers are all resolved."""
        config, storage, search = kanban_env

        # A(todo, no deps), B(todo, blocked_by A), C(backlog, blocked_by B)
        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        ready = graph.get_ready_tasks()

        assert a.id in ready
        assert b.id not in ready  # blocked by A (not done)
        assert c.id not in ready  # blocked by B (not done)

    def test_ready_after_blocker_resolved(self, kanban_env):
        """After marking blocker done, dependent becomes ready."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        # Mark A done
        storage.update_task(a.id, {"status": "done"})

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        ready = graph.get_ready_tasks()

        assert b.id in ready  # A is done, so B is now ready
        assert c.id not in ready  # B is still not done

    def test_ready_tasks_with_no_deps_are_always_ready(self, kanban_env):
        """Tasks with null/empty blocked_by are always ready."""
        config, storage, search = kanban_env

        t1 = storage.create_task(body="No deps", status="todo")
        t2 = storage.create_task(body="Also no deps", status="backlog")

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        ready = graph.get_ready_tasks()

        assert t1.id in ready
        assert t2.id in ready

    def test_ready_excludes_done_archive_tasks(self, kanban_env):
        """Done and archive tasks are never in ready list even if unblocked."""
        config, storage, search = kanban_env

        storage.create_task(body="Done task", status="done")
        storage.create_task(body="Archive task", status="archive")
        active = storage.create_task(body="Active task", status="todo")

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        ready = graph.get_ready_tasks()

        assert active.id in ready
        assert len(ready) == 1

    def test_order_respects_dependency_chain(self, kanban_env):
        """order returns tasks in correct topological order."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        # Filter to open tasks only (like cmd_order does)
        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                if task.get('status') in ('backlog', 'todo', 'in_progress'):
                    all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        order = graph.topological_sort()

        assert order.index(a.id) < order.index(b.id)
        assert order.index(b.id) < order.index(c.id)

    def test_order_with_priority_scores(self, kanban_env):
        """Priority scores reflect transitive dependent count."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Root", status="todo")
        b = storage.create_task(body="Mid", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Leaf", status="todo", blocked_by=[b.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)

        assert graph.get_priority_score(a.id) == 2  # blocks B and C transitively
        assert graph.get_priority_score(b.id) == 1  # blocks C only
        assert graph.get_priority_score(c.id) == 0  # leaf, blocks nothing

    def test_deps_shows_blockers_and_dependents(self, kanban_env):
        """deps command shows correct blocker/dependent info."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Task A", status="done")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)

        # B's blockers = [A], dependents = [C]
        assert graph.get_blockers(b.id) == [a.id]
        assert graph.get_dependents(b.id) == [c.id]

        # A's dependents = [B]
        assert graph.get_dependents(a.id) == [b.id]
        assert graph.get_blockers(a.id) == []

        # C's blockers = [B], dependents = []
        assert graph.get_blockers(c.id) == [b.id]
        assert graph.get_dependents(c.id) == []

    def test_deps_add_with_cycle_detection(self, kanban_env):
        """deps add rejects changes that would create cycles."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])

        # Attempt: add A blocked_by B → would create cycle A→B→A
        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        # Simulate proposed update
        for t in all_tasks:
            if t['id'] == a.id:
                t['blocked_by'] = [b.id]

        graph = DependencyGraph(all_tasks)
        cycle = graph.detect_cycle(b.id, a.id)
        assert cycle is not None, "Should detect cycle: A→B→A"

    def test_deps_remove(self, kanban_env):
        """deps remove correctly removes a blocker."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Blocker", status="todo")
        b = storage.create_task(body="Dependent", status="backlog", blocked_by=[a.id])

        # Verify blocked_by is set
        found = search.search_by_id(b.id)
        assert found["blocked_by"] == [a.id]

        # Remove the dependency
        existing = found.get('blocked_by') or []
        updated_blocked = [bid for bid in existing if bid != a.id]
        storage.update_task(b.id, {"blocked_by": updated_blocked})

        found = search.search_by_id(b.id)
        assert found["blocked_by"] == []

    def test_get_includes_dependency_info(self, kanban_env):
        """get command output includes _dependency_info when task has dependencies."""
        config, storage, search = kanban_env

        a = storage.create_task(body="Blocker A", status="in_progress")
        b = storage.create_task(body="Task B", status="backlog", blocked_by=[a.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)

        # B has blockers (A) and A is not done → is_blocked=True
        blockers = graph.get_blockers(b.id)
        dependents = graph.get_dependents(b.id)
        assert blockers == [a.id]
        assert dependents == []

        # A has no blockers but has dependents (B)
        a_blockers = graph.get_blockers(a.id)
        a_dependents = graph.get_dependents(a.id)
        assert a_blockers == []
        assert a_dependents == [b.id]

    def test_ready_with_diamond_dependency(self, kanban_env):
        """Diamond dependency: A→C, B→C. Both A and B must be done for C to be ready."""
        config, storage, search = kanban_env

        a = storage.create_task(body="A", status="todo")
        b = storage.create_task(body="B", status="todo")
        c = storage.create_task(body="C", status="backlog", blocked_by=[a.id, b.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        ready = graph.get_ready_tasks()

        assert a.id in ready
        assert b.id in ready
        assert c.id not in ready  # Both A and B must be done

        # Mark only A done → C still blocked
        storage.update_task(a.id, {"status": "done"})
        all_tasks2 = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks2.append(task)
        graph2 = DependencyGraph(all_tasks2)
        assert c.id not in graph2.get_ready_tasks()

        # Mark B done too → C now ready
        storage.update_task(b.id, {"status": "done"})
        all_tasks3 = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks3.append(task)
        graph3 = DependencyGraph(all_tasks3)
        assert c.id in graph3.get_ready_tasks()
