"""SQLite database initialisation and access helpers."""

import os
import sqlite3

from flask import g, current_app

DB_PATH = os.environ.get("WEBUI_DB_PATH", "/var/lib/asterisk-webui/webui.db")

SCHEMA_VERSION = 7

SCHEMA_SQL = """\
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Extensions (PJSIP)
CREATE TABLE IF NOT EXISTS extensions (
    ext             TEXT PRIMARY KEY,
    callerid_name   TEXT NOT NULL DEFAULT '',
    sip_password    TEXT NOT NULL,
    vm_pin          TEXT NOT NULL DEFAULT '1234',
    enabled         INTEGER NOT NULL DEFAULT 1,
    max_contacts    INTEGER NOT NULL DEFAULT 3,
    codecs          TEXT NOT NULL DEFAULT 'g722,ulaw,alaw',
    language        TEXT NOT NULL DEFAULT 'fr',
    dtmf_mode       TEXT NOT NULL DEFAULT 'rfc4733',
    musicclass      TEXT NOT NULL DEFAULT 'default'
);

-- Trunks
CREATE TABLE IF NOT EXISTS trunks (
    name                    TEXT PRIMARY KEY,
    type                    TEXT NOT NULL DEFAULT 'registration',
    host                    TEXT NOT NULL DEFAULT '',
    did                     TEXT NOT NULL DEFAULT '',
    username                TEXT NOT NULL DEFAULT '',
    password                TEXT NOT NULL DEFAULT '',
    from_domain             TEXT NOT NULL DEFAULT '',
    contact_uri             TEXT NOT NULL DEFAULT '',
    identify_match          TEXT NOT NULL DEFAULT '',
    registration_client_uri TEXT NOT NULL DEFAULT '',
    registration_server_uri TEXT NOT NULL DEFAULT '',
    enabled                 INTEGER NOT NULL DEFAULT 1
);

-- Voicemail boxes
CREATE TABLE IF NOT EXISTS voicemail_boxes (
    mailbox             TEXT PRIMARY KEY,
    pin                 TEXT NOT NULL DEFAULT '1234',
    name                TEXT NOT NULL DEFAULT '',
    email               TEXT NOT NULL DEFAULT '',
    attach              INTEGER NOT NULL DEFAULT 0,
    delete_after_email  INTEGER NOT NULL DEFAULT 0
);

-- Blast (voicemail broadcast) config
CREATE TABLE IF NOT EXISTS blast_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_list    TEXT NOT NULL DEFAULT '',
    voicemail_flags TEXT NOT NULL DEFAULT 'su'
);

-- Music on Hold classes
CREATE TABLE IF NOT EXISTS moh_classes (
    name        TEXT PRIMARY KEY,
    directory   TEXT NOT NULL
);

-- MoH individual tracks
CREATE TABLE IF NOT EXISTS moh_tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name  TEXT NOT NULL REFERENCES moh_classes(name) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    duration_sec REAL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Announcements
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_name    TEXT NOT NULL UNIQUE,
    filename    TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT 'fr',
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    active      INTEGER NOT NULL DEFAULT 0
);

-- Time groups (business hours)
CREATE TABLE IF NOT EXISTS time_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    timezone    TEXT NOT NULL DEFAULT 'Europe/Paris',
    rules_json  TEXT NOT NULL DEFAULT '[]'
);

-- Inbound routes
CREATE TABLE IF NOT EXISTS inbound_routes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL UNIQUE,
    open_target             TEXT NOT NULL DEFAULT '',
    closed_announcement     TEXT NOT NULL DEFAULT '',
    blast_profile           INTEGER REFERENCES blast_config(id),
    spam_family             TEXT NOT NULL DEFAULT 'spam-prefix',
    fixed_holiday_family    TEXT NOT NULL DEFAULT 'holidays-fixed',
    variable_holiday_family TEXT NOT NULL DEFAULT 'holidays-variable',
    time_group_id           INTEGER REFERENCES time_groups(id)
);

-- Conference rooms
CREATE TABLE IF NOT EXISTS conference_rooms (
    extension               TEXT PRIMARY KEY,
    bridge_profile          TEXT NOT NULL DEFAULT 'default_bridge',
    user_profile            TEXT NOT NULL DEFAULT 'default_user',
    menu_profile            TEXT NOT NULL DEFAULT 'default_menu',
    max_members             INTEGER NOT NULL DEFAULT 10,
    moh_class               TEXT NOT NULL DEFAULT 'default',
    announce_join_leave     INTEGER NOT NULL DEFAULT 1,
    music_on_hold_when_empty INTEGER NOT NULL DEFAULT 1
);

-- WebUI users
CREATE TABLE IF NOT EXISTS ui_users (
    username        TEXT PRIMARY KEY,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'admin',
    enabled         INTEGER NOT NULL DEFAULT 1
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    username    TEXT NOT NULL DEFAULT 'system',
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    before_json TEXT,
    after_json  TEXT,
    status      TEXT NOT NULL DEFAULT 'ok'
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target);

-- Ring Groups
CREATE TABLE IF NOT EXISTS ring_groups (
    extension               TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    strategy                TEXT NOT NULL DEFAULT 'ringall',
    members                 TEXT NOT NULL,
    ring_time               INTEGER NOT NULL DEFAULT 30,
    greeting_announcement   TEXT NOT NULL DEFAULT '',
    moh_class               TEXT NOT NULL DEFAULT 'default',
    noanswer_announcement   TEXT NOT NULL DEFAULT '',
    noanswer_action         TEXT NOT NULL DEFAULT 'hangup',
    noanswer_target         TEXT NOT NULL DEFAULT ''
);

-- IVR Menus
CREATE TABLE IF NOT EXISTS ivr_menus (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    greeting            TEXT NOT NULL DEFAULT '',
    timeout             INTEGER NOT NULL DEFAULT 5,
    invalid_retries     INTEGER NOT NULL DEFAULT 3,
    options_json        TEXT NOT NULL DEFAULT '[]'
);

-- Settings (key-value)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- Outbound routes
CREATE TABLE IF NOT EXISTS outbound_routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    pattern         TEXT NOT NULL,
    trunk_name      TEXT NOT NULL REFERENCES trunks(name),
    failover_trunk  TEXT DEFAULT '' REFERENCES trunks(name),
    priority        INTEGER NOT NULL DEFAULT 10,
    enabled         INTEGER NOT NULL DEFAULT 1
);
"""


