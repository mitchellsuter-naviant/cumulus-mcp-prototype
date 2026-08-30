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
        # The flow registry. Grading lives in the eval specs, not here.
        self.flows_collection: str = os.environ.get(
            "FLOWS_COLLECTION", "flows"
        )
        # The worlds eval cases run in, and the demo seed's fragment with them
        # — one registry, because they are the same kind of thing.
        self.fixtures_collection: str = os.environ.get(
            "FIXTURES_COLLECTION", "eval_fixtures"
        )
        # The Task Queue: units of human judgment, routed through queues.
        self.tasks_collection: str = os.environ.get("TASKS_COLLECTION", "tasks")
        # The reimbursements table (the expense-reimbursement flow's output)
        # and the Employee Hub's profile/PTO stores.
        self.reimbursements_collection: str = os.environ.get(
            "REIMBURSEMENTS_COLLECTION", "reimbursements"
        )
        self.employee_collection: str = os.environ.get(
            "EMPLOYEE_COLLECTION", "employee"
        )
        # The AP invoices table (the accounts-payable flow's output).
        self.payables_collection: str = os.environ.get(
            "PAYABLES_COLLECTION", "payables"
        )
        # ---- The entity layer ------------------------------------------------
        # Canonical Objects and typed Links (see graph.py). Two collections,
        # no joins: every traversal is a bounded walk from a known root, which
        # is what a document store does well. The seam to reconsider if
        # traversals ever stop being bounded.
        self.graph_objects_collection: str = os.environ.get(
            "GRAPH_OBJECTS_COLLECTION", "graph_objects"
        )
        self.graph_links_collection: str = os.environ.get(
            "GRAPH_LINKS_COLLECTION", "graph_links"
        )
        # Durable Flow runs: the journal a suspended run resumes from.
        self.runs_collection: str = os.environ.get("RUNS_COLLECTION", "flow_runs")
        # The curation ledger: append-only, one document per Action the
        # Gateway let through or refused. Separate from the runs collection on
        # purpose — a run is deleted when a flow is retired, and the record of
        # who decided what must outlive the machinery that ran it.
        self.ledger_collection: str = os.environ.get(
            "LEDGER_COLLECTION", "curation_ledger"
        )
        # The event log: append-only, one document per Event the Dispatcher
        # recorded or a Flow emitted. Its own collection for the ledger's
        # reason — a fact outlives the run it caused, and the run journals are
        # deleted with the flow.
        self.events_collection: str = os.environ.get(
            "EVENTS_COLLECTION", "events"
        )
        # Submitted artifacts (receipts, invoices) with their extracted regions.
        # Custody is "copy": the bytes live in the map, under the retention the
        # owning Collection declares.
        self.documents_collection: str = os.environ.get(
            "DOCUMENTS_COLLECTION", "documents"
        )
        # A Firestore document caps at ~1MiB, and a base64 payload inflates by
        # a third. Past this the Document Store capability needs a blob-store
        # adapter (GCS) rather than the document-store one — the capability is
        # the same either way, which is the point of naming it a capability.
        self.document_max_bytes: int = int(
            os.environ.get("DOCUMENT_MAX_BYTES", "700000")
        )
        # Per-vendor rolling statistics behind the anomaly screen. Derived, so
        # rebuildable: delete it and the profile Flow recomputes from the graph.
        self.vendor_profiles_collection: str = os.environ.get(
            "VENDOR_PROFILES_COLLECTION", "vendor_profiles"
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

        # ---- The estate connector -------------------------------------------
        # Machinery-config, not map: which live customer environment this
        # deployment pulls from. A Mapping never names a project, a URL or a
        # credential — those stay below the portability line, which is here.
        # Empty ESTATE_PROJECT means no estate is registered, which is the
        # default everywhere but a dev box and reads as "nothing to do".
        self.estate_project: str = os.environ.get("ESTATE_PROJECT", "")
        self.estate_url_template: str = os.environ.get(
            "ESTATE_URL_TEMPLATE", "https://{project}-{app}.web.app"
        )
        # Empty falls back to whatever the estate's discovery calls default.
        self.estate_namespace: str = os.environ.get("ESTATE_NAMESPACE", "")
        # 0 = the in-server loop is off, which is the default and the deployed
        # setting: Scheduling is a sourced capability, and the deployed form is
        # a managed scheduler POSTing /api/v1/estate/pull. This is the dev
        # adapter for that port and nothing else.
        self.estate_pull_interval_seconds: int = int(
            os.environ.get("ESTATE_PULL_INTERVAL_SECONDS", "0")
        )
        # Records per delivery. The estate does not page, so a delta arrives
        # whole and this is the only bound on one POST.
        self.estate_chunk_size: int = int(os.environ.get("ESTATE_CHUNK_SIZE", "500"))
        # Watermarks and last-pull outcomes. Machinery state, not a Dataset —
        # deliberately absent from data_collections.COLLECTIONS.
        self.connector_state_collection: str = os.environ.get(
            "CONNECTOR_STATE_COLLECTION", "connector_state"
        )
        # The HTTP door the connector delivers through. Its own deployment by
        # default: the connector is a client of its own server, because going
        # around the door would start runs with no Event behind them.
        self.connector_deliver_url: str = (
            os.environ.get("CONNECTOR_DELIVER_URL") or self.public_base_url
        )
        # The machine credential /sources/deliver is called with. Empty in dev
        # (AUTH_BACKEND=none makes everyone an owner).
        self.connector_api_key: str = os.environ.get("CONNECTOR_API_KEY", "")

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
        # One switch for every ModelCall. "none" (default) keeps the whole
        # system on its deterministic local readers and retrieval-only /ask —
        # no credential, no network, and what the test suite runs against.
        # "vertex" turns every ModelCall into a real call.
        self.model_backend: str = os.environ.get("MODEL_BACKEND", "none")
        # One generation model for every ModelCall in the system — the receipt
        # and invoice readers, the HR agent, the /ask planner. A deployment
        # that reasons about its own cost should have one number to reason
        # about, and a golden set graded against two different models is
        # measuring two systems.
        self.model: str = os.environ.get("MODEL", "gemini-3.5-flash-lite")
        # `global`-only, for the same reason as the encoder above.
        self.model_location: str = os.environ.get(
            "MODEL_LOCATION", os.environ.get("VERTEX_LOCATION", "global")
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
