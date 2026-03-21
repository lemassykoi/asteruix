"""SQLite database initialisation and access helpers."""

import os
import sqlite3

from flask import g, current_app

DB_PATH = os.environ.get("WEBUI_DB_PATH", "/var/lib/asterisk-webui/webui.db")

SCHEMA_VERSION = 2

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

-- IVR Menus
CREATE TABLE IF NOT EXISTS ivr_menus (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    greeting            TEXT NOT NULL DEFAULT '',
    timeout             INTEGER NOT NULL DEFAULT 5,
    invalid_retries     INTEGER NOT NULL DEFAULT 3,
    options_json        TEXT NOT NULL DEFAULT '[]'
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
        # Migration from v1 to v2: ivr_menus table added via CREATE IF NOT EXISTS
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.commit()

    conn.close()


def register_db(app):
    """Register database lifecycle hooks with the Flask app."""
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
