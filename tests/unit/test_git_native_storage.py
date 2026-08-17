"""Executable contracts for Git-native task storage."""
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from kanban.config import Config
from kanban.codec import MarkdownTaskCodec, TaskFormatError, TASK_FIELD_ORDER
from kanban.storage import ConflictError, TaskStorage
from kanban.search import SearchFilters, TaskSearch


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.delenv("JUNO_TASK_ROOT", raising=False)
    root = tmp_path / "project"
    tasks = root / ".juno_task" / "tasks"
    tasks.mkdir(parents=True)
    cfg = deepcopy(Config.DEFAULT_CONFIG)
    cfg["storage"] = {"base_path": ".juno_task/tasks", "file_pattern": "*/*.md", "default_file": ""}
    cfg["custom_fields"] = {"due_date": {"type": "date"}}
    path = tasks / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return TaskStorage(Config(str(path))), root


def test_codec_roundtrip_unknown_fields_comments_and_markdown():
    codec = MarkdownTaskCodec()
    source = """---
schema_version: 1
id: Ab1Cd2 # stable identity
status: todo
created_date: 2026-07-22T22:00:00Z
last_modified: 2026-07-22T22:00:00Z
commit_hash: null
feature_tags: []
related_tasks: []
blocked_by: []
fields:
  due_date: 2026-08-01 # customer promise
future_core: retained
---

<!-- juno:body:start -->
# arbitrary heading\nUnicode 🚀
<!-- juno:body:end -->

<!-- juno:response:start -->
response
<!-- juno:response:end -->
"""
    record = codec.loads(source)
    assert record["future_core"] == "retained"
    assert str(record["fields"]["due_date"]) == "2026-08-01"
    record["status"] = "done"
    rendered = codec.dumps(record)
    assert "# stable identity" in rendered
    assert "# customer promise" in rendered
    decoded = codec.loads(rendered)
    assert decoded["body"] == "# arbitrary heading\nUnicode 🚀"
    assert list(decoded)[:5] == ["id", "status", "body", "created_date", "last_modified"]
    yaml_keys = [line.split(":", 1)[0] for line in rendered.split("---\n", 2)[1].splitlines()
                 if line and not line.startswith(" ")]
    assert yaml_keys[:4] == ["id", "status", "created_date", "last_modified"]
    assert yaml_keys.index("schema_version") > yaml_keys.index("blocked_by")
    assert yaml_keys[-1] == "future_core"


def test_codec_reads_arbitrary_yaml_order_and_emits_public_order():
    codec = MarkdownTaskCodec()
    source = """---
fields: {}
last_modified: 2026-07-22T22:00:00Z
schema_version: 1
status: todo
id: Ab1Cd2
created_date: 2026-07-22T22:00:00Z
---

<!-- juno:body:start -->
body
<!-- juno:body:end -->

<!-- juno:response:start -->

<!-- juno:response:end -->
"""
    record = codec.loads(source)
    assert list(record)[:5] == list(TASK_FIELD_ORDER[:5])
    assert codec.loads(codec.dumps(record))["id"] == "Ab1Cd2"


@pytest.mark.parametrize("bad", [
    "---\n!!python/object/apply:os.system [echo, bad]\n---\n",
    "---\nid: Ab1Cd2\n---\n<!-- juno:body:start -->x<!-- juno:response:start -->y<!-- juno:body:end --><!-- juno:response:end -->",
])
def test_codec_rejects_unsafe_yaml_and_invalid_boundaries(bad):
    with pytest.raises(TaskFormatError):
        MarkdownTaskCodec().loads(bad)


def test_doctor_names_mixed_v1_v2_storage_remnant(native):
    storage, _ = native
    storage.create_task(id="Ab1Cd2", body="v2", status="todo")
    remnant = storage.tasks_root / "backlog.ndjson"
    remnant.write_text('{"id":"legacy"}\n', encoding="utf-8")

    failures = storage.doctor()

    mixed = [item for item in failures if item.get("diagnosis") == "mixed_v1_v2_storage"]
    assert len(mixed) == 1
    assert mixed[0]["path"] == str(remnant)
    assert "ignored by the active V2 runtime" in mixed[0]["error"]


def test_unrelated_update_preserves_nested_yaml_comments(native):
    storage, _ = native
    storage.create_task(id="Ab1Cd2", body="x", status="todo", fields={"customer": "enterprise"})
    path = Path(storage.find_task_file("Ab1Cd2"))
    annotated = path.read_text(encoding="utf-8").replace(
        "customer: enterprise", "customer: enterprise # preserve customer note"
    ).replace("id: Ab1Cd2", "id: Ab1Cd2 # preserve identity note")
    path.write_text(annotated, encoding="utf-8")

    storage.update_task("Ab1Cd2", {"status": "done"})

    rendered = path.read_text(encoding="utf-8")
    assert "customer: enterprise # preserve customer note" in rendered
    assert "id: Ab1Cd2 # preserve identity note" in rendered
    assert storage.find_task("Ab1Cd2")["status"] == "done"
    assert [event["operation"] for event in storage.history("Ab1Cd2")] == ["create", "update"]


