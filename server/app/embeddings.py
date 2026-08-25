"""Embedding backends for semantic retrieval.

Same shape as storage.py: a Protocol, one implementation per backend, and a
`build_embedder` factory driven by config. `EMBEDDING_BACKEND=none` (the local
default) returns None and the knowledge service falls back to pure BM25 — an
embedding outage or a missing credential degrades ranking, it never fails a
search.

Assets are embedded whole. There is no chunking yet, so only the first
`max_input_chars` of a document reaches the model; `embedding_text` puts the
title, path, and tags in front of the body so the identifying part of a long
document is always inside that window. Chunking is the fix, and it changes the
id scheme (`doc_id#chunk_id`), so it is deliberately a later step.
"""

import hashlib
import logging
import math
import re
import struct
import threading
from typing import Protocol

from .models import Asset

log = logging.getLogger("cumulus.embeddings")


class Embedder(Protocol):
    """A text -> vector encoder.

    `model` is recorded next to every stored vector: when it changes, existing
    vectors are stale and get recomputed, because vectors from two different
    models are not comparable.
    """

    model: str
    dimensions: int
    max_input_chars: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


# ---- Asset -> text ----------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def embedding_text(asset: Asset, max_chars: int) -> str:
    """The text actually sent to the model for an asset.

    Unlike `search._searchable_text`, fields are stated once. Repeating a title
    raises its term frequency, which is what BM25 scores on; an encoder just
    sees a document that stutters.
    """
    header = f"{asset.title}\n{asset.path.replace('/', ' › ')}"
    if asset.tags:
        header += f"\ntags: {', '.join(asset.tags)}"
    body = _WS_RE.sub(" ", asset.content).strip()
    return _truncate(f"{header}\n\n{body}", max_chars)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    # Cut on a word boundary when one is nearby, so the tail is not a fragment.
    cut = text.rfind(" ", max_chars - 200, max_chars)
    return text[: cut if cut > 0 else max_chars]


def content_fingerprint(text: str) -> str:
    """Identifies the exact text a stored vector was computed from.

    Re-embedding is driven off this plus the model name, so an edit that does
    not change the embedded window (metadata-only, or a change past the
    truncation point) correctly costs nothing.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


# ---- Vector helpers ---------------------------------------------------------


def l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


# ---- Backends ---------------------------------------------------------------


class HashingEmbedder:
    """Deterministic offline encoder — for tests, not for retrieval quality.

    Projects hashed tokens onto fixed pseudo-random unit vectors and sums them.
    That makes it a bag-of-words model with no semantics at all: it scores
    "car" and "automobile" as unrelated, which is the entire thing a real
    encoder buys you. It exists so the hybrid fusion path, the backfill loop,
    and the persistence round-trip can be exercised without a network call or a
    credential.

    Never set EMBEDDING_BACKEND=hash in an environment where search quality
    matters — it will rank worse than BM25 alone.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self.model = f"hash-v1-{dimensions}"
        self.dimensions = dimensions
        self.max_input_chars = 20_000
        self._token_re = re.compile(r"[a-z0-9]+")
        self._cache: dict[str, list[float]] = {}

    def _token_vector(self, token: str) -> list[float]:
        cached = self._cache.get(token)
        if cached is not None:
            return cached
        # blake2b rather than hash(): Python salts str hashing per process, so
        # hash() would give a different index every restart.
        needed = self.dimensions * 4
        raw = b""
        counter = 0
        while len(raw) < needed:
            raw += hashlib.blake2b(
                token.encode("utf-8"), digest_size=64, salt=str(counter).encode()[:16]
            ).digest()
            counter += 1
        floats = struct.unpack(f"<{self.dimensions}f", raw[:needed])
        # Map the raw bit patterns onto a bounded, zero-centred range; NaNs and
        # infinities are possible when reinterpreting arbitrary bytes as floats.
        vector = [
            0.0 if (f != f or f in (float("inf"), float("-inf"))) else math.tanh(f)
            for f in floats
        ]
        vector = l2_normalize(vector)
        self._cache[token] = vector
        return vector

    def _encode(self, text: str) -> list[float]:
        total = [0.0] * self.dimensions
        for token in self._token_re.findall(text.lower()):
            tv = self._token_vector(token)
            for i in range(self.dimensions):
                total[i] += tv[i]
        return l2_normalize(total)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)


# Vertex truncates any single input at this many tokens, so it is also the
# most a text can contribute to a request's token budget.
_VERTEX_MAX_TOKENS_PER_TEXT = 2048

# Rough chars-per-token for English prose. Only used to pack batches, and it
# errs toward over-estimating, which makes batches smaller rather than
# rejected.
_CHARS_PER_TOKEN = 4

