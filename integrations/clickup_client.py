"""
integrations/clickup_client.py
───────────────────────────────
Wraps the ClickUp REST API v2.

Responsibilities
────────────────
• Create tasks from EmailMessage + ClassificationResult
• Attach email metadata as task description
• Return ClickUp task URL for audit logging
"""
from __future__ import annotations

from typing import Any

import httpx

from config.settings import settings
from observability.logger import get_logger
from utils.models import ClassificationResult, EmailMessage

log = get_logger(__name__)

_BASE_URL = "https://api.clickup.com/api/v2"
_PRIORITY_MAP = {
    "urgent":       1,   # ClickUp urgent
    "task_request": 2,   # high
    "inquiry":      3,   # normal
    "newsletter":   4,   # low
}


class ClickUpClient:
    """
    ClickUp API v2 wrapper.

    Example
    -------
        client = ClickUpClient()
        task_url = client.create_task_from_email(email, classification)
    """

    def __init__(self) -> None:
        self._headers = {
            "Authorization": settings.clickup_api_token,
            "Content-Type": "application/json",
        }

    # ──────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────

    def create_task_from_email(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> dict[str, Any]:
        """
        Create a ClickUp task populated from email data.

        Returns a dict with `task_id`, `task_url`, `task_name`.
        Raises on HTTP errors.
        """
        task_name = self._build_task_name(email)
        description = self._build_description(email, classification)
        priority = _PRIORITY_MAP.get(classification.intent.value, 3)

        payload = {
            "name": task_name,
            "description": description,
            "priority": priority,
            "tags": [classification.intent.value],
            "custom_fields": [
                {"name": "Email ID",     "value": email.message_id},
                {"name": "Sender",       "value": email.sender},
                {"name": "Confidence",   "value": str(round(classification.confidence, 2))},
            ],
        }

        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{_BASE_URL}/list/{settings.clickup_list_id}/task",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        task_id  = data["id"]
        task_url = data.get("url", f"https://app.clickup.com/t/{task_id}")

        log.info(
            "clickup.task.created",
            task_id=task_id,
            task_url=task_url,
            email_id=email.message_id,
        )
        return {"task_id": task_id, "task_url": task_url, "task_name": task_name}

    def get_task(self, task_id: str) -> dict[str, Any]:
        """Fetch a task by ID."""
        with httpx.Client(timeout=15) as client:
            response = client.get(
                f"{_BASE_URL}/task/{task_id}",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_task_name(email: EmailMessage) -> str:
        """
        Produce a concise task name:
        '[From] Subject' truncated to 80 chars.
        """
        sender_name = email.sender.split("<")[0].strip().strip('"') or email.sender
        raw = f"[{sender_name}] {email.subject}"
        return raw[:80] if len(raw) > 80 else raw

    @staticmethod
    def _build_description(
        email: EmailMessage,
        classification: ClassificationResult,
    ) -> str:
        """
        Build a Markdown task description with email metadata and AI summary.
        """
        return (
            f"## Email Summary\n\n"
            f"{classification.summary}\n\n"
            f"---\n\n"
            f"**From:** {email.sender}\n"
            f"**Subject:** {email.subject}\n"
            f"**Received:** {email.received_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"**Intent:** `{classification.intent.value}` "
            f"(confidence: {round(classification.confidence * 100)}%)\n\n"
            f"---\n\n"
            f"## Original Message\n\n"
            f"{email.body_text[:2000]}"
            f"{'...[truncated]' if len(email.body_text) > 2000 else ''}\n\n"
            f"---\n\n"
            f"*Created automatically by MailMind — "
            f"Gmail ID: `{email.message_id}`*"
        )
