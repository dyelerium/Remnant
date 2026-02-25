"""
Tests for GmailTool.

Covers:
  - Credential resolution (env vars vs args vs missing)
  - action routing (list_unread, search, read, unknown)
  - _decode_header: ASCII, RFC-2047 encoded, bytes input
  - _extract_body: plain, multipart, empty
  - _fetch_summary: happy path + missing data
  - _list_unread, _search, _read via IMAP mock
  - IMAP4.error propagation
  - schema_hint structure
"""
from __future__ import annotations

import asyncio
import email
import email.header
import email.mime.multipart
import email.mime.text
import imaplib
from email.message import Message
from unittest.mock import MagicMock, patch, AsyncMock, call
from io import BytesIO

import pytest

from tools.gmail_tool import GmailTool


# ===========================================================================
# Helpers
# ===========================================================================

def _make_raw_email(
    subject: str = "Hello",
    from_: str = "sender@example.com",
    body: str = "Test body",
    date: str = "Mon, 01 Jan 2024 12:00:00 +0000",
) -> bytes:
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = "me@gmail.com"
    msg["Date"] = date
    return msg.as_bytes()


def _make_raw_multipart(body_text: str = "Multipart body") -> bytes:
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = "Multipart"
    msg["From"] = "multi@example.com"
    msg["To"] = "me@gmail.com"
    msg["Date"] = "Tue, 02 Jan 2024 10:00:00 +0000"
    msg.attach(email.mime.text.MIMEText(body_text, "plain", "utf-8"))
    msg.attach(email.mime.text.MIMEText("<b>HTML</b>", "html", "utf-8"))
    return msg.as_bytes()


# ===========================================================================
# Credential resolution
# ===========================================================================

