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

## Reimbursements (the live flow)

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

`accounts-payable` handles inbound vendor invoices, and its interesting move
is that **the extraction strategy depends on what the knowledge layer knows**:

1. **classify-vendor (ModelCall)** names the vendor and resolves it to a slug.
2. **load-exemplar (transform)** looks for `vendor-exemplar-{slug}` in the
   knowledge layer.
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
5. **three-way-match** and **fraud-screen** are declared **placeholders** in
   the export and journaled as such — honest about what isn't automated yet.
6. **post-or-review (Rule)** reads governed values from the
   `invoice-posting-policy` asset at run time (auto-post limit $2,500,
   classification floor 0.75); under the limit posts to the AP ledger,
   otherwise a review Task whose decision resumes the run.

The table lives in the **Accounts Payable** workspace, beside the Expense
Reimbursement one. Locally the stub readers honor hints (`vendor=acme-industrial total=1840
invoice=ACME-INV-20881 po=PO-7741`; an unrecognized vendor name takes the
ensemble path); with `RECEIPT_BACKEND=vertex` the extractions are real
multimodal calls.

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

The left rail has four stops: **Knowledge layer** (Library, the governed
corpus, and Data, the dataset registry), **Flows**, **Workspaces** — a
dropdown, because a workspace is a business view onto rows a flow wrote
rather than a plane of the map — and **Identities, access & keys**. The
Employee Hub hangs off the account avatar and the Task Queue off the top bar,
since both are asked for from every section rather than being one.

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
