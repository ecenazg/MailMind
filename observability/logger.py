"""
observability/logger.py
───────────────────────
Structured JSON logging (structlog) + Langfuse tracing helpers.

Usage
-----
    from observability.logger import get_logger, tracer

    log = get_logger(__name__)
    log.info("email.classified", intent="task_request", confidence=0.97)

    with tracer.trace("classify", input={"subject": "..."}) as span:
        result = classify(email)
        span.update(output=result.model_dump())
"""
from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

import structlog
from langfuse import Langfuse

from config.settings import settings


# ──────────────────────────────────────────────────────────────────────────────
# Structlog setup
# ──────────────────────────────────────────────────────────────────────────────

def _configure_structlog() -> None:
    """Wire structlog to emit JSON lines to a file + stderr."""
    log_dir = settings.log_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # File handler — newline-delimited JSON
    file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    # Stderr handler — human-readable coloured output
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(getattr(logging, settings.log_level))

    logging.basicConfig(
        format="%(message)s",
        handlers=[file_handler, stream_handler],
        level=logging.DEBUG,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_configure_structlog()


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a bound logger tagged with the module name."""
    return structlog.get_logger(name)


# ──────────────────────────────────────────────────────────────────────────────
# Langfuse tracer
# ──────────────────────────────────────────────────────────────────────────────

class _NullSpan:
    """Fallback span that does nothing — used when Langfuse is unavailable."""
    def update(self, **_kwargs: Any) -> None: ...
    def __enter__(self): return self
    def __exit__(self, *_): ...


class LangfuseTracer:
    """
    Thin wrapper around the Langfuse Python SDK.

    Usage
    -----
        with tracer.trace("classify", input={...}) as span:
            ...
            span.update(output={...}, metadata={"model": "gpt-4o"})
    """

    def __init__(self) -> None:
        try:
            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            self._enabled = True
        except Exception:
            self._enabled = False
            self._client = None

    @contextmanager
    def trace(
        self,
        name: str,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Context manager that wraps a logical operation in a Langfuse span.
        Gracefully falls back to a no-op if Langfuse is unreachable.
        """
        if not self._enabled:
            yield _NullSpan()
            return

        trace = self._client.trace(name=name, input=input or {}, metadata=metadata or {})
        span = trace.span(name=name)
        try:
            yield span
            span.end()
        except Exception as exc:
            span.end(status_message=str(exc), level="ERROR")
            raise

    def log_llm_call(
        self,
        name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        input_messages: list[dict],
        output: str,
        metadata: dict | None = None,
    ) -> None:
        """Record a single LLM generation event for cost/latency tracking."""
        if not self._enabled:
            return
        self._client.generation(
            name=name,
            model=model,
            usage={"input": prompt_tokens, "output": completion_tokens},
            input=input_messages,
            output=output,
            metadata=metadata or {},
        )

    def flush(self) -> None:
        """Flush pending events — call before process exit."""
        if self._enabled and self._client:
            self._client.flush()


# Singletons
tracer = LangfuseTracer()
