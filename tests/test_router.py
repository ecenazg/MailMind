"""
tests/test_router.py
─────────────────────
Unit tests for EmailRouter.

All integrations are mocked — no network calls.
Run with:  pytest tests/test_router.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.router import EmailRouter
from utils.models import (
    ClassificationResult,
    EmailIntent,
    EmailMessage,
    RoutingAction,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_email(**kwargs) -> EmailMessage:
    defaults = dict(
        message_id="msg-001",
        thread_id="thread-001",
        sender="alice@example.com",
        subject="Test email",
        body_text="Hello world",
        received_at=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def make_classification(intent: EmailIntent, **kwargs) -> ClassificationResult:
    defaults = dict(
        intent=intent,
        confidence=0.95,
        reasoning="Test reasoning",
        summary="Test summary",
    )
    defaults.update(kwargs)
    return ClassificationResult(**defaults)


def make_router():
    """Return an EmailRouter with all integrations mocked."""
    gmail   = MagicMock()
    clickup = MagicMock()
    sheets  = MagicMock()

    # Default happy-path returns
    clickup.create_task_from_email.return_value = {
        "task_id": "task-123",
        "task_url": "https://app.clickup.com/t/task-123",
        "task_name": "[Alice] Test email",
    }
    gmail.create_draft_reply.return_value = "draft-456"
    sheets.append_audit.return_value = True

    return EmailRouter(gmail, clickup, sheets), gmail, clickup, sheets


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestEmailRouter:

    def test_task_request_creates_clickup_task(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email()
        classification = make_classification(EmailIntent.TASK_REQUEST)

        result = router.route(email, classification)

        assert result.action == RoutingAction.CREATE_TASK
        assert result.success is True
        assert "task_url" in result.details
        clickup.create_task_from_email.assert_called_once_with(email, classification)

    def test_inquiry_creates_draft_reply(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email()
        classification = make_classification(
            EmailIntent.INQUIRY,
            draft_reply="Thank you for your question!",
        )

        result = router.route(email, classification)

        assert result.action == RoutingAction.DRAFT_REPLY
        assert result.success is True
        gmail.create_draft_reply.assert_called_once()

    def test_newsletter_logs_only(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email(subject="Weekly digest")
        classification = make_classification(EmailIntent.NEWSLETTER)

        result = router.route(email, classification)

        assert result.action == RoutingAction.LOG_NEWSLETTER
        assert result.success is True
        clickup.create_task_from_email.assert_not_called()
        gmail.create_draft_reply.assert_not_called()

    def test_urgent_creates_escalated_task(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email(subject="PRODUCTION DOWN")
        classification = make_classification(EmailIntent.URGENT)

        result = router.route(email, classification)

        assert result.action == RoutingAction.ESCALATE
        assert result.success is True
        assert result.details.get("escalated") is True
        clickup.create_task_from_email.assert_called_once()

    def test_email_always_marked_processed(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email()
        classification = make_classification(EmailIntent.NEWSLETTER)

        router.route(email, classification)

        gmail.mark_as_processed.assert_called_once_with(email.message_id)

    def test_audit_always_written(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email()
        classification = make_classification(EmailIntent.TASK_REQUEST)

        router.route(email, classification)

        sheets.append_audit.assert_called_once()

    def test_clickup_failure_returns_error_result(self):
        router, gmail, clickup, sheets = make_router()
        clickup.create_task_from_email.side_effect = Exception("ClickUp timeout")

        email = make_email()
        classification = make_classification(EmailIntent.TASK_REQUEST)

        result = router.route(email, classification)

        assert result.success is False
        assert "ClickUp timeout" in result.error
        # Still marks processed and writes audit
        gmail.mark_as_processed.assert_called_once()
        sheets.append_audit.assert_called_once()

    def test_inquiry_uses_fallback_reply_when_no_draft(self):
        router, gmail, clickup, sheets = make_router()
        email = make_email(subject="Question about the API")
        classification = make_classification(EmailIntent.INQUIRY, draft_reply=None)

        router.route(email, classification)

        call_args = gmail.create_draft_reply.call_args
        body_arg = call_args[0][1]  # second positional arg
        assert "Question about the API" in body_arg
