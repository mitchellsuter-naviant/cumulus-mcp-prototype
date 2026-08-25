"""Structured logging to stdout, in the shape Cloud Logging parses natively.

Cloud Run captures stdout; when a line is JSON with a `severity` field, Cloud
Logging promotes it to a structured entry instead of an opaque text blob. The
`logging.googleapis.com/trace` field ties an entry to its request trace so the
console groups all logs for one request together.

Every record carries the current request id, which comes from `X-Request-Id`
when the caller supplies one and is generated otherwise.
"""

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Set per request by RequestContextMiddleware; read by the formatter.
_request_id: ContextVar[str] = ContextVar("request_id", default="")
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

# Reserved LogRecord attributes, so `extra={...}` fields can be picked out.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}

_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


def request_id() -> str:
    """The current request's id, or "" outside a request."""
    return _request_id.get()


class CloudLoggingFormatter(logging.Formatter):
    """Render each record as a single JSON line."""

    def __init__(self, project: str | None = None) -> None:
        super().__init__()
        self._project = project

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "severity": _SEVERITY.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }

        rid = _request_id.get()
        if rid:
            entry["requestId"] = rid

        trace = _trace_id.get()
        if trace and self._project:
            entry["logging.googleapis.com/trace"] = (
                f"projects/{self._project}/traces/{trace}"
            )
        elif trace:
            entry["traceId"] = trace

        if record.exc_info:
            # Cloud Error Reporting picks up exceptions from `stack_trace`.
            entry["stack_trace"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = _coerce(value)

        return json.dumps(entry, default=str)


def _coerce(value):
    if isinstance(value, (str, int, float, bool, type(None), list, dict)):
        return value
    return str(value)


def configure_logging(project: str | None = None) -> None:
    """Install the JSON handler on the root logger. Idempotent."""
    root = logging.getLogger()
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(level)

    for existing in list(root.handlers):
        if getattr(existing, "_cumulus", False):
            return
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingFormatter(project))
    handler._cumulus = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # uvicorn's own access log duplicates RequestContextMiddleware; keep its
    # error channel, drop the access channel.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    for noisy in ("uvicorn", "uvicorn.error"):
        logging.getLogger(noisy).propagate = True
        logging.getLogger(noisy).handlers = []
    # Client libraries log a line per outbound call; only surface problems.
    for chatty in ("httpx", "httpx2", "httpcore", "google", "urllib3"):
        logging.getLogger(chatty).setLevel(logging.WARNING)


def _parse_cloud_trace(header: str) -> str:
    """`X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1` -> TRACE_ID."""
    return header.split("/", 1)[0].split(";", 1)[0].strip()


class RequestContextMiddleware:
    """Bind a request id + trace to the context, and log one line per request.

    Written as raw ASGI rather than BaseHTTPMiddleware so it can also wrap the
    mounted MCP sub-app, whose streaming responses BaseHTTPMiddleware buffers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._log = logging.getLogger("cumulus.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id") or uuid.uuid4().hex
        trace = _parse_cloud_trace(headers.get("x-cloud-trace-context", ""))

        rid_token = _request_id.set(rid)
        trace_token = _trace_id.set(trace)
        started = time.perf_counter()
        status_holder: dict = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (b"x-request-id", rid.encode("latin-1"))
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self._log.exception(
                "Unhandled exception",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "durationMs": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        else:
            status = status_holder["status"]
            self._log.log(
                logging.WARNING if status >= 500 else logging.INFO,
                "%s %s -> %s",
                scope.get("method"),
                scope.get("path"),
                status,
                extra={
                    "httpRequest": {
                        "requestMethod": scope.get("method"),
                        "requestUrl": scope.get("path"),
                        "status": status,
                        "userAgent": headers.get("user-agent", ""),
                        "remoteIp": headers.get("x-forwarded-for", "").split(",")[0].strip(),
                    },
                    "durationMs": round((time.perf_counter() - started) * 1000, 2),
                    **_caller_fields(scope),
                },
            )
        finally:
            _request_id.reset(rid_token)
            _trace_id.reset(trace_token)


def _caller_fields(scope: Scope) -> dict:
    """Attribution for the access log, when a route resolved a principal.

    Routes stash it on `request.state`; unauthenticated and 401 paths simply
    have nothing to report.
    """
    principal = (scope.get("state") or {}).get("principal")
    if principal is None:
        return {}
    fields = {"uid": principal.uid, "role": principal.role}
    if getattr(principal, "key_id", None):
        fields["keyId"] = principal.key_id
        fields["keyName"] = principal.email
    return fields
