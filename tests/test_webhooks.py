"""
tests/test_webhooks.py
───────────────────────
Integration-style tests for the FastAPI webhook server.
Uses TestClient — no real network calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from webhooks.server import app

SECRET = "test-secret-xyz"
HEADERS = {"X-Webhook-Secret": SECRET}


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Patch settings.webhook_secret so tests don't need .env."""
    monkeypatch.setattr("webhooks.server.settings.webhook_secret", SECRET)


client = TestClient(app)


class TestHealth:
    def test_health_returns_ok(self):
        with patch("webhooks.server._get_agent") as mock_agent_fn:
            mock_agent = MagicMock()
            mock_agent.health.return_value = {"running": True, "poll_count": 5}
            mock_agent_fn.return_value = mock_agent

            resp = client.get("/webhooks/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"


class TestProcessEmail:
    def test_requires_secret(self):
        resp = client.post(
            "/webhooks/process-email",
            json={"message_id": "msg-001"},
        )
        assert resp.status_code == 422  # Missing header

    def test_rejects_wrong_secret(self):
        resp = client.post(
            "/webhooks/process-email",
            json={"message_id": "msg-001"},
            headers={"X-Webhook-Secret": "wrong"},
        )
        assert resp.status_code == 401

    def test_success(self):
        with patch("webhooks.server._get_agent") as mock_agent_fn:
            mock_agent = MagicMock()
            mock_agent.process_by_id.return_value = {
                "message_id": "msg-001",
                "intent": "task_request",
                "confidence": 0.97,
                "action": "create_task",
                "success": True,
                "details": {"task_url": "https://app.clickup.com/t/1"},
            }
            mock_agent_fn.return_value = mock_agent

            resp = client.post(
                "/webhooks/process-email",
                json={"message_id": "msg-001"},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["data"]["intent"] == "task_request"


class TestClassify:
    def test_classify_returns_intent(self):
        from utils.models import ClassificationResult, EmailIntent

        mock_result = ClassificationResult(
            intent=EmailIntent.TASK_REQUEST,
            confidence=0.94,
            reasoning="sender requests action",
            summary="Please update the docs",
        )

        with patch("webhooks.server.EmailClassifier") as cls:
            instance = MagicMock()
            instance.classify.return_value = mock_result
            cls.return_value = instance

            resp = client.post(
                "/webhooks/classify",
                json={
                    "subject": "Please update the docs",
                    "body": "Hi, can you update the documentation?",
                    "sender": "alice@example.com",
                },
                headers=HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"]["intent"] == "task_request"
            assert data["data"]["confidence"] == pytest.approx(0.94)
