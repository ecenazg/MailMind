"""
integrations/sheets_client.py
──────────────────────────────
Appends structured audit rows to a Google Sheets spreadsheet.

Each processed email produces one AuditRecord → one Sheets row.
Columns match AuditRecord.sheet_headers().
"""
from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import settings
from observability.logger import get_logger
from utils.models import AuditRecord

log = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    """
    Append-only Google Sheets writer.

    Example
    -------
        client = SheetsClient()
        client.append_audit(record)
    """

    def __init__(self) -> None:
        self._service = self._build_service()
        self._ensure_headers()

    # ──────────────────────────────────────────────────────────────────────
    # Authentication (reuses token.json from Gmail if scopes overlap)
    # ──────────────────────────────────────────────────────────────────────

    def _build_service(self):
        creds: Credentials | None = None
        token_path = settings.google_token_path
        creds_path = settings.google_credentials_path

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(creds_path), _SCOPES
                )
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json())

        return build("sheets", "v4", credentials=creds)

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def append_audit(self, record: AuditRecord) -> bool:
        """
        Append one audit row to the configured sheet tab.
        Returns True on success, False on error.
        """
        range_name = f"{settings.google_sheet_audit_tab}!A1"
        values = [record.to_sheet_row()]
        try:
            self._service.spreadsheets().values().append(
                spreadsheetId=settings.google_sheet_id,
                range=range_name,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()
            log.info(
                "sheets.audit.appended",
                message_id=record.message_id,
                intent=record.intent.value,
            )
            return True
        except HttpError as exc:
            log.error("sheets.audit.error", error=str(exc))
            return False

    def read_recent(self, limit: int = 100) -> list[dict]:
        """
        Read the most recent `limit` audit rows.
        Useful for the dashboard / health-check endpoint.
        """
        range_name = f"{settings.google_sheet_audit_tab}!A1:L{limit + 1}"
        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=settings.google_sheet_id,
                    range=range_name,
                )
                .execute()
            )
        except HttpError as exc:
            log.error("sheets.read_recent.error", error=str(exc))
            return []

        rows = result.get("values", [])
        if len(rows) < 2:
            return []

        headers = rows[0]
        return [dict(zip(headers, row)) for row in rows[1:]]

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _ensure_headers(self) -> None:
        """
        Write the header row to row 1 if the sheet is empty.
        Idempotent — only writes if A1 is blank.
        """
        range_name = f"{settings.google_sheet_audit_tab}!A1:L1"
        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=settings.google_sheet_id,
                    range=range_name,
                )
                .execute()
            )
        except HttpError:
            return

        if not result.get("values"):
            self._service.spreadsheets().values().update(
                spreadsheetId=settings.google_sheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [AuditRecord.sheet_headers()]},
            ).execute()
            log.info("sheets.headers.written")
