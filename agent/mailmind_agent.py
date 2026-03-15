"""
agent/mailmind_agent.py
────────────────────────
The autonomous MailMind agent.

The agent runs an infinite polling loop:
  1. Fetch unread, unprocessed emails from Gmail
  2. Classify each email with GPT-4o
  3. Route to the correct downstream action
  4. Mark email processed + write audit log

Everything is orchestrated here.  The agent can be:
  • Run directly:  python -m agent.mailmind_agent
  • Imported and run programmatically
  • Stopped gracefully via SIGTERM/SIGINT
"""
from __future__ import annotations

import asyncio
import signal
import time
from datetime import datetime

from classifiers.email_classifier import EmailClassifier
from agent.router import EmailRouter
from integrations.clickup_client import ClickUpClient
from integrations.gmail_client import GmailClient
from integrations.sheets_client import SheetsClient
from config.settings import settings
from observability.logger import get_logger, tracer
from utils.models import EmailMessage

log = get_logger(__name__)


class MailMindAgent:
    """
    Autonomous email routing agent.

    Lifecycle
    ---------
        agent = MailMindAgent()
        agent.start()       # blocks; polls until stopped
        agent.stop()        # call from signal handler or test

    Example (non-blocking)
    ----------------------
        agent = MailMindAgent()
        thread = threading.Thread(target=agent.start, daemon=True)
        thread.start()
    """

    def __init__(self) -> None:
        log.info("agent.init.start")

        self._gmail      = GmailClient()
        self._clickup    = ClickUpClient()
        self._sheets     = SheetsClient()
        self._classifier = EmailClassifier()
        self._router     = EmailRouter(self._gmail, self._clickup, self._sheets)

        self._running    = False
        self._poll_count = 0
        self._total_processed = 0

        log.info("agent.init.complete")

    # ──────────────────────────────────────────────────────────────────────
    # Start / Stop
    # ──────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Enter the polling loop.  Blocks until stop() is called or the
        process receives SIGINT/SIGTERM.
        """
        self._running = True
        self._register_signals()

        log.info(
            "agent.start",
            poll_interval=settings.gmail_poll_interval_seconds,
        )

        while self._running:
            self._poll_cycle()
            if self._running:
                time.sleep(settings.gmail_poll_interval_seconds)

        log.info(
            "agent.stopped",
            total_polls=self._poll_count,
            total_processed=self._total_processed,
        )
        tracer.flush()

    def stop(self) -> None:
        """Signal the agent to stop after the current poll cycle."""
        log.info("agent.stopping")
        self._running = False

    # ──────────────────────────────────────────────────────────────────────
    # One poll cycle
    # ──────────────────────────────────────────────────────────────────────

    def _poll_cycle(self) -> None:
        """
        Fetch all unread emails and process them.
        Errors on individual emails are caught here so the agent keeps running.
        """
        self._poll_count += 1
        cycle_start = datetime.utcnow()

        with tracer.trace("agent.poll_cycle", input={"poll": self._poll_count}):
            emails = list(self._gmail.iter_new_messages())

            if not emails:
                log.debug("agent.poll.no_new_emails", poll=self._poll_count)
                return

            log.info("agent.poll.emails_found", count=len(emails), poll=self._poll_count)

            for email in emails:
                self._process_email(email)

            duration_ms = int((datetime.utcnow() - cycle_start).total_seconds() * 1000)
            log.info(
                "agent.poll.cycle_complete",
                poll=self._poll_count,
                processed=len(emails),
                duration_ms=duration_ms,
            )

    def _process_email(self, email: EmailMessage) -> None:
        """
        Full classify → route pipeline for one email.
        Errors are logged but do NOT propagate — the agent continues.
        """
        try:
            log.info(
                "agent.email.processing",
                message_id=email.message_id,
                subject=email.subject,
                sender=email.sender,
            )

            classification = self._classifier.classify(email)
            routing_result = self._router.route(email, classification)

            self._total_processed += 1

            log.info(
                "agent.email.processed",
                message_id=email.message_id,
                intent=classification.intent.value,
                confidence=round(classification.confidence, 3),
                action=routing_result.action.value,
                success=routing_result.success,
            )

        except Exception as exc:
            log.error(
                "agent.email.error",
                message_id=email.message_id,
                error=str(exc),
                exc_info=True,
            )

    # ──────────────────────────────────────────────────────────────────────
    # Process one email by ID (used by webhooks)
    # ──────────────────────────────────────────────────────────────────────

    def process_by_id(self, message_id: str) -> dict:
        """
        Fetch, classify, and route a specific Gmail message ID.
        Returns a summary dict.  Used by the webhook endpoints.
        """
        email = self._gmail.fetch_message(message_id)
        if not email:
            return {"error": f"Message {message_id} not found", "success": False}

        classification = self._classifier.classify(email)
        routing_result = self._router.route(email, classification)

        return {
            "message_id":  email.message_id,
            "subject":     email.subject,
            "sender":      email.sender,
            "intent":      classification.intent.value,
            "confidence":  classification.confidence,
            "action":      routing_result.action.value,
            "success":     routing_result.success,
            "details":     routing_result.details,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Signal handling
    # ──────────────────────────────────────────────────────────────────────

    def _register_signals(self) -> None:
        """Register SIGTERM and SIGINT for graceful shutdown."""
        def _handler(sig, _frame):
            log.info("agent.signal_received", signal=sig)
            self.stop()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    # ──────────────────────────────────────────────────────────────────────
    # Health
    # ──────────────────────────────────────────────────────────────────────

    def health(self) -> dict:
        """Return agent health stats."""
        return {
            "running":         self._running,
            "poll_count":      self._poll_count,
            "total_processed": self._total_processed,
            "poll_interval_s": settings.gmail_poll_interval_seconds,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    agent = MailMindAgent()
    agent.start()
