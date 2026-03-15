"""
classifiers/email_classifier.py
────────────────────────────────
GPT-4o email intent classifier built with LangChain.

Design
──────
• Uses a structured output chain (JSON mode) so classification fields
  are always present and type-safe.
• Implements retry logic with tenacity for transient API errors.
• Traces every LLM call to Langfuse for observability.
• Returns a ClassificationResult including:
    - intent (one of 4 categories)
    - confidence  (0.0 – 1.0)
    - reasoning   (chain-of-thought, 1-2 sentences)
    - summary     (one-sentence plain-English summary)
    - draft_reply (auto-generated reply for INQUIRY emails, else None)
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from observability.logger import get_logger, tracer
from utils.models import ClassificationResult, EmailIntent, EmailMessage

log = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Prompts
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """
You are MailMind, an expert email routing agent.

Your job is to classify incoming emails into exactly one of these four intents:
  • task_request  — The sender asks for an action to be performed.
  • inquiry       — The sender asks a question or requests information.
  • newsletter    — Marketing, digest, automated notification, or subscription content.
  • urgent        — Time-sensitive: escalation, incident, hard deadline, or emergency.

Rules
─────
1. Choose the single best-fitting intent.  When in doubt between task_request and
   urgent, choose urgent only if explicit time-pressure or severity is present.
2. Newsletters should be identified even if phrased personally — check for
   unsubscribe links, bulk headers (List-Unsubscribe), or promotional language.
3. Confidence is your internal calibration: 1.0 = certain, 0.5 = unsure.

Output format — respond with ONLY valid JSON, no markdown:
{
  "intent": "<one of: task_request | inquiry | newsletter | urgent>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences explaining your choice>",
  "summary": "<one-sentence plain-English summary of the email>",
  "draft_reply": "<a helpful draft reply — ONLY include this key when intent is inquiry, otherwise null>"
}
""".strip()


def _build_user_prompt(email: EmailMessage) -> str:
    """Format the email for the LLM."""
    body = (email.body_text or "").strip()[:3000]  # cap at 3k chars
    return (
        f"From: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Received: {email.received_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"\n"
        f"Body:\n{body}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Classifier class
# ──────────────────────────────────────────────────────────────────────────────

class EmailClassifier:
    """
    Classifies emails using GPT-4o via LangChain.

    Example
    -------
        classifier = EmailClassifier()
        result = classifier.classify(email_message)
        print(result.intent, result.confidence)
    """

    def __init__(self) -> None:
        self._llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.0,        # deterministic
            response_format={"type": "json_object"},
            openai_api_key=settings.openai_api_key,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────

    def classify(self, email: EmailMessage) -> ClassificationResult:
        """
        Classify one email.  Includes retry logic and Langfuse tracing.
        """
        with tracer.trace(
            "email.classify",
            input={"subject": email.subject, "sender": email.sender},
        ) as span:
            result = self._classify_with_retry(email)
            span.update(
                output=result.model_dump(),
                metadata={"model": settings.openai_model},
            )

        log.info(
            "email.classified",
            message_id=email.message_id,
            intent=result.intent.value,
            confidence=result.confidence,
        )
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Internal implementation with retries
    # ──────────────────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _classify_with_retry(self, email: EmailMessage) -> ClassificationResult:
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_build_user_prompt(email)),
        ]

        response = self._llm.invoke(messages)
        raw_content: str = response.content

        # Log to Langfuse
        tracer.log_llm_call(
            name="email_classify",
            model=settings.openai_model,
            prompt_tokens=response.usage_metadata.get("input_tokens", 0),
            completion_tokens=response.usage_metadata.get("output_tokens", 0),
            input_messages=[m.dict() for m in messages],
            output=raw_content,
        )

        return self._parse_response(raw_content, email.message_id)

    @staticmethod
    def _parse_response(raw: str, message_id: str) -> ClassificationResult:
        """
        Parse the JSON response from the LLM.
        Falls back gracefully if the JSON is malformed.
        """
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "classifier.parse_error",
                message_id=message_id,
                raw=raw[:200],
            )
            # Fallback — treat as inquiry with low confidence
            return ClassificationResult(
                intent=EmailIntent.INQUIRY,
                confidence=0.5,
                reasoning="Parse error — defaulting to inquiry",
                summary="Could not parse classification response",
            )

        # Validate intent value
        raw_intent = data.get("intent", "inquiry")
        try:
            intent = EmailIntent(raw_intent)
        except ValueError:
            intent = EmailIntent.INQUIRY

        return ClassificationResult(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
            summary=data.get("summary", ""),
            draft_reply=data.get("draft_reply") or None,
        )