def get_db() -> sqlite3.Connection:
    """Return per-request database connection (stored on Flask g)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(exc=None):
    """Close the per-request database connection."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create all tables if the database is empty or needs migration."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Check current schema version
    try:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        current = row["v"] if row and row["v"] else 0
    except sqlite3.OperationalError:
        current = 0

    if current < SCHEMA_VERSION:
        conn.executescript(SCHEMA_SQL)

        # Migration v2 → v3: add CHECK-like triggers for critical fields
        if current < 3:
            _migrate_to_v3(conn)

        # Migration v3 → v4: add context column to extensions
        if current < 4:
            _migrate_to_v4(conn)

        # Migration v4 → v5: ring_groups table (created by SCHEMA_SQL above)

        # Migration v5 → v6: add settings table + seed defaults
        if current < 6:
            _migrate_to_v6(conn)

        # Migration v6 → v7: add did column to trunks + outbound_routes table
        if current < 7:
            _migrate_to_v7(conn)

        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.commit()

    conn.close()


def _migrate_to_v3(conn: sqlite3.Connection):
    """Add validation triggers for critical config fields.

    SQLite does not support ALTER TABLE ADD CHECK, so we use BEFORE
    INSERT/UPDATE triggers to reject values containing characters that
    would break Asterisk config file syntax (newlines, semicolons).
    """
    triggers = [
        # Extensions: ext must be digits only, 3-6 chars
        """
        CREATE TRIGGER IF NOT EXISTS chk_ext_format
        BEFORE INSERT ON extensions
        FOR EACH ROW
        WHEN NEW.ext NOT GLOB '[0-9][0-9][0-9]*'
          OR length(NEW.ext) < 3 OR length(NEW.ext) > 6
        BEGIN
            SELECT RAISE(ABORT, 'ext must be 3-6 digits');
        END
        """,
        # Trunks: name must be alphanumeric identifier
        """
        CREATE TRIGGER IF NOT EXISTS chk_trunk_name_format
        BEFORE INSERT ON trunks
        FOR EACH ROW
        WHEN NEW.name GLOB '*[^a-zA-Z0-9_-]*'
          OR length(NEW.name) < 1 OR length(NEW.name) > 32
        BEGIN
            SELECT RAISE(ABORT, 'trunk name must be 1-32 alphanumeric/hyphen/underscore chars');
        END
        """,
        # Reject newlines and semicolons in extension callerid_name
        """
        CREATE TRIGGER IF NOT EXISTS chk_ext_callerid_nolf
        BEFORE INSERT ON extensions
        FOR EACH ROW
        WHEN NEW.callerid_name LIKE '%' || X'0A' || '%'
          OR NEW.callerid_name LIKE '%;%'
        BEGIN
            SELECT RAISE(ABORT, 'callerid_name must not contain newlines or semicolons');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS chk_ext_callerid_nolf_upd
        BEFORE UPDATE ON extensions
        FOR EACH ROW
        WHEN NEW.callerid_name LIKE '%' || X'0A' || '%'
          OR NEW.callerid_name LIKE '%;%'
        BEGIN
            SELECT RAISE(ABORT, 'callerid_name must not contain newlines or semicolons');
        END
        """,
    ]
    for sql in triggers:
        conn.execute(sql)


