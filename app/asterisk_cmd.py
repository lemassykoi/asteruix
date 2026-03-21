"""Asterisk CLI adapter — allowlist-only command execution layer.

Provides a safe wrapper around ``asterisk -rx`` and typed parsers for
common Asterisk CLI outputs.  Also includes a fail2ban status helper.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ASTERISK_BIN = "/usr/sbin/asterisk"
FAIL2BAN_CLIENT = "/usr/bin/fail2ban-client"
COMMAND_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Allowlist — only these command prefixes may be executed
# ---------------------------------------------------------------------------

ALLOWED_PREFIXES: list[str] = [
    "core show version",
    "core show channels concise",
    "core show uptime",
    "pjsip show endpoints",
    "pjsip reload",
    "dialplan reload",
    "moh reload",
    "module reload",
    "database show",
    "database put",
    "database del",
    "database get",
    "voicemail show users",
    "voicemail reload",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AsteriskCommandError(Exception):
    """Raised when an ``asterisk -rx`` invocation fails."""


class CommandNotAllowed(AsteriskCommandError):
    """Raised when a command is not in the allowlist."""


# ---------------------------------------------------------------------------
# Low-level executor
# ---------------------------------------------------------------------------

def run_command(command: str, *, timeout: int = COMMAND_TIMEOUT) -> str:
    """Execute an Asterisk CLI command via ``asterisk -rx``.

    Only commands matching :data:`ALLOWED_PREFIXES` are accepted.

    Returns the raw stdout text.  Raises :class:`AsteriskCommandError` on
    non-zero exit or timeout, and :class:`CommandNotAllowed` if the command
    is not in the allowlist.
    """
    if not any(command.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        raise CommandNotAllowed(f"Command not allowed: {command!r}")

    try:
        result = subprocess.run(
            [ASTERISK_BIN, "-rx", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise AsteriskCommandError(
            f"Asterisk binary not found at {ASTERISK_BIN}"
        )
    except subprocess.TimeoutExpired:
        raise AsteriskCommandError(
            f"Command timed out after {timeout}s: {command!r}"
        )

    if result.returncode != 0:
        raise AsteriskCommandError(
            f"asterisk -rx {command!r} exited {result.returncode}: "
            f"{result.stderr.strip()}"
        )

    return result.stdout


# ---------------------------------------------------------------------------
# Typed result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AsteriskVersion:
    version: str
    build_user: str
    build_host: str
    build_date: str


@dataclass
class EndpointContact:
    uri: str
    status: str
    rtt_ms: float | None = None


@dataclass
class Endpoint:
    name: str
    caller_id: str
    state: str
    channel_count: int
    auth_username: str = ""
    aor: str = ""
    max_contacts: int = 0
    contacts: list[EndpointContact] = field(default_factory=list)


@dataclass
class Channel:
    channel: str
    context: str
    extension: str
    priority: str
    state: str
    application: str
    application_data: str
    caller_id: str
    account_code: str
    peer_account: str
    ama_flags: str
    duration: str
    bridged_to: str


@dataclass
class AstDBEntry:
    key: str
    value: str


@dataclass
class AsteriskUptime:
    system_uptime: str
    last_reload: str


@dataclass
class Fail2banStatus:
    currently_failed: int
    total_failed: int
    currently_banned: int
    total_banned: int
    banned_ips: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_version(raw: str) -> AsteriskVersion:
    """Parse ``core show version`` output.

    Example input::

        Asterisk 22.8.2 built by root @ asterisk on a x86_64 running Linux on 2026-03-20 23:02:10 UTC
    """
    m = re.match(
        r"Asterisk\s+(\S+)\s+built\s+by\s+(\S+)\s+@\s+(\S+)\s+.*on\s+"
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\S+)",
        raw.strip(),
    )
    if not m:
        raise AsteriskCommandError(f"Cannot parse version string: {raw!r}")
    return AsteriskVersion(
        version=m.group(1),
        build_user=m.group(2),
        build_host=m.group(3),
        build_date=m.group(4),
    )


def parse_endpoints(raw: str) -> list[Endpoint]:
    """Parse ``pjsip show endpoints`` output into a list of :class:`Endpoint`."""
    endpoints: list[Endpoint] = []
    current: Endpoint | None = None

    for line in raw.splitlines():
        # Endpoint line:  " Endpoint:  4900/4900  Not in use  0 of inf"
        ep_match = re.match(
            r"\s+Endpoint:\s+(\S+?)(?:/(\S+))?\s{2,}(\S.*?)\s{2,}(\d+)\s+of\s+",
            line,
        )
        if ep_match:
            if current is not None:
                endpoints.append(current)
            current = Endpoint(
                name=ep_match.group(1),
                caller_id=ep_match.group(2) or "",
                state=ep_match.group(3).strip(),
                channel_count=int(ep_match.group(4)),
            )
            continue

        if current is None:
            continue

        # InAuth line: "     InAuth:  4900-auth/4900"
        auth_match = re.match(r"\s+InAuth:\s+\S+/(\S+)", line)
        if auth_match:
            current.auth_username = auth_match.group(1)
            continue

        # Aor line: "        Aor:  4900  3"
        aor_match = re.match(r"\s+Aor:\s+(\S+)\s+(\d+)", line)
        if aor_match:
            current.aor = aor_match.group(1)
            current.max_contacts = int(aor_match.group(2))
            continue

        # Contact line: "      Contact:  4900/sip:4900@10.0.0.24:5060  907ff... Avail  29.680"
        contact_match = re.match(
            r"\s+Contact:\s+\S+/(\S+)\s+\S+\s+(\S+)\s*([\d.]*)",
            line,
        )
        if contact_match:
            rtt = None
            if contact_match.group(3):
                try:
                    rtt = float(contact_match.group(3))
                except ValueError:
                    pass
            current.contacts.append(
                EndpointContact(
                    uri=contact_match.group(1),
                    status=contact_match.group(2),
                    rtt_ms=rtt,
                )
            )
            continue

    if current is not None:
        endpoints.append(current)

    return endpoints


def parse_channels_concise(raw: str) -> list[Channel]:
    """Parse ``core show channels concise`` output.

    Each line is ``!``-delimited with these fields (in order):
    channel, context, exten, prio, state, application, data,
    callerid, accountcode, peeraccount, amaflags, duration, bridgedto
    """
    channels: list[Channel] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("!")
        if len(parts) < 13:
            continue
        channels.append(
            Channel(
                channel=parts[0],
                context=parts[1],
                extension=parts[2],
                priority=parts[3],
                state=parts[4],
                application=parts[5],
                application_data=parts[6],
                caller_id=parts[7],
                account_code=parts[8],
                peer_account=parts[9],
                ama_flags=parts[10],
                duration=parts[11],
                bridged_to=parts[12],
            )
        )
    return channels


def parse_database_show(raw: str) -> list[AstDBEntry]:
    """Parse ``database show <family>`` output.

    Each data line looks like::

        /spam-prefix/0161                                 : 1

    The trailing summary line (``N results found.``) is skipped.
    """
    entries: list[AstDBEntry] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.endswith("results found.") or line.endswith("result found."):
            continue
        m = re.match(r"(/\S+)\s*:\s*(.*)", line)
        if m:
            # Extract the last path component as the key
            full_path = m.group(1)
            key = full_path.rsplit("/", 1)[-1]
            value = m.group(2).strip()
            entries.append(AstDBEntry(key=key, value=value))
    return entries


# ---------------------------------------------------------------------------
# High-level convenience functions
# ---------------------------------------------------------------------------

def get_version() -> AsteriskVersion:
    """Return parsed Asterisk version info."""
    return parse_version(run_command("core show version"))


def get_endpoints() -> list[Endpoint]:
    """Return all PJSIP endpoints with contact/state info."""
    return parse_endpoints(run_command("pjsip show endpoints"))


def get_channels() -> list[Channel]:
    """Return active channels (may be empty list)."""
    return parse_channels_concise(run_command("core show channels concise"))


def get_database(family: str) -> list[AstDBEntry]:
    """Return all entries for an AstDB *family*.

    The family name is validated to contain only safe characters.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", family):
        raise CommandNotAllowed(f"Invalid AstDB family name: {family!r}")
    return parse_database_show(run_command(f"database show {family}"))


