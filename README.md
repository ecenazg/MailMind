# MailMind — Autonomous AI Email Routing Agent

> Python · LangChain · Gmail API · GPT-4o · ClickUp · Google Sheets · n8n

MailMind monitors a Gmail inbox, classifies every incoming email with GPT-4o into one of four intents, and triggers downstream actions — all with zero human input.

---

## Architecture

```
Gmail Inbox
    │
    │  (poll every 30 s)
    ▼
GmailClient.iter_new_messages()
    │
    ▼
EmailClassifier   (GPT-4o via LangChain, JSON mode)
    │
    │  ClassificationResult
    │    intent: task_request | inquiry | newsletter | urgent
    │    confidence: 0.0 – 1.0
    │    summary, draft_reply
    ▼
EmailRouter
    ├── task_request → ClickUpClient.create_task_from_email()
    ├── inquiry      → GmailClient.create_draft_reply()
    ├── newsletter   → log only
    └── urgent       → ClickUpClient (priority=1) + warning log
    │
    ├── GmailClient.mark_as_processed()   (always)
    └── SheetsClient.append_audit()       (always)

FastAPI WebhookServer  ← n8n / Make.com
Langfuse Tracing       ← LLMOps observability
structlog JSON Logging ← structured audit trail
```

## Setup Guide

### 1. Clone and install dependencies

```bash
git clone https://github.com/you/mailmind.git
cd mailmind
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in all required values (see below)
```

**Required values in `.env`:**

| Variable | Where to get it |
|---|---|
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console → OAuth2 credentials |
| `CLICKUP_API_TOKEN` | ClickUp → Settings → Apps → API Token |
| `CLICKUP_LIST_ID` | Open a ClickUp List → URL contains the ID |
| `GOOGLE_SHEET_ID` | Spreadsheet URL → `…/d/<ID>/edit` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | https://cloud.langfuse.com |
| `WEBHOOK_SECRET` | Any random string you choose |

### 3. Set up Google APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project (or reuse one)
3. Enable: **Gmail API** and **Google Sheets API**
4. Create credentials: **OAuth 2.0 Client ID** → Desktop app
5. Download as `credentials.json` → place in `config/credentials.json`

### 4. Run the OAuth2 consent flow

```bash
python scripts/setup_oauth.py
```

A browser window opens. Sign in and grant permissions.
`config/token.json` is saved automatically.

### 5. Run MailMind

```bash
# Run both the agent (polling) and webhook server
python main.py

# Agent only (no webhooks)
python main.py --agent-only

# Webhook server only
python main.py --server-only
```

---

## Webhook API

All POST endpoints require the header: `X-Webhook-Secret: <your-secret>`

### Process an email by Gmail ID
```http
POST /webhooks/process-email
Content-Type: application/json
X-Webhook-Secret: your-secret

{ "message_id": "18c7f2a3b4d9e1f0" }
```

### Classify without routing
```http
POST /webhooks/classify

{
  "subject": "Can you send me the Q3 report?",
  "body": "Hi, I need the Q3 financial report by EOD.",
  "sender": "boss@company.com"
}
```

### Create a ClickUp task directly
```http
POST /webhooks/create-task

{
  "subject": "Fix the login bug",
  "body": "The login page is broken on mobile.",
  "sender": "user@example.com",
  "intent": "task_request"
}
```

### Draft a reply
```http
POST /webhooks/draft-reply

{
  "message_id": "18c7f2a3b4d9e1f0",
  "draft_body": "Hi, thanks for reaching out! I'll look into this."
}
```

### Health check
```http
GET /webhooks/health
```

---

## n8n Integration

1. In n8n, add an **HTTP Request** node
2. Set method to `POST`
3. URL: `http://your-server:8000/webhooks/process-email`
4. Add header: `X-Webhook-Secret` = your secret
5. Body: `{ "message_id": "{{ $json.id }}" }`

Trigger it from a **Gmail Trigger** node that fires on new emails.

---

## Running Tests

```bash
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## Evaluating Classifier Accuracy

Prepare a CSV with columns: `message_id, sender, subject, body_text, true_intent`

```bash
python scripts/evaluate_classifier.py --csv data/labelled_emails.csv
```

Output:
```
==================================================
  Accuracy: 94.5%  (189/200)
==================================================
  Intent           Precision   Recall       F1   Support
  ----------------------------------------------------
  task_request         0.961    0.944    0.952        90
  inquiry              0.933    0.933    0.933        30
  newsletter           0.960    0.960    0.960        50
  urgent               0.909    1.000    0.952        30
==================================================
```

---

## Docker

```bash
# Build and run
docker compose up -d

# View logs
docker compose logs -f mailmind

# Stop
docker compose down
```

---

## Intent Categories

| Intent | Description | Action |
|---|---|---|
| `task_request` | Sender asks for something to be done | Creates ClickUp task |
| `inquiry` | Sender asks a question | Creates Gmail draft reply |
| `newsletter` | Marketing, digest, subscription | Logged only |
| `urgent` | Incident, escalation, hard deadline | Creates ClickUp task (priority 1) + warning log |
