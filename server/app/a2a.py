"""Minimal A2A front door: agent card + JSON-RPC message/send.

Server-side synthesis is intentionally retrieval-based for the prototype:
the agent answers with the best-matching documents and citations rather
than calling an LLM.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .auth import Principal, get_principal, require_reader
from .config import settings
from .deps import get_answer_service, get_asset_usage
from .usage import CITED

log = logging.getLogger("cumulus.a2a")

router = APIRouter(tags=["a2a"])

AGENT_CARD = {
    "protocolVersion": "0.3.0",
    "name": "Cumulus Knowledge Agent",
    "description": "Answers questions from the enterprise knowledge layer with cited source documents.",
    "version": "0.1.0",
    "preferredTransport": "JSONRPC",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "securitySchemes": {
        "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    },
    "security": [{"apiKey": []}, {"bearer": []}],
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/markdown"],
    "skills": [
        {
            "id": "knowledge-lookup",
            "name": "Knowledge lookup",
            "description": "Retrieves the most relevant published knowledge documents for a question.",
            "tags": ["knowledge", "retrieval"],
            "examples": ["What is our expense approval policy?"],
        }
    ],
}


@router.get("/.well-known/agent-card.json")
@router.get("/.well-known/agent.json")
def agent_card() -> dict:
    return {**AGENT_CARD, "url": settings.public_base_url.rstrip("/") + "/a2a"}


def _extract_text(message: dict) -> str:
    parts = message.get("parts", [])
    texts = [p.get("text", "") for p in parts if p.get("kind") == "text" or "text" in p]
    return " ".join(t for t in texts if t).strip()


def _answer(question: str) -> str:
    """One answer path shared with the console's /ask endpoint.

    The card advertises "answers questions with cited source documents", so
    this has to be the real answering stack rather than a concatenation of the
    top three hits — otherwise the card is a promise the endpoint does not
    keep. With MODEL_BACKEND=none it degrades to ranked documents, and says
    so, which is still honest.
    """
    result = get_answer_service().ask(question)
    # Generated answers only — the retrieval fallback lists its top hits as
    # citations, and counting those would turn an impression into the system's
    # strongest usage signal. See the same guard in rest_api.ask.
    if result.backend != "retrieval":
        for citation in result.citations:
            try:
                get_asset_usage().record(
                    citation.asset_id, CITED, "a2a", "a2a-client"
                )
            except Exception:
                log.exception("Could not record A2A citation")
    if not result.citations:
        return result.answer
    sources = "\n".join(
        f"- kb://{c.asset_id} · {c.title} · {c.path} · "
        + (f"authority: {c.authority_level}" if c.authority_level else f"kind: {c.kind}")
        + ("" if c.status == "published" else f" · status: {c.status}")
        for c in result.citations
    )
    return f"{result.answer}\n\n---\n\n**Sources**\n{sources}"


@router.post("/a2a")
async def a2a_endpoint(
    request: Request, principal: Principal = Depends(get_principal)
) -> JSONResponse:
    require_reader(principal)
    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error")
    rpc_id = body.get("id")
    if body.get("jsonrpc") != "2.0":
        return _rpc_error(rpc_id, -32600, "Invalid request: expected JSON-RPC 2.0")
    method = body.get("method")
    if method != "message/send":
        return _rpc_error(rpc_id, -32601, f"Method not supported: {method}")

    message = (body.get("params") or {}).get("message") or {}
    question = _extract_text(message)
    if not question:
        return _rpc_error(rpc_id, -32602, "No text part found in message")

    log.info(
        "A2A message/send",
        extra={"actor": principal.uid, "questionChars": len(question)},
    )
    reply = {
        "kind": "message",
        "role": "agent",
        "messageId": str(uuid.uuid4()),
        "contextId": message.get("contextId") or str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": _answer(question)}],
    }
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": reply})


def _rpc_error(rpc_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
        status_code=200,
    )