def test_stable_paths_ledger_receipt_and_update_amplification(native):
    storage, root = native
    first = storage.create_task(id="Ab1Cd2", body="one", status="todo", fields={"due_date": "2026-07-01"})
    second = storage.create_task(id="Xy9Za8", body="two", status="todo")
    first_path = Path(storage.find_task_file(first.id))
    second_path = Path(storage.find_task_file(second.id))
    second_before = second_path.read_bytes()
    receipt = storage.update_task(first.id, {"status": "done"}, return_receipt=True)
    assert first_path == root / ".juno_task/tasks/ab/Ab1Cd2.md"
    assert second_path.read_bytes() == second_before
    assert receipt.task_id == first.id and "/status" in receipt.changed_paths
    assert receipt.before_sha256 != receipt.after_sha256
    events = storage.history(first.id, include_content=True)
    assert [e["operation"] for e in events] == ["create", "update"]
    assert all(e["event_sha256"] for e in events)


def test_search_limit_is_applied_after_complete_canonical_sort(native):
    storage, _ = native
    storage.create_task(id="Aa1Aa1", body="match", last_modified="2026-07-01T00:00:00Z")
    storage.create_task(id="Bb2Bb2", body="match", last_modified="2026-07-02T00:00:00Z")
    storage.create_task(id="Zz9Zz9", body="match", last_modified="2026-07-31T00:00:00Z")
    results = TaskSearch(storage.config, storage).search(SearchFilters(body_text="match", limit=1))
    assert [task["id"] for task in results] == ["Zz9Zz9"]


def test_mixed_case_prefixes_share_one_normalized_shard_without_losing_tasks(native):
    storage, root = native
    lower_prefix = storage.create_task(id="0mAAAA", body="lower prefix")
    upper_prefix = storage.create_task(id="0MBBBB", body="upper prefix")

    assert Path(storage.find_task_file(lower_prefix.id)) == root / ".juno_task/tasks/0m/0mAAAA.md"
    assert Path(storage.find_task_file(upper_prefix.id)) == root / ".juno_task/tasks/0m/0MBBBB.md"
    assert storage.ledger.directory(lower_prefix.id) == root / ".juno_task/ledger/0m/0mAAAA"
    assert storage.ledger.directory(upper_prefix.id) == root / ".juno_task/ledger/0m/0MBBBB"
    assert {task["id"] for task in storage.read_all_tasks_canonical()} == {"0mAAAA", "0MBBBB"}
    assert storage.doctor() == []


def test_case_collision_and_revision_cas(native):
    storage, _ = native
    task = storage.create_task(id="Ab1Cd2", body="x")
    with pytest.raises(ValueError, match="case-insensitive"):
        storage.create_task(id="aB1cD2", body="collision")
    revision = storage.normalized_hash(storage.find_task(task.id))
    storage.update_task(task.id, {"status": "todo"})
    with pytest.raises(ConflictError):
        storage.update_task(task.id, {"status": "done"}, expected_revision=revision)


def test_external_edit_reconciled_before_mutation_and_cache_disposable(native):
    storage, root = native
    task = storage.create_task(id="Ab1Cd2", body="original")
    path = Path(storage.find_task_file(task.id))
    path.write_text(path.read_text().replace("original", "manual"), encoding="utf-8")
    storage.update_task(task.id, {"status": "done"})
    assert [e["operation"] for e in storage.history(task.id)] == ["create", "reconcile", "update"]
    assert storage.find_task(task.id)["body"] == "manual"
    cache = root / ".juno_task/cache/kanban.sqlite3"
    assert cache.exists()
    cache.unlink()
    assert storage.find_task(task.id)["status"] == "done"
    storage.rebuild_cache()
    assert cache.exists()


def test_typed_field_queries_and_overdue(native):
    storage, _ = native
    storage.create_task(id="Ab1Cd2", body="late", status="todo", fields={"due_date": "2026-07-01", "customer": "a"})
    storage.create_task(id="Xy9Za8", body="closed", status="done", fields={"due_date": "2026-06-01"})
    assert [t["id"] for t in storage.query_fields(field_equals={"customer": "a"})] == ["Ab1Cd2"]
    assert [t["id"] for t in storage.query_fields(overdue=True, today=date(2026, 7, 2))] == ["Ab1Cd2"]


def test_conversion_activation_fault_restores_original_tree(native, tmp_path, monkeypatch):
    storage, _ = native
    legacy = tmp_path / "legacy.ndjson"
    row = {"id": "Ab1Cd2", "status": "todo", "body": "hello", "agent_response": "", "created_date": "2026-07-22 00:00:00", "last_modified": "2026-07-22 00:00:00", "commit_hash": None, "feature_tags": None, "related_tasks": None, "blocked_by": None}
    legacy.write_text(json.dumps(row) + "\n", encoding="utf-8")
    import kanban.storage as storage_module
    original_replace = storage_module.os.replace

    injected = False
    def fail_activation(source, destination):
        nonlocal injected
        if not injected and ".juno-conversion-" in str(source) and Path(destination) == storage.tasks_root:
            injected = True
            raise OSError("injected activation fault")
        return original_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", fail_activation)
    with pytest.raises(OSError, match="injected activation"):
        storage.convert_legacy(legacy)
    assert (storage.tasks_root / "config.json").exists()
    assert not list(storage.tasks_root.glob("*/*.md"))


