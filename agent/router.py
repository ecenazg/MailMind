"""
agent/router.py
───────────────
Maps a ClassificationResult to a concrete downstream action and executes it.

Routing table
─────────────
  TASK_REQUEST  → create ClickUp task
  INQUIRY       → create Gmail draft reply
  NEWSLETTER    → log to Sheets only  (no task, no reply)
  URGENT        → create ClickUp task (priority=1) + log warning

After the action, every email is:
  1. Marked processed in Gmail
  2. Written to the Sheets audit log
"""
from __future__ import annotations

from datetime import datetime

from config.settings import settings
from integrations.clickup_client import ClickUpClient
from integrations.gmail_client import GmailClient
from integrations.sheets_client import SheetsClient
from observability.logger import get_logger, tracer
from utils.models import (
    AuditRecord,
    ClassificationResult,
    EmailIntent,
    EmailMessage,
    RoutingAction,
    RoutingResult,
)

log = get_logger(__name__)


class EmailRouter:
    """
    Executes routing actions for a classified email.

    Example
    -------
        router = EmailRouter(gmail, clickup, sheets)
        result = router.route(email, classification)
    """

    def __init__(
        self,
        gmail: GmailClient,
        clickup: ClickUpClient,
        sheets: SheetsClient,
    ) -> None:
        self._gmail   = gmail
        self._clickup = clickup
        self._sheets  = sheets

    # ──────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────

    def route(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> RoutingResult:
        """
        Route an email based on its classification.
        Always marks the email as processed and appends an audit row.
        """
        with tracer.trace(
            "email.route",
            input={
                "message_id": email.message_id,
                "intent": classification.intent.value,
            },
        ) as span:
            result = self._dispatch(email, classification)
            span.update(
                output=result.model_dump(),
            )

        # Always mark as processed in Gmail
        self._gmail.mark_as_processed(email.message_id)

        # Always write to audit log
        self._write_audit(email, classification, result)

        log.info(
            "email.routed",
            message_id=email.message_id,
            action=result.action.value,
            success=result.success,
        )
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Dispatch table
    # ──────────────────────────────────────────────────────────────────────

    def _dispatch(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> RoutingResult:
        """Route to the correct handler based on intent."""
        intent = classification.intent

        if intent == EmailIntent.TASK_REQUEST:
            return self._handle_task_request(email, classification)

        elif intent == EmailIntent.INQUIRY:
            return self._handle_inquiry(email, classification)

        elif intent == EmailIntent.NEWSLETTER:
            return self._handle_newsletter(email, classification)

        elif intent == EmailIntent.URGENT:
            return self._handle_urgent(email, classification)

        # Unreachable, but safe fallback
        return RoutingResult(
            action=RoutingAction.NO_OP,
            success=True,
            details={"reason": "Unknown intent"},
        )

    # ──────────────────────────────────────────────────────────────────────
    # Intent handlers
    # ──────────────────────────────────────────────────────────────────────

    def _handle_task_request(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> RoutingResult:
        """Create a ClickUp task for task-request emails."""
        try:
            task_data = self._clickup.create_task_from_email(email, classification)
            return RoutingResult(
                action=RoutingAction.CREATE_TASK,
                success=True,
                details=task_data,
            )
        except Exception as exc:
            log.error(
                "router.task_request.failed",
                message_id=email.message_id,
                error=str(exc),
            )
            return RoutingResult(
                action=RoutingAction.CREATE_TASK,
                success=False,
                error=str(exc),
            )

    def _handle_inquiry(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> RoutingResult:
        """Create a Gmail draft reply for inquiry emails."""
        draft_body = classification.draft_reply or self._fallback_reply(email)
        try:
            draft_id = self._gmail.create_draft_reply(email, draft_body)
            return RoutingResult(
                action=RoutingAction.DRAFT_REPLY,
                success=draft_id is not None,
                details={"draft_id": draft_id, "draft_preview": draft_body[:200]},
            )
        except Exception as exc:
            log.error(
                "router.inquiry.failed",
                message_id=email.message_id,
                error=str(exc),
            )
            return RoutingResult(
                action=RoutingAction.DRAFT_REPLY,
                success=False,
                error=str(exc),
            )

    def _handle_newsletter(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> RoutingResult:
        """For newsletters: log only, no task or reply."""
        log.info(
            "router.newsletter.logged",
            message_id=email.message_id,
            subject=email.subject,
        )
        return RoutingResult(
            action=RoutingAction.LOG_NEWSLETTER,
            success=True,
            details={"summary": classification.summary},
        )

    def _handle_urgent(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> RoutingResult:
        """Create a high-priority ClickUp task and emit a warning log."""
        log.warning(
            "router.urgent.detected",
            message_id=email.message_id,
            subject=email.subject,
            sender=email.sender,
        )
        try:
            task_data = self._clickup.create_task_from_email(email, classification)
            return RoutingResult(
                action=RoutingAction.ESCALATE,
                success=True,
                details={**task_data, "escalated": True},
            )
        except Exception as exc:
            log.error(
                "router.urgent.failed",
                message_id=email.message_id,
                error=str(exc),
            )
            return RoutingResult(
                action=RoutingAction.ESCALATE,
                success=False,
                error=str(exc),
            )

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_reply(email: EmailMessage) -> str:
        """Generic reply when the LLM didn't generate one."""
        return (
            f"Hi,\n\n"
            f"Thank you for your email regarding '{email.subject}'.\n\n"
            f"I've received your message and will get back to you shortly.\n\n"
            f"Best regards"
        )

    def _write_audit(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
        result: RoutingResult,
    ) -> None:
        """Append one row to the Sheets audit log."""
        action_detail = ""
        if "task_url" in result.details:
            action_detail = result.details["task_url"]
        elif "draft_id" in result.details:
            action_detail = result.details["draft_id"]

        record = AuditRecord(
            message_id=email.message_id,
            thread_id=email.thread_id,
            sender=email.sender,
            subject=email.subject,
            received_at=email.received_at,
            intent=classification.intent,
            confidence=classification.confidence,
            action=result.action,
            action_detail=action_detail,
            success=result.success,
            processed_at=datetime.utcnow(),
            error=result.error,
        )
        self._sheets.append_audit(record)
