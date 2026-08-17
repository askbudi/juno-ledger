"""End-to-end integration tests for the task dependency workflow.

Why: The dependency system spans models (blocked_by parsing), storage (persistence),
graph (topological sort, cycle detection, ready queries), and CLI (deps/ready/order commands).
These tests verify the full workflow from task creation through dependency resolution,
catching integration bugs that unit tests on individual modules cannot.
"""

import json
import pytest
from copy import deepcopy
from kanban.config import Config
from kanban.storage import TaskStorage
from kanban.search import TaskSearch
from kanban.models import parse_blocked_by_ids
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


def _load_graph(storage):
    """Helper: load all tasks from storage and build a DependencyGraph."""
    all_tasks = []
    for filepath in storage.get_files():
        for task in storage.read_tasks(filepath):
            all_tasks.append(task)
    return DependencyGraph(all_tasks)


class TestFullDependencyChain:
    """Test progressive dependency resolution through A -> B -> C chain.

    Why: Validates that marking blockers as done correctly unlocks downstream
    tasks one at a time, and that the graph engine reflects storage state changes.
    """

    def test_initial_state_only_root_is_ready(self, kanban_env):
        """Create A (no deps), B (blocked_by A), C (blocked_by B).
        Only A should be ready initially."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Task A - root", status="todo")
        b = storage.create_task(body="Task B - mid", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C - leaf", status="backlog", blocked_by=[b.id])

        graph = _load_graph(storage)
        ready = graph.get_ready_tasks()

        assert a.id in ready
        assert b.id not in ready
        assert c.id not in ready

    def test_mark_a_done_unlocks_b_only(self, kanban_env):
        """After marking A done, B becomes ready but C is still blocked."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        storage.update_task(a.id, {"status": "done"})

        graph = _load_graph(storage)
        ready = graph.get_ready_tasks()

        assert b.id in ready
        assert c.id not in ready

    def test_mark_b_done_unlocks_c(self, kanban_env):
        """After marking both A and B done, C becomes ready."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        storage.update_task(a.id, {"status": "done"})
        storage.update_task(b.id, {"status": "done"})

        graph = _load_graph(storage)
        ready = graph.get_ready_tasks()

        assert c.id in ready

    def test_order_reflects_chain(self, kanban_env):
        """Topological order must be A before B before C."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[b.id])

        graph = _load_graph(storage)
        order = graph.topological_sort()

        assert order.index(a.id) < order.index(b.id)
        assert order.index(b.id) < order.index(c.id)


