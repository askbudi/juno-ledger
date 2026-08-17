"""Byte-level contracts for immutable cold archive packs."""
import hashlib
import json
import random
import string
import pytest

from kanban.archive import (
    DEFAULT_HARD_MAX_BYTES,
    DEFAULT_MAX_RECORDS,
    DEFAULT_TARGET_BYTES,
    ArchiveFormatError,
    decode_envelope,
    encode_envelope,
    make_envelope,
    read_record,
    rebuild_manifest,
    split_records,
    verify_archive_artifact,
    verify_manifest,
    write_archive_packs,
)
from kanban.codec import normalized_bytes
from kanban.ledger import TaskLedger


ARCHIVED_AT = "2026-07-23T18:00:00Z"
TERMINAL_AT = "2026-04-01T12:00:00Z"


def _task(task_id="Ab1Cd2", body="body 🚀", **extra):
    task = {
        "schema_version": 1,
        "id": task_id,
        "status": "done",
        "created_date": "2025-01-01T00:00:00Z",
        "last_modified": "2026-04-01T12:00:00Z",
        "commit_hash": "abc123",
        "feature_tags": ["backend", "秘密"],
        "related_tasks": ["Xy9Zz8"],
        "blocked_by": [],
        "body": body,
        "agent_response": "finished\nexactly",
        "unknown": {"nested": [True, None, 3.25, "é"]},
    }
    task.update(extra)
    return task


def _ledger(tmp_path, task):
    ledger = TaskLedger(tmp_path / ("ledger-" + task["id"]))
    digest = hashlib.sha256(normalized_bytes(task)).hexdigest()
    ledger.append(task["id"], "create", "cli", None, digest, {}, task, True)
    return ledger.read(task["id"])


def _envelope(tmp_path, task_id="Ab1Cd2", body="body 🚀", **extra):
    task = _task(task_id, body, **extra)
    return make_envelope(task, _ledger(tmp_path, task), ARCHIVED_AT,
                         TERMINAL_AT, "sha256:" + "a" * 64)


def test_unicode_markdown_unknown_fields_and_complete_ledger_roundtrip(tmp_path):
    body = "# arbitrary Markdown\n\n```yaml\n---\n秘密: 🚀\n```\n" + "x" * 4096
    task = _task(body=body, custom_yaml_value={"dates-stay-semantic": "2026-07-23"})
    ledger_store = TaskLedger(tmp_path / "ledger")
    first_hash = hashlib.sha256(normalized_bytes(task)).hexdigest()
    ledger_store.append(task["id"], "create", "cli", None, first_hash, {}, task, True)
    before = dict(task)
    task["agent_response"] = "response\nwith NUL-like text: \\0 and Ω"
    second_hash = hashlib.sha256(normalized_bytes(task)).hexdigest()
    ledger_store.append(task["id"], "mark", "cli", first_hash, second_hash, before, task)

    envelope = make_envelope(task, ledger_store.read(task["id"]), ARCHIVED_AT,
                             TERMINAL_AT, "sha256:" + "b" * 64)
    decoded = decode_envelope(encode_envelope(envelope))

    assert normalized_bytes(decoded["task"]) == normalized_bytes(task)
    assert decoded["ledger"] == ledger_store.read(task["id"])
    assert decoded["task"]["body"] == body
    assert decoded["task"]["unknown"]["nested"][-1] == "é"


def test_property_style_semantic_roundtrips_are_deterministic(tmp_path):
    rng = random.Random(20260723)
    alphabet = string.ascii_letters + string.digits + " #`\nΩ秘密🚀"
    for index in range(40):
        body = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 800)))
        task_id = "P%05d" % index
        envelope = _envelope(
            tmp_path / "property", task_id, body,
            arbitrary={"index": index, "values": [None, rng.randint(-1000, 1000), body[:20]]})
        first = encode_envelope(envelope)
        assert encode_envelope(decode_envelope(first)) == first
        assert decode_envelope(first)["task"]["arbitrary"]["index"] == index


def test_partial_corrupt_and_truncated_records_fail_closed(tmp_path):
    envelope = _envelope(tmp_path)
    with pytest.raises(ArchiveFormatError, match="exactly one LF"):
        decode_envelope(encode_envelope(envelope)[:-1])
    with pytest.raises(ArchiveFormatError, match="invalid archive record JSON"):
        decode_envelope(b'{"archive_schema":1\n')

    corrupt = json.loads(encode_envelope(envelope))
    corrupt["task"]["body"] = "silently changed"
    with pytest.raises(ArchiveFormatError, match="task hash mismatch"):
        decode_envelope(json.dumps(corrupt).encode() + b"\n")

    task = _task()
    complete = _ledger(tmp_path, task)
    with pytest.raises(ArchiveFormatError, match="chain discontinuity|creation snapshot"):
        make_envelope(task, complete[1:] or [dict(complete[0], previous_event_sha256="bad")],
                      ARCHIVED_AT, TERMINAL_AT, "sha256:" + "c" * 64)


def test_archive_time_cannot_precede_terminal_transition(tmp_path):
    task = _task()
    with pytest.raises(ArchiveFormatError, match="cannot precede"):
        make_envelope(task, _ledger(tmp_path, task), "2026-03-01T00:00:00Z",
                      TERMINAL_AT, "sha256:" + "d" * 64)


