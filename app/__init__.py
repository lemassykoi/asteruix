import os
from datetime import timedelta

from flask import Flask
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def _get_secret_key() -> str:
    env_key = os.environ.get("WEBUI_SECRET_KEY")
    if env_key:
        return env_key
    key_file = "/var/lib/asterisk-webui/.secret_key"
    if os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()
    import secrets
    key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    with open(key_file, "w") as f:
        f.write(key)
    os.chmod(key_file, 0o600)
    return key


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = _get_secret_key()
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit

    # Session security
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("WEBUI_SECURE_COOKIES"))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

    # CSRF protection (all POST/PUT/PATCH/DELETE validated automatically)
    csrf.init_app(app)

    from app.db import register_db
    register_db(app)

    from app.routes import core_bp
    from app.auth import auth_bp
    from app.system import system_bp
    from app.extensions import extensions_bp
    from app.trunks import trunks_bp
    from app.moh import moh_bp
    from app.announcements import announcements_bp
    from app.voicemail import voicemail_bp
    from app.timegroups import timegroups_bp
    from app.holidays import holidays_bp
    from app.spam import spam_bp
    from app.inbound import inbound_bp
    from app.conference import conference_bp
    from app.ivr import ivr_bp
    from app.dialplan import dialplan_bp
    from app.backups import backups_bp
    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(extensions_bp)
    app.register_blueprint(trunks_bp)
    app.register_blueprint(moh_bp)
    app.register_blueprint(announcements_bp)
    app.register_blueprint(voicemail_bp)
    app.register_blueprint(timegroups_bp)
    app.register_blueprint(holidays_bp)
    app.register_blueprint(spam_bp)
    app.register_blueprint(inbound_bp)
    app.register_blueprint(conference_bp)
    app.register_blueprint(ivr_bp)
    app.register_blueprint(dialplan_bp)
    app.register_blueprint(backups_bp)

    return app
