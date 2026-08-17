"""Tests for kanban.validators module.

Why: TaskValidator is the safety net that prevents corrupt data from entering NDJSON storage.
Every task passes through validation on create/update - bugs here silently corrupt the database.
"""

import re
import pytest
from kanban.validators import TaskValidator, ValidationError


class TestValidateId:
    """Task ID validation: 6-char alphanumeric with mixed letters+digits."""

    def test_valid_id(self):
        is_valid, error = TaskValidator.validate_id("Ab3cD4")
        assert is_valid is True
        assert error is None

    def test_valid_id_mixed(self):
        is_valid, _ = TaskValidator.validate_id("x1y2z3")
        assert is_valid is True

    def test_rejects_non_string(self):
        is_valid, error = TaskValidator.validate_id(123456)
        assert is_valid is False
        assert "string" in error

    def test_rejects_too_short(self):
        is_valid, error = TaskValidator.validate_id("Ab3c")
        assert is_valid is False
        assert "6 characters" in error

    def test_rejects_too_long(self):
        is_valid, error = TaskValidator.validate_id("Ab3cD4E")
        assert is_valid is False
        assert "6 characters" in error

    def test_rejects_special_chars(self):
        is_valid, error = TaskValidator.validate_id("Ab3c!4")
        assert is_valid is False
        assert "alphanumeric" in error

    def test_rejects_all_digits(self):
        is_valid, error = TaskValidator.validate_id("123456")
        assert is_valid is False
        assert "only numeric" in error

    def test_rejects_all_alpha(self):
        is_valid, error = TaskValidator.validate_id("abcdef")
        assert is_valid is False
        assert "only alphabetic" in error

    def test_rejects_empty(self):
        is_valid, _ = TaskValidator.validate_id("")
        assert is_valid is False


class TestValidateStatus:
    """Status validation against allowed values."""

    def test_valid_status(self):
        allowed = ["backlog", "todo", "in_progress", "done", "archive"]
        is_valid, error = TaskValidator.validate_status("todo", allowed)
        assert is_valid is True
        assert error is None

    def test_invalid_status(self):
        allowed = ["backlog", "todo", "in_progress", "done", "archive"]
        is_valid, error = TaskValidator.validate_status("invalid", allowed)
        assert is_valid is False
        assert "invalid" in error.lower()

    def test_rejects_non_string(self):
        is_valid, error = TaskValidator.validate_status(42, ["todo"])
        assert is_valid is False
        assert "string" in error

    def test_all_default_statuses(self):
        allowed = ["backlog", "todo", "in_progress", "done", "archive"]
        for status in allowed:
            is_valid, _ = TaskValidator.validate_status(status, allowed)
            assert is_valid is True


class TestValidateStatusTransition:
    """Workflow transition enforcement."""

    def test_valid_transition(self):
        transitions = {"todo": ["in_progress", "backlog"]}
        is_valid, _ = TaskValidator.validate_status_transition("todo", "in_progress", transitions, True)
        assert is_valid is True

    def test_invalid_transition(self):
        transitions = {"todo": ["in_progress"]}
        is_valid, error = TaskValidator.validate_status_transition("todo", "done", transitions, True)
        assert is_valid is False
        assert "Cannot transition" in error

    def test_no_enforcement(self):
        """When enforce=False, any transition is allowed."""
        is_valid, _ = TaskValidator.validate_status_transition("todo", "done", {}, False)
        assert is_valid is True


class TestValidateCommitHash:
    """Git commit hash validation."""

    def test_valid_short_hash(self):
        is_valid, _ = TaskValidator.validate_commit_hash("abc1234")
        assert is_valid is True

    def test_valid_full_hash(self):
        is_valid, _ = TaskValidator.validate_commit_hash("a" * 40)
        assert is_valid is True

    def test_none_is_valid(self):
        is_valid, _ = TaskValidator.validate_commit_hash(None)
        assert is_valid is True

    def test_too_short(self):
        is_valid, error = TaskValidator.validate_commit_hash("abc12")
        assert is_valid is False
        assert "7-40" in error

    def test_too_long(self):
        is_valid, _ = TaskValidator.validate_commit_hash("a" * 41)
        assert is_valid is False

    def test_rejects_uppercase(self):
        is_valid, _ = TaskValidator.validate_commit_hash("ABC1234")
        assert is_valid is False

    def test_rejects_non_hex(self):
        is_valid, _ = TaskValidator.validate_commit_hash("xyz1234")
        assert is_valid is False

    def test_rejects_non_string(self):
        is_valid, error = TaskValidator.validate_commit_hash(1234567)
        assert is_valid is False
        assert "string" in error