def test_deterministic_pack_name_bytes_order_offsets_and_manifest_rebuild(tmp_path):
    envelopes = [_envelope(tmp_path / "inputs", task_id) for task_id in ("zz9Zz9", "Aa1Aa1", "Mm5Mm5")]
    kwargs = dict(source_head="deadbeef", config_sha256="f" * 64,
                  creator_version="9.9.9", created_at=ARCHIVED_AT)
    first = write_archive_packs(tmp_path / "one", envelopes, **kwargs)[0]
    second = write_archive_packs(tmp_path / "two", reversed(envelopes), **kwargs)[0]

    assert first.pack.name == second.pack.name
    assert first.pack.read_bytes() == second.pack.read_bytes()
    assert first.manifest.read_bytes() == second.manifest.read_bytes()
    assert first.pack.name.endswith("-%s.ndjson" % hashlib.sha256(first.pack.read_bytes()).hexdigest())

    manifest = json.loads(first.manifest.read_text())
    rebuilt = rebuild_manifest(first.pack, "deadbeef", "f" * 64, "9.9.9")
    assert manifest == rebuilt == verify_manifest(first.pack, manifest)
    assert [entry["task_id"] for entry in manifest["records"]] == ["Aa1Aa1", "Mm5Mm5", "zz9Zz9"]
    assert [entry["offset"] for entry in manifest["records"]] == [
        0,
        manifest["records"][0]["length"],
        manifest["records"][0]["length"] + manifest["records"][1]["length"],
    ]
    for entry in manifest["records"]:
        assert read_record(first.pack, entry)["task"]["id"] == entry["task_id"]
    checksum_lines = first.checksum.read_text().splitlines()
    assert checksum_lines == [
        "%s  %s" % (first.pack_sha256, first.pack.name),
        "%s  %s" % (first.manifest_sha256, first.manifest.name),
    ]
    assert first.pack.stat().st_mode & 0o222 == 0
    assert first.manifest.stat().st_mode & 0o222 == 0


def test_manifest_tamper_and_wrong_exact_offset_fail_closed(tmp_path):
    artifact = write_archive_packs(
        tmp_path / "archive", [_envelope(tmp_path / "input")], "head", "e" * 64,
        "1.0", ARCHIVED_AT)[0]
    manifest = json.loads(artifact.manifest.read_text())
    tampered = dict(manifest)
    tampered["record_count"] = 99
    with pytest.raises(ArchiveFormatError, match="does not match"):
        verify_manifest(artifact.pack, tampered)
    artifact.manifest.chmod(0o644)
    artifact.manifest.write_text(json.dumps(dict(manifest, source_head="tampered")) + "\n")
    with pytest.raises(ArchiveFormatError, match="checksum sidecar mismatch"):
        verify_archive_artifact(artifact.pack, artifact.manifest, artifact.checksum)
    artifact.manifest.write_bytes(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode() + b"\n")
    wrong = dict(manifest["records"][0], offset=1)
    with pytest.raises(ArchiveFormatError):
        read_record(artifact.pack, wrong)


def test_target_hard_max_record_cap_and_dedicated_oversized_behavior(tmp_path):
    assert (DEFAULT_TARGET_BYTES, DEFAULT_HARD_MAX_BYTES, DEFAULT_MAX_RECORDS) == (
        25 * 1024 * 1024, 45 * 1024 * 1024, 1000)
    normal = _envelope(tmp_path / "normal", "Aa1Aa1", "small")
    large = _envelope(tmp_path / "large", "Bb2Bb2", "x" * 10000)
    normal_size = len(encode_envelope(normal))
    large_size = len(encode_envelope(large))
    hard = (normal_size + large_size) // 2
    assert normal_size < hard < large_size

    batches = split_records([large, normal], target_bytes=hard, hard_max_bytes=hard)
    assert [[item[0] for item in batch] for batch in batches] == [["Aa1Aa1"], ["Bb2Bb2"]]
    artifacts = write_archive_packs(
        tmp_path / "archive", [large, normal], "head", "d" * 64, "1.0", ARCHIVED_AT,
        target_bytes=hard, hard_max_bytes=hard)
    assert [item.oversized_record for item in artifacts] == [False, True]
    oversized_manifest = json.loads(artifacts[1].manifest.read_text())
    assert oversized_manifest["oversized_records"] == [{
        "task_id": "Bb2Bb2", "size_bytes": large_size, "hard_max_bytes": hard}]
    assert artifacts[0].size_bytes <= hard
    assert artifacts[1].size_bytes > hard and artifacts[1].record_count == 1

    three = [_envelope(tmp_path / "cap", task_id) for task_id in ("Cc3Cc3", "Dd4Dd4", "Ee5Ee5")]
    capped = split_records(three, target_bytes=10 ** 9, hard_max_bytes=10 ** 9,
                           max_records=2)
    assert [len(batch) for batch in capped] == [2, 1]
    with pytest.raises(ValueError, match="limits"):
        split_records(three, max_records=1001)


def test_default_one_thousand_record_cap(tmp_path):
    # Reusing an immutable semantic record with distinct IDs keeps this boundary
    # test fast while exercising canonical validation and ordering for every row.
    envelopes = [_envelope(tmp_path / "many", "A%05d" % index)
                 for index in range(1001)]
    batches = split_records(envelopes, target_bytes=10 ** 9, hard_max_bytes=10 ** 9)
    assert [len(batch) for batch in batches] == [1000, 1]


def test_existing_immutable_output_is_never_replaced(tmp_path):
    envelope = _envelope(tmp_path / "input")
    args = (tmp_path / "archive", [envelope], "head", "c" * 64, "1.0", ARCHIVED_AT)
    artifact = write_archive_packs(*args)[0]
    original = artifact.pack.read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        write_archive_packs(*args)
    assert artifact.pack.read_bytes() == original
