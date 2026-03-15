"""
tests/test_classifier.py
─────────────────────────
Unit tests for the email classifier.

Mocks the OpenAI API — no real calls are made.
Run with:  pytest tests/test_classifier.py -v
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from classifiers.email_classifier import EmailClassifier, _build_user_prompt
from utils.models import ClassificationResult, EmailIntent, EmailMessage


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

def make_email(
    subject: str = "Test subject",
    body: str = "Test body",
    sender: str = "alice@example.com",
) -> EmailMessage:
    return EmailMessage(
        message_id="test-msg-001",
        thread_id="test-thread-001",
        sender=sender,
        subject=subject,
        body_text=body,
        received_at=datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


def make_llm_response(
    intent: str = "task_request",
    confidence: float = 0.95,
    reasoning: str = "The sender is requesting an action.",
    summary: str = "Sender wants a report by Friday.",
    draft_reply: str | None = None,
) -> MagicMock:
    """Build a mock LangChain AIMessage."""
    payload = {
        "intent":     intent,
        "confidence": confidence,
        "reasoning":  reasoning,
        "summary":    summary,
    }
    if draft_reply is not None:
        payload["draft_reply"] = draft_reply

    mock_response = MagicMock()
    mock_response.content = json.dumps(payload)
    mock_response.usage_metadata = {"input_tokens": 120, "output_tokens": 80}
    return mock_response


# ──────────────────────────────────────────────────────────────────────────────
# Tests: _build_user_prompt
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildUserPrompt:
    def test_includes_sender(self):
        email = make_email(sender="bob@corp.com")
        prompt = _build_user_prompt(email)
        assert "bob@corp.com" in prompt

    def test_includes_subject(self):
        email = make_email(subject="Quarterly report needed")
        prompt = _build_user_prompt(email)
        assert "Quarterly report needed" in prompt

    def test_body_truncated_at_3000_chars(self):
        email = make_email(body="x" * 5000)
        prompt = _build_user_prompt(email)
        assert "x" * 3000 in prompt
        assert "x" * 3001 not in prompt


# ──────────────────────────────────────────────────────────────────────────────
# Tests: EmailClassifier
# ──────────────────────────────────────────────────────────────────────────────

class TestEmailClassifier:

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_classify_task_request(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        mock_llm.invoke.return_value = make_llm_response(intent="task_request", confidence=0.97)

        classifier = EmailClassifier()
        email = make_email(subject="Please update the roadmap doc")
        result = classifier.classify(email)

        assert result.intent == EmailIntent.TASK_REQUEST
        assert result.confidence == pytest.approx(0.97)
        assert result.summary == "Sender wants a report by Friday."

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_classify_inquiry(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        mock_llm.invoke.return_value = make_llm_response(
            intent="inquiry",
            confidence=0.88,
            draft_reply="Hi, thanks for reaching out! The answer is...",
        )

        classifier = EmailClassifier()
        email = make_email(subject="When is the next release?")
        result = classifier.classify(email)

        assert result.intent == EmailIntent.INQUIRY
        assert result.draft_reply is not None
        assert "thanks for reaching out" in result.draft_reply

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_classify_newsletter(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        mock_llm.invoke.return_value = make_llm_response(
            intent="newsletter", confidence=0.99
        )

        classifier = EmailClassifier()
        email = make_email(subject="Your weekly digest")
        result = classifier.classify(email)

        assert result.intent == EmailIntent.NEWSLETTER

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_classify_urgent(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        mock_llm.invoke.return_value = make_llm_response(
            intent="urgent", confidence=0.98
        )

        classifier = EmailClassifier()
        email = make_email(subject="PRODUCTION DOWN - all hands")
        result = classifier.classify(email)

        assert result.intent == EmailIntent.URGENT

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_malformed_json_falls_back_to_inquiry(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        bad_response = MagicMock()
        bad_response.content = "This is not JSON at all"
        bad_response.usage_metadata = {"input_tokens": 50, "output_tokens": 10}
        mock_llm.invoke.return_value = bad_response

        classifier = EmailClassifier()
        result = classifier.classify(make_email())

        assert result.intent == EmailIntent.INQUIRY
        assert result.confidence == pytest.approx(0.5)

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_unknown_intent_falls_back_to_inquiry(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        mock_llm.invoke.return_value = make_llm_response(intent="spam")

        classifier = EmailClassifier()
        result = classifier.classify(make_email())

        assert result.intent == EmailIntent.INQUIRY

    @patch("classifiers.email_classifier.ChatOpenAI")
    def test_confidence_clamped_to_float(self, mock_openai_cls):
        mock_llm = MagicMock()
        mock_openai_cls.return_value = mock_llm
        mock_llm.invoke.return_value = make_llm_response(confidence=0.945)

        classifier = EmailClassifier()
        result = classifier.classify(make_email())

        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0
