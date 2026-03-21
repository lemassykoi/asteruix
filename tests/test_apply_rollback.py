"""Tests for safe_apply rollback orchestration and config sanitizer."""

import pytest

from app.apply import sanitize_config_value, safe_apply


class TestSanitizeConfigValue:
    """Verify dangerous characters are removed from Asterisk config values."""

    def test_none_returns_empty(self):
        assert sanitize_config_value(None) == ""

    def test_plain_text_unchanged(self):
        assert sanitize_config_value("g722,ulaw,alaw") == "g722,ulaw,alaw"

    def test_strips_newlines(self):
        assert "\n" not in sanitize_config_value("line1\nline2")
        assert sanitize_config_value("a\nb") == "ab"

    def test_strips_carriage_return(self):
        assert "\r" not in sanitize_config_value("a\r\nb")

    def test_strips_semicolons(self):
        assert ";" not in sanitize_config_value("a;comment")

    def test_strips_backticks(self):
        assert "`" not in sanitize_config_value("a`cmd`b")

    def test_strips_dollar(self):
        assert "$" not in sanitize_config_value("${SHELL}")

    def test_strips_backslash(self):
        assert "\\" not in sanitize_config_value("a\\nb")

    def test_length_capped(self):
        result = sanitize_config_value("A" * 200)
        assert len(result) == 128

    def test_custom_max_length(self):
        result = sanitize_config_value("A" * 200, max_length=50)
        assert len(result) == 50

    def test_strips_and_trims(self):
        assert sanitize_config_value("  hello  ") == "hello"

    def test_integer_coerced(self):
        assert sanitize_config_value(4900) == "4900"


class TestSafeApplyRollback:
    """Verify safe_apply rolls back on reload failure."""

    def test_success_path(self, tmp_path):
        """When reload succeeds, returns True."""
        import app.apply as mod
        calls = []

        def fake_take_snapshot(label):
            calls.append(("snap", label))
            d = str(tmp_path / "snap")
            import os; os.makedirs(d, exist_ok=True)
            return d

        def fake_run_command(cmd):
            calls.append(("reload", cmd))

        orig_snap = mod.take_snapshot
        orig_run = mod.run_command
        mod.take_snapshot = fake_take_snapshot
        mod.run_command = fake_run_command
        try:
            ok, msg = safe_apply(
                label="test",
                writers=[lambda: calls.append(("write",))],
                reload_commands=["pjsip reload"],
            )
            assert ok is True
            assert "applied" in msg.lower() or "reloaded" in msg.lower()
            assert ("snap", "test") in calls
            assert ("write",) in calls
            assert ("reload", "pjsip reload") in calls
        finally:
            mod.take_snapshot = orig_snap
            mod.run_command = orig_run

    def test_rollback_on_failure(self, tmp_path):
        """When reload raises AsteriskCommandError, rollback is attempted."""
        import app.apply as mod
        from app.asterisk_cmd import AsteriskCommandError
        calls = []

        snap_dir = str(tmp_path / "snap")
        import os; os.makedirs(snap_dir, exist_ok=True)

        def fake_take_snapshot(label):
            return snap_dir

        reload_count = 0
        def fake_run_command(cmd):
            nonlocal reload_count
            reload_count += 1
            if reload_count == 1:
                raise AsteriskCommandError("reload failed")
            calls.append(("retry-reload", cmd))

        def fake_restore(d):
            calls.append(("restore", d))

        orig_snap = mod.take_snapshot
        orig_run = mod.run_command
        orig_restore = mod.restore_snapshot
        mod.take_snapshot = fake_take_snapshot
        mod.run_command = fake_run_command
        mod.restore_snapshot = fake_restore
        try:
            ok, msg = safe_apply(
                label="test",
                writers=[lambda: None],
                reload_commands=["dialplan reload"],
            )
            assert ok is False
            assert "rolled back" in msg.lower()
            assert ("restore", snap_dir) in calls
        finally:
            mod.take_snapshot = orig_snap
            mod.run_command = orig_run
            mod.restore_snapshot = orig_restore
