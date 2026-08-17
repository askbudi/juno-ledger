"""Real-Git and injected-boundary contracts for archive-pack activation."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from kanban.archive import (ArchiveFormatError, create_archive, plan_archive,
                            recover_archive)
from kanban.config import Config
from kanban.ledger import _hash_event
from kanban.storage import TaskStorage

NOW = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)
OLD = "2026-04-01T12:00:00Z"


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def make_repository(tmp_path):
    root = tmp_path / "repository"
    tasks = root / ".juno_task" / "tasks"
    tasks.mkdir(parents=True)
    config = deepcopy(Config.DEFAULT_CONFIG)
    config["storage"] = {"base_path": str(tasks), "file_pattern": "*/*.md",
                         "default_file": ""}
    config_path = tasks / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (root / ".gitignore").write_text(".juno_task/cache/\n.juno_task/locks/\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "config", "user.email", "archive@example.test")
    git(root, "config", "user.name", "Archive Test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    storage = TaskStorage(Config(str(config_path)))
    storage.create_task(id="Aa1Aa1", body="complete body", status="todo")
    storage.update_task("Aa1Aa1", {"status": "done"})
    events = storage.ledger.read("Aa1Aa1")
    previous = None
    for event, timestamp in zip(events, ["2025-01-01T00:00:00Z", OLD]):
        event["timestamp"] = timestamp
        event["previous_event_sha256"] = previous
        event["event_sha256"] = _hash_event(event)
        previous = event["event_sha256"]
    segment = storage.ledger.segments("Aa1Aa1")[0]
    segment.write_text("".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
                               for event in events), encoding="utf-8")
    (root / "unrelated.txt").write_text("preserve\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "eligible terminal task")
    plan = plan_archive(storage, now=NOW)
    assert plan["selected_ids"] == ["Aa1Aa1"]
    return storage, root, plan


def snapshot_hot(storage):
    paths = [storage.task_path("Aa1Aa1"), *storage.ledger.segments("Aa1Aa1")]
    return {path.relative_to(storage.project_root): path.read_bytes() for path in paths}


@pytest.mark.parametrize("boundary", [
    "after_freeze", "before_staging", "after_staging", "before_verification",
    "after_verification", "before_deletion", "after_deletion", "before_commit",
])
def test_every_precommit_fault_restores_exact_hot_tree(tmp_path, boundary):
    storage, root, plan = make_repository(tmp_path)
    before = snapshot_hot(storage)
    head = git(root, "rev-parse", "HEAD").stdout.strip()

    def fail(name):
        if name == boundary:
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match=boundary):
        create_archive(storage, plan, tmp_path / "receipt.json", "test", fault=fail)
    assert snapshot_hot(storage) == before
    assert git(root, "rev-parse", "HEAD").stdout.strip() == head
    assert git(root, "status", "--porcelain").stdout == ""
    assert not (storage.juno_root / "ARCHIVE_PACK_FREEZE.json").exists()
    assert not list((storage.juno_root / "archive").glob("**/pack-*.ndjson"))


@pytest.mark.parametrize("boundary", [
    "after_commit", "before_cache_rebuild", "after_cache_rebuild", "before_doctors",
    "after_doctors", "before_freeze_cleanup",
])
def test_postcommit_fault_retains_freeze_and_recovery_converges(tmp_path, boundary):
    storage, root, plan = make_repository(tmp_path)
    report = tmp_path / "receipt.json"

    def fail(name):
        if name == boundary:
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match=boundary):
        create_archive(storage, plan, report, "test", fault=fail)
    freeze = storage.juno_root / "ARCHIVE_PACK_FREEZE.json"
    assert freeze.exists()
    receipt = recover_archive(storage, report)
    assert receipt["archive_commit"] == git(root, "rev-parse", "HEAD").stdout.strip()
    assert not freeze.exists()
    assert storage.find_task("Aa1Aa1") is None
    assert git(root, "status", "--porcelain").stdout == ""


def test_archive_freeze_refuses_ordinary_mutations(tmp_path):
    storage, root, plan = make_repository(tmp_path)

    def probe(name):
        if name == "after_freeze":
            with pytest.raises(ValueError, match="mutations are frozen"):
                storage.create_task(id="Bb2Bb2", body="must not persist", status="todo")
            raise RuntimeError("stop after freeze probe")

    with pytest.raises(RuntimeError, match="freeze probe"):
        create_archive(storage, plan, tmp_path / "receipt.json", "test", fault=probe)
    assert storage.find_task("Bb2Bb2") is None
    assert git(root, "status", "--porcelain").stdout == ""


def test_fault_after_freeze_cleanup_is_a_verified_committed_state(tmp_path):
    storage, root, plan = make_repository(tmp_path)
    report = tmp_path / "receipt.json"

    def fail(name):
        if name == "after_freeze_cleanup":
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match="after_freeze_cleanup"):
        create_archive(storage, plan, report, "test", fault=fail)
    assert report.exists()
    assert not (storage.juno_root / "ARCHIVE_PACK_FREEZE.json").exists()
    assert storage.find_task("Aa1Aa1") is None
    assert git(root, "status", "--porcelain").stdout == ""


def test_success_stages_only_owned_paths_and_verified_revert_restores_bytes(tmp_path):
    storage, root, plan = make_repository(tmp_path)
    before = snapshot_hot(storage)
    unrelated = (root / "unrelated.txt").read_bytes()
    parent = git(root, "rev-parse", "HEAD").stdout.strip()
    receipt = create_archive(storage, plan, tmp_path / "receipt.json", "test")
    commit = receipt["archive_commit"]
    assert git(root, "rev-parse", commit + "^").stdout.strip() == parent
    changed = set(git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).stdout.split())
    assert ".juno_task/tasks/aa/Aa1Aa1.md" in changed
    assert any(path.startswith(".juno_task/archive/") for path in changed)
    assert "unrelated.txt" not in changed
    assert (root / "unrelated.txt").read_bytes() == unrelated
    assert receipt["revert"] == "git revert %s" % commit
    git(root, "revert", "--no-edit", commit)
    assert snapshot_hot(storage) == before
    assert not list((storage.juno_root / "archive").glob("**/pack-*.ndjson"))
    assert storage.doctor() == []


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_worktree_or_index_fails_closed(tmp_path, staged):
    storage, root, plan = make_repository(tmp_path)
    (root / "dirty.txt").write_text("dirty", encoding="utf-8")
    if staged:
        git(root, "add", "dirty.txt")
    with pytest.raises(ArchiveFormatError, match="clean worktree"):
        create_archive(storage, plan, tmp_path / "dirty-receipt.json", "test")


def test_selected_linked_worktree_fails_closed(tmp_path):
    storage, root, plan = make_repository(tmp_path)
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "-q", "-b", "linked-archive-test", str(linked), "HEAD")
    selected = linked / ".juno_task" / "tasks" / "aa" / "Aa1Aa1.md"
    selected.write_text(selected.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ArchiveFormatError, match="linked worktree"):
        create_archive(storage, plan, tmp_path / "linked-receipt.json", "test")


def test_selected_revision_race_after_staging_restores_exact_bytes(tmp_path):
    storage, root, plan = make_repository(tmp_path)
    before = snapshot_hot(storage)

    def race(name):
        if name == "before_verification":
            path = storage.task_path("Aa1Aa1")
            path.write_text(path.read_text(encoding="utf-8").replace(
                "complete body", "raced body"), encoding="utf-8")

    with pytest.raises(ArchiveFormatError, match="revision"):
        create_archive(storage, plan, tmp_path / "race-receipt.json", "test", fault=race)
    assert snapshot_hot(storage) == before
    assert git(root, "status", "--porcelain").stdout == ""


def test_unrelated_linked_worktree_change_is_not_committed(tmp_path):
    storage, root, plan = make_repository(tmp_path)
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "-q", "-b", "linked-unrelated-test", str(linked), "HEAD")
    (linked / "unrelated.txt").write_text("linked dirty\n", encoding="utf-8")
    receipt = create_archive(storage, plan, tmp_path / "receipt.json", "test")
    changed = git(root, "show", "--format=", "--name-only", receipt["archive_commit"]).stdout
    assert "unrelated.txt" not in changed


def test_create_cli_controls_are_exact():
    from kanban.cli import TaskCLI
    parser = TaskCLI().parser
    parsed = parser.parse_args(["archive-pack", "create", "--plan", "/tmp/p",
                                "--report", "/tmp/r"])
    assert parsed.archive_pack_command == "create"
    for args in (["archive-pack", "create", "--pla", "/tmp/p", "--report", "/tmp/r"],
                 ["archive-pack", "create", "--plan", "/tmp/p", "--report", "/tmp/r", "--force"]):
        with pytest.raises(SystemExit):
            parser.parse_args(args)
