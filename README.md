# Cumulus Knowledge Layer (prototype)

An enterprise knowledge layer for `.md`/`.txt` assets with three protocol front doors and a Flutter admin app.

```
┌─────────────┐   MCP (Streamable HTTP)   /mcp/
│  FastAPI on │   REST admin API          /api/v1
│  Cloud Run  │   A2A agent               /a2a  (+ /.well-known/agent-card.json)
└──────┬──────┘
       │ Firestore (assets: text + metadata) — or in-memory for local dev
       │ In-process BM25 index, rebuilt on writes
┌──────┴──────┐
│ Flutter web │   admin_app/ — browse, search, create, edit, delete assets
└─────────────┘   deployed to Firebase Hosting
```

Repo layout:

| Path | What it is |
| --- | --- |
| [server/](server/) | FastAPI app — REST + MCP + A2A, BM25 search, Firestore/in-memory storage |
| [server/seed/](server/seed/) | Seed documents loaded on first start when the store is empty |
| [admin_app/](admin_app/) | Flutter **web** admin UI (`admin_app/`, *not* `app/`) |
| [deploy/](deploy/) | `deploy.sh` (one-command deploy) and `bootstrap_owner.sh` (first owner account) |
| [.devcontainer/](.devcontainer/) | Dev container with Flutter, Python, gcloud, and Firebase CLIs — see its [README](.devcontainer/README.md) |
| [firebase.json](firebase.json) | Firebase Hosting config; serves `admin_app/build/web` |

## Security posture

Every endpoint requires a credential except two deliberate exceptions:

