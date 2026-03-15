"""
scripts/setup_oauth.py
───────────────────────
Run this ONCE to complete the Google OAuth2 flow and save token.json.

Prerequisites
─────────────
1. Create a project at https://console.cloud.google.com
2. Enable: Gmail API, Google Sheets API
3. Create OAuth2 credentials → Desktop App
4. Download as credentials.json → place in config/credentials.json
5. Run:  python scripts/setup_oauth.py

The script opens a browser window for consent.
After approval, token.json is written to config/token.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow
from config.settings import settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main() -> None:
    creds_path = settings.google_credentials_path
    token_path = settings.google_token_path

    if not creds_path.exists():
        print(f"[ERROR] credentials.json not found at: {creds_path}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials")
        sys.exit(1)

    print(f"Starting OAuth2 flow using: {creds_path}")
    print("A browser window will open. Sign in and grant permissions.")

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())

    print(f"\n[OK] token.json saved to: {token_path}")
    print("You can now run MailMind.")


if __name__ == "__main__":
    main()
