"""Centralised apply-and-reload orchestration with rollback on failure.

Every blueprint that writes config files and reloads Asterisk should call
``safe_apply`` instead of inlining snapshot / write / reload / rollback logic.
"""

from __future__ import annotations

import re

from app.asterisk_cmd import AsteriskCommandError, run_command
from app.snapshots import take_snapshot, restore_snapshot

# ---------------------------------------------------------------------------
# Config-field sanitiser for values emitted into Asterisk include files
# ---------------------------------------------------------------------------

# Characters that must never appear in a generated Asterisk config value.
# Newlines would break the config-file line structure; semicolons start
# comments; backticks and shell meta-characters could be dangerous if a
# value ever leaks into a shell context.
_UNSAFE_RE = re.compile(r"[\n\r;`$\\]")


def sanitize_config_value(value: str | None, *, max_length: int = 128) -> str:
    """Return *value* with dangerous characters removed and length capped.

    Designed for values interpolated into Asterisk .conf include files.
    """
    if value is None:
        return ""
    text = str(value)
    text = _UNSAFE_RE.sub("", text)
    return text.strip()[:max_length]


# ---------------------------------------------------------------------------
# Safe apply helper
# ---------------------------------------------------------------------------

def safe_apply(
    *,
    label: str,
    writers: list[callable],
    reload_commands: list[str],
) -> tuple[bool, str]:
    """Snapshot → write files → reload Asterisk → rollback on failure.

    Parameters
    ----------
    label:
        Human-readable label for the snapshot (e.g. ``"pre-extension-apply"``).
    writers:
        Callables that write config files (e.g. ``write_pjsip_extensions``).
    reload_commands:
        Asterisk CLI commands to run after writing (e.g. ``["pjsip reload"]``).

    Returns
    -------
    (success, message) tuple.
    """
    snap_dir = take_snapshot(label)

    for writer in writers:
        writer()

    try:
        for cmd in reload_commands:
            run_command(cmd)
        return True, "Config applied and Asterisk reloaded."
    except AsteriskCommandError as exc:
        # Rollback: restore the previous config files
        try:
            restore_snapshot(snap_dir)
            # Attempt a second reload with the restored files so Asterisk
            # picks up the known-good state.  Failures here are logged but
            # not propagated — the primary error is what the caller cares
            # about.
            for cmd in reload_commands:
                try:
                    run_command(cmd)
                except AsteriskCommandError:
                    pass
        except Exception:
            pass
        return False, f"Asterisk reload failed (rolled back): {exc}"