# Encoders that accept exactly one text per request.
#
# This is not a performance note, it is a correctness one. Sending a list to
# one of these does not fail — it returns a *single* vector for the whole list
# fused together. Under the batching path that means one asset gets a garbage
# vector built from 250 documents and the other 249 are silently never
# embedded, with no error anywhere. Verified against the live API:
# `gemini-embedding-2` given two unrelated texts returns one embedding.
_SINGLE_TEXT_MODELS = ("gemini-embedding-2",)


def _max_texts_per_request(model: str, configured: int) -> int:
    if any(model.startswith(prefix) for prefix in _SINGLE_TEXT_MODELS):
        return 1
    return configured


class VertexEmbedder:
    """Vertex AI text embeddings via the google-genai client.

    Authenticates with Application Default Credentials, which on Cloud Run is
    the service account already attached to the revision — no new secret to
    manage. That account needs `roles/aiplatform.user` (or a custom role with
    `aiplatform.endpoints.predict`), and `aiplatform.googleapis.com` enabled.

    Three details here are load-bearing and easy to get wrong:

    - Documents and queries use different task types. Omitting the type does
      not disable the behaviour, it silently defaults to RETRIEVAL_QUERY — so
      forgetting it on the indexing path degrades recall with no error.
    - A request is capped on *two* axes, 250 texts and 20,000 total tokens.
      The token cap binds first for anything but very short documents, so
      batching purely on count would fail once documents got long.
    - Reducing `output_dimensionality` below the model's native size returns
      vectors that are no longer unit length. Cosine scoring downstream assumes
      they are, hence the unconditional re-normalization.
    """

    def __init__(
        self,
        project: str | None,
        location: str,
        model: str,
        dimensions: int,
        max_batch: int,
        max_input_chars: int,
        max_batch_tokens: int = 20_000,
        retry_attempts: int = 5,
    ) -> None:
        # Deferred: only needed when this backend is selected.
        from google import genai
        from google.genai.types import HttpOptions, HttpRetryOptions

        # The client does not retry by default — one attempt, zero retries —
        # which is the wrong posture for a bulk backfill against a quota that
        # answers 429.
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=HttpOptions(
                retry_options=HttpRetryOptions(attempts=retry_attempts)
            ),
        )
        self.model = model
        self.dimensions = dimensions
        self.max_batch = _max_texts_per_request(model, max_batch)
        self.max_input_chars = max_input_chars
        self.max_batch_tokens = max_batch_tokens
        self._lock = threading.Lock()

    @staticmethod
    def _estimated_tokens(text: str) -> int:
        return min(
            _VERTEX_MAX_TOKENS_PER_TEXT,
            max(1, -(-len(text) // _CHARS_PER_TOKEN)),
        )

    def _batches(self, texts: list[str]) -> list[list[str]]:
        """Split into requests that satisfy the count and token caps at once."""
        batches: list[list[str]] = []
        current: list[str] = []
        tokens = 0
        for text in texts:
            cost = self._estimated_tokens(text)
            over_count = len(current) >= self.max_batch
            over_tokens = current and tokens + cost > self.max_batch_tokens
            if over_count or over_tokens:
                batches.append(current)
                current, tokens = [], 0
            current.append(text)
            tokens += cost
        if current:
            batches.append(current)
        return batches

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai.types import EmbedContentConfig

        config = EmbedContentConfig(task_type=task_type)
        if self.dimensions:
            config.output_dimensionality = self.dimensions
        # The client is not documented as thread-safe, and the backfill thread
        # can overlap with a query on the request path.
        with self._lock:
            response = self._client.models.embed_content(
                model=self.model, contents=texts, config=config
            )
        vectors = [l2_normalize(list(e.values)) for e in response.embeddings]
        if len(vectors) != len(texts):
            # The caller zips these against asset ids, so a short response does
            # not raise there — it silently drops documents from the index and
            # mislabels whichever ones remain. Failing loudly here is the only
            # way this class of bug stays visible; `_SINGLE_TEXT_MODELS` exists
            # so it never gets the chance to fire.
            raise RuntimeError(
                f"{self.model} returned {len(vectors)} vectors for "
                f"{len(texts)} texts. This encoder fuses batched inputs; add "
                f"it to _SINGLE_TEXT_MODELS in embeddings.py."
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(self._embed(batch, "RETRIEVAL_DOCUMENT"))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


def build_embedder(
    backend: str,
    project: str | None,
    location: str,
    model: str,
    dimensions: int,
    max_batch: int,
    max_input_chars: int,
    max_batch_tokens: int = 20_000,
) -> Embedder | None:
    """None means "no semantic retrieval" — search stays lexical-only."""
    if backend == "none":
        return None
    if backend == "hash":
        return HashingEmbedder(dimensions=dimensions or 256)
    if backend == "vertex":
        return VertexEmbedder(
            project=project,
            location=location,
            model=model,
            dimensions=dimensions,
            max_batch=max_batch,
            max_input_chars=max_input_chars,
            max_batch_tokens=max_batch_tokens,
        )
    raise ValueError(f"Unknown EMBEDDING_BACKEND: {backend}")
