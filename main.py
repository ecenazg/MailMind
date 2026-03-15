"""
main.py
───────
MailMind entry point.

Starts two concurrent processes:
  1. FastAPI webhook server (uvicorn)
  2. MailMindAgent polling loop (background thread)

Usage
-----
    python main.py                  # run both
    python main.py --agent-only     # polling loop only (no webhooks)
    python main.py --server-only    # webhook server only (no polling)
"""
from __future__ import annotations

import argparse
import threading

import uvicorn

from agent.mailmind_agent import MailMindAgent
from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)


def run_agent(agent: MailMindAgent) -> None:
    """Run the agent in its own thread."""
    log.info("main.agent_thread.start")
    agent.start()


def run_server() -> None:
    """Run the FastAPI webhook server."""
    log.info(
        "main.server.start",
        host=settings.webhook_host,
        port=settings.webhook_port,
    )
    uvicorn.run(
        "webhooks.server:app",
        host=settings.webhook_host,
        port=settings.webhook_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MailMind Autonomous Email Router")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--agent-only", action="store_true", help="Run only the polling agent")
    group.add_argument("--server-only", action="store_true", help="Run only the webhook server")
    args = parser.parse_args()

    log.info("mailmind.starting", version="1.0.0")

    if args.agent_only:
        agent = MailMindAgent()
        run_agent(agent)

    elif args.server_only:
        run_server()

    else:
        # Run agent in background thread, server in foreground
        agent = MailMindAgent()
        agent_thread = threading.Thread(
            target=run_agent,
            args=(agent,),
            daemon=True,
            name="mailmind-agent",
        )
        agent_thread.start()
        run_server()   # blocks


if __name__ == "__main__":
    main()
