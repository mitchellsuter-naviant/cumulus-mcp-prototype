"""MCP server exposing the knowledge layer to AI clients (Streamable HTTP).

The MCP app is a mounted ASGI sub-app, so it is outside FastAPI's dependency
system; `RequireAuthMiddleware` enforces credentials at the transport instead,
which covers the handshake and every tool and resource call in one place.
"""

import json
import logging

from fastapi import HTTPException
from mcp.server import MCPServer
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import principal_from_headers
from .deps import get_asset_usage
from .usage import READ, RETRIEVED, current_caller, reset_caller, set_caller
from .deps import get_service
from .models import AUTHORITY_ORDER
from .service import AssetNotFoundError

log = logging.getLogger("cumulus.mcp")

mcp = MCPServer(
    "cumulus-knowledge",
    instructions=(
        "Enterprise knowledge layer. Use list_docs/get_doc to browse the taxonomy, "
        "search_docs for hybrid keyword-plus-semantic retrieval, and get_context to "
        "see where a document sits in the hierarchy. Prefer higher authority_level "
        f"documents ({' > '.join(AUTHORITY_ORDER)}) when sources disagree."
    ),
)


@mcp.tool()
def list_docs(prefix: str = "") -> list[dict]:
    """List documents, optionally filtered by taxonomy path prefix (e.g. 'finance/policies')."""
    assets = get_service().list_assets(prefix=prefix or None, status="published")
    return [a.summary().model_dump(mode="json") for a in assets]


@mcp.tool()
def get_doc(doc_id: str) -> dict:
    """Fetch a document's full content and metadata by id."""
    try:
        asset = get_service().get(doc_id)
    except AssetNotFoundError:
        return {"error": f"Document not found: {doc_id}"}
    _record(doc_id, READ)
    result = asset.model_dump(mode="json")
    if asset.superseded_by:
        result["warning"] = (
            f"This document is superseded by '{asset.superseded_by}'; fetch that instead."
        )
    return result


@mcp.tool()
def search_docs(query: str, limit: int = 10, mode: str = "hybrid") -> list[dict]:
    """Search published documents.

    Hybrid retrieval by default: a BM25 keyword ranking and a vector-similarity
    ranking are fused, so exact names and paraphrases both work. Set mode to
    "lexical" when you know the exact term (an acronym, a client name, an error
    code) and want literal matching only, or "semantic" to find documents that
    describe an idea without sharing its vocabulary.

    Each hit reports which retriever found it and where it ranked, so a result
    matched only semantically can be treated with more caution than one both
    retrievers agreed on.
    """
    if mode not in ("hybrid", "lexical", "semantic"):
        return [{"error": f"mode must be hybrid, lexical, or semantic (got {mode!r})"}]
    hits = get_service().search(query, limit=limit, mode=mode)
    for hit in hits:
        if not hit.weak:
            _record(hit.asset.id, RETRIEVED)
    return [h.model_dump(mode="json") for h in hits]


@mcp.tool()
def get_context(doc_id: str) -> dict:
    """Get a document's place in the hierarchy: ancestors, children, siblings, related docs."""
    try:
        ctx = get_service().context(doc_id)
    except AssetNotFoundError:
        return {"error": f"Document not found: {doc_id}"}
    return {
        key: (
            [s.model_dump(mode="json") for s in val]
            if isinstance(val, list)
            else _dump(val)
        )
        for key, val in ctx.items()
    }


def _dump(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


@mcp.resource("kb://{doc_id}")
def read_doc(doc_id: str) -> str:
    """Document content by id."""
    content = get_service().get(doc_id).content
    _record(doc_id, READ)
    return content


def _record(doc_id: str, event: str) -> None:
    """Count agent traffic against a document.

    Wrapped because accounting must never be the reason a tool call fails —
    an agent losing its answer to a metrics write would be a bad trade.
    """
    try:
        surface, actor = current_caller()
        get_asset_usage().record(doc_id, event, surface, actor)
    except Exception:
        log.exception(
            "Could not record MCP usage", extra={"docId": doc_id, "event": event}
        )


class RequireAuthMiddleware:
    """Reject unauthenticated MCP traffic before it reaches the protocol handler.

    Swap `principal_from_headers` for an OAuth 2.1 resource-server verifier to
    follow the MCP authorization spec; the enforcement point stays here.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        # ASGI `path` already includes `root_path`; concatenating them would
        # double-count the mount prefix and record usage under "/mcp/mcp".
        full_path = scope.get("path") or "/"
        try:
            principal = principal_from_headers(
                headers.get("authorization"),
                headers.get("x-api-key"),
                full_path,
            )
        except HTTPException as exc:
            log.warning(
                "Unauthenticated MCP request rejected",
                extra={"path": full_path, "detail": exc.detail},
            )
            await _send_401(send, exc.detail)
            return

        # Make the caller visible to anything downstream that wants it. The
        # tool functions get only their own arguments, so usage accounting
        # reads the caller from a context variable rather than a parameter.
        scope["state"] = {**scope.get("state", {}), "principal": principal}
        token = set_caller("mcp", principal.uid)
        if principal.key_id:
            log.info(
                "MCP request authorized by API key",
                extra={"keyId": principal.key_id, "keyName": principal.email,
                       "role": principal.role, "path": full_path},
            )
        try:
            await self.app(scope, receive, send)
        finally:
            reset_caller(token)


async def _send_401(send: Send, detail: str) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32001, "message": f"Unauthorized: {detail}"},
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="cumulus-knowledge"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
