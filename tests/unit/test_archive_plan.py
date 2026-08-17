"""Revision-bound eligibility contracts for immutable cold archive plans."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import subprocess

import pytest

from kanban.archive import (ArchiveFormatError, make_envelope, plan_archive,
                            verify_archive_plan, write_archive_packs)
from kanban.cli import TaskCLI
from kanban.config import Config
from kanban.ledger import _hash_event
from kanban.storage import TaskStorage

NOW = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)
OLD = "2026-04-01T12:00:00Z"
BOUNDARY = "2026-04-24T18:00:00Z"


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.delenv("JUNO_TASK_ROOT", raising=False)
    root = tmp_path / "project"
    tasks = root / ".juno_task" / "tasks"
    tasks.mkdir(parents=True)
    cfg = deepcopy(Config.DEFAULT_CONFIG)
    cfg["storage"] = {"base_path": ".juno_task/tasks", "file_pattern": "*/*.md", "default_file": ""}
    path = tasks / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "archive@example.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Archive Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    return TaskStorage(Config(str(path))), root


def _rehash_ledger(storage, task_id, timestamps):
    events = storage.ledger.read(task_id)
    assert len(events) == len(timestamps)
    previous = None
    for event, timestamp in zip(events, timestamps):
        event["timestamp"] = timestamp
        event["previous_event_sha256"] = previous
        event["event_sha256"] = _hash_event(event)
        previous = event["event_sha256"]
    segments = storage.ledger.segments(task_id)
    assert len(segments) == 1
    segments[0].write_text("".join(json.dumps(event, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")) + "\n" for event in events),
                           encoding="utf-8")


def _terminal(storage, task_id="Aa1Aa1", terminal_at=OLD, body="x", blocked_by=None):
    storage.create_task(id=task_id, body=body, status="todo", blocked_by=blocked_by)
    storage.update_task(task_id, {"status": "done"})
    _rehash_ledger(storage, task_id, ["2025-01-01T00:00:00Z", terminal_at])
    return task_id


def test_clock_boundary_is_inclusive_and_last_modified_is_ignored(native):
    storage, _ = native
    _terminal(storage, "Aa1Aa1", BOUNDARY)
    _terminal(storage, "Bb2Bb2", "2026-04-24T18:00:01Z")
    plan = plan_archive(storage, now=NOW)
    assert plan["selected_ids"] == ["Aa1Aa1"]
    assert {item["task_id"]: item["reason"] for item in plan["rejected"]}["Bb2Bb2"] == "too_young"


def test_reopen_reclose_uses_latest_terminal_transition(native):
    storage, _ = native
    _terminal(storage)
    storage.config.config["status_workflow"]["enforce_transitions"] = False
    storage.update_task("Aa1Aa1", {"status": "todo"})
    storage.update_task("Aa1Aa1", {"status": "done"})
    _rehash_ledger(storage, "Aa1Aa1", ["2025-01-01T00:00:00Z", OLD,
                                       "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z"])
    plan = plan_archive(storage, now=NOW)
    assert not plan["selected_ids"]
    assert plan["rejected"][0]["reason"] == "too_young"


def test_plan_binds_task_head_config_and_rejects_tamper(native):
    storage, root = native
    _terminal(storage)
    plan = plan_archive(storage, now=NOW)
    assert verify_archive_plan(storage, plan)["selected_ids"] == ["Aa1Aa1"]
    tampered = deepcopy(plan)
    tampered["selected_ids"] = []
    with pytest.raises(ArchiveFormatError, match="hash mismatch"):
        verify_archive_plan(storage, tampered)
    storage.update_task("Aa1Aa1", {"agent_response": "changed"})
    with pytest.raises(ArchiveFormatError, match="task revision"):
        verify_archive_plan(storage, plan)
    fresh = plan_archive(storage, now=NOW)
    (root / "head.txt").write_text("head", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "head.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "advance"], check=True)
    with pytest.raises(ArchiveFormatError, match="HEAD"):
        verify_archive_plan(storage, fresh)
    newest = plan_archive(storage, now=NOW)
    storage.config.config["output"]["default_format"] = "json"
    with pytest.raises(ArchiveFormatError, match="config"):
        verify_archive_plan(storage, newest)


def test_active_reservation_is_excluded_and_bound(native):
    storage, _ = native
    _terminal(storage)
    reservations = storage.juno_root / "reservations"
    reservations.mkdir()
    receipt = reservations / "run.json"
    receipt.write_text(json.dumps({"active": True, "task_ids": ["Aa1Aa1"]}), encoding="utf-8")
    plan = plan_archive(storage, now=NOW)
    assert plan["selected_ids"] == [] and plan["rejected"][0]["reason"] == "reserved"
    receipt.write_text(json.dumps({"active": False, "task_ids": ["Aa1Aa1"]}), encoding="utf-8")
    with pytest.raises(ArchiveFormatError, match="reservations"):
        verify_archive_plan(storage, plan)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_ledger_is_excluded(native, damage):
    storage, _ = native
    _terminal(storage)
    segment = storage.ledger.segments("Aa1Aa1")[0]
    if damage == "missing":
        segment.unlink()
    else:
        segment.write_text("not json\n", encoding="utf-8")
    plan = plan_archive(storage, now=NOW)
    assert plan["selected_ids"] == []
    assert plan["rejected"][0]["reason"] == "missing_or_corrupt_ledger"


def test_case_insensitive_duplicate_is_excluded(native):
    storage, _ = native
    _terminal(storage)
    original = storage.find_task("Aa1Aa1")
    duplicate = dict(original, id="aa1aa1")

    class SyntheticRoot:
        def glob(self, pattern):
            assert pattern == "*/*.md"
            return [storage.task_path("Aa1Aa1"), storage.task_path("Aa1Aa1").with_name("synthetic.md")]

    class SyntheticStorage:
        tasks_root = SyntheticRoot()
        juno_root = storage.juno_root
        ledger = storage.ledger
        _git_head = storage._git_head
        _config_hash = storage._config_hash
        normalized_hash = storage.normalized_hash

        @staticmethod
        def _read_path(path):
            return original if path.name != "synthetic.md" else duplicate

    plan = plan_archive(SyntheticStorage(), now=NOW)
    reasons = {(item["task_id"], item["reason"]) for item in plan["rejected"]}
    assert reasons == {("Aa1Aa1", "case_insensitive_duplicate"),
                       ("aa1aa1", "case_insensitive_duplicate")}


def test_hot_id_colliding_with_verified_cold_inventory_is_excluded(native):
    storage, _ = native
    _terminal(storage)
    task = storage.find_task("Aa1Aa1")
    ledger = storage.ledger.read("Aa1Aa1")
    envelope = make_envelope(task, ledger, NOW.isoformat().replace("+00:00", "Z"),
                             OLD, "sha256:" + storage.normalized_hash(task))
    write_archive_packs(storage.juno_root / "archive", [envelope], storage._git_head(),
                        storage._config_hash(), "test", NOW.isoformat().replace("+00:00", "Z"))
    plan = plan_archive(storage, now=NOW)
    assert plan["selected_ids"] == []
    assert plan["rejected"][0]["reason"] == "case_insensitive_duplicate"


def test_active_outgoing_dependency_is_refused_before_terminal_state(native):
    storage, _ = native
    storage.create_task(id="Bb2Bb2", body="active", status="todo")
    storage.create_task(id="Aa1Aa1", body="x", status="todo", blocked_by=["Bb2Bb2"])
    before = storage.normalized_hash(storage.find_task("Aa1Aa1"))
    with pytest.raises(ValueError, match="unmet blockers: Bb2Bb2"):
        storage.update_task("Aa1Aa1", {"status": "done"})
    assert storage.normalized_hash(storage.find_task("Aa1Aa1")) == before
    assert [event["operation"] for event in storage.history("Aa1Aa1")] == ["create"]


def test_terminal_time_then_utf8_id_order_and_size_batches(native):
    storage, _ = native
    _terminal(storage, "Zz9Zz9", OLD, body="z" * 400)
    _terminal(storage, "Aa1Aa1", OLD, body="a" * 400)
    _terminal(storage, "Mm5Mm5", "2026-03-01T00:00:00Z", body="m" * 400)
    baseline = plan_archive(storage, now=NOW, target_bytes=10**9, hard_max_bytes=10**9)
    assert baseline["selected_ids"] == ["Mm5Mm5", "Aa1Aa1", "Zz9Zz9"]
    first_size = baseline["selected"][0]["estimated_bytes"]
    split = plan_archive(storage, now=NOW, target_bytes=first_size,
                         hard_max_bytes=first_size)
    assert [batch["task_ids"] for batch in split["batches"]] == [["Mm5Mm5"], ["Aa1Aa1"], ["Zz9Zz9"]]
    oversized = plan_archive(storage, now=NOW, target_bytes=first_size - 1,
                             hard_max_bytes=first_size - 1)
    assert oversized["batches"][0]["oversized_record"]


def test_one_thousand_selection_cap_is_enforced_before_batching(native):
    storage, _ = native
    with pytest.raises(ValueError, match="1000"):
        plan_archive(storage, now=NOW, max_tasks=1001)


def test_archive_plan_cli_requires_exact_supported_long_controls():
    parser = TaskCLI().parser
    accepted = parser.parse_args(["archive-pack", "plan", "--older-than", "90d",
                                  "--report", "/tmp/plan.json"])
    assert accepted.archive_pack_command == "plan"
    for arguments in (["archive-pack", "plan", "--older-tha", "90d", "--report", "/tmp/x"],
                      ["archive-pack", "plan", "--force", "--report", "/tmp/x"],
                      ["archive-pack", "plan", "--archive-all", "--report", "/tmp/x"]):
        with pytest.raises(SystemExit):
            parser.parse_args(arguments)
