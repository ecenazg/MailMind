"""
utils/models.py
───────────────
Shared Pydantic models that flow through every layer of MailMind.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────────
# Intent categories
# ──────────────────────────────────────────────────────────────────────────────

class EmailIntent(str, Enum):
    """
    The 4 intent categories the classifier assigns.

    TASK_REQUEST  – sender asks for something to be done
    INQUIRY       – sender asks a question or requests information
    NEWSLETTER    – marketing, digest, or subscription content
    URGENT        – time-sensitive: escalation, incident, or deadline
    """
    TASK_REQUEST = "task_request"
    INQUIRY      = "inquiry"
    NEWSLETTER   = "newsletter"
    URGENT       = "urgent"


# ──────────────────────────────────────────────────────────────────────────────
# Email envelope
# ──────────────────────────────────────────────────────────────────────────────

class EmailMessage(BaseModel):
    """
    Normalised representation of a Gmail message.
    Created by GmailClient and passed through the entire pipeline.
    """
    message_id: str          = Field(..., description="Gmail message ID")
    thread_id: str           = Field(..., description="Gmail thread ID")
    sender: str              = Field(..., description="From header")
    recipients: list[str]    = Field(default_factory=list)
    subject: str             = Field("")
    body_text: str           = Field("", description="Plain-text body")
    body_html: str           = Field("", description="HTML body (may be empty)")
    received_at: datetime    = Field(default_factory=datetime.utcnow)
    labels: list[str]        = Field(default_factory=list)
    snippet: str             = Field("", description="Gmail snippet preview")


# ──────────────────────────────────────────────────────────────────────────────
# Classification result
# ──────────────────────────────────────────────────────────────────────────────

class ClassificationResult(BaseModel):
    """Output from EmailClassifier."""
    intent:     EmailIntent = Field(...)
    confidence: float       = Field(..., ge=0.0, le=1.0)
    reasoning:  str         = Field("", description="LLM chain-of-thought")
    summary:    str         = Field("", description="One-sentence email summary")
    draft_reply: Optional[str] = Field(None, description="Auto-drafted reply (inquiries)")


# ──────────────────────────────────────────────────────────────────────────────
# Routing result
# ──────────────────────────────────────────────────────────────────────────────

class RoutingAction(str, Enum):
    CREATE_TASK    = "create_task"
    DRAFT_REPLY    = "draft_reply"
    LOG_NEWSLETTER = "log_newsletter"
    ESCALATE       = "escalate"
    NO_OP          = "no_op"


class RoutingResult(BaseModel):
    """Produced by the router after running downstream actions."""
    action:         RoutingAction
    success:        bool
    details:        dict[str, Any] = Field(default_factory=dict)
    error:          Optional[str]  = None
    executed_at:    datetime       = Field(default_factory=datetime.utcnow)


# ──────────────────────────────────────────────────────────────────────────────
# Audit log row
# ──────────────────────────────────────────────────────────────────────────────

class AuditRecord(BaseModel):
    """One row written to Google Sheets per processed email."""
    message_id:    str
    thread_id:     str
    sender:        str
    subject:       str
    received_at:   datetime
    intent:        EmailIntent
    confidence:    float
    action:        RoutingAction
    action_detail: str           = ""
    success:       bool
    processed_at:  datetime      = Field(default_factory=datetime.utcnow)
    error:         Optional[str] = None

    def to_sheet_row(self) -> list[str]:
        """Serialise to a flat list for Sheets API append."""
        return [
            self.message_id,
            self.thread_id,
            self.sender,
            self.subject,
            self.received_at.isoformat(),
            self.intent.value,
            str(round(self.confidence, 4)),
            self.action.value,
            self.action_detail,
            str(self.success),
            self.processed_at.isoformat(),
            self.error or "",
        ]

    @staticmethod
    def sheet_headers() -> list[str]:
        return [
            "message_id", "thread_id", "sender", "subject",
            "received_at", "intent", "confidence",
            "action", "action_detail", "success",
            "processed_at", "error",
        ]
