"""Gmail IMAP tool — list, search, and read emails via App Password."""
from __future__ import annotations

import email as _email_stdlib
import email.header
import imaplib
import logging
import os
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_IMAP_HOST = "imap.gmail.com"
_IMAP_PORT = 993
_BODY_CAP = 3000  # characters returned for a full read


class GmailTool(BaseTool):
    """Read Gmail via IMAP SSL + App Password.

    Credentials are read from the ``GMAIL_ADDRESS`` and ``GMAIL_APP_PASSWORD``
    environment variables by default; they can also be passed per-call.

    Actions
    -------
    list_unread  — list recent unread messages in INBOX (default 10)
    search       — search by subject or sender string
    read         — fetch the full body of a single email by UID
    """

    name = "gmail"
    description = "List, search, or read Gmail emails via IMAP App Password."
    safety_flags = ["network"]

    def __init__(
        self,
        address: Optional[str] = None,
        app_password: Optional[str] = None,
    ) -> None:
        self._address = address or os.environ.get("GMAIL_ADDRESS", "")
        self._app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD", "")

    async def run(self, args: dict, **context) -> ToolResult:
        action = args.get("action", "list_unread")
        address = args.get("address") or self._address
        password = args.get("app_password") or self._app_password

        if not address or not password:
            return ToolResult(
                self.name,
                False,
                error=(
                    "Gmail credentials not configured. "
                    "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in your .env file "
                    "(use a Gmail App Password, not your regular password)."
                ),
            )

        import asyncio

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, self._imap_call, action, args, address, password
            )
            return result
        except Exception as exc:
            logger.exception("[GMAIL] Unexpected error")
            return ToolResult(self.name, False, error=str(exc))

    # ------------------------------------------------------------------
    # Internal — runs in a thread-pool executor (blocking IMAP)
    # ------------------------------------------------------------------

    def _imap_call(self, action: str, args: dict, address: str, password: str) -> ToolResult:
        try:
            with imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT) as imap:
                imap.login(address, password)
                if action == "list_unread":
                    return self._list_unread(imap, count=int(args.get("count", 10)))
                elif action == "search":
                    return self._search(
                        imap,
                        query=str(args.get("query", "")),
                        count=int(args.get("count", 10)),
                    )
                elif action == "read":
                    return self._read(imap, uid=str(args.get("uid", "")))
                else:
                    return ToolResult(
                        self.name,
                        False,
                        error=f"Unknown action {action!r}. Allowed: list_unread, search, read",
                    )
        except imaplib.IMAP4.error as exc:
            return ToolResult(self.name, False, error=f"IMAP error: {exc}")

    def _list_unread(self, imap: imaplib.IMAP4_SSL, count: int) -> ToolResult:
        imap.select("INBOX", readonly=True)
        _, uids_raw = imap.search(None, "UNSEEN")
        all_uids = uids_raw[0].split()
        # Most recent first (IMAP UIDs are ascending)
        recent_uids = all_uids[-count:][::-1]
        emails = [self._fetch_summary(imap, uid) for uid in recent_uids]
        return ToolResult(
            self.name,
            True,
            output={"emails": emails, "total_unread": len(all_uids)},
        )

    def _search(self, imap: imaplib.IMAP4_SSL, query: str, count: int) -> ToolResult:
        if not query:
            return ToolResult(self.name, False, error="query is required for action=search")
        imap.select("INBOX", readonly=True)
        # Search subject OR sender
        safe_q = query.replace('"', "")  # strip quotes to avoid IMAP injection
        criteria = f'(OR SUBJECT "{safe_q}" FROM "{safe_q}")'
        _, uids_raw = imap.search(None, criteria)
        all_uids = uids_raw[0].split()
        recent_uids = all_uids[-count:][::-1]
        emails = [self._fetch_summary(imap, uid) for uid in recent_uids]
        return ToolResult(
            self.name,
            True,
            output={"emails": emails, "total_matches": len(all_uids)},
        )

    def _read(self, imap: imaplib.IMAP4_SSL, uid: str) -> ToolResult:
        if not uid:
            return ToolResult(self.name, False, error="uid is required for action=read")
        imap.select("INBOX", readonly=True)
        _, msg_data = imap.fetch(uid.encode(), "(RFC822)")
        if not msg_data or not msg_data[0]:
            return ToolResult(self.name, False, error=f"Email UID {uid!r} not found")
        raw = msg_data[0][1]
        msg = _email_stdlib.message_from_bytes(raw)
        body = self._extract_body(msg)
        return ToolResult(
            self.name,
            True,
            output={
                "uid": uid,
                "from": self._decode_header(msg.get("From", "")),
                "to": self._decode_header(msg.get("To", "")),
                "subject": self._decode_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": body[:_BODY_CAP],
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_summary(self, imap: imaplib.IMAP4_SSL, uid: bytes) -> dict:
        _, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if not msg_data or not msg_data[0]:
            return {"uid": uid.decode(), "error": "fetch failed"}
        raw = msg_data[0][1]
        msg = _email_stdlib.message_from_bytes(raw)
        return {
            "uid": uid.decode(),
            "from": self._decode_header(msg.get("From", "")),
            "subject": self._decode_header(msg.get("Subject", "")),
            "date": msg.get("Date", ""),
        }

    @staticmethod
    def _decode_header(value: str) -> str:
        """Decode a potentially RFC-2047-encoded header value to plain text."""
        parts = email.header.decode_header(value)
        result = []
        for part, charset in parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(str(part))
        return " ".join(result)

    @staticmethod
    def _extract_body(msg: _email_stdlib.message.Message) -> str:
        """Extract plain-text body from a (possibly multipart) email."""
        if msg.is_multipart():
            for part in msg.walk():
                if (
                    part.get_content_type() == "text/plain"
                    and part.get_content_disposition() != "attachment"
                ):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_unread", "search", "read"],
                        "description": "list_unread: show unread inbox; search: find by subject/sender; read: fetch full email body",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of emails to return (default 10)",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search string — matches subject or sender (for action=search)",
                    },
                    "uid": {
                        "type": "string",
                        "description": "Email UID returned by list_unread/search (for action=read)",
                    },
                },
                "required": ["action"],
            },
        }