def parse_uptime(raw: str) -> AsteriskUptime:
    """Parse ``core show uptime`` output.

    Example::

        System uptime: 10 hours, 41 minutes, 27 seconds
        Last reload: 10 hours, 3 minutes, 13 seconds
    """
    system = ""
    reload_ = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("System uptime:"):
            system = line.split(":", 1)[1].strip()
        elif line.startswith("Last reload:"):
            reload_ = line.split(":", 1)[1].strip()
    return AsteriskUptime(system_uptime=system, last_reload=reload_)


def get_uptime() -> AsteriskUptime:
    """Return parsed Asterisk uptime info."""
    return parse_uptime(run_command("core show uptime"))


def get_fail2ban_status(jail: str = "asterisk") -> Fail2banStatus:
    """Query fail2ban-client for jail status.

    This runs ``fail2ban-client status <jail>`` — not an Asterisk
    command, so it bypasses the allowlist but uses a fixed binary path
    and validates the jail name.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", jail):
        raise CommandNotAllowed(f"Invalid jail name: {jail!r}")

    try:
        result = subprocess.run(
            [FAIL2BAN_CLIENT, "status", jail],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return Fail2banStatus(
            currently_failed=0, total_failed=0,
            currently_banned=0, total_banned=0,
        )

    if result.returncode != 0:
        return Fail2banStatus(
            currently_failed=0, total_failed=0,
            currently_banned=0, total_banned=0,
        )

    def _extract_int(label: str) -> int:
        m = re.search(rf"{re.escape(label)}:\s*(\d+)", result.stdout)
        return int(m.group(1)) if m else 0

    banned_ips: list[str] = []
    m = re.search(r"Banned IP list:\s*(.*)", result.stdout)
    if m and m.group(1).strip():
        banned_ips = m.group(1).strip().split()

    return Fail2banStatus(
        currently_failed=_extract_int("Currently failed"),
        total_failed=_extract_int("Total failed"),
        currently_banned=_extract_int("Currently banned"),
        total_banned=_extract_int("Total banned"),
        banned_ips=banned_ips,
    )