def test_conversion_dry_run_accepts_legacy_related_tasks_nullability(native, tmp_path):
    storage, _ = native
    legacy = storage.tasks_root / "backlog.ndjson"
    base = {
        "status": "todo", "body": "hello", "agent_response": "",
        "created_date": "2026-07-22 00:00:00", "last_modified": "2026-07-22 00:00:00",
        "commit_hash": None, "feature_tags": None, "blocked_by": None,
    }
    omitted = {"id": "Ab1Cd2", **base}
    explicit_null = {"id": "Xy9Za8", **base, "related_tasks": None}
    linked = {"id": "Qr7St6", **base, "related_tasks": ["Ab1Cd2"]}
    legacy.write_text("".join(json.dumps(row) + "\n" for row in (omitted, explicit_null, linked)),
                      encoding="utf-8")
    receipt = tmp_path / "conversion-dry-run.json"

    report = storage.convert_legacy(legacy, dry_run=True, report_path=receipt)

    assert report["verdict"] == "pass" and report["validated"] == 3
    assert report["semantic_hashes_match"] is True and report["content_sha256"]
    assert json.loads(receipt.read_text(encoding="utf-8")) == report
    assert storage.find_task("Ab1Cd2") is None
    # A real related-task edge remains distinct; compatibility is limited to
    # omitted versus explicit-null empty state.
    storage.convert_legacy(legacy)
    assert storage.find_task("Qr7St6")["related_tasks"] == ["Ab1Cd2"]


def test_conversion_tiers_active_hot_and_terminal_cold_with_complete_export(native, tmp_path):
    storage, _ = native
    legacy = storage.tasks_root / "backlog.ndjson"
    base = {
        "body": "task", "agent_response": "", "created_date": "2026-07-22 00:00:00",
        "last_modified": "2026-07-22 00:00:00", "commit_hash": None,
        "feature_tags": None, "related_tasks": None, "blocked_by": None,
    }
    rows = [
        {"id": "Do1Ne1", "status": "done", **base},
        {"id": "Ar1Ch1", "status": "archive", **base},
        {"id": "To1Do1", "status": "todo", **base,
         "blocked_by": ["Do1Ne1"], "related_tasks": ["Ar1Ch1"]},
        {"id": "Ip1Now", "status": "in_progress", **base},
    ]
    legacy.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    dry = storage.convert_legacy(legacy, dry_run=True)
    assert dry["partition"] == {
        "hot_statuses": ["backlog", "in_progress", "todo"],
        "cold_statuses": ["archive", "done"],
        "hot_tasks": 2, "cold_tasks": 2, "source_tasks": 4,
    }

    report = storage.convert_legacy(legacy)
    assert report["semantic_hashes_match"] is True
    assert len(list(storage.read_all_tasks_canonical())) == 2
    assert len(list(storage.read_all_tasks_complete())) == 4
    assert storage.find_task("Do1Ne1") is None
    assert storage.find_task_exact("Do1Ne1")["status"] == "done"
    assert storage.find_task_exact("Ar1Ch1")["status"] == "archive"
    assert storage.dependency_info("To1Do1")["blockers"] == [("Do1Ne1", "done")]
    assert storage.doctor() == []
    manifest = json.loads(next((storage.juno_root / "archive").glob("*/*/pack-*.manifest.json")).read_text())
    assert manifest["config_sha256"] == storage._config_hash()

    exported = tmp_path / "complete.ndjson"
    receipt = storage.export_legacy(exported)
    exported_rows = [json.loads(line) for line in exported.read_text().splitlines()]
    assert receipt["tasks"] == 4
    assert {row["id"] for row in exported_rows} == {row["id"] for row in rows}


def test_conversion_dry_run_and_lossless_export(native, tmp_path):
    storage, _ = native
    # Legacy configuration stores backlog.ndjson inside the tasks directory;
    # activation must preserve it across the atomic directory swap until verified.
    legacy = storage.tasks_root / "backlog.ndjson"
    # Older valid boards can omit blocked_by entirely. The v2 model materializes
    # it as null, which must remain semantically equivalent during conversion.
    row = {"id": "Ab1Cd2", "status": "todo", "body": "hello", "agent_response": "", "created_date": "2026-07-22 00:00:00", "last_modified": "2026-07-22 00:00:00", "commit_hash": None, "feature_tags": None, "related_tasks": None, "custom": {"x": 1}}
    legacy.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = storage.convert_legacy(legacy, dry_run=True)
    assert report["validated"] == 1 and storage.find_task("Ab1Cd2") is None
    report = storage.convert_legacy(legacy)
    assert report["semantic_hashes_match"] is True
    exported = tmp_path / "rollback.ndjson"
    storage.export_legacy(exported)
    restored = json.loads(exported.read_text())
    assert restored["custom"] == {"x": 1}