class TestValidateTimestamp:
    """ISO 8601 timestamp validation."""

    def test_valid_timestamp(self):
        is_valid, _ = TaskValidator.validate_timestamp("2026-02-18 14:30:00")
        assert is_valid is True

    def test_valid_iso_with_z(self):
        is_valid, _ = TaskValidator.validate_timestamp("2026-02-18T14:30:00Z")
        assert is_valid is True

    def test_invalid_format(self):
        is_valid, error = TaskValidator.validate_timestamp("not-a-date")
        assert is_valid is False
        assert "Invalid timestamp" in error

    def test_rejects_non_string(self):
        is_valid, _ = TaskValidator.validate_timestamp(12345)
        assert is_valid is False


class TestValidateTags:
    """Feature tag validation."""

    def test_valid_tags(self):
        is_valid, _ = TaskValidator.validate_tags(["backend", "urgent"])
        assert is_valid is True

    def test_none_is_valid(self):
        is_valid, _ = TaskValidator.validate_tags(None)
        assert is_valid is True

    def test_empty_list_is_valid(self):
        is_valid, _ = TaskValidator.validate_tags([])
        assert is_valid is True

    def test_rejects_non_list(self):
        is_valid, error = TaskValidator.validate_tags("not-a-list")
        assert is_valid is False
        assert "list" in error

    def test_rejects_too_many_tags(self):
        tags = [f"tag{i}" for i in range(25)]
        is_valid, error = TaskValidator.validate_tags(tags, max_tags=20)
        assert is_valid is False
        assert "Too many" in error

    def test_rejects_duplicates(self):
        is_valid, error = TaskValidator.validate_tags(["dup", "dup"])
        assert is_valid is False
        assert "Duplicate" in error

    def test_rejects_invalid_format(self):
        is_valid, error = TaskValidator.validate_tags(["has space"])
        assert is_valid is False
        assert "Invalid tag" in error

    def test_rejects_special_chars(self):
        is_valid, _ = TaskValidator.validate_tags(["tag!@#"])
        assert is_valid is False

    def test_tags_with_underscore_and_hyphen(self):
        is_valid, _ = TaskValidator.validate_tags(["my_tag", "my-tag"])
        assert is_valid is True

    def test_allowed_tags_whitelist(self):
        is_valid, error = TaskValidator.validate_tags(["banned"], allowed_tags=["allowed"])
        assert is_valid is False
        assert "not in allowed" in error

    def test_allowed_tags_pass(self):
        is_valid, _ = TaskValidator.validate_tags(["allowed"], allowed_tags=["allowed", "other"])
        assert is_valid is True

    def test_custom_pattern(self):
        pattern = re.compile(r'^[a-z]+$')
        is_valid, _ = TaskValidator.validate_tags(["lowercase"], pattern=pattern)
        assert is_valid is True

        is_valid, _ = TaskValidator.validate_tags(["MixedCase"], pattern=pattern)
        assert is_valid is False

    def test_rejects_non_string_tag(self):
        is_valid, error = TaskValidator.validate_tags([123])
        assert is_valid is False
        assert "string" in error