class TestCredentials:
    def test_reads_address_from_env(self, monkeypatch):
        monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "secret")
        tool = GmailTool()
        assert tool._address == "test@gmail.com"
        assert tool._app_password == "secret"

    def test_constructor_args_override_env(self, monkeypatch):
        monkeypatch.setenv("GMAIL_ADDRESS", "env@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "env_pass")
        tool = GmailTool(address="arg@gmail.com", app_password="arg_pass")
        assert tool._address == "arg@gmail.com"
        assert tool._app_password == "arg_pass"

    def test_missing_credentials_returns_error(self, monkeypatch):
        monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        tool = GmailTool()
        result = asyncio.get_event_loop().run_until_complete(
            tool.run({"action": "list_unread"})
        )
        assert not result.success
        assert "credentials" in result.error.lower() or "gmail_address" in result.error.lower()

    def test_per_call_address_overrides_env(self, monkeypatch):
        """If address passed in args, use that instead of env."""
        monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        tool = GmailTool()
        # Should fail on IMAP — not credential check
        with patch("imaplib.IMAP4_SSL") as mock_ssl:
            mock_ssl.return_value.__enter__ = lambda s: MagicMock()
            mock_ssl.return_value.__exit__ = MagicMock(return_value=False)
            # Tool resolves address from args
            result = asyncio.get_event_loop().run_until_complete(
                tool.run({"action": "list_unread", "address": "x@gmail.com", "app_password": "p"})
            )
        # Not a credential error
        assert "credentials" not in (result.error or "")


# ===========================================================================
# schema_hint
# ===========================================================================

class TestSchemaHint:
    def test_has_required_action(self):
        tool = GmailTool()
        schema = tool.schema_hint
        assert "required" in schema["parameters"]
        assert "action" in schema["parameters"]["required"]

    def test_action_enum(self):
        tool = GmailTool()
        props = tool.schema_hint["parameters"]["properties"]
        assert set(props["action"]["enum"]) == {"list_unread", "search", "read"}

    def test_name_and_description(self):
        tool = GmailTool()
        assert tool.schema_hint["name"] == "gmail"
        assert len(tool.schema_hint["description"]) > 0

    def test_uid_and_query_in_properties(self):
        tool = GmailTool()
        props = tool.schema_hint["parameters"]["properties"]
        assert "uid" in props
        assert "query" in props
        assert "count" in props


# ===========================================================================
# _decode_header
# ===========================================================================

class TestDecodeHeader:
    def test_plain_ascii(self):
        result = GmailTool._decode_header("Hello World")
        assert result == "Hello World"

    def test_utf8_encoded_header(self):
        # RFC 2047 encoded: =?utf-8?b?...?=
        encoded = email.header.make_header([(b"Caf\xc3\xa9", "utf-8")])
        value = str(encoded)
        result = GmailTool._decode_header(value)
        assert "Caf" in result

    def test_empty_string(self):
        result = GmailTool._decode_header("")
        assert result == ""

    def test_bytes_with_charset(self):
        # Simulate decode_header returning [(bytes, charset)]
        with patch("email.header.decode_header", return_value=[(b"Test", "utf-8")]):
            result = GmailTool._decode_header("anything")
        assert result == "Test"

    def test_mixed_encoded_and_plain(self):
        # Two parts: one plain string, one encoded
        with patch("email.header.decode_header", return_value=[("plain", None), (b"enc", "ascii")]):
            result = GmailTool._decode_header("x")
        assert "plain" in result
        assert "enc" in result


# ===========================================================================
# _extract_body
# ===========================================================================

class TestExtractBody:
    def test_plain_text_message(self):
        msg = email.mime.text.MIMEText("Hello from plain", "plain", "utf-8")
        body = GmailTool._extract_body(msg)
        assert "Hello from plain" in body

    def test_multipart_returns_text_plain(self):
        raw = _make_raw_multipart("Multipart plain body")
        msg = email.message_from_bytes(raw)
        body = GmailTool._extract_body(msg)
        assert "Multipart plain body" in body

    def test_multipart_skips_html(self):
        raw = _make_raw_multipart("Only plain text here")
        msg = email.message_from_bytes(raw)
        body = GmailTool._extract_body(msg)
        assert "<b>" not in body

    def test_empty_payload_returns_empty_string(self):
        msg = Message()
        body = GmailTool._extract_body(msg)
        assert body == ""

    def test_multipart_no_plain_part_returns_empty(self):
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg.attach(email.mime.text.MIMEText("<b>HTML only</b>", "html", "utf-8"))
        body = GmailTool._extract_body(msg)
        assert body == ""


# ===========================================================================
# IMAP action routing via mock
# ===========================================================================

def _setup_imap_mock(uids: list[bytes], raw_emails: dict[bytes, bytes]):
    """Return a mock IMAP4_SSL context manager for the given UID list."""
    imap = MagicMock(spec=imaplib.IMAP4_SSL)

    # imap.search returns (typ, [b"uid1 uid2 ..."])
    imap.search.return_value = ("OK", [b" ".join(uids)])

    def fetch_side_effect(uid, spec):
        raw = raw_emails.get(uid, b"")
        if b"HEADER.FIELDS" in spec:
            # Return headers only
            return ("OK", [(None, raw)])
        return ("OK", [(None, raw)])

    imap.fetch.side_effect = fetch_side_effect
    return imap


class TestListUnread:
    def test_returns_emails_list(self):
        raw = _make_raw_email("Test Subject", "sender@x.com")
        tool = GmailTool(address="a@gmail.com", app_password="pass")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b"1 2 3"])
        imap.fetch.return_value = ("OK", [(None, raw)])

        result = tool._list_unread(imap, count=3)
        assert result.success
        assert "emails" in result.output
        assert len(result.output["emails"]) == 3

    def test_total_unread_count(self):
        raw = _make_raw_email()
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b"1 2 3 4 5"])
        imap.fetch.return_value = ("OK", [(None, raw)])

        result = tool._list_unread(imap, count=2)
        assert result.output["total_unread"] == 5
        assert len(result.output["emails"]) == 2

    def test_empty_inbox(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b""])

        result = tool._list_unread(imap, count=10)
        assert result.success
        assert result.output["emails"] == []
        assert result.output["total_unread"] == 0

    def test_selects_inbox(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b""])
        tool._list_unread(imap, count=5)
        imap.select.assert_called_once_with("INBOX", readonly=True)


