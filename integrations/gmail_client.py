"""
integrations/gmail_client.py
─────────────────────────────
Wraps the Google Gmail API (v1).

Responsibilities
────────────────
• OAuth2 token refresh (local token.json cache)
• List unread messages in the inbox
• Fetch & parse a full message into EmailMessage
• Apply / remove labels (mark processed)
• Create draft replies
• Watch for new messages (polling loop)
"""
from __future__ import annotations

import base64
import email as email_stdlib
import html
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import settings
from observability.logger import get_logger
from utils.models import EmailMessage

log = get_logger(__name__)

# Minimum scopes required by MailMind
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",   # read + labels + drafts
    "https://www.googleapis.com/auth/gmail.compose",  # create drafts
]


class GmailClient:
    """
    Authenticated Gmail API client.

    Example
    -------
        client = GmailClient()
        for msg in client.fetch_unread():
            print(msg.subject)
    """

    def __init__(self) -> None:
        self._service = self._build_service()

    # ──────────────────────────────────────────────────────────────────────
    # Authentication
    # ──────────────────────────────────────────────────────────────────────

    def _build_service(self):
        """Load or refresh OAuth2 credentials and return a Gmail service."""
        creds: Credentials | None = None
        token_path: Path = settings.google_token_path
        creds_path: Path = settings.google_credentials_path

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log.info("gmail.auth.refresh_token")
                creds.refresh(Request())
            else:
                log.info("gmail.auth.oauth_flow_start")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(creds_path), _SCOPES
                )
                creds = flow.run_local_server(port=0)

            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())
            log.info("gmail.auth.token_saved", path=str(token_path))

        return build("gmail", "v1", credentials=creds)

    # ──────────────────────────────────────────────────────────────────────
    # Listing
    # ──────────────────────────────────────────────────────────────────────

    def list_unread_ids(self, max_results: int = 50) -> list[str]:
        """
        Return Gmail message IDs for unread inbox messages that have NOT
        already been labelled as processed.
        """
        query = (
            "is:unread in:inbox "
            f"-label:{settings.gmail_label_processed.replace('/', '-')}"
        )
        try:
            response = (
                self._service.users()
                .messages()
                .list(
                    userId=settings.gmail_user_id,
                    q=query,
                    maxResults=max_results,
                )
                .execute()
            )
            messages = response.get("messages", [])
            ids = [m["id"] for m in messages]
            log.info("gmail.list_unread", count=len(ids))
            return ids
        except HttpError as exc:
            log.error("gmail.list_unread.error", error=str(exc))
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Fetching & parsing
    # ──────────────────────────────────────────────────────────────────────

    def fetch_message(self, message_id: str) -> EmailMessage | None:
        """
        Download a full Gmail message and parse it into an EmailMessage.
        Returns None if the message cannot be fetched.
        """
        try:
            raw = (
                self._service.users()
                .messages()
                .get(
                    userId=settings.gmail_user_id,
                    id=message_id,
                    format="full",
                )
                .execute()
            )
        except HttpError as exc:
            log.error("gmail.fetch.error", message_id=message_id, error=str(exc))
            return None

        return self._parse_message(raw)

    def _parse_message(self, raw: dict) -> EmailMessage:
        """Convert the Gmail API payload dict into a clean EmailMessage."""
        headers = {
            h["name"].lower(): h["value"]
            for h in raw.get("payload", {}).get("headers", [])
        }

        # Parse received timestamp from internal-date (ms since epoch)
        internal_date_ms = int(raw.get("internalDate", 0))
        received_at = datetime.fromtimestamp(
            internal_date_ms / 1000, tz=timezone.utc
        )

        # Extract body parts
        body_text, body_html = self._extract_body(raw.get("payload", {}))

        return EmailMessage(
            message_id=raw["id"],
            thread_id=raw["threadId"],
            sender=headers.get("from", ""),
            recipients=self._parse_address_list(headers.get("to", "")),
            subject=headers.get("subject", "(no subject)"),
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
            labels=raw.get("labelIds", []),
            snippet=raw.get("snippet", ""),
        )

    @staticmethod
    def _extract_body(payload: dict) -> tuple[str, str]:
        """
        Recursively walk MIME parts to extract plain-text and HTML bodies.
        Returns (plain_text, html).
        """
        body_text = ""
        body_html = ""

        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if body_data:
            decoded = base64.urlsafe_b64decode(
                body_data + "=="  # pad safely
            ).decode("utf-8", errors="replace")
            if mime_type == "text/plain":
                body_text = decoded
            elif mime_type == "text/html":
                body_html = decoded

        for part in payload.get("parts", []):
            pt, ph = GmailClient._extract_body(part)
            body_text = body_text or pt
            body_html = body_html or ph

        return body_text, body_html

    @staticmethod
    def _parse_address_list(header_value: str) -> list[str]:
        """Split a To/CC header into individual addresses."""
        if not header_value:
            return []
        return [addr.strip() for addr in header_value.split(",") if addr.strip()]

    # ──────────────────────────────────────────────────────────────────────
    # Label management
    # ──────────────────────────────────────────────────────────────────────

    def _get_or_create_label(self, label_name: str) -> str:
        """Return the label ID for label_name, creating it if needed."""
        labels_response = (
            self._service.users().labels().list(userId=settings.gmail_user_id).execute()
        )
        for label in labels_response.get("labels", []):
            if label["name"] == label_name:
                return label["id"]

        # Create the label
        new_label = (
            self._service.users()
            .labels()
            .create(
                userId=settings.gmail_user_id,
                body={"name": label_name, "labelListVisibility": "labelShow"},
            )
            .execute()
        )
        log.info("gmail.label.created", name=label_name, id=new_label["id"])
        return new_label["id"]

    def mark_as_processed(self, message_id: str) -> None:
        """
        Apply the MailMind/Processed label and remove UNREAD.
        """
        label_id = self._get_or_create_label(settings.gmail_label_processed)
        try:
            self._service.users().messages().modify(
                userId=settings.gmail_user_id,
                id=message_id,
                body={
                    "addLabelIds": [label_id],
                    "removeLabelIds": ["UNREAD"],
                },
            ).execute()
            log.info("gmail.mark_processed", message_id=message_id)
        except HttpError as exc:
            log.error("gmail.mark_processed.error", message_id=message_id, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────
    # Draft creation
    # ──────────────────────────────────────────────────────────────────────

    def create_draft_reply(
        self,
        original: EmailMessage,
        body: str,
    ) -> str | None:
        """
        Create a Gmail draft that replies to `original`.
        Returns the draft ID or None on failure.
        """
        import email.mime.text

        reply = email.mime.text.MIMEText(body)
        reply["to"] = original.sender
        reply["subject"] = (
            original.subject
            if original.subject.lower().startswith("re:")
            else f"Re: {original.subject}"
        )
        reply["In-Reply-To"] = original.message_id
        reply["References"] = original.message_id

        raw_bytes = base64.urlsafe_b64encode(reply.as_bytes()).decode("utf-8")

        try:
            draft = (
                self._service.users()
                .drafts()
                .create(
                    userId=settings.gmail_user_id,
                    body={
                        "message": {
                            "raw": raw_bytes,
                            "threadId": original.thread_id,
                        }
                    },
                )
                .execute()
            )
            log.info("gmail.draft.created", draft_id=draft["id"])
            return draft["id"]
        except HttpError as exc:
            log.error("gmail.draft.error", error=str(exc))
            return None

    # ──────────────────────────────────────────────────────────────────────
    # Polling iterator
    # ──────────────────────────────────────────────────────────────────────

    def iter_new_messages(self) -> Generator[EmailMessage, None, None]:
        """
        Yield unread, unprocessed EmailMessages.
        Designed to be called in a polling loop by the agent.
        """
        for msg_id in self.list_unread_ids():
            msg = self.fetch_message(msg_id)
            if msg:
                yield msg
