"""Call Logs — read-only CDR viewer with pagination.

Reads Asterisk's ``Master.csv`` CDR file and presents call records
in a paginated table (100 records per page, newest first).
"""

from __future__ import annotations

import csv
import os
import re

from flask import Blueprint, jsonify, render_template, request

from app.auth import login_required

calllogs_bp = Blueprint("calllogs", __name__)

CDR_FILE = "/var/log/asterisk/cdr-csv/Master.csv"
PER_PAGE = 100

CDR_FIELDS = [
    "accountcode", "src", "dst", "dcontext", "clid", "channel",
    "dstchannel", "lastapp", "lastdata", "start", "answer", "end",
    "duration", "billsec", "disposition", "amaflags", "uniqueid",
    "userfield",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_cdr() -> list[dict]:
    """Read all CDR records from Master.csv, return newest first."""
    if not os.path.isfile(CDR_FILE):
        return []

    records: list[dict] = []
    try:
        with open(CDR_FILE, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < len(CDR_FIELDS):
                    continue
                rec = dict(zip(CDR_FIELDS, row))
                records.append(rec)
    except (OSError, PermissionError):
        return []

    records.reverse()
    return records


_INTERNAL_SRC_RE = re.compile(r"^(\*?\d{1,4})$")


def _is_internal(rec: dict) -> bool:
    """Return True if the record looks like an internal call."""
    return bool(_INTERNAL_SRC_RE.match(rec.get("src", "")))


def _format_duration(seconds: str) -> str:
    """Convert seconds string to 'Xm Ys' format."""
    try:
        s = int(seconds)
    except (ValueError, TypeError):
        return seconds
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@calllogs_bp.route("/api/v1/call-logs")
@login_required
def api_list():
    records = _read_cdr()
    hide_internal = request.args.get("hide_internal", "0") == "1"
    if hide_internal:
        records = [r for r in records if not _is_internal(r)]
    total = len(records)

    page = request.args.get("page", 1, type=int)
    page = max(1, page)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    page_records = records[start:end]

    return jsonify({
        "records": page_records,
        "page": page,
        "per_page": PER_PAGE,
        "total": total,
        "total_pages": total_pages,
        "hide_internal": hide_internal,
    })


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@calllogs_bp.route("/call-logs")
@login_required
def ui_list():
    records = _read_cdr()
    hide_internal = request.args.get("hide_internal", "0") == "1"
    if hide_internal:
        records = [r for r in records if not _is_internal(r)]
    total = len(records)

    page = request.args.get("page", 1, type=int)
    page = max(1, page)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    page_records = records[start:end]

    # Pre-format durations for display
    for rec in page_records:
        rec["duration_fmt"] = _format_duration(rec.get("billsec", "0"))

    return render_template(
        "calllogs.html",
        records=page_records,
        page=page,
        total_pages=total_pages,
        total=total,
        hide_internal=hide_internal,
    )
