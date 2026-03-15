"""
tests/test_gmail_client.py
──────────────────────────
Unit tests for GmailClient parsing and label logic.
No real Gmail API calls are made.
"""
from __future__ import annotations

import base64
from datetime import timezone
from unittest.mock import MagicMock, patch

import pytest

from integrations.gmail_client import GmailClient


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


SAMPLE_RAW = {
    "id": "abc123",
    "threadId": "thread456",
    "internalDate": "1717228800000",  # 2024-06-01 00:00:00 UTC
    "snippet": "Hello this is a test email",
    "labelIds": ["INBOX", "UNREAD"],
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From",    "value": "Bob Smith <bob@example.com>"},
            {"name": "To",      "value": "alice@example.com"},
            {"name": "Subject", "value": "Test subject"},
        ],
        "body": {"data": b64("Hello, this is the plain text body.")},
        "parts": [],
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestGmailClientParsing:
    """Tests for the message parsing logic — no network required."""

    @patch.object(GmailClient, "_build_service", return_value=MagicMock())
    def test_parse_basic_message(self, _):
        client = GmailClient()
        msg = client._parse_message(SAMPLE_RAW)

        assert msg.message_id == "abc123"
        assert msg.thread_id  == "thread456"
        assert msg.sender     == "Bob Smith <bob@example.com>"
        assert msg.subject    == "Test subject"
        assert "plain text body" in msg.body_text
        assert msg.recipients == ["alice@example.com"]

    @patch.object(GmailClient, "_build_service", return_value=MagicMock())
    def test_received_at_parsed_correctly(self, _):
        client = GmailClient()
        msg = client._parse_message(SAMPLE_RAW)

        assert msg.received_at.year  == 2024
        assert msg.received_at.month == 6
        assert msg.received_at.day   == 1

    @patch.object(GmailClient, "_build_service", return_value=MagicMock())
    def test_missing_subject_defaults_to_no_subject(self, _):
        raw = {**SAMPLE_RAW, "payload": {**SAMPLE_RAW["payload"], "headers": []}}
        client = GmailClient()
        msg = client._parse_message(raw)
        assert msg.subject == "(no subject)"

    @patch.object(GmailClient, "_build_service", return_value=MagicMock())
    def test_parse_address_list_splits_multiple(self, _):
        result = GmailClient._parse_address_list("a@x.com, b@x.com, c@x.com")
        assert result == ["a@x.com", "b@x.com", "c@x.com"]

    @patch.object(GmailClient, "_build_service", return_value=MagicMock())
    def test_extract_body_plain(self, _):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": b64("Plain body here")},
            "parts": [],
        }
        text, html = GmailClient._extract_body(payload)
        assert "Plain body here" in text
        assert html == ""

    @patch.object(GmailClient, "_build_service", return_value=MagicMock())
    def test_extract_body_multipart(self, _):
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64("Plain part")},
                    "parts": [],
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": b64("<p>HTML part</p>")},
                    "parts": [],
                },
            ],
        }
        text, html = GmailClient._extract_body(payload)
        assert "Plain part"    in text
        assert "<p>HTML part</p>" in html