class TestDiamondDependencies:
    """Test diamond dependency pattern: A and B both block C.

    Why: Diamond dependencies are a common real-world pattern where a task
    requires multiple independent prerequisites. The graph engine must require
    ALL blockers to be resolved, not just one.
    """

    def test_both_roots_ready_leaf_blocked(self, kanban_env):
        """A and B (no deps) are ready. C (blocked_by A and B) is blocked."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Prereq A", status="todo")
        b = storage.create_task(body="Prereq B", status="todo")
        c = storage.create_task(body="Depends on both", status="backlog", blocked_by=[a.id, b.id])

        graph = _load_graph(storage)
        ready = graph.get_ready_tasks()

        assert a.id in ready
        assert b.id in ready
        assert c.id not in ready

    def test_one_resolved_still_blocked(self, kanban_env):
        """Marking only A done does not unblock C (B still blocking)."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Prereq A", status="todo")
        b = storage.create_task(body="Prereq B", status="todo")
        c = storage.create_task(body="Depends on both", status="backlog", blocked_by=[a.id, b.id])

        storage.update_task(a.id, {"status": "done"})

        graph = _load_graph(storage)
        assert c.id not in graph.get_ready_tasks()

    def test_both_resolved_unlocks_leaf(self, kanban_env):
        """Marking both A and B done unlocks C."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Prereq A", status="todo")
        b = storage.create_task(body="Prereq B", status="todo")
        c = storage.create_task(body="Depends on both", status="backlog", blocked_by=[a.id, b.id])

        storage.update_task(a.id, {"status": "done"})
        storage.update_task(b.id, {"status": "done"})

        graph = _load_graph(storage)
        assert c.id in graph.get_ready_tasks()


class TestCycleRejection:
    """Test that circular dependencies are detected and prevented.

    Why: Cycles in the dependency graph would cause infinite loops in topological
    sort and make tasks permanently unresolvable. The graph engine must detect
    cycles before they are persisted.
    """

    def test_direct_cycle_detected(self, kanban_env):
        """A -> B -> A cycle is detected."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo", blocked_by=[a.id])

        # Simulate proposed: A blocked_by B (would create A->B->A cycle)
        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        cycle = graph.detect_cycle(b.id, a.id)
        assert cycle is not None, "Should detect direct cycle A->B->A"

    def test_transitive_cycle_detected(self, kanban_env):
        """A -> B -> C, then C -> A would create a transitive cycle."""
        _, storage, _ = kanban_env

        # Mixed-case shard prefixes must remain deterministic on both
        # case-sensitive and case-insensitive filesystems.
        a = storage.create_task(id="0mAAAA", body="Task A", status="todo")
        b = storage.create_task(id="0MBBBB", body="Task B", status="todo", blocked_by=[a.id])
        c = storage.create_task(id="0mCCCC", body="Task C", status="backlog", blocked_by=[b.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        # Proposed: A becomes blocked_by C (C blocks A)
        # detect_cycle(from_id=C, to_id=A): checks if path exists from A to C
        # Path A->B->C exists, so adding C->A would create cycle C->A->B->C
        cycle = graph.detect_cycle(c.id, a.id)
        assert cycle is not None, "Should detect transitive cycle C->A->B->C"

    def test_no_false_positive_cycle(self, kanban_env):
        """Adding a valid dependency should not be rejected as a cycle."""
        _, storage, _ = kanban_env

        a = storage.create_task(body="Task A", status="todo")
        b = storage.create_task(body="Task B", status="todo")
        c = storage.create_task(body="Task C", status="backlog", blocked_by=[a.id])

        all_tasks = []
        for filepath in storage.get_files():
            for task in storage.read_tasks(filepath):
                all_tasks.append(task)

        graph = DependencyGraph(all_tasks)
        # Adding C blocked_by B is valid (no cycle)
        cycle = graph.detect_cycle(b.id, c.id)
        assert cycle is None, "Should not detect cycle for valid dependency"


class TestMixedRelatedAndBlocked:
    """Test tasks with both related_tasks and blocked_by fields.

    Why: related_tasks (informational links) and blocked_by (blocking dependencies)
    are independent features that can coexist on the same task. They must not
    interfere with each other.
    """

    def test_both_fields_persisted_independently(self, kanban_env):
        _, storage, search = kanban_env

        ref = storage.create_task(body="Reference task", status="done")
        blocker = storage.create_task(body="Blocker task", status="todo")

        # Body markup is parsed by CLI layer, not storage. Pass both explicitly.
        task = storage.create_task(
            body=f"Task with both refs and blockers",
            status="backlog",
            blocked_by=[blocker.id],
            related_tasks=[ref.id],
        )

        found = search.search_by_id(task.id)
        assert found["blocked_by"] == [blocker.id]
        assert found.get("related_tasks") == [ref.id]

    def test_related_does_not_affect_readiness(self, kanban_env):
        """A task referencing a related task (not blocked_by) should be ready
        regardless of the related task's status."""
        _, storage, _ = kanban_env

        related = storage.create_task(body="Related (in_progress)", status="in_progress")
        task = storage.create_task(
            body=f"Refs [task_id]{related.id}[/task_id] but not blocked",
            status="todo",
        )

        graph = _load_graph(storage)
        ready = graph.get_ready_tasks()

        # Task has related_tasks but NO blocked_by, so it should be ready
        assert task.id in ready

    def test_blocked_by_controls_readiness_not_related(self, kanban_env):
        """blocked_by controls readiness; related_tasks is purely informational."""
        _, storage, _ = kanban_env

        blocker = storage.create_task(body="Blocker", status="todo")
        related = storage.create_task(body="Related (done)", status="done")

        task = storage.create_task(
            body=f"Refs [task_id]{related.id}[/task_id]",
            status="backlog",
            blocked_by=[blocker.id],
        )

        graph = _load_graph(storage)
        ready = graph.get_ready_tasks()

        # blocker is not done → task is blocked, even though related task is done
        assert task.id not in ready

        # Resolve blocker → task becomes ready
        storage.update_task(blocker.id, {"status": "done"})
        graph2 = _load_graph(storage)
        assert task.id in graph2.get_ready_tasks()


class TestSynonymBodyParsing:
    """Test that all 4 body markup synonyms are parsed to blocked_by.

    Why: The system supports multiple tag names for declaring blockers in task
    body text, accommodating different mental models (parent_task, block, etc.).
    All synonyms must produce the same result in the blocked_by field.
    """

    def test_blocked_by_tag(self, kanban_env):
        """Body markup [blocked_by] is parsed and used to populate blocked_by field."""
        _, storage, search = kanban_env

        blocker = storage.create_task(body="Blocker", status="todo")
        body = f"Needs [blocked_by]{blocker.id}[/blocked_by] done first"
        parsed = parse_blocked_by_ids(body)
        assert blocker.id in parsed

        # Create task using parsed blocked_by (as CLI layer would)
        task = storage.create_task(body=body, status="backlog", blocked_by=parsed)
        found = search.search_by_id(task.id)
        assert blocker.id in (found.get("blocked_by") or [])

    def test_block_by_tag(self, kanban_env):
        """Body markup [block_by] is a synonym for [blocked_by]."""
        _, storage, search = kanban_env

        blocker = storage.create_task(body="Blocker", status="todo")
        body = f"Needs [block_by]{blocker.id}[/block_by] done first"
        parsed = parse_blocked_by_ids(body)
        assert blocker.id in parsed

        task = storage.create_task(body=body, status="backlog", blocked_by=parsed)
        found = search.search_by_id(task.id)
        assert blocker.id in (found.get("blocked_by") or [])

    def test_block_tag(self, kanban_env):
        """Body markup [block] is a synonym for [blocked_by]."""
        _, storage, search = kanban_env

        blocker = storage.create_task(body="Blocker", status="todo")
        body = f"Needs [block]{blocker.id}[/block] done first"
        parsed = parse_blocked_by_ids(body)
        assert blocker.id in parsed

        task = storage.create_task(body=body, status="backlog", blocked_by=parsed)
        found = search.search_by_id(task.id)
        assert blocker.id in (found.get("blocked_by") or [])

    def test_parent_task_tag(self, kanban_env):
        """Body markup [parent_task] is a synonym for [blocked_by]."""
        _, storage, search = kanban_env

        blocker = storage.create_task(body="Parent", status="todo")
        body = f"Child of [parent_task]{blocker.id}[/parent_task]"
        parsed = parse_blocked_by_ids(body)
        assert blocker.id in parsed

        task = storage.create_task(body=body, status="backlog", blocked_by=parsed)
        found = search.search_by_id(task.id)
        assert blocker.id in (found.get("blocked_by") or [])

    def test_multiple_synonyms_in_same_body(self, kanban_env):
        """Multiple synonym tags in the same body should all be merged."""
        _, storage, search = kanban_env

        a = storage.create_task(body="A", status="todo")
        b = storage.create_task(body="B", status="todo")
        body = f"Blocked by [blocked_by]{a.id}[/blocked_by] and [parent_task]{b.id}[/parent_task]"
        parsed = parse_blocked_by_ids(body)
        assert a.id in parsed
        assert b.id in parsed

        task = storage.create_task(body=body, status="backlog", blocked_by=parsed)
        found = search.search_by_id(task.id)
        blocked = found.get("blocked_by") or []
        assert a.id in blocked
        assert b.id in blocked

    def test_parse_blocked_by_ids_directly(self):
        """Unit-level check that parse_blocked_by_ids handles all 4 synonyms."""
        body = "[blocked_by]AAA111[/blocked_by] [block_by]BBB222[/block_by] [block]CCC333[/block] [parent_task]DDD444[/parent_task]"
        result = parse_blocked_by_ids(body)
        assert "AAA111" in result
        assert "BBB222" in result
        assert "CCC333" in result
        assert "DDD444" in result
