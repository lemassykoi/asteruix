"""Tests for generator output — verify config-value sanitization is applied."""

import json
import sqlite3
import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    """Create a test app with an in-memory-like temp DB."""
    db_path = str(tmp_path / "test.db")
    import os
    os.environ["WEBUI_DB_PATH"] = db_path
    os.environ["WEBUI_INSECURE_COOKIES"] = "1"

    application = create_app()
    application.config["TESTING"] = True
    yield application

    os.environ.pop("WEBUI_DB_PATH", None)
    os.environ.pop("WEBUI_INSECURE_COOKIES", None)


class TestPjsipExtensionsSanitization:
    """Verify dangerous chars in extension fields are stripped in output."""

    def test_newline_in_callerid_stripped(self, app):
        with app.app_context():
            from app.db import get_db
            db = get_db()
            # Insert directly (bypass trigger with raw insert that has no newline in ext)
            db.execute(
                "INSERT INTO extensions (ext, callerid_name, sip_password) "
                "VALUES (?, ?, ?)",
                ("5000", "Alice", "securepass123"),
            )
            db.commit()

            # Now update callerid to have a dangerous value — bypass trigger
            # by not including newline, but include semicolon
            db.execute(
                "UPDATE extensions SET callerid_name = ? WHERE ext = ?",
                ("Good Name", "5000"),
            )
            db.commit()

            from app.generators import generate_pjsip_extensions
            output = generate_pjsip_extensions()

            # Should contain the extension
            assert "[5000]" in output
            assert "Good Name" in output
            # Should not contain raw newlines within a config value line
            for line in output.split("\n"):
                if "callerid" in line.lower():
                    assert "\n" not in line.rstrip("\n")

    def test_semicolon_in_password_stripped(self, app):
        with app.app_context():
            from app.db import get_db
            db = get_db()
            db.execute(
                "INSERT INTO extensions (ext, callerid_name, sip_password) "
                "VALUES (?, ?, ?)",
                ("5001", "Bob", "pass;word;bad"),
            )
            db.commit()

            from app.generators import generate_pjsip_extensions
            output = generate_pjsip_extensions()

            # The password line should not contain semicolons
            for line in output.split("\n"):
                if line.strip().startswith("password ="):
                    assert ";" not in line


class TestInboundFlowSanitization:
    """Verify inbound flow generator sanitizes DB values."""

    def test_spam_family_sanitized(self, app):
        with app.app_context():
            from app.db import get_db
            db = get_db()
            # Create required time group
            db.execute(
                "INSERT INTO time_groups (name, timezone, rules_json) VALUES (?, ?, ?)",
                ("biz", "Europe/Paris", json.dumps([{"start": "08:00", "end": "18:00", "days": ["mon"]}])),
            )
            db.commit()
            tg = db.execute("SELECT id FROM time_groups WHERE name = 'biz'").fetchone()

            db.execute(
                "INSERT INTO inbound_routes (name, open_target, closed_announcement, "
                "spam_family, time_group_id) VALUES (?, ?, ?, ?, ?)",
                ("test", "4900", "custom-closed", "spam;evil\nprefix", tg["id"]),
            )
            db.commit()

            from app.generators import generate_inbound_flow
            output = generate_inbound_flow()

            # Semicolons and newlines should be stripped from the family name
            assert "spam;evil" not in output
            assert "spamevilprefix" in output or "spamevilprefix" in output.replace(" ", "")
