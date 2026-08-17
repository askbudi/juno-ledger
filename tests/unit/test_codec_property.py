"""Deterministic property-style round trips and ledger segmentation."""
import random
import string
from pathlib import Path

from kanban.codec import MarkdownTaskCodec, TaskFormatError, normalized_bytes
from kanban.ledger import TaskLedger


def test_arbitrary_markdown_and_json_compatible_values_roundtrip():
    rng = random.Random(20260722)
    codec = MarkdownTaskCodec()
    for index in range(100):
        body = ''.join(rng.choice(string.ascii_letters + string.digits + " #`\n🚀") for _ in range(200))
        response = ''.join(rng.choice(string.ascii_letters + "\n") for _ in range(80))
        record = {"schema_version": 1, "id": f"P{index:05d}"[-6:], "status": "todo",
                  "created_date": "2026-07-22T00:00:00Z", "last_modified": "2026-07-22T00:00:00Z",
                  "commit_hash": None, "feature_tags": [], "related_tasks": [], "blocked_by": [],
                  "fields": {"nested": {"number": index, "flags": [True, None, "x"]}},
                  "body": body, "agent_response": response}
        assert normalized_bytes(codec.loads(codec.dumps(record))) == normalized_bytes(record)


def test_quoted_exclamation_is_data_but_custom_yaml_tag_is_rejected():
    codec = MarkdownTaskCodec()
    record = {"schema_version": 1, "id": "Ab1Cd2", "status": "todo",
              "created_date": "2026-07-22T00:00:00Z", "last_modified": "2026-07-22T00:00:00Z",
              "fields": {"note": "ship ! now"}, "body": "safe ! body", "agent_response": ""}
    rendered = codec.dumps(record).replace("note: ship ! now", 'note: "ship ! now"')
    assert codec.loads(rendered)["fields"]["note"] == "ship ! now"

    tagged = rendered.replace('note: "ship ! now"', "note: !python/object:builtins.str unsafe")
    try:
        codec.loads(tagged)
    except TaskFormatError as exc:
        assert "unsupported YAML value" in str(exc)
    else:
        raise AssertionError("custom YAML tag was accepted")


def test_ledger_rotates_before_configured_boundary(tmp_path):
    ledger = TaskLedger(tmp_path, max_segment_bytes=900)
    before = {}
    before_hash = None
    for index in range(8):
        after = {"id": "Ab1Cd2", "body": "x" * 250, "sequence": index}
        digest = str(index)
        ledger.append("Ab1Cd2", "update", "cli", before_hash, digest, before, after, index == 0)
        before, before_hash = after, digest
    segments = ledger.segments("Ab1Cd2")
    assert len(segments) > 1
    assert all(path.stat().st_size <= 900 or path == segments[0] for path in segments)
    assert len(ledger.read("Ab1Cd2")) == 8