class TestValidateBlockedBy:
    """blocked_by field validation: list of task IDs for dependency declaration.

    Why: blocked_by is the dependency mechanism — invalid entries corrupt the dependency graph.
    Must accept None (no deps), reject non-list types, validate each ID format, and catch duplicates.
    """

    def test_none_is_valid(self):
        is_valid, _ = TaskValidator.validate_blocked_by(None)
        assert is_valid is True

    def test_empty_list_is_valid(self):
        is_valid, _ = TaskValidator.validate_blocked_by([])
        assert is_valid is True

    def test_single_valid_id(self):
        is_valid, _ = TaskValidator.validate_blocked_by(["Ab3cD4"])
        assert is_valid is True

    def test_multiple_valid_ids(self):
        is_valid, _ = TaskValidator.validate_blocked_by(["Ab3cD4", "Xy1zW2"])
        assert is_valid is True

    def test_rejects_non_list(self):
        is_valid, error = TaskValidator.validate_blocked_by("Ab3cD4")
        assert is_valid is False
        assert "list" in error

    def test_rejects_non_string_entry(self):
        is_valid, error = TaskValidator.validate_blocked_by([123456])
        assert is_valid is False
        assert "strings" in error

    def test_rejects_invalid_id_format(self):
        is_valid, error = TaskValidator.validate_blocked_by(["!!!!!!"])
        assert is_valid is False
        assert "Invalid task ID" in error

    def test_rejects_too_short_id(self):
        is_valid, error = TaskValidator.validate_blocked_by(["Ab3c"])
        assert is_valid is False
        assert "Invalid task ID" in error

    def test_rejects_too_long_id(self):
        is_valid, error = TaskValidator.validate_blocked_by(["Ab3cD4E"])
        assert is_valid is False
        assert "Invalid task ID" in error

    def test_rejects_duplicates(self):
        is_valid, error = TaskValidator.validate_blocked_by(["Ab3cD4", "Ab3cD4"])
        assert is_valid is False
        assert "Duplicate" in error


class TestValidateBody:
    """Task body validation."""

    def test_valid_body(self):
        is_valid, _ = TaskValidator.validate_body("A normal task body")
        assert is_valid is True

    def test_empty_body(self):
        is_valid, _ = TaskValidator.validate_body("")
        assert is_valid is True

    def test_rejects_non_string(self):
        is_valid, error = TaskValidator.validate_body(12345)
        assert is_valid is False
        assert "string" in error

    def test_rejects_oversized_body(self):
        huge = "x" * 2_000_000
        is_valid, error = TaskValidator.validate_body(huge, max_length=1048576)
        assert is_valid is False
        assert "too large" in error.lower()

    def test_unicode_body(self):
        is_valid, _ = TaskValidator.validate_body("Unicode: 日本語テスト 🚀")
        assert is_valid is True


class TestValidateTask:
    """Complete task validation (integration of all field validators)."""

    def _make_valid_task(self, **overrides):
        task = {
            "id": "Ab3cD4",
            "status": "todo",
            "body": "Test task",
            "commit_hash": None,
            "agent_response": "",
            "created_date": "2026-02-18 14:30:00",
            "last_modified": "2026-02-18 14:30:00",
            "feature_tags": ["test"],
        }
        task.update(overrides)
        return task

    def test_valid_task(self):
        is_valid, _ = TaskValidator.validate_task(self._make_valid_task())
        assert is_valid is True

    def test_missing_required_field(self):
        task = self._make_valid_task()
        del task["id"]
        is_valid, error = TaskValidator.validate_task(task)
        assert is_valid is False
        assert "Missing required" in error

    def test_invalid_id_in_task(self):
        is_valid, error = TaskValidator.validate_task(self._make_valid_task(id="!!!!!!"))
        assert is_valid is False

    def test_invalid_status_in_task(self):
        is_valid, error = TaskValidator.validate_task(self._make_valid_task(status="invalid"))
        assert is_valid is False

    def test_custom_config_statuses(self):
        config = {"status_workflow": {"values": ["open", "closed"]}}
        is_valid, _ = TaskValidator.validate_task(
            self._make_valid_task(status="open"), config=config
        )
        assert is_valid is True

    def test_invalid_tags_in_task(self):
        is_valid, _ = TaskValidator.validate_task(self._make_valid_task(feature_tags=["has space"]))
        assert is_valid is False

    def test_valid_blocked_by_in_task(self):
        is_valid, _ = TaskValidator.validate_task(
            self._make_valid_task(blocked_by=["Xy1zW2"])
        )
        assert is_valid is True

    def test_none_blocked_by_in_task(self):
        is_valid, _ = TaskValidator.validate_task(
            self._make_valid_task(blocked_by=None)
        )
        assert is_valid is True

    def test_invalid_blocked_by_in_task(self):
        is_valid, error = TaskValidator.validate_task(
            self._make_valid_task(blocked_by=["!!!!!!"])
        )
        assert is_valid is False

    def test_task_without_blocked_by_key(self):
        """Old tasks without blocked_by in dict should validate fine."""
        task = self._make_valid_task()
        # Ensure blocked_by is not in the dict
        task.pop("blocked_by", None)
        is_valid, _ = TaskValidator.validate_task(task)
        assert is_valid is True
