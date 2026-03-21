"""Authentication: login/logout, session management, admin bootstrap."""

import functools

import bcrypt
from urllib.parse import urlparse

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.db import get_db
from app.audit import log_action

auth_bp = Blueprint("auth", __name__)

SESSION_IDLE_MINUTES = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def get_current_user() -> str | None:
    """Return the logged-in username or None."""
    return session.get("username")


def _is_safe_redirect(target: str) -> bool:
    """Return True if *target* is a safe internal redirect (relative path)."""
    if not target:
        return False
    parsed = urlparse(target)
    # Allow only relative paths (no scheme, no netloc)
    if parsed.scheme or parsed.netloc:
        return False
    # Must start with / to be a valid internal path
    return target.startswith("/")


def login_required(view):
    """Decorator that redirects to login if not authenticated."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Session idle timeout enforcement
# ---------------------------------------------------------------------------

@auth_bp.before_app_request
def enforce_session_timeout():
    import time

    if "username" not in session:
        return
    last = session.get("_last_active", 0)
    now = time.time()
    if now - last > SESSION_IDLE_MINUTES * 60:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("auth.login"))
    session["_last_active"] = now


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    from flask_wtf import FlaskForm
    from wtforms import StringField, PasswordField
    from wtforms.validators import DataRequired

    class LoginForm(FlaskForm):
        username = StringField("Username", validators=[DataRequired()])
        password = PasswordField("Password", validators=[DataRequired()])

    form = LoginForm()
    if form.validate_on_submit():
        db = get_db()
        row = db.execute(
            "SELECT username, password_hash, enabled FROM ui_users WHERE username = ?",
            (form.username.data,),
        ).fetchone()
        if row and row["enabled"] and check_password(form.password.data, row["password_hash"]):
            import time

            session.clear()
            session["username"] = row["username"]
            session["_last_active"] = time.time()
            session.permanent = True
            log_action("login", target=row["username"], username=row["username"])
            next_url = request.args.get("next", "")
            if not _is_safe_redirect(next_url):
                next_url = url_for("core.index")
            return redirect(next_url)
        flash("Invalid credentials.", "danger")
        log_action("login_failed", target=form.username.data, status="fail")

    return render_template("login.html", form=form)


@auth_bp.route("/logout")
def logout():
    username = get_current_user() or "unknown"
    log_action("logout", target=username, username=username)
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