def _migrate_to_v4(conn: sqlite3.Connection):
    """Add context column to extensions table.

    Allows overriding the PJSIP endpoint context per extension
    (default 'internal', alternative 'from-trunk' for gateway devices).
    """
    try:
        conn.execute(
            "ALTER TABLE extensions ADD COLUMN context TEXT NOT NULL DEFAULT 'internal'"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists


def _migrate_to_v6(conn: sqlite3.Connection):
    """Add settings table and seed default values."""
    defaults = [
        ("telegram_enabled", "0"),
        ("telegram_bot_token", ""),
        ("telegram_chat_id", ""),
        ("pbx_name", "Asterisk SOHO PBX"),
        ("default_language", "fr"),
        ("timezone", "Europe/Paris"),
        ("smtp_host", ""),
        ("smtp_port", "587"),
        ("smtp_username", ""),
        ("smtp_password", ""),
        ("smtp_from", ""),
        ("smtp_tls", "1"),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


def _migrate_to_v7(conn: sqlite3.Connection):
    """Add did column to trunks and create outbound_routes table."""
    try:
        conn.execute(
            "ALTER TABLE trunks ADD COLUMN did TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS outbound_routes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            pattern         TEXT NOT NULL,
            trunk_name      TEXT NOT NULL REFERENCES trunks(name),
            failover_trunk  TEXT DEFAULT '' REFERENCES trunks(name),
            priority        INTEGER NOT NULL DEFAULT 10,
            enabled         INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Seed default outbound routes if table is empty and referenced trunks exist
    count = conn.execute("SELECT COUNT(*) FROM outbound_routes").fetchone()[0]
    trunk_exists = conn.execute(
        "SELECT COUNT(*) FROM trunks WHERE name IN ('OVH_IPC', 'OVH_IPA')"
    ).fetchone()[0]
    if count == 0 and trunk_exists == 2:
        defaults = [
            ("Urgences", "_1[578]", "OVH_IPC", "OVH_IPA", 1, 1),
            ("Urgences 112", "112", "OVH_IPC", "OVH_IPA", 2, 1),
            ("France fixe/VoIP", "_0[1-59]XXXXXXXX", "OVH_IPC", "OVH_IPA", 10, 1),
        ]
        for name, pattern, trunk, failover, priority, enabled in defaults:
            conn.execute(
                "INSERT INTO outbound_routes (name, pattern, trunk_name, failover_trunk, priority, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, pattern, trunk, failover, priority, enabled),
            )


def register_db(app):
    """Register database lifecycle hooks with the Flask app."""
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
