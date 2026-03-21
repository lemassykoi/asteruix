"""Config snapshot helper — snapshot managed files before each apply."""

import os
import shutil
from datetime import datetime, timezone

WEBUI_CONF_DIR = "/etc/asterisk/webui"
SNAPSHOT_BASE = "/opt/asterisk-webui/config-snapshots"


def take_snapshot(label: str = "pre-apply") -> str:
    """Copy all managed config files to a timestamped snapshot directory.

    Returns the snapshot directory path.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snap_dir = os.path.join(SNAPSHOT_BASE, f"{label}-{ts}")
    os.makedirs(snap_dir, exist_ok=True)

    if os.path.isdir(WEBUI_CONF_DIR):
        for name in os.listdir(WEBUI_CONF_DIR):
            src = os.path.join(WEBUI_CONF_DIR, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(snap_dir, name))

    return snap_dir


def restore_snapshot(snap_dir: str):
    """Restore managed config files from a snapshot directory."""
    if not os.path.isdir(snap_dir):
        raise FileNotFoundError(f"Snapshot not found: {snap_dir}")

    for name in os.listdir(snap_dir):
        src = os.path.join(snap_dir, name)
        dst = os.path.join(WEBUI_CONF_DIR, name)
        if os.path.isfile(src):
            shutil.copy2(src, dst)


def list_snapshots() -> list[dict]:
    """Return available snapshots sorted newest first."""
    result = []
    if not os.path.isdir(SNAPSHOT_BASE):
        return result
    for name in sorted(os.listdir(SNAPSHOT_BASE), reverse=True):
        path = os.path.join(SNAPSHOT_BASE, name)
        if os.path.isdir(path):
            files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
            result.append({"name": name, "path": path, "files": files})
    return result
