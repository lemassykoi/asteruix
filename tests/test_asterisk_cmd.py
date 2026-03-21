"""Tests for the Asterisk CLI adapter (parsers + allowlist enforcement)."""

import pytest

from app.asterisk_cmd import (
    AstDBEntry,
    AsteriskCommandError,
    AsteriskVersion,
    Channel,
    CommandNotAllowed,
    Endpoint,
    EndpointContact,
    parse_channels_concise,
    parse_database_show,
    parse_endpoints,
    parse_version,
    run_command,
)


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_allowed_command_prefix(self, monkeypatch):
        """Allowed commands pass the allowlist check (mock subprocess)."""
        import subprocess

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="ok\n", stderr=""),
        )
        assert run_command("core show version") == "ok\n"

    def test_blocked_command(self):
        with pytest.raises(CommandNotAllowed, match="not allowed"):
            run_command("system shutdown now")

    def test_blocked_arbitrary_shell(self):
        with pytest.raises(CommandNotAllowed):
            run_command("! rm -rf /")

    def test_unrelated_prefix_rejected(self):
        with pytest.raises(CommandNotAllowed):
            run_command("core restart now")


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------

VERSION_RAW = (
    "Asterisk 22.8.2 built by root @ asterisk on a x86_64 "
    "running Linux on 2026-03-20 23:02:10 UTC"
)


class TestParseVersion:
    def test_normal(self):
        v = parse_version(VERSION_RAW)
        assert v == AsteriskVersion(
            version="22.8.2",
            build_user="root",
            build_host="asterisk",
            build_date="2026-03-20 23:02:10 UTC",
        )

    def test_garbage_raises(self):
        with pytest.raises(AsteriskCommandError, match="Cannot parse"):
            parse_version("not a version string")


# ---------------------------------------------------------------------------
# parse_endpoints
# ---------------------------------------------------------------------------

ENDPOINTS_RAW = """\

 Endpoint:  <Endpoint/CID.....................................>  <State.....>  <Channels.>
    I/OAuth:  <AuthId/UserName...........................................................>
        Aor:  <Aor............................................>  <MaxContact>
      Contact:  <Aor/ContactUri..........................> <Hash....> <Status> <RTT(ms)..>
  Transport:  <TransportId........>  <Type>  <cos>  <tos>  <BindAddress..................>
   Identify:  <Identify/Endpoint.........................................................>
        Match:  <criteria.........................>
    Channel:  <ChannelId......................................>  <State.....>  <Time.....>
        Exten: <DialedExten...........>  CLCID: <ConnectedLineCID.......>
==========================================================================================

 Endpoint:  4900/4900                                            Not in use    0 of inf
     InAuth:  4900-auth/4900
        Aor:  4900                                               3
      Contact:  4900/sip:4900@10.0.0.24:5060               907ffae993 Avail        29.680

 Endpoint:  4901/4901                                            Unavailable   0 of inf
     InAuth:  4901-auth/4901
        Aor:  4901                                               3

 Endpoint:  4902/4902                                            Unavailable   0 of inf
     InAuth:  4902-auth/4902
        Aor:  4902                                               3

 Endpoint:  4903/4903                                            Unavailable   0 of inf
     InAuth:  4903-auth/4903
        Aor:  4903                                               3

 Endpoint:  4904/4904                                            Unavailable   0 of inf
     InAuth:  4904-auth/4904
        Aor:  4904                                               3


Objects found: 5

"""


class TestParseEndpoints:
    def test_all_five(self):
        eps = parse_endpoints(ENDPOINTS_RAW)
        assert len(eps) == 5

    def test_registered_endpoint(self):
        eps = parse_endpoints(ENDPOINTS_RAW)
        e4900 = eps[0]
        assert e4900.name == "4900"
        assert e4900.caller_id == "4900"
        assert e4900.state == "Not in use"
        assert e4900.channel_count == 0
        assert e4900.auth_username == "4900"
        assert e4900.aor == "4900"
        assert e4900.max_contacts == 3
        assert len(e4900.contacts) == 1
        assert e4900.contacts[0].uri == "sip:4900@10.0.0.24:5060"
        assert e4900.contacts[0].status == "Avail"
        assert e4900.contacts[0].rtt_ms == pytest.approx(29.68)

    def test_unavailable_endpoint(self):
        eps = parse_endpoints(ENDPOINTS_RAW)
        e4901 = eps[1]
        assert e4901.name == "4901"
        assert e4901.state == "Unavailable"
        assert e4901.contacts == []

    def test_empty_input(self):
        assert parse_endpoints("") == []


# ---------------------------------------------------------------------------
# parse_channels_concise
# ---------------------------------------------------------------------------

CHANNELS_RAW = (
    "PJSIP/4900-00000001!internal!4901!1!Ring!Dial!"
    "PJSIP/4901,,30!4900!!!!3!7!(None)\n"
)


class TestParseChannelsConcise:
    def test_single_channel(self):
        chs = parse_channels_concise(CHANNELS_RAW)
        assert len(chs) == 1
        ch = chs[0]
        assert ch.channel == "PJSIP/4900-00000001"
        assert ch.context == "internal"
        assert ch.extension == "4901"
        assert ch.state == "Ring"
        assert ch.application == "Dial"
        assert ch.caller_id == "4900"

    def test_empty_input(self):
        assert parse_channels_concise("") == []

    def test_blank_lines_skipped(self):
        assert parse_channels_concise("\n\n\n") == []


# ---------------------------------------------------------------------------
# parse_database_show
# ---------------------------------------------------------------------------

DB_SPAM_RAW = """\
/spam-prefix/0161                                 : 1                        
/spam-prefix/0162                                 : 1                        
/spam-prefix/0270                                 : 1                        
12 results found.
"""

DB_HOLIDAYS_RAW = """\
/holidays-fixed/0101                              : Jour_de_lan              
/holidays-fixed/0501                              : Fete_du_travail          
2 results found.
"""


class TestParseDatabaseShow:
    def test_spam_prefix(self):
        entries = parse_database_show(DB_SPAM_RAW)
        assert len(entries) == 3
        assert entries[0] == AstDBEntry(key="0161", value="1")
        assert entries[2] == AstDBEntry(key="0270", value="1")

    def test_holidays_with_names(self):
        entries = parse_database_show(DB_HOLIDAYS_RAW)
        assert len(entries) == 2
        assert entries[0] == AstDBEntry(key="0101", value="Jour_de_lan")
        assert entries[1] == AstDBEntry(key="0501", value="Fete_du_travail")

    def test_empty_input(self):
        assert parse_database_show("") == []

    def test_only_summary_line(self):
        assert parse_database_show("0 results found.\n") == []

    def test_single_result(self):
        raw = "/test/abc  : val\n1 result found.\n"
        entries = parse_database_show(raw)
        assert len(entries) == 1
        assert entries[0].key == "abc"


# ---------------------------------------------------------------------------
# get_database family validation
# ---------------------------------------------------------------------------

class TestGetDatabaseValidation:
    def test_invalid_family_rejected(self):
        from app.asterisk_cmd import get_database

        with pytest.raises(CommandNotAllowed, match="Invalid AstDB family"):
            get_database("spam; rm -rf /")

    def test_valid_family_names(self):
        from app.asterisk_cmd import get_database
        import subprocess

        # Mock subprocess to avoid real Asterisk call
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="0 results found.\n", stderr="")

        import app.asterisk_cmd as mod
        original = subprocess.run
        subprocess.run = mock_run
        try:
            assert get_database("spam-prefix") == []
            assert get_database("holidays_fixed") == []
        finally:
            subprocess.run = original
