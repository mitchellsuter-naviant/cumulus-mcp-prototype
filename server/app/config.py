import os


class Settings:
    """Environment-driven configuration."""

    def __init__(self) -> None:
        # "memory" (default, local dev) or "firestore"
        self.storage_backend: str = os.environ.get("STORAGE_BACKEND", "memory")
        self.firestore_project: str | None = os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.firestore_collection: str = os.environ.get(
            "FIRESTORE_COLLECTION", "assets"
        )
        # If set, write operations on the REST API require X-API-Key to match.
        # Legacy single machine credential with owner rights; prefer minted keys
        # (see api_keys.py), which are per-client, revocable, and capped below owner.
        self.admin_api_key: str | None = os.environ.get("ADMIN_API_KEY")
        # Minted API keys live wherever assets do: "memory" or "firestore".
        self.api_keys_collection: str = os.environ.get(
            "API_KEYS_COLLECTION", "api_keys"
        )
        self.api_key_usage_collection: str = os.environ.get(
            "API_KEY_USAGE_COLLECTION", "api_key_usage"
        )
        # Per-asset usage rollups: which documents are actually read and cited.
        self.asset_usage_collection: str = os.environ.get(
            "ASSET_USAGE_COLLECTION", "asset_usage"
        )
        # Agent and service identities (people live in Firebase Auth).
        self.identities_collection: str = os.environ.get(
            "IDENTITIES_COLLECTION", "identities"
        )
        # Teams: the owner of assets and the second axis of authorization.
        self.teams_collection: str = os.environ.get("TEAMS_COLLECTION", "teams")
        # Flow registry, plus its golden sets and test runs.
        self.flows_collection: str = os.environ.get(
            "FLOWS_COLLECTION", "flows"
        )
        self.golden_sets_collection: str = os.environ.get(
            "GOLDEN_SETS_COLLECTION", "flow_golden_sets"
        )
        self.test_runs_collection: str = os.environ.get(
            "TEST_RUNS_COLLECTION", "flow_test_runs"
        )
        # "none" (default, local dev: everyone is owner) or "firebase".
        self.auth_backend: str = os.environ.get("AUTH_BACKEND", "none")
        # "memory" (default) or "firebase" — where platform users live.
        self.user_backend: str = os.environ.get(
            "USER_BACKEND",
            self.auth_backend if self.auth_backend == "firebase" else "memory",
        )
        # Directory of seed .md/.txt files loaded at startup when the store is empty.
        self.seed_dir: str | None = os.environ.get(
            "SEED_DIR",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed"
            ),
        )
        # Public base URL used in the A2A agent card.
        self.public_base_url: str = os.environ.get(
            "PUBLIC_BASE_URL", "http://localhost:8080"
        )

        # ---- Semantic search ------------------------------------------------
        # "none" (default): lexical BM25 only — no credentials, no network, and
        # what the tests run against. "vertex": Vertex AI embeddings. "hash": a
        # deterministic offline stand-in that exercises the hybrid code path
        # without a network call; it has no semantic value, so never use it
        # where search quality matters (see embeddings.HashingEmbedder).
        self.embedding_backend: str = os.environ.get("EMBEDDING_BACKEND", "none")
        self.embedding_model: str = os.environ.get(
            "EMBEDDING_MODEL", "gemini-embedding-2"
        )
        # gemini-embedding-2 is served only from the `global` endpoint — a
        # regional one returns 404 for it. Older encoders (text-embedding-005)
        # are regional, so override this when pinning back to one.
        self.embedding_location: str = os.environ.get(
            "EMBEDDING_LOCATION", os.environ.get("VERTEX_LOCATION", "global")
        )
        # gemini-embedding-2 is natively 3072 and supports Matryoshka
        # truncation to 1536 or 768, returning unit-length vectors at each.
        # 768 is kept from the previous encoder: it is a quarter of the storage
        # and of the per-query dot product, which matters more than the last
        # few points of recall on a corpus this size. Raising it re-embeds the
        # whole corpus automatically — vectors record the model they came from.
        self.embedding_dimensions: int = int(
            os.environ.get("EMBEDDING_DIMENSIONS", "768")
        )
        # A request is capped on two axes at once: 250 texts and 20,000 total
        # input tokens. The token cap is the one that actually binds for
        # anything but very short documents, so both are enforced when packing
        # batches. (gemini-embedding-2 is the exception to all of this — it
        # takes one text per call and silently fuses a list into a single
        # vector, so it is not a drop-in here.)
        self.embedding_max_batch: int = int(os.environ.get("EMBEDDING_MAX_BATCH", "250"))
        self.embedding_max_batch_tokens: int = int(
            os.environ.get("EMBEDDING_MAX_BATCH_TOKENS", "20000")
        )
        # ~2048 tokens at roughly four characters each. Assets are embedded
        # whole (no chunking), so anything past this is not semantically
        # indexed — see embeddings.embedding_text.
        self.embedding_max_input_chars: int = int(
            os.environ.get("EMBEDDING_MAX_INPUT_CHARS", "8000")
        )
        self.embeddings_collection: str = os.environ.get(
            "EMBEDDINGS_COLLECTION", "asset_embeddings"
        )
        # Relative pull of each retriever in the RRF blend. Lexical is weighted
        # a little higher by default because the corpus is full of proper nouns
        # and internal jargon, which is where vector search is weakest.
        self.hybrid_lexical_weight: float = float(
            os.environ.get("HYBRID_LEXICAL_WEIGHT", "1.0")
        )
        self.hybrid_semantic_weight: float = float(
            os.environ.get("HYBRID_SEMANTIC_WEIGHT", "0.8")
        )
        # How deep each retriever goes before fusion. Deeper costs nothing on
        # the lexical side and gives RRF more overlap to work with.
        self.hybrid_candidates: int = int(os.environ.get("HYBRID_CANDIDATES", "50"))
        # Cosine floor for semantic candidates. Off by default, and
        # deliberately so: a dense retriever scores every document, so a query
        # with no keyword match still returns its nearest neighbours, which
        # reads as noise. A floor fixes that, but the right value depends on
        # the encoder and the corpus — guessing one here would silently drop
        # good results. Set it once you have real queries to calibrate against,
        # using the `similarity` field on hits.
        self.hybrid_min_similarity: float = float(
            os.environ.get("HYBRID_MIN_SIMILARITY", "0.0")
        )

        # ---- Answering ------------------------------------------------------
        # The generative half of search. "none" (default) keeps the /ask
        # endpoint retrieval-only: it still answers, but by handing back ranked
        # documents rather than prose, so local dev and the test suite need no
        # credential and no network. "vertex" enables the Gemini planner in
        # answering.py, which is the only thing that can resolve a question
        # like "which assets does the HR team manage" — that answer lives in
        # the owner field, not in any document's text, so retrieval alone
        # cannot reach it.
        self.answer_backend: str = os.environ.get("ANSWER_BACKEND", "none")
        self.answer_model: str = os.environ.get(
            "ANSWER_MODEL", "gemini-3.5-flash-lite"
        )
        # Also `global`-only, for the same reason as the encoder above.
        self.answer_location: str = os.environ.get(
            "ANSWER_LOCATION", os.environ.get("VERTEX_LOCATION", "global")
        )
        # Each step is one model call plus the tool calls it requests. Six is
        # enough for resolve-team -> list-assets -> read-a-document -> answer
        # with room to recover from a wrong turn, and low enough that a
        # confused planner costs a bounded amount before it gives up.
        self.answer_max_steps: int = int(os.environ.get("ANSWER_MAX_STEPS", "6"))
        self.answer_max_output_tokens: int = int(
            os.environ.get("ANSWER_MAX_OUTPUT_TOKENS", "2048")
        )
        # Documents the planner may pull in full during one question. Whole
        # assets are large; without a cap a broad question can walk the corpus
        # into the context window.
        self.answer_max_documents: int = int(os.environ.get("ANSWER_MAX_DOCUMENTS", "8"))


settings = Settings()