class TestSearch:
    def test_search_returns_matches(self):
        raw = _make_raw_email("Invoice from Google", "billing@google.com")
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b"10 11"])
        imap.fetch.return_value = ("OK", [(None, raw)])

        result = tool._search(imap, query="Google", count=5)
        assert result.success
        assert result.output["total_matches"] == 2
        assert len(result.output["emails"]) == 2

    def test_empty_query_returns_error(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        result = tool._search(imap, query="", count=10)
        assert not result.success
        assert "query" in result.error.lower()

    def test_strips_quotes_from_query(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b""])
        tool._search(imap, query='say "hello"', count=5)
        call_args = imap.search.call_args[0][1]
        assert '"' not in call_args.replace('OR SUBJECT "', "").replace('" FROM "', "").replace('")', "")

    def test_no_matches_returns_empty_list(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.search.return_value = ("OK", [b""])
        result = tool._search(imap, query="noresult", count=10)
        assert result.success
        assert result.output["emails"] == []


class TestRead:
    def test_reads_email_body(self):
        raw = _make_raw_email("Important", "boss@corp.com", body="Read me carefully")
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.fetch.return_value = ("OK", [(None, raw)])

        result = tool._read(imap, uid="42")
        assert result.success
        assert result.output["uid"] == "42"
        assert "Read me carefully" in result.output["body"]

    def test_missing_uid_returns_error(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        result = tool._read(imap, uid="")
        assert not result.success
        assert "uid" in result.error.lower()

    def test_uid_not_found_returns_error(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.fetch.return_value = ("OK", [None])
        result = tool._read(imap, uid="999")
        assert not result.success
        assert "999" in result.error

    def test_subject_decoded(self):
        raw = _make_raw_email(subject="Meeting agenda")
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.fetch.return_value = ("OK", [(None, raw)])
        result = tool._read(imap, uid="1")
        assert "Meeting agenda" in result.output["subject"]

    def test_body_capped_at_3000_chars(self):
        long_body = "x" * 5000
        raw = _make_raw_email(body=long_body)
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.fetch.return_value = ("OK", [(None, raw)])
        result = tool._read(imap, uid="1")
        assert len(result.output["body"]) <= 3000


# ===========================================================================
# Unknown action
# ===========================================================================

class TestUnknownAction:
    def test_unknown_action_returns_error(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")
        imap = MagicMock()
        imap.login.return_value = ("OK", [b"Logged in"])

        with patch("imaplib.IMAP4_SSL") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = tool._imap_call("fly", {}, "a@gmail.com", "p")
        assert not result.success
        assert "fly" in result.error or "Unknown" in result.error


# ===========================================================================
# IMAP error propagation
# ===========================================================================

class TestImapErrors:
    def test_login_failure_captured(self):
        tool = GmailTool(address="a@gmail.com", app_password="wrong")

        with patch("imaplib.IMAP4_SSL") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.login.side_effect = imaplib.IMAP4.error("LOGIN failed")
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = tool._imap_call("list_unread", {}, "a@gmail.com", "wrong")
        assert not result.success
        assert "IMAP error" in result.error

    def test_run_returns_error_on_exception(self):
        tool = GmailTool(address="a@gmail.com", app_password="p")

        with patch.object(tool, "_imap_call", side_effect=RuntimeError("network down")):
            result = asyncio.get_event_loop().run_until_complete(
                tool.run({"action": "list_unread"})
            )
        assert not result.success
        assert "network down" in result.error