| Surface | Auth |
| --- | --- |
| `GET /health`, `/healthz` | **Open** — Cloud Run and uptime probes need it. Reports liveness, a document count, and whether retrieval is warm (`{"mode","ready"}`). Never document contents, and never the embedding model, dimensions, or fusion weights — those need auth, via `/api/v1/search/status`. |
| `GET /.well-known/agent-card.json` | **Open** — A2A discovery metadata. Contains no secrets and advertises the accepted schemes via `securitySchemes`. |
| REST reads (`/api/v1/assets*`, `/api/v1/search`) | Any authenticated role (`user`+) |
| REST writes (`POST`/`PUT`/`DELETE /api/v1/assets*`) | `admin`+ |
| User management (`/api/v1/users*`) | `admin`+; touching an admin/owner account needs `owner` |
| Identities (`/api/v1/identities*`) | `admin`+ to read; creating, re-scoping, or deleting anything with admin authority needs `owner`. Minted keys may read but not write — see [Identities](#identities) |
| API keys (`/api/v1/api-keys*`) | `admin`+; keys held by an admin identity need `owner`. Minted keys are refused outright — see [API keys](#api-keys) |
| MCP (`/mcp/`) | Enforced at the transport by `RequireAuthMiddleware` — covers the handshake and every tool and resource call |
| A2A (`POST /a2a`) | Any authenticated role (`user`+) |

Three credential types are accepted anywhere:

| Credential | Header | Role |
| --- | --- | --- |
| Firebase ID token | `Authorization: Bearer <token>` | From the `role` custom claim |
| Minted API key | `X-API-Key: cumk_<id>_<secret>` | Inherited from the identity the key was minted for — see [Identities](#identities) |
| `ADMIN_API_KEY` | `X-API-Key: <value>` | `owner` — legacy root credential |

A supplied-but-invalid `X-API-Key` is a hard 401. It never falls through to
`AUTH_BACKEND=none` dev mode, which would otherwise silently promote a revoked
key to owner.

With `AUTH_BACKEND=none` — the local-dev default — every caller is treated as an
owner, so nothing prompts for credentials while developing. Setting
`AUTH_BACKEND=firebase` turns on real verification.

All three front doors resolve credentials through
[`principal_from_headers`](server/app/auth.py), so there is one place to swap in
a different verifier.

### Firestore is closed to clients

[firestore.rules](firestore.rules) denies every client-SDK read and write. Cloud
Run reaches Firestore with a service account through the Admin SDK, which
bypasses rules, so this costs the server nothing and closes the direct path.

It is deny-all rather than `if request.auth != null` on purpose: the Firebase
web API key ships inside the admin app's JavaScript bundle and is public by
design, so trusting `request.auth` would let any signed-in account read the
whole corpus directly, bypassing team ownership, draft status, and the
authority model. No client-side rule reproduces those checks.

This was already the effective default. It is committed so it stays true —
enabling "test mode" in the Firebase console would otherwise make the corpus
world-readable with no deploy and no signal. `deploy.sh` ships the rules
alongside hosting; to apply them alone:

```bash
firebase deploy --only firestore:rules --project <PROJECT_ID>
```

### Known gaps

- **API keys are not the endstate for MCP.** The MCP authorization spec expects
  an OAuth 2.1 resource-server flow; `RequireAuthMiddleware` in
  [server/app/mcp_server.py](server/app/mcp_server.py) is the single enforcement
  point to replace when you get there. Minted keys are the interim step: at
  least they are per-client, revocable, and attributable.
- **`ADMIN_API_KEY` is still an unrevocable owner credential.** It lives in a
  Cloud Run env var, so rotating it means a redeploy, and anyone holding it has
  owner. Prefer minted keys for anything long-lived; keep this one for
  bootstrapping.
- **Usage counts are best-effort.** They are buffered in memory and flushed on a
  threshold (see `_UsageBuffer` in [server/app/api_keys.py](server/app/api_keys.py)),
  so a hard instance kill can lose the last few requests. They are an activity
  signal, not a billing ledger.
- **App-level auth is not a substitute for a perimeter.** IAP or an OAuth proxy
  in front of Cloud Run is complementary defence in depth, not redundant.
- **CORS is `allow_origins=["*"]`.** Credentials travel in headers rather than
  cookies, so this is not an ambient-credential CSRF surface, but narrow it to
  the Hosting origin once that URL is fixed.
- **No optimistic concurrency.** Two admins editing one document silently
  clobber each other — no ETag or `If-Match`.

**Never build the admin app with `--dart-define=API_KEY=...` for a hosted
deployment.** A dart-define is compiled into the JavaScript bundle, so the key
would be readable by anyone who loads the page — and `ADMIN_API_KEY` grants
owner privileges. Use it only against a local server. Hosted builds authenticate
with Firebase ID tokens instead.

## Server

```bash
cd server
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8080
```

Local dev uses the in-memory backend and auto-seeds from [server/seed](server/seed) when the store is empty.

- Health: `GET /health` — `/healthz` also works locally, but Google's front end
  intercepts `/healthz` on `*.run.app`, so `/health` is the path to use against Cloud Run.
- REST: `GET/POST /api/v1/assets`, `GET/PUT/DELETE /api/v1/assets/{id}`, `GET /api/v1/assets/{id}/context`, `GET /api/v1/assets/tree`, `GET /api/v1/search?q=...`, `GET /api/v1/search/status`, `POST /api/v1/search/reindex`
- Users: `GET/POST /api/v1/users`, `PUT /api/v1/users/{uid}/role`, `DELETE /api/v1/users/{uid}`, `GET /api/v1/users/me`
- Identities: `GET/POST /api/v1/identities`, `PUT /api/v1/identities/{uid}/role`, `DELETE /api/v1/identities/{uid}`
- Flows: `GET/POST /api/v1/flows`, `GET /api/v1/flows/stats`, `GET/PATCH/DELETE /api/v1/flows/{id}`, `GET .../usage`, `GET .../runs`, `GET .../runs/{run_id}`, `GET .../audit`, `GET/POST .../golden-set`, `DELETE .../golden-set/{case_id}`, `GET/POST .../test-runs`, `GET .../test-runs/{test_id}` — see [Flows](#flows)
- Tasks: `GET /api/v1/tasks`, `GET /api/v1/tasks/stats`, `GET /api/v1/tasks/{id}`, `POST /api/v1/tasks/{id}/decide` — see [Tasks](#tasks)
- Reimbursements: `GET/POST /api/v1/reimbursements`, `GET .../stats`, `GET .../{id}` — see [Reimbursements](#reimbursements-the-live-flow)
- Accounts payable: `GET/POST /api/v1/payables`, `GET .../stats`, `GET .../{id}` — see [Accounts payable](#accounts-payable-the-second-live-flow)
- Refs: `GET /api/v1/refs/{ref}` — resolve `knowledge:{id}@rev{n}` read-set references; every asset carries a `revision` that bumps on write
- Employee Hub: `GET /api/v1/employee/me`, `GET/POST /api/v1/employee/pto`, `POST /api/v1/employee/hr-chat` — see [Employee Hub](#employee-hub)
- MCP: `POST /mcp/` — tools `list_docs`, `get_doc`, `search_docs`, `get_context`; resource template `kb://{doc_id}`
- A2A: `POST /a2a` with JSON-RPC `message/send`; agent card at `/.well-known/agent-card.json`

Requires the **MCP Python SDK 2.x** (`mcp.server.MCPServer`). The 1.x API
(`FastMCP`) is not compatible with [server/app/mcp_server.py](server/app/mcp_server.py).

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORAGE_BACKEND` | `memory` | `memory` or `firestore` |
| `GOOGLE_CLOUD_PROJECT` | — | Firestore project |
| `FIRESTORE_COLLECTION` | `assets` | Firestore collection name |
| `ADMIN_API_KEY` | unset | Legacy machine credential: `X-API-Key` acts as owner |
| `API_KEYS_COLLECTION` | `api_keys` | Firestore collection for minted keys |
| `API_KEY_USAGE_COLLECTION` | `api_key_usage` | Firestore collection for daily usage rollups |
| `FLOWS_COLLECTION` | `flows` | Firestore collection for Flow definitions |
| `GOLDEN_SETS_COLLECTION` | `flow_golden_sets` | Firestore collection for golden sets |
| `TEST_RUNS_COLLECTION` | `flow_test_runs` | Firestore collection for harness test runs |
| `TASKS_COLLECTION` | `tasks` | Firestore collection for the Task Queue |
| `REIMBURSEMENTS_COLLECTION` | `reimbursements` | Firestore collection for the reimbursements table |
| `EMPLOYEE_COLLECTION` | `employee` | Firestore collection prefix for profiles and PTO |
| `GRAPH_OBJECTS_COLLECTION` | `graph_objects` | Canonical Objects — the entity layer |
| `GRAPH_LINKS_COLLECTION` | `graph_links` | Typed, reversible Links between them |
| `RUNS_COLLECTION` | `flow_runs` | Durable Flow runs: the journal a suspended run resumes from |
| `DOCUMENTS_COLLECTION` | `documents` | Submitted artifacts and the regions an extraction read |
| `DOCUMENT_MAX_BYTES` | `700000` | Refuse artifacts larger than the document-store adapter can hold |
| `VENDOR_PROFILES_COLLECTION` | `vendor_profiles` | Derived per-vendor statistics behind the anomaly screen |
| `RECEIPT_BACKEND` | follows `ANSWER_BACKEND` | `none` (deterministic stub) or `vertex` (multimodal receipt reading) |
| `RECEIPT_MODEL` | `gemini-2.5-flash` | Vertex model for receipt extraction |
| `AUTH_BACKEND` | `none` | `none` (dev: everyone is owner) or `firebase` (verify ID tokens) |
| `USER_BACKEND` | follows `AUTH_BACKEND` | `memory` or `firebase` — where platform users live |
| `SEED_DIR` | `server/seed` | Seed docs loaded when store is empty |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Used in the A2A agent card and the generated MCP client config |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |

## Search

Retrieval is hybrid: a BM25 keyword ranking and a vector-similarity ranking are
computed independently, then fused with reciprocal rank fusion. RRF is used
rather than adding the two scores because they are not on a comparable scale —
BM25 is unbounded and moves with corpus statistics, cosine sits in `[-1, 1]` —
so only their *ranks* are combined. A document both retrievers place mid-list
outranks one that only a single retriever loved.

Each hit reports how it was found:

```json
{ "retrieval": "hybrid", "lexical_rank": 1, "semantic_rank": 3, "similarity": 0.7412 }
```

`mode=lexical` forces exact-term matching (right for acronyms, client names,
error codes); `mode=semantic` ignores vocabulary entirely.

**Everything degrades to lexical rather than failing.** No embedder configured,
a missing credential, an encoder that raises mid-query, a document that has not
been embedded yet — each of those costs ranking quality and nothing else. A
document absent from the semantic ranking simply contributes nothing to fusion,
which is what lets a half-embedded corpus serve sensible results while it warms.

### How vectors are managed

Vectors live in their own Firestore collection, not on the asset — a vector is
~6KB of floats that would otherwise ride along on every REST and MCP response,
and it belongs to a *model*, not to the document.

Each stored vector records the model and a fingerprint of the exact text it was
computed from. Re-embedding happens only when one of those drifts, so restarts
and metadata-only edits cost nothing, and changing `EMBEDDING_MODEL`
invalidates everything automatically. Writes embed inline; startup reconciles
the rest on a background thread so a large corpus cannot outlast the window
Cloud Run gives a container to report healthy.

`GET /api/v1/search/status` reports coverage (`embedded` vs `assets`), which is
how you tell "hybrid, still warming" from "lexical, misconfigured".
`POST /api/v1/search/reindex` (admin) re-embeds whatever drifted and is free
when nothing has.

### Known limits

- **No chunking.** Assets are embedded whole, so only the first
  `EMBEDDING_MAX_INPUT_CHARS` of a document is semantically indexed — Vertex
  truncates a single input at 2048 tokens regardless. Title, path, and tags are
  prepended so the identifying part of a long document always survives the cut,
  but a long document's tail is reachable by keyword only. Chunking is the fix
  and it changes the id scheme (`doc_id#chunk_id`), so it is deliberately a
  separate step.
- **A dense retriever scores every document**, so a query with no keyword match
  still returns its nearest neighbours rather than nothing. Read `similarity`
  to judge, or set `HYBRID_MIN_SIMILARITY` once you have real queries to
  calibrate against — it is off by default because the right threshold depends
  on the encoder and corpus, and guessing one silently drops good results.
- **No reranking.** It lives entirely inside the retrieval service, so it is a
  clean addition later, but it cannot be tuned without knowing what people
  actually ask.

### Search environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMBEDDING_BACKEND` | `none` | `none` (BM25 only), `vertex`, or `hash` |
| `EMBEDDING_MODEL` | `text-embedding-005` | Vertex model id |
| `EMBEDDING_LOCATION` | `us-central1` | Vertex region |
| `EMBEDDING_DIMENSIONS` | `768` | Native for `text-embedding-005`. Reducing it below native denormalizes the vector, so the client re-normalizes unconditionally |
| `EMBEDDING_MAX_BATCH` | `250` | Texts per request — the hard Vertex cap |
| `EMBEDDING_MAX_BATCH_TOKENS` | `20000` | Tokens per request. Enforced alongside the count, and it is the one that actually binds for real documents |
| `EMBEDDING_MAX_INPUT_CHARS` | `8000` | ≈2048 tokens; anything past this is not semantically indexed |
| `EMBEDDINGS_COLLECTION` | `asset_embeddings` | Firestore collection for vectors |
| `HYBRID_LEXICAL_WEIGHT` | `1.0` | Pull of the keyword ranking in fusion |
| `HYBRID_SEMANTIC_WEIGHT` | `0.8` | Pull of the vector ranking. Lexical is weighted higher by default because proper nouns and internal jargon are where vector search is weakest |
| `HYBRID_CANDIDATES` | `50` | Depth each retriever goes to before fusion |
| `HYBRID_MIN_SIMILARITY` | `0.0` | Cosine floor for semantic candidates; `0` disables it |

`EMBEDDING_BACKEND=hash` is a deterministic offline encoder for tests. It has
**no semantic value** — it is a bag of hashed words, so it ranks worse than BM25
alone. It exists so the fusion, backfill, and persistence paths can be exercised
without a network call or a credential. Never set it where search quality
matters.

On Cloud Run, Vertex authenticates with the revision's own service account, so
there is no key to manage — but `aiplatform.googleapis.com` must be enabled and
the account needs `roles/aiplatform.user`. [deploy/deploy.sh](deploy/deploy.sh)
does both unless you set `EMBEDDING_BACKEND=none`.

> Note on the client: this uses `google-genai`, not
> `vertexai.language_models`. The latter is deprecated and past its stated
> removal date, and cannot load newer embedding models at all.

### Tests

Run from inside `server/` — the tests `import app.*`, so `server/` must be the
working directory (a `pyproject.toml` there sets `pythonpath` for pytest):

```bash
cd server && .venv/bin/python -m pytest tests/
```

## Deploy

One command, from a clean checkout — enables APIs, creates Firestore, generates
`ADMIN_API_KEY`, deploys Cloud Run, configures FlutterFire, builds the web app,
and deploys Firebase Hosting:

```bash
./deploy/deploy.sh <PROJECT_ID> [REGION]     # region defaults to us-central1
```

Then enable Email/Password sign-in in the Firebase console (one-time, UI only)
and create the first owner:

```bash
./deploy/bootstrap_owner.sh you@example.com 'a-strong-password' [REGION]
```

`deploy.sh` is the single source of truth for deployment. It writes the generated
key to `deploy/.admin_api_key` (gitignored) and reuses it on later runs.

### Shipping new or edited governed knowledge

The live flows read their governed values — the expense auto-approval limit,
the invoice auto-post limit, the PTO window — out of knowledge-layer documents
at run time. But `seed_if_empty` only runs against an *empty* corpus, so
editing a file in [server/seed/](server/seed/) and redeploying does **not**
update a deployment that already has documents: the flow keeps reading the old
revision. Push them explicitly:

```bash
./deploy/upsert_knowledge.py <SERVICE_URL> "$(cat deploy/.admin_api_key)"
```

Idempotent, and each update is a real revision bump — which is what a governed
knowledge change should be. Pass filenames to push only some of them.

### Reshaped collections

The flow rewrite changed the stored shape of the tasks, reimbursements and
payables collections, and the seeds only run against an *empty* collection. So
`deploy.sh` pins those three to a generation (`tasks_v3`, `reimbursements_v3`,
`payables_v3`, overridable with `COLLECTION_GENERATION`). Pointing at the old
ones would leave stale rows in place and skip the seed that builds the graph,
the runs and the Tasks that belong with them — a deployment that looks fine and
demonstrates nothing. The graph, run, document and profile collections are new,
so they need no pinning.

The generation is set inside `deploy.sh` rather than applied by hand afterwards
because `--set-env-vars` replaces the whole environment: an override added to a
live revision is silently dropped by the next deploy.

New and edited seed documents also need pushing, because `seed_if_empty` never
touches a non-empty corpus:

```bash
./deploy/upsert_knowledge.py <SERVICE_URL> "$(cat deploy/.admin_api_key)"
```

### Redeploying just the server

```bash
cd server && \
.venv/bin/python -m pytest tests/ -q && \
ADMIN_API_KEY=$(cat ../deploy/.admin_api_key) && \
gcloud run deploy cumulus-knowledge \
  --source . --region us-central1 --project <PROJECT_ID> \
  --allow-unauthenticated \
  --set-env-vars "STORAGE_BACKEND=firestore,AUTH_BACKEND=firebase,GOOGLE_CLOUD_PROJECT=<PROJECT_ID>,ADMIN_API_KEY=$ADMIN_API_KEY,PUBLIC_BASE_URL=<SERVICE_URL>,EMBEDDING_BACKEND=vertex,EMBEDDING_LOCATION=us-central1" \
  --quiet && \
curl -sS --fail-with-body -w '\nHTTP %{http_code}\n' <SERVICE_URL>/health
```

`--set-env-vars` **replaces** the whole environment, so every variable the
service needs has to be in that one string — dropping `EMBEDDING_BACKEND`
silently reverts the service to keyword-only search, and because retrieval
degrades rather than fails, nothing will error to tell you. Check the
`retrieval` block in `/health` after any deploy. (`--update-env-vars` sets
individual variables without touching the rest.)

Do **not** pipe the test or deploy step through `tail` — a pipeline's exit status
is the last command's, so `pytest … | tail -1` reports success even when tests
fail and the `&&` chain stops gating anything.

The Cloud Run service account needs `roles/datastore.user`, plus the Firebase
Authentication Admin role when `AUTH_BACKEND=firebase`.

### Connect an MCP client

Create the identity that will use the key (**Identities** → **New agent**), then
mint a key for it (**API keys** → **Mint key**) and copy
the generated configuration straight into your client — the mint response builds
it for you, with the token already substituted:

```json
{
  "servers": {
    "cumulus-knowledge": {
      "type": "http",
      "url": "https://cumulus-knowledge-XXXX.run.app/mcp/",
      "headers": { "X-API-Key": "cumk_<id>_<secret>" }
    }
  }
}
```

A key's authority is its identity's, so give the client a `user`-role identity
unless it needs to write documents. Keys are revocable from the same screen and
stop working immediately; so is withdrawing the identity's authority.

Unauthenticated calls get HTTP 401 with `WWW-Authenticate: Bearer` and a
JSON-RPC error `-32001`. Against a local server with `AUTH_BACKEND=none` the
`headers` block can be omitted.

## Identities

An identity is *who or what* is acting. Everything that holds a credential is
one, and every minted key names exactly one — so a request is always
attributable and authority has a single home. Managed at **Identities** in the
admin app, or over [`/api/v1/identities`](server/app/identities_api.py).

| Kind | Signs in? | Lives in | Prefix |
| --- | --- | --- | --- |
| `person` | Yes, with a password | Firebase Auth (`role` custom claim) — projected into the identity list, never copied | the Firebase uid |
| `agent` | No | `identities` collection | `agt_` |
| `service` | No | `identities` collection | `svc_` |

- **Authority lives on the identity.** A key carries no role of its own; the
  holder's current role is resolved on every request, so re-scoping an identity
  from `user` to `admin` — or back — re-scopes every key it holds at once.
  Resolution is cached for 60s on the authentication path, so a change takes up
  to a minute to bite.
- **Machine identities are capped below `owner`**, and **owner identities cannot
  hold keys at all** — owner is a human sign-in privilege, not something that
  belongs in a file on disk.
- **Deleting an identity revokes its keys**, in that order. A key whose identity
  vanished is refused at the door regardless.
- **A key cannot reshape the identity graph** it authenticates against: keys may
  read the identity list but not create, re-scope, or delete.

## API keys

Machine clients — MCP configurations, integrations, scripts — authenticate with
minted keys rather than the shared `ADMIN_API_KEY`. Managed at
**API keys** in the admin app, or over
[`/api/v1/api-keys`](server/app/api_keys_api.py).

| | |
| --- | --- |
| **Format** | `cumk_<key_id>_<secret>`. The id is public; only a SHA-256 of the whole token is stored. |
| **Shown** | Once, at mint time. Not recoverable — the list view shows a masked `cumk_<id>_abcd…`. |
| **Holder** | Required. Every key names an [identity](#identities) and inherits its role. |
| **Expiry** | Optional: 30 / 90 / 365 days, or never. |
| **Revocation** | Immediate, on both REST and MCP. Withdrawing or deleting the holder has the same effect. |

Two rules exist to keep revocation meaningful:

- **A minted key cannot manage keys or identities.** Any `admin` key could
  otherwise mint replacements for itself, and revoking would stop being
  containment.
- **A minted key cannot be `owner`**, so a leaked config file cannot grant roles
  or delete accounts.

Keys minted before identities existed have no holder. They authenticate nothing,
and the server revokes them once at startup.

### The log and usage tracking

The `api_keys` collection *is* the log — every key records `created_at`,
`created_by`, `revoked_at`, and `revoked_by`, and revoked keys are kept rather
than deleted so the trail survives. Mint and revoke also emit structured log
lines (`API key minted` / `API key revoked`) carrying `keyId` and `actor`.

Usage is tracked at two levels:

- **Per key**: `request_count` and `last_used_at`, shown in the list.
- **Per key per day**: rollup documents in `api_key_usage` with a per-endpoint
  breakdown, behind the chart icon on each row.

Endpoint paths are templated before counting — `/api/v1/assets/expense-approval-policy`
becomes `/api/v1/assets/:id` — so counter cardinality does not grow with the
corpus. Writes are batched (daily rollups with atomic increments, flushed on a
time/count threshold and at shutdown), so a busy MCP client does not cost one
Firestore write per request.

Every request a key makes is attributed in the access log, so Cloud Logging can
answer "what has this client been doing?":

```bash
gcloud run services logs read cumulus-knowledge --region us-central1 \
  --log-filter='jsonPayload.keyId="<key_id>"'
```

## Flows

The **Flows** nav section is the landing and management page for all
authored logic in the enterprise. A fleet page lists every definition; opening
one gives three views under a shared top bar (title, status, **version
selector**, pause/test actions):

| Tab | What it holds |
| --- | --- |
| **Overview** | Five KPI tiles across the top, then the definition (metadata and governed ROI assumptions) beside one daily chart whose measure is chosen from a dropdown — runs, model spend, tokens, or human judgment. Whichever measure is on screen, hovering a day tooltips all four, so answering "and what did that day cost?" never means switching charts. |
| **Flow canvas** | A full-bleed workbench, split down the middle. On the left, the declarative export as an interactive DAG flowing **north to south** on an infinite dotted field — pan, zoom, click, branch-labelled edges, distinct node types. (The dots are painted in viewport space from the viewer's transform, not inside the transformed child: a pattern drawn on the child stops at the graph's bounds and never repaints as you move, so the field would sit still while the graph slid over it and run out at the edges.) On the right, three tabs: **Definition** shows the selected node's exported configuration; **Runs** lists recent executions — selecting one traces its actual path through the graph (steps it never reached fade to ghosts, traversed edges light up in the accent) and collapses the list into a compact header, so clicking a step shows what went in and what came out (prompt, raw response, model, tokens, cost, rule verdict, deciding human, gateway ledger ref), with **Promote to golden set** in that header; and **Eval** paints a selected experiment onto the graph as a heatmap — each node shows the pass rate of the assertions it owns, clicking a hot node lists its failing assertions with expected-vs-actual diffs. Selecting a draft in the top-bar version selector overlays the diff instead (green glow added, red removed, amber modified). |
| **Evaluation & optimization** | Two columns. **Datasets** on the left: the golden dataset as a *versioned* knowledge asset (revision history, provenance chips, JSONL upload, the `cumulus eval push` command — authoring lives in code; the console curates), and the per-ModelCall **correction sets** the flywheel accumulates. **Experiments** on the right: candidate vs baseline over a pinned dataset revision (pinned or live reads), a pass-rate trend, per-run **gate** verdicts, and the recommender — each card carrying its evidence and an **Apply & measure** action that stages the draft *and* auto-launches the experiment, so the estimate becomes a measured delta. |

The run-path trace is derived from the journal rather than from step status
alone, because status is ambiguous: when a Rule branches to both an Action and
a Task and the Task ran, `rule → action` and `task → action` are *both* edges
between executed steps, but only the second was traversed. Walking the journal
in order resolves it — an edge is taken when the next executed step is one this
step actually points at — so a resumed run traces `rule → task → action` and
never around the Task, and a suspended run simply has no edge out of it.

It is kept deliberately close to DOCTRINE.md:

- There is **no "Agent" primitive** — the Flow *is* the agent (Lens 2).
- There is **one kind of authored logic: a Flow.** The doctrine's original
  Workflow/DataFlow split is retired. It was right about the physics — per-case
  work and windowed bulk work really do behave differently — and wrong about
  the level, putting a behavioural difference in the type system, where it
  bought arguments about which noun a definition was rather than better
  defaults. What differs is now read off a definition's own shape:
  `is_windowed()` asks whether the trigger is a window or a business event, and
  `writes_canonical()` asks whether it commits into the graph. Those two derive
  the batching insight and the claim-resolution audit entries that the type
  used to gate.
- **Cost is an audit product.** Every spend number rolls up from ModelCall
  records (model, tokens, cost) — the governed operation the doctrine requires
  to be audited. There is no separate metering path.
- The detail page shows the **Tier-2 declarative export**: typed steps
  (`trigger · modelcall · rule · task · action · event · transform`), each
  with a plain-language summary — the transparency bar, not runnable code.
- **Audit** is the run journal plus the ledger view: Task decisions (with the
  decider), Action Gateway commits (with ledger refs), resolutions, and
  knowledge changes.
- Ground truth keys to the **flow contract** — the declared canonical facts a
  flow establishes (`invoice.line_items[1].amount`), never to step ids. Each
  version carries a *derived* producer map ({fact path → step id}, read off
  the topology; in a real engine, off journal lineage), so grading projects
  stable labels onto whatever the current shape is: the flow can be
  refactored freely without relabelling a single case.
- A **golden case** pins its whole world: the fixture document, the trigger
  payload, fact assertions (equals / approx / one-of / judge-with-rubric),
  trajectory assertions (actions committed, task raised, terminal state), and
  the **read-set** — the knowledge-layer records the run read, at their
  revisions, captured off the journal on promotion. Experiments serve those
  reads back (`pinned`, the default), so a regression grade is reproducible
  even when the vendor master moves; `live` reads are a visibly different
  kind of run. The golden set itself is **versioned**, and every experiment
  records the dataset revision it measured.
- The **test harness** grades assertion-by-assertion with writes suppressed —
  no Action reaches the gateway from a test run, and grading is deterministic
  per (case, candidate) so evals reproduce. Every case is graded against the
  baseline too, so the comparison is like-for-like, a *regression* (baseline
  passed, candidate did not) is visible per case, and the **promotion gate**
  is stated on the run: no regressions, pass rate at or above the baseline.
  Failures localize to the step owning the fact, which is what the canvas
  eval lens paints. A judge that cannot decide raises a review Task rather
  than guessing.
- The **review screen** (open any golden case) shows the fixture document
  with its grounded field regions — bounding boxes over the values — beside
  the case's labels; confirming or correcting them is a ledgered human pass
  that moves the dataset version. It is the same surface that captures
  corrections from live Tasks and onboards a new flow by labelling its first
  documents.
- A **version** is a full snapshot of the declarative export, never a delta —
  a chain of deltas is exactly the opaque artifact the transparency bar rules
  out. Exactly one version is active; at most one draft is open.
- The **recommender** derives insights from a definition's own telemetry and
  carries the evidence on every card. Two rules keep it honest: a model
  downgrade is proposed one rung at a time, and only when the flow is
  already succeeding — spending less on something that is failing is not a
  saving — and applying a card edits the **draft**, never the live version, so
  the change has to survive the golden set before it becomes what runs.
  Applying also auto-launches the experiment against that draft, so the card
  reports a measured delta and a gate verdict, not just an estimate.
- ROI figures derive from per-definition assumptions (`roi_minutes_per_run`,
  `roi_hourly_rate_usd`) — governed knowledge, editable in the console,
  logged on change.

**Prototype honesty:** the Flows section's run telemetry (runs, journals,
spend, audit entries) is *synthesized deterministically* from the
definition's seed profile — stable across restarts and identical on every
backend. Definitions, golden sets, and test runs are real stored state
(`flows`, `flow_golden_sets`, `flow_test_runs` collections; in-memory for
local dev). The registry seeds **one** definition on first start:
`expense-reimbursement`, the flow that also has a *real* execution path —
see [Reimbursements](#reimbursements-the-live-flow). When a full engine
lands, the synthesis functions in
[server/app/flows.py](server/app/flows.py) (`usage_series`,
`recent_runs`, `find_run`, `audit_entries`) are the seams to replace — the
REST contract and the console stay as they are.

Management is admin-gated, and minted API keys may read everything but never
reshape the registry (same posture as teams); launching a test run is the one
admin write a key *may* do, so CI can kick off evals.

## Tasks

The Task Queue is the console's human-in-the-loop spine: *an agent proposes, a
person decides, the system resumes.* It is global rather than a nav section —
an inbox icon in the top bar's account cluster, badged with the open count
(red when anything is high priority), because judgment is requested from
wherever you are, not from a place you visit. Opening it slides in **My
Tasks**: open tasks grouped by Queue (the doctrinal unit of work
distribution), high priority first, oldest first within a band, plus a
Decided view where the record lives on.

Three streams raise tasks, and all three are knowledge-layer streams:

| Stream | Kinds | The decision produces |
| --- | --- | --- |
| HITL proper | `decision`, `exception` | a gateway-committed Action; the run resumes |
| Entity resolution | `match_review` | a confirmed (or reversed) identity Link |
| Governed knowledge | `curation`, `eval_review` | a ledgered knowledge revision |

Every task carries its world — evidence rows, the refs it rests on in the
read-set namespaces (`knowledge:…@rev`, `vendor:…`, `graph:…`), and the
proposed action — so the decision takes seconds. Deciding
(`POST /api/v1/tasks/{id}/decide` with `approved` / `rejected` / `corrected`
plus an optional note) is durable, attributed, and **once-only**: a second
decision gets HTTP 409, because the second decider must learn the judgment
already happened.

Two governance rules at the API boundary:

- **Minted API keys may read the queue but never decide** (403). A key
  deciding a Task would be an agent approving its own proposal.
- Reads are `user`+ — deciding an item in your queue is clerk work, not
  registry administration, so it is deliberately *not* admin-gated.

**Prototype honesty:** tasks are real stored state (`tasks` collection;
in-memory locally) seeded with a demo backlog tied to the seeded fleet, and a
decision is a real, attributed write — the stand-in for the curation-ledger
entry the doctrine requires. Nothing actually resumes yet; each task's
`resumes` field says what would happen once a real engine lands.

## The data & knowledge layer, rationalized

Why any of this exists is argued once, in [THESIS.md](THESIS.md); what the
primitives are is ruled in [DOCTRINE.md](DOCTRINE.md). This section is the
working model that everything below implements: **five concepts, two axes**.
Nothing in the system falls outside the five; several things started as two of
them wearing one name.

```
                        ┌─────────────────────────────┐
                        │   THE GRAPH  (TBox + ABox)  │  ← the only place meaning joins
                        │   types · individuals ·     │
                        │   links · retained claims   │
                        └──────┬──────────┬───────────┘
              refs out         │          │        refs out
        ┌──────────────────────┘          └──────────────────┐
        ▼                                                    ▼
  ┌───────────┐   MAPPINGS (declared seams)           ┌───────────┐
  │  STORES   │ ◄──── column↔type+key bindings ────── │ EXTERNAL  │
  │ documents │       natural keys · precedence       │ SYSTEMS   │
  │ corpus    │       freshness · match policy        │ ERP · IoT │
  │ tables    │                                       │ historian │
  └───────────┘                                       └───────────┘
        ▲
        │  COLLECTIONS (governance declarations over sets)
        │  role · kind · origin · custody · retention · authoritative
```

1. **The Graph** — TBox + ABox. The index and meaning layer of everything
   else. If two things relate, the relation is a Link; there is no second
   relating mechanism. Everything else is a leaf the graph points at.
2. **Stores** — where bytes and rows live, internal or external, chosen by
   access shape. No meaning and no governance of their own.
3. **Collections** — declared sets carrying governance (retention, custody,
   authoritativeness). A Collection can govern content in a store, a subgraph,
   or both.
4. **Mappings** — the declared seams between sources and the graph: which
   columns bind to which types via which keys, under which match policy and
   precedence, at which freshness ([server/app/mappings.py](server/app/mappings.py)).
**Identity is minted; keys are aliases.** An object's uid is random and
stable; the deterministic hash of (type, natural key) is a *lookup alias*
that `get()` resolves transparently. Re-keying (an employee's email changes)
appends a new alias, keeps the old ones resolving, and lands as a claim with
an author — identity survives, journals that recorded the old address still
dereference, and a key collision on rekey is refused ("merge is a supersede,
not a rekey").

5. **Refs** — the universal address grammar (`graph:`, `knowledge:`,
   `document:`, `link:`, run/step/task ids). Provenance is refs stamped on
   claims, read-sets and journals; the graph is the only place refs join.

Two **axes** classify rather than contain — they drive navigation, never
storage: **Role** (data: delete it and history is lost / knowledge: delete it
and the agents get dumber) and **Origin** (authored / flywheel / derived /
synced / flow-written). "Encoded vs. emergent" is Origin: encoded is authored
documents plus the declarations (ontology, Mappings, Collections); emergent is
synced claims, derived profiles, and flywheel exemplars.

**Replication is safe exactly when the replica knows where it came from.**
Three modes are legitimate: *copy custody* (bytes landed with lineage and
hash), *reference custody* (a pointer plus freshness policy; the store stays
external), and *claim replication* (a field value copied into the graph **as a
claim naming its source**, re-resolvable under precedence because the loser is
kept — the MDM case). The forbidden fourth is a bare copy with no lineage.

**Reify vs. bind.** When a source row enters the system, its Mapping picks one
of two materialization strategies:

| | **Reify rows** (SourceRecord + identity Link) | **Column-bind** (Dataset + lazy resolution) |
|---|---|---|
| Volume | low (vendor lists, POs) | high (telemetry, GL transactions) |
| Human adjudicates a row? | yes — a steward rules on a match | no — aggregates only |
| Row is a Link endpoint? | yes (`identified_as` needs it) | no |
| Escalation | per-row Task possible | windowed, aggregate Task only |

Vendors reify; meter readings bind. A reading's meaning is a column binding
("`asset_id` contains Machine identifiers, keyed by asset tag") resolved
lazily at query or rollup time — never eagerly per row. What the readings
*teach* the entity lands as derived claims with a recipe, the
`vendor_payment_profile` pattern.

## The map: entities, runs, collections

Three pieces of machinery the two live flows sit on. They were added together
because each was the thing blocking the others.

### Durable runs (`server/app/runs.py`)

A run is stored state, and its journal of completed steps *is* the run — the
Python frame is disposable. Re-entering a flow body **replays** completed steps
from the journal instead of re-executing them, which is what makes the
interesting behaviour possible:

- A **gate** (`ctx.gate`) raises a Task and suspends. When the Task is decided,
  the same gate returns the decision and execution continues past it. Several
  gates in one flow work by construction — the AP flow uses three.
- A **correction re-runs the checks.** A reviewer fixes an amount, the Rule runs
  again over the corrected facts, and the run either finishes or asks again
  (capped, so it stops asking rather than looping).
- **Restart-safe.** A suspended run holds no compute; it is a document with a
  cursor. `POST /api/v1/runs/{id}/resume` exists for the narrow case where the
  process died between the decision write and the resume, and is idempotent
  because completed steps replay.
- **Retry-then-escalate.** `ctx.step(..., retries=n, escalate_queue=…)` retries
  a transient failure in place, then hands the error to a person as an
  exception Task rather than failing silently.

Everything non-deterministic — model calls, id minting, clock reads — has to
happen *inside* a step, or a replay would not be exact.

### The entity layer (`server/app/graph.py`, `ontology.py`)

Canonical Objects, typed Links, retained Claims.

- An Object's id is **derived from a declared natural key**, so the same
  real-world thing seen twice resolves to one entity — and a replayed step
  mints the same id rather than a duplicate.
- Field values are the **resolution of claims**, not a write. Every value
  arrives as a Claim carrying its source and confidence; `props` is those claims
  reduced under the ontology's precedence order (`human` → `erp` →
  `procurement` → `flow` → `model`). Losing claims stay on the record, so two
  systems disagreeing about a vendor's payment terms is a normal state you can
  look at, and changing precedence **re-resolves history rather than rewriting
  it**.
- Links are typed, directional, carry a confidence and a method, and are
  **reversible**: withdrawing an identity assertion writes an end to its
  validity instead of deleting it.

The ontology is declared in Python and exported to the corpus as the
`enterprise-ontology` document, so the document a person reads and the schema
the system runs cannot drift. `GET /api/v1/graph/ontology` returns the
declaration with live instance counts beside each type.

Why Firestore and not a graph database: every traversal here is a bounded walk
from a known root (`Employee --submitted--> ExpenseRequest
--has_expense_line--> ExpenseLine`), which is point-lookup work. The seam to
reconsider is unbounded-hop traversal, and `graph.traverse` takes a *declared*
path specifically so that stays visible.

Not everything is a link, and the `Claim` is why. In a pure triple world,
attaching "the model said this, at 10:02, with 0.97 confidence" to a value
needs reification — wrapping the statement in another statement — which is what
makes RDF provenance miserable in practice. A Claim *is* that reification, done
once and made first-class, so every fact carries its source without the store
paying for it on reads. What earns its own object, then, is not "is it a fact"
but **does it have its own identity and its own edges**: a line item does (it
is evidenced by a document region and pairs to a PO line), a total does not,
and a *state with a cause* gets an object behind it — `paid` is a projection of
`Invoice --settled_by--> Payment` existing, not a field someone set. A
`last_modified` is not a fact about the invoice at all; it is a fact about the
record, and lives on the claim.

### The TBox is enforced, and that is the testing story

`ontology.py` is not a description any more — it is a constraint. Every write
is validated against it, and `strict` (the default) *refuses* the write rather
than logging it:

- an object type or link type that is not declared;
- a claim on a field the type does not declare, or a non-numeric value on a
  field declared `money`/`number`;
- a link whose endpoints are the wrong types;
- a link that breaks the declared cardinality.

Cardinality is declared only where a second edge would definitely be a bug:
`one-to-many` constrains the inbound side ("an invoice has one issuing
vendor", "a line has one parent"), `many-to-one` the outbound ("a purchase
order is placed with one vendor"), and `many-to-many` is chosen deliberately
where a second edge is legitimate — a request reviewed twice can be decided by
two people. Turning validation on immediately found four cardinalities that
were declared the wrong way round and six fields the flows wrote that the
ontology never mentioned; both are fixed, which is the point.

`GET /api/v1/graph/invariants` sweeps the whole graph. This is the tier of
testing that needs no per-case labels: the invariants hold for every object and
edge, so a duplicate vendor, an orphaned line, or an edge pointing at the wrong
type is caught without anyone having written an assertion about it. The seeded
world is asserted to have zero violations.

### Provenance: the actor, and the act

A claim records **who** asserted a value (`source`: `human:dana@…`,
`erp:erp-vendors`, `model:stub-extractor`) and **which act** wrote it
(`run_id` + `step_id`). Links carry the same pair. With both, "why does this
invoice say $1,425.25" resolves to a step in a journal rather than to a
category.

The other half of that join is the **write-set**: a step records what it wrote
(`graph:Invoice/…@rev2`, `link:issued/…`, `document:…`, `knowledge:…@rev1`)
alongside what it read. Runs had always recorded their read-set; without the
write-set they could not say what they *changed*, which is the half an auditor
actually asks about — and it is what lets the console show the graph assembling
itself step by step.

Writes go through `GraphService.writer(run_id, step_id)`, a view of the service
bound to the act doing the writing: it stamps the provenance and accumulates
the refs, so a flow cannot write to the graph without saying who and when.

### Collections and retention (`server/app/data_collections.py`)

Retention, custody and authoritativeness are properties of a **set**, not of a
row — "keep approved receipts for two years" is a sentence about a Collection.
So Collections are declared in Python, exported as the `data-collections`
document, and `retention_until` is stamped on every artifact at write time.
`GET /api/v1/documents/expiring` reports what is past its horizon; the purge
pass is *not* built, and saying so is better than implying otherwise.

### Inbound sync as Flows (`server/app/sync.py`)

`POST /api/v1/sources/deliver` accepts a push of vendors, purchase orders or
receiving reports and runs the matching sync Flow. The rules that matter:

- Rows **land exactly as delivered** before resolution, so changing the
  matching rules re-resolves without re-fetching.
- A byte-identical redelivery is counted `unchanged` and disturbs nothing;
  passing `idempotency_key` makes a retried delivery re-enter the same run,
  replay its steps and change nothing.
- **A batch feed never suspends for one ambiguous row.** An unclear match lands,
  asserts nothing, and raises a `match_review` Task; the steward's decision is
  the first write. (A per-case flow does the opposite and blocks, because
  posting against the wrong vendor is not recoverable.)

`GET /api/v1/sources` is assembled from the sync runs themselves — there is no
second bookkeeping store to disagree with them.

### Entity resolution (`server/app/resolution.py`)

One resolver, two callers: the vendor sync when an ERP row arrives, and the AP
flow when a ModelCall has read candidate names off an invoice. One piece of
machinery so the two can never disagree about what counts as a match, which is
the failure mode that produces two vendors named Acme.

Thresholds are governed knowledge (`entity-resolution-policy`): auto-link at
0.92, steward review from 0.70, mint below. The scoring blends exact token
overlap, character similarity, and token-wise soft overlap — three signals
because a typo, a word reorder and an extra word each defeat a different one.

### Seeing the ABox (`server/app/worlds.py`)

The TBox is declared in `ontology.py` and the console draws it. A good deal of
**ABox** is declared in Python too — every eval fixture, and the demo seed's
feed deliveries — and until now it was only ever observable through its
effects: you could read the fixture source, or read a run's journal afterwards,
but not look at the instance graph a fragment establishes.

`GET /api/v1/worlds` lists those fragments with where they are declared;
`GET /api/v1/worlds/{id}` **materializes** one. Materializing is not expanding
the payload — it builds a throwaway in-memory world, replays the fragment
through the real sync flows, and reads the graph back. That distinction is the
whole value: what comes back includes the work establishing it *did* — which
vendor each source row resolved to, at what score, by what method, and the
steward Tasks it escalated — none of which is in the fixture text. Every object
carries both halves of the natural-key question (the key as authored and the id
it resolves to), every claim says whether it won under precedence, and the TBox
invariant sweep runs over the result, so a fragment that would violate the
ontology says so before a case uses it.

`GET /api/v1/graph/instances` returns the **live** ABox in the same shape, which
is what makes the console honest: the running graph and a declared fixture are
peers in one renderer rather than two views that merely resemble each other. A
`type` filter pulls in that type's one-hop neighbours too — a screen of boxes
with no edges answers less than a smaller one showing how they join.

Both are plain reads. Nothing real is touched by looking.

## Evals: a per-flow spec over the shared model

`server/app/evals.py` + `server/app/eval_specs.py`, exported to the corpus as
`flow-eval-specs`.

There is **one** enterprise model — a global `Invoice`, a global `Vendor` — and
a flow's eval is an **overlay** on it saying which parts that flow is
answerable for and how tightly to judge them. The spec carries the match
policy; the case carries only the values:

    Invoice.invoice_number   exact
    Invoice.total_usd        approx ±0.01
    Invoice.due_date         ignore          (the vendor's terms, not ours)
    Vendor.name              fuzzy >= 0.90   (a model read it off a page)
    issued                   exists
    has_invoice_line         count

Three things follow. Cases stop repeating themselves — one decision about how
strictly to compare a total, applied everywhere. Adding a field to the ontology
cannot silently start failing cases, because unlisted fields are ignored unless
a spec says `unlisted_fields: require` and asks to be told about drift. And the
spec is reviewable on its own: "what does the AP flow promise about an Invoice"
is a document rather than an inference over fifty cases. `ignore` is as
load-bearing as the rest and is shown, not hidden — a due date is the vendor's
payment terms, so grading it would be grading the wrong system.

**Checkpoints** are the other half. A spec evaluates the world at points *in*
the run: after `z1` the vendor must be resolved and nothing extracted yet;
after `a1` the Invoice, its lines and the MatchSet must exist. That works
without snapshots because claims and links record the step that wrote them, so
the state as of a step is the resolution of the claims whose step came no
later — point-in-time reconstruction, and the direct payoff of recording
provenance. Trajectory rules are scoped the same way, so `step_absent m2` at
`z1` means "not yet" rather than "never".

Because the flows write the graph once, at commit, a run held at a gate never
reaches the `committed` checkpoint — so declaring it in a case *is* the
assertion that this invoice should have posted, and a case for a held invoice
asserts the entities are **absent** instead. That pins the rule that nothing is
committed until the gate is answered. (Writing entities earlier as drafts is
the doctrine's Draft-Object status-gating, and would let a held invoice be
visible as an entity; it is not built, and it would need the anomaly profile to
filter drafts.)

**Isolation, and why separate collections was the call.** Natural keys
resolve to entities, so an eval fixture named "Acme Industrial Inc." would
resolve to the production vendor. An unisolated run would append `model:` claims to a real
vendor and — the one that actually matters — feed eval invoices into the
`vendor_payment_profile` that real invoices are judged against, changing flow
behaviour rather than a number on a screen. So every case runs in its own
in-memory world with the corpus copied in (frozen for the run and writable, so
a flow that promotes an exemplar cannot promote a real one), and the revisions
it read are recorded on the result — two pass rates are only comparable when
they measured the same knowledge.

A fixture is expressed as **the deliveries the real feeds would have made**, so
a precondition is established the way production established it and the sync
flows are exercised on the way in. Fixtures and expectations are written in
**natural keys, never ids** — that is the part that would be hard to undo, and
it is what lets a case promoted from a real run need no rewriting. `$run` and an
empty key cover the keys that are minted rather than authored.

The TBox invariant sweep runs per case at no authoring cost, so a structural
bug fails a case even when no expectation named it.

`GET /api/v1/evals` lists the specs with how much of the model each grades;
`POST /api/v1/evals/{spec}/run` runs them. Launching is `admin+` but **not**
human-gated: a CI key launching an eval is the point of having keys, and it is
the one write in this API where a credential doing the work is correct.

## Reimbursements (the live flow)

Rebuilt on the run engine and the graph. A submission is **N documents plus the
submitter's own commentary**, and a document is **not** a line:

- Each document carries its own note, and the note is a real input: "I am only
  requesting $75 of the $110 — I ordered wine on my own tab" lowers what that
  line claims, and the journal records that the number came from the note.
  Claiming *less* than the receipt is legitimate and needs nobody; claiming more
  is always elevated.
- A compound receipt yields **two lines from one document**, and a reviewer can
  add a line the extraction missed. Lines are facts with an `evidenced_by` edge
  back to the artifact they were read from.
- Escalation reasons are **typed** (`over-limit`, `extraction-uncertain`,
  `restricted-category`, `purpose-missing`, `amount-mismatch`) because they need
  different fixes, and the review screen edits the facts rather than overriding
  the rule. A correction re-runs the Rule; a `purpose-missing` fix that leaves
  the invoice still over the limit correctly asks again.
- The run writes the graph: `Employee --submitted--> ExpenseRequest
  --has_expense_line--> ExpenseLine --evidenced_by--> Document`. A
  human-corrected value lands as a `human:` claim outranking the model's, with
  the model's original reading still on the record.
- Two governed values were added to the policy document: a purpose is required
  above $75.00, and a requested-amount tolerance of $0.50.

Submission lives in the Employee Hub (drag several receipts in at once); the
Workspace is read-and-act. The seed history is produced by *running the flow* —
twelve real runs with real journals, documents, graph objects and Task
decisions — so a bug in the flow shows up at startup rather than on the first
live submission.

The one seeded flow, `expense-reimbursement`, is also the prototype's first
**live** execution path. `POST /api/v1/reimbursements` runs the same steps
the Tier-2 export declares, synchronously:

1. **extract-receipt (ModelCall)** — with `RECEIPT_BACKEND=vertex`, a
   multimodal model reads the receipt image; locally a deterministic stub
   honors hints (`total=64.20 category=meals vendor=corner-bistro`, and the
   word `blurry` drops confidence below the floor), so the whole pipeline is
   drivable without a credential.
2. **read-policy (transform)** — the auto-approval limit, minimum extraction
   confidence, and restricted categories are parsed *at run time* from the
   `expense-approval-policy` knowledge asset. **Editing that document in the
   console changes what the flow does** — no code change; this is the
   governed-knowledge demo, and there is a test asserting it.
3. **needs-elevation (Rule)** — a pure check over the gathered inputs;
   in-policy receipts are approved and recorded, everything else suspends
   into a real Task in the `expense-review` queue.
4. **Deciding the Task resumes the run** — the reimbursement's state commits
   (`approved`/`denied`), the decider and note land on the row, and the run
   journal completes through the Action step.

Every row carries its journal, so the **Expense Reimbursement** workspace
(the reimbursements table) can show provenance per row: which policy was
read, what the rule said, who decided. The table seeds nine decided/pending
rows on first start; live submissions append.

## Accounts payable (the second live flow)

`accounts-payable` resolves, extracts, matches, screens and posts. **A vendor is
an entity, not a slug** — the previous version matched a slug against exemplar
asset ids, which made the knowledge shelf the primary key and meant one company
in two systems was two vendors.

1. **read-vendor-candidates (ModelCall)** does one narrow job: strip every party
   named on the page. Deciding which entity that text refers to is the
   resolver's job against the vendor master, not the model's against its memory.
2. **resolve-vendor (resolve)** fuzzy-matches the master. A confident match
   links; an unknown name mints an `onboarding` vendor; an ambiguous one
   **suspends the run** for a clerk, because posting against the wrong vendor is
   not recoverable.
3. **load-exemplar (transform)** looks for the exemplar keyed by the *canonical
   vendor id*.
3. **Known vendor → one-shot**: a single extraction pass using the exemplar
   as the template — cheap, confident, boring in the right way.
4. **Unknown vendor → ensemble**: three independent model extractions, an
   **adjudicator** that merges them field-by-field (majority wins, amounts
   within a cent agree, disagreements recorded as conflicts), then a
   **verify-exemplar Task** to the `ap-clerk` queue. On approval, the flow
   **writes the verified exemplar into the knowledge layer** — a real
   governed knowledge write — so the vendor graduates from unknown to known
   and the *next* invoice takes the one-shot path. This is the doctrine's
   unknown-vendor flywheel, running.
5. **three-way-match (check)** pairs each invoice line against the PO line that
   ordered it and the receiving line that booked it in — both landed by their
   own sync Flows — and **retains the comparison as a `MatchSet` object** with
   the variance and the tolerance that judged it. Three misses are modelled
   distinctly because they mean different things: a goods line with no PO line
   is a variance, a freight line under the governed allowance is normal, and
   goods not booked in yet is a *sequencing* state (`blocked`), not an error.
6. **anomaly-screen (check)** replaces the fraud placeholder. It scores the
   invoice against *this vendor's own history* from the derived
   `vendor_payment_profile` — amount outlier, first-time-large, suspected
   duplicate, out-of-cadence, round-number — because "unusual" only has a
   useful definition per vendor. Derived statistics are data; the thresholds
   that judge them are governed knowledge in the policy document.
7. **post-or-review (Rule)** reads governed values from the
   `invoice-posting-policy` asset at run time (auto-post limit $2,500,
   classification floor 0.75, match tolerances, anomaly thresholds). Under the
   limit and clean, it posts; otherwise a review Task, and a clerk's correction
   **re-runs the match** rather than overriding it.

**The flywheel still closes, and now it is provable.** Verifying an ensemble
writes the exemplar keyed by the canonical vendor id and graduates the vendor
out of `onboarding`; the *next* invoice from that vendor takes the one-shot
path. The seeded world demonstrates it end to end (`run_ap_seed04` ensemble →
`run_ap_seed05` one-shot, same vendor).

The table lives in the **Accounts Payable** workspace, which also shows the
vendor master — one row per canonical vendor carrying every source system's id
for it. Locally the stub readers honor hints so every branch is drivable
without a credential:

| Hint | What it exercises |
| --- | --- |
| `vendor=acme-industrial` | a known vendor, resolved against the master |
| `newvendor=meridian-supply` | an unknown vendor, stably named — run it twice to watch the flywheel close |
| `po=PO-7741 lines=STL-0250:2,FAS-M12:3` | bills that PO exactly, so the three-way match really matches |
| `pricebump` | a unit price 8% off the PO — price variance |
| `overbill` | bills more than was received — quantity variance |
| `nopo` | no purchase order — `unmatched` |
| `blurry` | drops confidence below the governed floor |
| `total=9800.00` | over the auto-post limit, and an amount outlier once the vendor has history |

With `RECEIPT_BACKEND=vertex` the extractions are real multimodal calls.

The AP world is seeded the same way as the expense history: four vendors
arrive through a real `vendor-sync` delivery (plus a byte-identical
redelivery, a cross-system match that auto-links, and a misspelling that waits
for a steward), the purchase orders and receiving reports arrive through their
own sync Flows, and every invoice is an actual run whose Tasks are decided
through the path a clerk would use.

## Employee Hub

Opened from the account menu (upper right): the one-stop personal page —
PTO balance and a request form, benefits, expense submission, and the **HR
Agent**. Two governed behaviors worth knowing:

- **PTO follows the flow pattern in miniature.** Requests of 3 working days
  or fewer auto-approve (the window is a governed value parsed from the
  `pto-policy` asset); longer requests raise a Task to the `hr` queue, and
  the HR decision resumes the request and deducts the balance.
- **The HR Agent is grounded and cited.** With `ANSWER_BACKEND=vertex` it
  plans with tools over the knowledge layer; locally it degrades to
  retrieval over the `people/` taxonomy plus the caller's own structured
  facts (balance, pending requests, benefits), and labels itself
  `"backend": "retrieval"` rather than pretending.

## Flutter admin app

```bash
cd admin_app
flutter pub get
flutter run -d chrome --dart-define=API_BASE_URL=http://localhost:8080
```

Inside the dev container there is no display, so use the web-server device and
your host browser instead — see [.devcontainer/README.md](.devcontainer/README.md).

The left rail has four stops: **Knowledge layer**, **Flows**, **Workspaces** —
a dropdown, because a workspace is a business view onto rows a flow wrote
rather than a plane of the map — and **Identities, access & keys**. The
Employee Hub hangs off the account avatar and the Task Queue off the top bar,
since both are asked for from every section rather than being one.

The Knowledge layer itself opens on an **Overview** — the landing screen and
the only place metrics live, because reporting on the layer as a whole is a
different job from browsing any one plane of it. It carries two labelled
strips (the corpus, and the model) plus a card per plane. Then four planes,
cut by **origin** — who or what put each thing there:

- **Library** — what people wrote: policies, standards, guidelines. The
  authority lens applies here and only here, because authority is a property
  of documents that argue.
- **Ontology** — what we encoded: **Model · TBox**, **Instances · ABox**, the
  Collection declarations, and the **Mappings** that bind sources in. The
  generated exports carry a "regenerated from Python on every startup" banner
  and refuse edits (409) rather than accepting one the next boot discards.
- **Learned** — what the system figured out through use: the exemplar shelf
  (with origin and revision pins), vendor payment profiles, and the eval
  datasets. The flywheel's output, gathered in one place instead of scattered
  across three.
- **Sources** — what arrives from outside: each feed shown against its
  declared contract (strategy, bindings, escalation shape), and the artifact
  store.

An exemplar is no longer filed as a `how-to` next to the Expense Approval
Policy: assets carry `kind` (document / exemplar / schema-export / prompt /
config) and `origin` (authored / flywheel / derived), `authority_level` is
None for anything that isn't a document, and search takes a `kind` filter so
governed-context retrieval and exemplar lookup cannot cross.

The two graph lenses are deliberately separate screens rather than one
diagram with a toggle, because they answer different questions. **Model · TBox**
is the schema: types, fields, link types with their cardinality, and whether the
ontology conforms. **Instances · ABox** is what actually exists, and it offers
the live graph and all ten authored fragments as peers in one list — drawn with
the *TBox's* geometry, so an instance sits where its type sits on the schema
map and the two screens are readable against each other. Alongside each authored
fragment is what establishing it did: the resolutions the sync flows performed
and the Tasks they escalated.

Each workspace shows the **Flow runs** list above its table: a run carries the
gates, the read-set and the replay count, which a row cannot. Clicking a run
opens its journal step by step. Tasks with something structured to edit open a
**full review screen** from the queue rather than deciding in the drawer —
correcting an amount, splitting a receipt into two lines, adding a line the
model missed, or picking the right vendor — because doing that in a 380px
drawer would be a worse job badly done.

Hosted build (what `deploy.sh` runs):

```bash
cd admin_app && flutter build web --release --dart-define=API_BASE_URL=<SERVICE_URL>
../deploy/deploy_hosting.py                    # not `firebase deploy` — see below
```

`firebase deploy --only hosting` needs the firebase CLI's own interactive
login, which a dev container does not have, and it rejects Application Default
Credentials without a quota project.
[deploy/deploy_hosting.py](deploy/deploy_hosting.py) does the same work over
the Hosting REST API using the `gcloud` token you already have, so anyone who
has run `gcloud auth login` can deploy. `deploy.sh` still uses the CLI, which
works on a workstation where you have logged in.

### API reference

The **?** menu in the top bar, left of the account avatar, opens a built-in API
reference covering every REST endpoint, the MCP tool surface, and A2A — with
parameters, response shapes, auth requirements, and error details. It reads the
base URL from the running app's own configuration, so the examples point at the
server the console is actually talking to.

The endpoint catalog lives in
[admin_app/lib/screens/api_docs_data.dart](admin_app/lib/screens/api_docs_data.dart)
as plain data. When you change a route in `server/app/*_api.py`, update its
entry there in the same commit — a reference that has drifted is worse than
none, because people code against it.

### Authentication & user management

- Roles: `owner > admin > user`. Asset writes require admin+; user management
  requires admin+ (touching admin/owner accounts requires owner). Roles are
  stored as the `role` custom claim in Firebase Auth.
- Enable auth end to end:
  1. Create a Firebase project and enable Email/Password sign-in.
  2. Server: set `AUTH_BACKEND=firebase` (Cloud Run service account needs the
     Firebase Admin role, or set `GOOGLE_APPLICATION_CREDENTIALS` locally).
  3. Flutter: run `flutterfire configure` in `admin_app/` to regenerate
     [admin_app/lib/firebase_options.dart](admin_app/lib/firebase_options.dart).
  4. Bootstrap the first owner: `./deploy/bootstrap_owner.sh you@example.com '<password>'`
- With the placeholder `firebase_options.dart`, `Firebase.initializeApp()` throws,
  which the app catches and treats as **dev mode**: no login screen, and the
  in-memory user directory is seeded with a dev owner. Pair it with a server
  running `AUTH_BACKEND=none`. Note that "Sign out" is a no-op in dev mode.

## Observability

Logs are single-line JSON on stdout, which Cloud Logging promotes to structured
entries automatically ([server/app/logging_config.py](server/app/logging_config.py)):

```json
{"severity":"INFO","message":"Asset updated","logger":"cumulus.rest",
 "requestId":"9f2c…","assetId":"expense-approval-policy","actor":"api-key"}
```

- Every request gets a `requestId` — taken from `X-Request-Id` if the caller
  supplies one, generated otherwise — echoed back on the response header and
  attached to every log line emitted while handling it.
- On Cloud Run, `X-Cloud-Trace-Context` is turned into
  `logging.googleapis.com/trace`, so the console groups a request's logs together.
- Mutations log an audit line naming the actor: asset create/update/delete and
  every user-management change.
- Unhandled exceptions log a `stack_trace` (which Error Reporting picks up) and
  return `{"detail": "Internal server error", "request_id": "…"}`.

```bash
gcloud run services logs read cumulus-knowledge --region us-central1 --limit 50
```

## Design notes

- Assets carry hierarchy metadata: taxonomy `path`, `parent`/`related` links, `supersedes`/`superseded_by`, `authority_level` (`policy > standard > guideline > how-to > note`), and `scope`. The MCP `get_context` tool and REST `context` endpoint expose ancestors/children/siblings/related.
- Retrieval is BM25 (title/path/tags boosted) over the full corpus, rebuilt on every write — appropriate while the corpus fits in memory. Embeddings/vector search and the ingest pipeline (chunking, other doc types) are intentionally deferred.
- The A2A agent answers with retrieved documents and citations rather than LLM synthesis; swap `_answer()` in [server/app/a2a.py](server/app/a2a.py) for a model call when synthesis is wanted.
- Retrieval keeps an inverted index (term -> {doc_id: tf}) so a query scores
  only documents containing a query term, and writes update a single document
  rather than rebuilding the corpus. Callers that post-filter results must ask
  `SearchIndex.search` for `limit=None` and truncate afterwards — truncating in
  the index would discard candidates the filter would have kept.
