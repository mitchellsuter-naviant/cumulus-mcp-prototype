# Cumulus Knowledge Layer (prototype)

An enterprise knowledge layer for `.md`/`.txt` assets with three protocol front doors and a Flutter admin app.

```
┌─────────────┐   MCP (Streamable HTTP)   /mcp/
│  FastAPI on │   REST admin API          /api/v1
│  Cloud Run  │   A2A agent               /a2a  (+ /.well-known/agent-card.json)
└──────┬──────┘
       │ Firestore (assets: text + metadata) — or in-memory for local dev
       │ In-process hybrid index: BM25 + vectors, updated per write
┌──────┴──────┐
│ Flutter web │   admin_app/ — browse, search, create, edit, delete assets
└─────────────┘   deployed to Firebase Hosting
```

Repo layout:

| Path | What it is |
| --- | --- |
| [server/](server/) | FastAPI app — REST + MCP + A2A, BM25 search, Firestore/in-memory storage |
| [server/app/tasks/](server/app/tasks/) | FDE-authored flow packages — **every** flow in the fleet, one directory per flow, one `v<N>/` per **shipped version**. The two business processes carry their whole body here (`accounts_payable/v6/` + `v7/`, `expense_reimbursement/v9/`); the eleven data-sync feeds, the two sweeps, `order_release/` and `answer_question/` ship a `v1/` holding the declarative steps and the binding to a body that lives beside its machinery. The versions the console offers are exactly the `v<N>/` packages in the deployed code; which one is live is a stored decision the console flips (`POST /api/v1/flows/{id}/versions/{n}/activate`) |
| [server/app/connectors/](server/app/connectors/) | The sync machinery the feed flows are made of: `sync.py` (landing, echo suppression, the SyncService) and the `commerce`/`office`/`dms`/`psa` per-system sidecars. Machinery only — each feed's *flow* registers and versions from its package under `app/tasks/` |
| [server/app/ontology/](server/app/ontology/) | The customer's canonical model — the TBox, its precedence policy, and its governed rendering |
| [server/seed/](server/seed/) | Seed documents loaded on first start when the store is empty |
| [admin_app/](admin_app/) | Flutter **web** admin UI (`admin_app/`, *not* `app/`) |
| [admin_app/lib/tasks/](admin_app/lib/tasks/) | The UI mirror of `server/app/tasks/`: per-process, per-version task review screens (`accounts_payable/v6/extraction_review.dart`, `…/verify_three_way_match.dart`, `expense_reimbursement/v9/expense_review.dart`), each flow's workspace and case models, kind-keyed shared screens, and the one registry (`registry.dart`) that picks the screen for a task from `(flow_id, flow_version, kind)` |
| [deploy/](deploy/) | `deploy.sh` (one-command deploy) and `bootstrap_owner.sh` (first owner account) |
| [.devcontainer/](.devcontainer/) | Dev container with Flutter, Python, gcloud, and Firebase CLIs — see its [README](.devcontainer/README.md) |
| [cumulus.code-workspace](cumulus.code-workspace) | Multi-root workspace: this repo plus `cumulus-demo-ecosystem/` (the simulation engine), `cumulus-vision/` (Cumulus proper, rebuilt one step at a time with this prototype as the guide), and `customer-ai-projects/` (reference-only, read-only), which the dev container bind-mounts from sibling directories on the host — see [Three trees, one window](.devcontainer/README.md#three-trees-one-window) |
| [firebase.json](firebase.json) | Firebase Hosting config; serves `admin_app/build/web` |

## Security posture

Every endpoint requires a credential except two deliberate exceptions:

| Surface | Auth |
| --- | --- |
| `GET /health`, `/healthz` | **Open** — Cloud Run and uptime probes need it. Reports liveness, a document count, and whether retrieval is warm (`{"mode","ready"}`). Never document contents, and never the embedding model, dimensions, or fusion weights — those need auth, via `/api/v1/search/status`. |
| `GET /.well-known/agent-card.json` | **Open** — A2A discovery metadata. Contains no secrets and advertises the accepted schemes via `securitySchemes`. |
| REST reads (`/api/v1/assets*`, `/api/v1/search`) | Any authenticated role (`user`+) |
| REST writes (`POST`/`PUT`/`DELETE /api/v1/assets*`) | `admin`+ |
| Creating a person (`POST /api/v1/users`) | `admin`+; minting an admin/owner account needs `owner`. Listing, re-roling and deleting a person are identity acts — see Identities below |
| Identities (`/api/v1/identities*`) | `admin`+ to read; creating, re-scoping, or deleting anything with admin authority needs `owner`. Minted keys may read but not write — see [Identities](#identities) |
| API keys (`/api/v1/api-keys*`) | `admin`+; keys held by an admin identity need `owner`. Minted keys are refused outright — see [API keys](#api-keys) |
| Task decisions (`POST /api/v1/tasks/{id}/decide`) | The queue must be **this persona's own work** (`require_queue`) — see [Personas](#personas-and-the-two-front-doors). Minted keys refused: a key deciding a Task would be an agent approving its own proposal |
| Console API (`/api/ui/v1/*`) | **Human sessions only** — a minted key is refused at the door. A machine has no screens |
| Curation ledger (`/api/v1/ledger*`) | Any authenticated role, scoped: a principal that reads beyond itself sees every entry, one confined to its own work sees the entries it was a party to — see [The Action Gateway](#the-action-gateway) |
| MCP (`/mcp/`) | Enforced at the transport by `RequireAuthMiddleware` — covers the handshake and every tool and resource call |
| A2A (`POST /a2a`) | Any authenticated role (`user`+) |

Three credential types are accepted anywhere:

| Credential | Header | Role |
| --- | --- | --- |
| Firebase ID token | `Authorization: Bearer <token>` | From the `role` custom claim |
| Minted API key | `X-API-Key: cumk_<id>_<secret>` | Inherited from the identity the key was minted for — see [Identities](#identities). Never a persona: keys may not impersonate |
| `ADMIN_API_KEY` | `X-API-Key: <value>` | `owner` — legacy root credential |

A supplied-but-invalid `X-API-Key` is a hard 401. It never falls through to
`AUTH_BACKEND=none` dev mode, which would otherwise silently promote a revoked
key to owner.

With `AUTH_BACKEND=none` — the local-dev default — a session opens as the
Naviant Engineer persona and may switch to any other with `X-Cumulus-Persona`,
so nothing prompts for credentials while developing and every role is testable.
Setting `AUTH_BACKEND=firebase` turns on real verification, and impersonation
narrows to real engineers only.

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
- Users: `POST /api/v1/users` (mint an account), `GET /api/v1/users/me` — everything else about a person is an identity act, under `/api/v1/identities`
- Identities: `GET/POST /api/v1/identities`, `PUT /api/v1/identities/{uid}/role`, `DELETE /api/v1/identities/{uid}`
- Flows: `GET/POST /api/v1/flows`, `GET /api/v1/flows/stats`, `GET/PATCH/DELETE /api/v1/flows/{id}`, `GET .../usage`, `GET .../versions`, `GET .../versions/{v}/diff`, `GET .../recommendations`, `POST .../recommendations/{id}/apply` — see [Flows](#flows)
- Runs: `GET /api/v1/runs`, `GET /api/v1/runs/stats`, `GET /api/v1/runs/{id}`, `GET .../steps`, `POST .../resume` — the journals every Flow number is read off
- Evals: `GET /api/v1/evals`, `GET /api/v1/evals/{spec}`, `GET .../cases`, `GET .../result`, `POST .../run`, `GET /api/v1/evals/fixtures` — see [Evals](#evals-a-per-flow-spec-over-the-shared-model)
- Ledger: `GET /api/v1/ledger`, `GET /api/v1/ledger/actions`, `GET /api/v1/ledger/{id}` — see [The Action Gateway](#the-action-gateway)
- Tasks: `GET /api/v1/tasks`, `GET /api/v1/tasks/stats`, `GET /api/v1/tasks/{id}`, `POST /api/v1/tasks/{id}/decide` — see [Tasks](#tasks)
- Reimbursements: `GET/POST /api/v1/reimbursements`, `GET .../stats`, `GET .../{id}` — see [Reimbursements](#reimbursements-the-live-flow)
- Accounts payable: `GET/POST /api/v1/payables`, `GET .../stats`, `GET .../{id}` — see [Accounts payable](#accounts-payable-the-second-live-flow)
- Refs: `GET /api/v1/refs/{ref}` — resolve `knowledge:{id}@rev{n}` read-set references; every asset carries a `revision` that bumps on write
- Employee Hub: `GET /api/v1/employee/me`, `GET/POST /api/v1/employee/pto`, `POST /api/v1/employee/hr-chat` — see [Employee Hub](#employee-hub)
- Calendars: `GET /api/v1/calendar/me`, `GET /api/v1/calendar/calendars` — read-only; the provider is the system of record — see [Calendars](#calendars-the-schedule-as-a-mirror-serverappcalendarspy)
- MCP: `POST /mcp/` — tools `list_docs`, `get_doc`, `search_docs`, `get_context`; resource template `kb://{doc_id}`
- A2A: `POST /a2a` with JSON-RPC `message/send`; agent card at `/.well-known/agent-card.json`

Requires the **MCP Python SDK 2.x** (`mcp.server.MCPServer`). The 1.x API
(`FastMCP`) is not compatible with [server/app/mcp_server.py](server/app/mcp_server.py).

### Environment variables

**One model, one switch.** `RECEIPT_MODEL`/`ANSWER_MODEL` and
`RECEIPT_BACKEND`/`ANSWER_BACKEND` collapsed into `MODEL`/`MODEL_BACKEND`. A
deployment that reasons about its own cost should have one number to reason
about, and a suite graded against two different models is measuring two
systems.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORAGE_BACKEND` | `memory` | `memory` or `firestore` |
| `GOOGLE_CLOUD_PROJECT` | — | Firestore project |
| `FIRESTORE_COLLECTION` | `assets` | Firestore collection name |
| `ADMIN_API_KEY` | unset | Legacy machine credential: `X-API-Key` acts as owner |
| `API_KEYS_COLLECTION` | `api_keys` | Firestore collection for minted keys |
| `API_KEY_USAGE_COLLECTION` | `api_key_usage` | Firestore collection for daily usage rollups |
| `FLOWS_COLLECTION` | `flows` | Firestore collection for Flow definitions |
| `FIXTURES_COLLECTION` | `eval_fixtures` | The named worlds eval cases run in — see [Evals](#evals-a-per-flow-spec-over-the-shared-model) |
| `LEDGER_COLLECTION` | `curation_ledger` | The curation ledger: one entry per Action the Gateway committed or refused |
| `TASKS_COLLECTION` | `tasks` | Firestore collection for the Task Queue |
| `REIMBURSEMENTS_COLLECTION` | `reimbursements` | Firestore collection for the reimbursements table |
| `PAYABLES_COLLECTION` | `payables` | Firestore collection for the AP invoices table |
| `EMPLOYEE_COLLECTION` | `employee` | Firestore collection prefix for profiles and PTO |
| `GRAPH_OBJECTS_COLLECTION` | `graph_objects` | Canonical Objects — the entity layer |
| `GRAPH_LINKS_COLLECTION` | `graph_links` | Typed, reversible Links between them |
| `RUNS_COLLECTION` | `flow_runs` | Durable Flow runs: the journal a suspended run resumes from |
| `DOCUMENTS_COLLECTION` | `documents` | Submitted artifacts and the regions an extraction read |
| `DOCUMENT_MAX_BYTES` | `700000` | Refuse artifacts larger than the document-store adapter can hold |
| `VENDOR_PROFILES_COLLECTION` | `vendor_profiles` | Derived per-vendor statistics behind the anomaly screen |
| `MODEL_BACKEND` | `none` | One switch for every ModelCall: `none` or `stub` (deterministic local readers, retrieval-only `/ask` — no credential, no network, what the tests run against) or `vertex`. Any other value, and a `vertex` client that will not construct, refuse at startup: a deployment that silently fell back to the stub would fabricate every extraction while reporting healthy |
| `MODEL` | `gemini-3.5-flash-lite` | The model behind every ModelCall — document readers, HR agent, `/ask` planner |
| `MODEL_LOCATION` | `global` | The Gemini models are served only from the `global` endpoint; a regional one 404s |
| `AUTH_BACKEND` | `none` | `none` (dev: sessions open as the Naviant Engineer persona) or `firebase` (verify ID tokens) |
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

### Authority: how much a document's standing is worth

Relevance decides whether a document is an answer. Authority decides which of
the answers governs. Keeping those apart is the whole design, and conflating
them is the failure it exists to prevent: sorting results by authority puts the
least relevant policy above the how-to that answers the question, and a ranking
readers learn to distrust is worse than no ranking.

So **authority buys places, not score.** After relevance ranks the candidates
and before the page is cut, each document may climb a bounded number of
positions:

| Authority level | Lift |
|---|---|
| `policy` | 3 |
| `standard` | 2 |
| `guideline` | 1 |
| `how-to`, `note` | 0 |
| none (a prompt, a config, a schema export) | 0, and never an answer |

Three places settles which of the documents that answered is the one that
governs. It never lifts a document relevance did not already put near the
answer — a policy at rank 12 stays at rank 9 — and the rule is stable, so it
only ever breaks ties in authority's favour and never invents one. Every hit
reports the `authority_lift` it received, because a reader who sees a policy
above a closer match deserves to know that is the rule working rather than the
ranking misbehaving.

The lift works in *rank* space rather than on the scores, because the scores
are not comparable between the two retrieval paths — BM25 is unbounded, a fused
RRF score sits in a narrow band whose width depends on how many rankings a
document appeared in. A place is a place in both, so one rule covers
lexical-only and hybrid, and "authority is worth up to three places" is a
sentence you can say to a reader and have them check.

**The table is a document, not a constant.** It lives in
[`retrieval-authority`](server/seed/retrieval-authority.md) — the precedence
order, the lift, and the two coverage bands below — for the same reason the PTO
window and the model price sheet do: the order is a statement about how the
customer's organization is governed, and they own it. Editing the asset moves
the ranking, the `sort=authority` browse control, the planner's own system
instruction, and the sentence the HR Agent prints, together. The `MAX_LIFT` cap
is *not* governed: a lift of 50 written into the document would silently turn
retrieval into a sort by authority, which is the thing the number bounds.

### Coverage: is the top of the ranking an answer at all?

Every ranking has a first row, and `score` cannot say whether that row is about
the question. `coverage` can: it is the share of a question's *information* —
its meaningful words, each weighted by IDF — that a document contains.

Weighting matters. "How much PTO do I have" is one topic word after
stopwording, so a memo that merely says "much" would clear any floor built on
word count while answering nothing. A word no document contains is charged at
the corpus's highest observed IDF and never counted as covered, which is what
makes a query of invented words score near zero *everywhere* instead of picking
a winner.

Two spellings of one thing count once. `401(k)` indexes as `401` and `k`; a
person types `401k`. The mixed token is split at query time — query-side like
the stopword list, so it changes what is asked and needs no reindex — and the
parts stay grouped, so a document holding either has covered that part of the
question. A three-digit floor keeps `401` and drops `10`, which is common
enough to make a PTO policy look like half an answer about invoices.

A word no document contains *exactly* falls back to a five-character stem at
retrieval, so "reimbursed" reaches the guideline that says "reimbursement".
Deliberately narrow: the fallback fires only where the term was scoring nothing
for anybody, so it adds matches and never reshuffles ones that already matched.

**Coverage is only the lexical half.** On a hybrid deployment the document
that answers "how do I get money back for a flight I booked" is the guideline
that says *reimbursement* and *airfare* — it shares no word with the question
and its coverage is zero. A floor read off that number alone would drop exactly
the hits embeddings were added to catch, which makes hybrid retrieval worse
than lexical rather than better. (Found live, not reasoned about: every hit for
a paraphrased question on the deployed corpus came back at coverage `0.0`.)

So each hit also carries `semantic_strength` — how far up the ranking's *lead*
it sits, measured against the pack for the reason set out under
`MIN_SEMANTIC_LEAD`: unrelated documents score 0.48-0.58 against anything, and
only the lead separates signal from noise. A ranking with no lead reports zero
strength everywhere, which is what nonsense should score.

`SearchHit.grounding` is the **max** of the two, and the max rather than a
blend on purpose: these are independent pieces of evidence that the document is
about the question, and a perfect lexical match must not be dragged under the
floor by an embedder that happened not to rank it. The bands below are read off
`grounding`, so either retriever can be the one that found the answer.

The two governed bands are what a grounded answerer does with the number:

| Band | What may be said |
|---|---|
| below `Minimum coverage` (0.34) | Not citable. Say the corpus does not answer this and name who owns it. |
| between the two | *The closest thing in the corpus, which may not answer this* — and the gap is the finding: the document that should have answered does not exist. |
| at or above `Governing coverage` (0.60) | The highest-authority survivor governs, and the answer says so. |

Three honest things to say, and only two of them are "here is the answer".

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

### Shipping a change

`deploy.sh` deploys whatever is in the working tree; it does not check that the
tree is sound first. [deploy/ship.sh](deploy/ship.sh) wraps it with the two
steps a deploy of changed code needs and that one deliberately does not take —
running the suites before anything is pushed, and pushing governed knowledge
that `seed_if_empty` will not update on a corpus that already has documents:

```bash
./deploy/ship.sh                          # analyze + test, then deploy Cumulus
./deploy/ship.sh --checks-only            # the suites, and stop
./deploy/ship.sh --knowledge              # deploy, then push server/seed/
./deploy/ship.sh --with-estate            # also build and deploy the demo estate
./deploy/ship.sh --dry-run                # print the plan, touch nothing
```

It reads the project from `.firebaserc` and the estate project from the estate
repo's own `.firebaserc`, so neither is retyped; `--project` and `--estate`
override. A failing check exits non-zero and nothing is deployed. `--help`
lists every flag. It re-implements none of `deploy.sh` — a deployment step that
is wrong is wrong there.

### Shipping new or edited governed knowledge

The live flows read their governed values — the expense auto-approval limit,
the invoice auto-post limit, the PTO window — out of knowledge-layer documents
at run time, and **the extraction prompts are among them**.
`invoice-extraction-prompt` and `receipt-extraction-prompt` are `kind: prompt`
assets read the way a threshold is, with `knowledge:{id}@rev{n}` stamped onto
the ModelCall step that ran on them, so a decision stays explainable against
the wording that produced it. A prompt held as a Python string literal is the
opposite of governed: invisible to the corpus, uneditable without a deploy, and
absent from the read-set of the call it steers. When the asset is missing the
flow falls back to a built-in default and records the ref **unpinned** — the
absent `@rev` is how the journal says it never got one.

But `seed_if_empty` only runs against an *empty* corpus, so
editing a file in [server/seed/](server/seed/) and redeploying does **not**
update a deployment that already has documents: the flow keeps reading the old
revision. Push them explicitly:

```bash
./deploy/upsert_knowledge.py <SERVICE_URL> "$(cat deploy/.admin_api_key)"
```

Idempotent, and each update is a real revision bump — which is what a governed
knowledge change should be. Pass filenames to push only some of them.

`GET /api/v1/refs/{ref}` is the read back. It resolves a `knowledge:{id}@revN`
ref — out of a run journal, a Task, or a fixture's read-set — to the document
it names, and says whether the revision the run read is still the one on the
shelf. When it is not, the text is labelled *the current text, not a record of
what was read*, and the gap is named: superseded revisions are not retained, so
the counter is real and the history is not. That is the whole point of
answering rather than staying silent — a chip that served today's policy as
though it were the rule a decision was measured against would be worse than no
chip.

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

## Personas, and the two front doors

### Who you are acting as

A **persona** is the doctrine's platform Role made concrete
([server/app/personas.py](server/app/personas.py)). Each is a real seeded
employee wherever one exists, because a persona with nobody behind it opens
onto an empty hub, an empty calendar and no history — which teaches you nothing
about the screen you are testing.

| Persona | Authority | Scope | Acts as | Queues |
| --- | --- | --- | --- | --- |
| Naviant Engineer | `owner` | platform | Mitchell Suter | every queue |
| Naviant Support | `user` | platform | *nobody — not in the org chart* | none |
| Customer Admin | `admin` | tenant | Sam Okafor | stewardship, curation, eval |
| Tomas Berg | `user` | own | Tomas Berg | `ap-clerk` |
| Nadia Farrell | `user` | own | Nadia Farrell | `expense-review`, `ap-clerk` |
| Kim Reyes | `user` | own | Kim Reyes | none |
| Rowan Ellis | `user` | own | Rowan Ellis | `expense-review` |

**Two axes, because one ladder cannot say what Support is.** `authority` is a
rung on the existing `owner > admin > user` write ladder, so every
`require_role` call in the server is unchanged. `scope` is the orthogonal read
reach — Naviant Support reads *more* than a Customer Admin and writes *less*,
which is above it on one axis and below it on the other. A full capability
matrix would express that too, and would be machinery nothing here needs.

**Queue-bindings are the other half of a Role.** The ladder cannot tell an AP
clerk from an expense approver — they sit on the same rung and own different
work — so `require_queue` gates a Task decision on the queue actually being
this persona's. Reading is a separate question from deciding: a
platform-scoped principal reads every queue in order to diagnose it and still
decides only its own.

**Switching persona rewrites the `Principal` itself** — uid, email, authority,
scope, queues — rather than travelling beside it, so everything downstream
scopes correctly with no plumbing of its own: the hub resolves "me", the queue
filters to yours, a claim records its author. The real operator is preserved on
`Principal.impersonated_by`, so the ledger names both parties.

Send `X-Cumulus-Persona: <id>`. Locally (`AUTH_BACKEND=none`) it is
unrestricted — that is what local is for. Deployed, only a real
`naviant-engineer` may impersonate. **A minted key may never impersonate**: a
credential picking a human identity is exactly the confusion the Action Gateway
exists to prevent.

### Two APIs, deliberately different shapes

| | `/api/v1` | `/api/ui/v1` |
| --- | --- | --- |
| For | machines — MCP, A2A, integrations, CI | this console |
| Shape | resource-shaped, one noun per route | view-shaped, one route per screen |
| Credentials | minted keys, ID tokens | human sessions only — **keys are refused** |
| Contract | public; changing it costs a version | private; free to change when a screen changes |

The console used to reach 84 distinct `/api/v1` endpoints directly, which meant
every one of them had to be safe to expose to a browser, and a screen rendering
from four resources made four requests and stitched them in Dart — putting part
of the view's logic in the client, where it could drift. The console API
([server/app/ui_api.py](server/app/ui_api.py)) composes once, on the server.
`GET /api/ui/v1/session` is the whole of what the shell needs before its first
paint: who you are, what you may do, which queues are yours, and the roster.

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

## The Action Gateway

Every governed write goes through one place:
[`ActionGateway`](server/app/gateway.py). What the doctrine's governance spine
draws as one control point had been three half-mechanisms in three places —
permission at the HTTP boundary, provenance on `GraphWriter`, and no ledger at
all — and three duties in three places is the same as none, because the hole is
wherever they fail to overlap. A caller reaching `GraphService` directly was
checked by nobody.

It is a chokepoint, not a policy engine. Three duties, and nothing else:

| Duty | What it adds |
| --- | --- |
| **Validate** | The ontology check already lives in `graph.py`; what the Gateway adds is that a `TBoxError` *refuses the write here* rather than being reported by a sweep three weeks later. |
| **Permission** | Two checks per write, against the Principal named at the call site rather than at the route that led there — the rung on the write ladder, then the queue binding, because a Role confers both. |
| **Ledger** | One append per commit **or refusal**, naming both principals when the write happened under impersonation. |

`ACTIONS` is a **closed table of seven declared mutations**; a write naming
anything else is refused before it touches the graph, because an undeclared
Action is an ungoverned one. `GET /api/v1/ledger/actions` serves the table:
"which writes are governed" is a question asked before any particular entry,
and a set that can be enumerated is a set that can be reviewed.

| Action | Regime | Queue |
| --- | --- | --- |
| `post-invoice` | decision | `ap-clerk` |
| `record-expense` | decision | `expense-review` |
| `link-source-record` | decision | `vendor-stewardship` |
| `confirm-vendor` | decision | `ap-clerk` |
| `land-source-records` | observation | — |
| `apply-source-records` | observation | — |
| `resolve-identities` | resolution | — |

**The regime is declared, never inferred.** An observation, a decision and a
resolution are indistinguishable by their effect on the graph — all three end
in an upsert — so the Action carries its regime and the call site picks the
Action. Reading it back off the shape of a write would be a guess recorded as
an audit fact. The three are separable for the first time, which is what makes
"a source reported this" a different row from "a person decided this".

Who commits is *the human who released the last gate, or the Flow's own
declared Principal when no gate ran* — so an invoice that auto-posts under the
governed limit is committed by `flow:accounts-payable`, itself bound to
`ap-clerk`, rather than being credited to whoever submitted it. Un-binding that
queue stops auto-posting rather than exempting it from the check.

The chokepoint claim is checkable rather than asserted:
`grep "graph.writer" server/app/*.py` returns nothing outside
[graph.py](server/app/graph.py), where it is defined,
[gateway.py](server/app/gateway.py), and [evals.py](server/app/evals.py),
which materializes eval fixtures deliberately outside it — a graded world's
ledger should hold the run's writes, not the fixture's.

`GET /api/v1/ledger` reads it back, newest first, filterable by regime, action,
run, task and outcome. It is read-only and there is no way to make it
otherwise: the store has an `append` and nothing else, and a route that could
write an entry could write a *false* one, which is worse than no ledger.

**What the ledger does not guarantee**, said rather than glossed:

- **Not a redo log.** Graph writes are reconstructible because claims are
  retained; knowledge writes are not — `service.update()` puts over the
  document, so an entry can say *that* an asset changed and never restore it.
- **Not transactional.** A write refused halfway leaves what already landed,
  which is why a refused entry still carries the refs it managed to write.
- **Not redaction-proof.** Append-only by omission — there is no update and no
  delete to call — not by cryptography.
- **Write-sets truncate at 500 refs**, with the exact count kept beside them
  (calendar sync's commit wrote 1,398). An entry that fails to store is worse
  than one that says how much of itself it is not showing.

Several paths stay outside it on purpose, each named in
[gateway.py](server/app/gateway.py): knowledge writes and PTO approval are
decision writes to non-graph stores and need their own pass, document writes
are artifact custody rather than core-graph mutation, and
`supersede`/`rekey`/`reverse_link` remain callable directly.

## Flows

The **Flows** nav section is the landing and management page for all
authored logic in the enterprise. The fleet page lists **every flow that
runs**, not every flow that has a definition — five of the seven are inbound
sync feeds with no declarative export, and counting only the two that have one
told the reader the enterprise had two flows while seven were writing the
graph. Opening one gives three views under a shared top bar (title, status,
**version selector**, pause):

| Tab | What it holds |
| --- | --- |
| **Overview** | Five KPI tiles across the top, then the definition (metadata and governed ROI assumptions) beside one daily chart whose measure is chosen from a dropdown — runs, model spend, tokens, or human judgment. Whichever measure is on screen, hovering a day tooltips all four, so answering "and what did that day cost?" never means switching charts. The recommender sits under both, because cost work reads the same telemetry the chart plots. |
| **Flow canvas** | A full-bleed workbench, split down the middle. On the left, the declarative export as an interactive DAG flowing **north to south** on an infinite dotted field — pan, zoom, click, branch-labelled edges, distinct node types. (The dots are painted in viewport space from the viewer's transform, not inside the transformed child: a pattern drawn on the child stops at the graph's bounds and never repaints as you move, so the field would sit still while the graph slid over it and run out at the edges.) On the right, two tabs: **Definition** shows the selected node's exported configuration; **Runs** lists the stored executions — selecting one traces its actual path through the graph (steps it never reached fade to ghosts, traversed edges light up in the accent), and clicking a step then shows that step's own row of the journal: what it read, what it wrote, what it returned, the model and cost it carried, a retry count when there was one, and the human who decided it. A commit names its ledger entry in the same line. Selecting a draft in the top-bar version selector overlays the diff instead (green glow added, red removed, amber modified). |
| **Evaluation** | The flow's spec, its cases, and one run button — see [Evals](#evals-a-per-flow-spec-over-the-shared-model). It answers three questions in order: what does this flow promise, does it still keep that promise, and how do I add a case. There is exactly one population of cases on the screen and it is never counted twice — a result annotates the declared rows rather than repeating them under a second noun. |

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
  Workflow/DataFlow split is retired — it was right that per-case and windowed
  bulk work behave differently, and wrong to put that in the type system, where
  it bought arguments about which noun a definition was rather than better
  defaults. What differs is read off a definition's own shape: `is_windowed()`
  asks whether the trigger is a window or a business event, `writes_canonical()`
  whether it commits into the graph.
- **Cost is an audit product**: model spend is the sum of `cost_usd` over the
  `modelcall` steps of stored runs, never a separate meter. What is missing is
  at the far end — the two live call sites pass a model and no cost. See below.
- The detail page shows the **Tier-2 declarative export**: typed steps
  (`trigger · modelcall · transform · rule · task · action · event · sync ·
  check · resolve`), each with a plain-language summary — the transparency
  bar, not runnable code. The declared vocabulary is the executed one, so a
  step a run journals can always be found on the definition — which is what
  lets an eval milestone anchor on a *kind* rather than an id.
- **Audit** is the run journal plus the curation ledger — every Action the
  Gateway committed or refused, naming both principals when it happened under
  impersonation. See [The Action Gateway](#the-action-gateway).
- What a flow **promises**, and whether it still keeps that promise, is not
  on this page: it is one eval spec per flow, graded by executing the flow in
  an isolated world — see
  [Evals](#evals-a-per-flow-spec-over-the-shared-model). The golden-set
  harness that used to live here graded with a seeded coin flip, never ran the
  flow, and reported a cost and a duration it invented. It was deleted rather
  than repaired: a grader that cannot fail is worse than no grader, because it
  publishes a pass rate.
- A **version** is a full snapshot of the declarative export, never a delta —
  a chain of deltas is exactly the opaque artifact the transparency bar rules
  out. Exactly one version is active; at most one draft is open.
- The **recommender** derives insights from a flow's own runs and carries the
  evidence on every card. Three rules keep it honest: a model downgrade is
  proposed one rung at a time and only when the flow is already succeeding,
  because spending less on something that is failing is not a saving; a card
  whose measurement was never taken is **withheld and named** rather than
  estimated; and applying one edits the **draft**, so the change has to survive
  the eval spec before it becomes what runs. The first card always states what
  the list rests on and what was withheld for want of evidence, because an
  empty list reads as a clean bill of health.
- ROI figures derive from per-definition assumptions (`roi_minutes_per_run`,
  `roi_hourly_rate_usd`) — governed knowledge, editable in the console, logged
  on change — times a real completed-run count. Nothing timed a person against
  these flows, so `roi_basis` states the derivation beside the figure: *16 of
  24 completed run(s) × 7.12 assumed min/run × $30.00/h — an assumption, not a
  measurement.* Runs whose flow declares no assumption are named in that count
  rather than folded into it.

**Nothing here is synthesized.** `GET /api/v1/flows/stats` aggregates the
stored runs and nothing else: a freshly seeded deployment reports 27 runs over
7 flows — 2 with a declarative export, 5 without — `$0.00` of model spend with
`cost_basis: "unrecorded"`, and 0 tokens. Where the journals record nothing the
answer is zero, and a day with no runs is a zero row rather than a gap; the
shape of that series is what the old curve was hiding.

Zero has two readings that point in opposite directions, so the spend figure
travels with a `cost_basis` saying which one this is:

| `cost_basis` | What the zero means |
| --- | --- |
| `measured` | every ModelCall was priced |
| `partial` | some were |
| `unrecorded` | calls happened and nobody priced them — today's answer, over 20 calls |
| `no-model-calls` | nothing called a model: a genuine zero |

Pricing the two call sites in
[server/app/tasks/expense_reimbursement/domain.py](server/app/tasks/expense_reimbursement/domain.py) and
[server/app/tasks/accounts_payable/domain.py](server/app/tasks/accounts_payable/domain.py) — `ctx.step(..., cost_usd=,
tokens=)` — is the seam that turns the figure on; nothing else has to change.

This replaces a per-definition **seed profile** that manufactured a plausible
month of traffic — 2,748 runs, $558.56 of spend, a 99.5% success rate — over
the same 27 stored runs, and rendered a $9,718 ROI beside it with no marking.
A synthesized count is tolerable scaffolding and a synthesized finance figure
is not, and on the page the two were indistinguishable. The definitions
themselves are real stored state (`flows` collection; in-memory for local dev),
and both have a real execution path — see
[Reimbursements](#reimbursements-the-live-flow) and
[Accounts payable](#accounts-payable-the-second-live-flow).

The five feeds with no export are not a gap in the page but a finding it now
reports: each earns a **Needs attention** row reading *"n run(s) with no
declarative export to audit"*. A flow writing the graph with nothing to show a
customer is what the transparency bar forbids, and it was invisible while the
fleet counted definitions.

Management is admin-gated, and minted API keys may read everything but never
reshape the registry (same posture as teams). Launching an eval is the one
admin write a key *may* do, so CI can grade a flow.

## Tasks

The Task Queue is the console's human-in-the-loop spine: *an agent proposes, a
person decides, the system resumes.* It is a rail stop — **My Tasks**, third
in the personal group and badged with the open count — with the queue list on
the left and a full review pane on the right. It used to be a badged icon in
the top bar opening a 660px drawer, and that was the wrong shape for the work:
deciding a task means reading the evidence, following the refs it rests on,
and often opening the full review to correct the facts. Open tasks read high
priority first and oldest first within a band, filtered by Queue (the
doctrinal unit of work distribution); a Decided tab is where the record lives
on.

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
entry the doctrine requires. Deciding a task genuinely resumes the run it
suspended: `tasks_api` wires four resume hooks (reimbursements, payables,
employee PTO, and the steward's entity-resolution decision), each a no-op on
tasks it does not own, and a hook failure never un-decides the task.

**Deciding is gated on the queue being yours.** The doctrine's Role confers
queue-bindings as well as permissions, and the write ladder cannot tell an AP
clerk from an expense approver — they sit on the same rung and own different
work. `require_queue` is that half. Reading is a separate question: a
platform-scoped principal reads every queue in order to diagnose it and still
decides only its own.

## The data & knowledge layer, rationalized

Why any of this exists is argued once, in [THESIS.md](THESIS.md); what the
primitives are is ruled in [DOCTRINE.md](DOCTRINE.md). This section is the
working model that everything below implements: **five concepts, two axes**.
Nothing in the system falls outside the five; several things started as two of
them wearing one name.

It is deliberately **not** the navigation. The console's Knowledge layer is
three tabs — Ontology, Library, Sources — and `Collection`, `TBox` and `ABox`
were taken out of it entirely: none of the three appears anywhere in
DOCTRINE.md, and they had been holding top-level stops. A Collection turns out
to be a *property of a type* — the seven of them exactly partition the
seventeen object types, nothing unclaimed and nothing claimed twice, asserted
from both ends by a test — so the diagram draws them as the frames around its
clusters. The TBox and the ABox are one subject at two zoom levels. A model
earns its place by being true of the server; a navigation earns its place by
being the questions a person arrives with, and those are different lists.

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
synced claims, derived profiles, and the corrections a reviewer records on
the Task they close.

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

The commerce feeds reify, because a vendor name is fuzzy and a steward rules
on the match; the HRIS and calendar feeds bind, because a work email is exact
and nobody adjudicates it. A bound column's meaning is a declaration
("`owner_email` contains Employee identifiers, keyed by work email") resolved
at read time rather than eagerly per row, and validation refuses a bind mapping
that names a `lands_as` type so the distinction cannot blur. What a high-volume
feed *teaches* an entity lands as derived claims with a recipe — the
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

### The entity layer (`server/app/graph.py`, `ontology/tbox.py`)

Canonical Objects, typed Links, retained Claims.

- An Object's id is **derived from a declared natural key**, so the same
  real-world thing seen twice resolves to one entity — and a replayed step
  mints the same id rather than a duplicate.
- Field values are the **resolution of claims**, not a write. Every value
  arrives as a Claim carrying its source and confidence; `props` is those claims
  reduced under the ontology's precedence order (`human` → `erp` → `hris` →
  `calendar` → `procurement` → `flow` → `model`). Losing claims stay on the record, so two
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

### Inbound sync as Flows (`server/app/connectors/sync.py`)

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

### Calendars: the schedule as a mirror (`server/app/calendars.py`)

The home screen and the week view needed a schedule, and the honest way to
build one was to ask what a calendar *is* in the ontology rather than to put a
table beside it. Two types earned their place:

- **Calendar** — one person's schedule at a provider. A root, deliberately
  **not authoritative**: Microsoft 365 owns it, we mirror it. Its competency
  questions are ones nothing else here answers — what is on somebody's Friday,
  and when are they free.
- **CalendarEvent** — one block, and a **part** of the calendar holding it.
  That is not a modelling flourish: a meeting with four attendees is four
  rows, one per calendar, each with its own provider id, reminders and
  response. The shared meeting is not a row; it is what the rows agree on, and
  `attended_by` is where that agreement lives. The part rule is enforced —
  each event's natural key embeds its calendar's, checked by
  `GraphService.check_invariants`.

Attendance and authorship are separate links on purpose: `organized_by` is
many-to-one (who called it — an organizer can book a room they never enter),
`attended_by` is many-to-many with `required` and `response` on the *link*,
because both are facts about the invitation rather than about the person or
the meeting. Times land as **wall-clock local** in the calendar's own zone, as
every provider stores them: a recurring 9am has to stay 9am across a
daylight-saving change.

The feed is a real Flow — `calendar-sync`, a **bind** Mapping like the HRIS,
declared in `mappings.py` and journalled like every other delivery. An
attendee the workforce does not contain (a client sponsor) is counted as
unresolved and **never minted**: an invitation is not evidence of employment.
The mock delivery in `calendars.py` builds three weeks around the current
Monday and is the only fiction — it goes in through `POST`-shaped
`SyncRequest`, and everything downstream is the running system.

The roster includes **the console's operator** (`workforce.OPERATOR_EMAIL`), a
real work address among the fictional ones, because a signed-in principal that
resolves to nobody turns every personal surface into a demo persona with an
apology attached. Both seeds only fire on an empty store, so a person added to
the roster later would never arrive in a deployment that was seeded last week;
`sync_workforce_additions` and `sync_calendar_additions` run on every start and
deliver exactly the people the graph is missing — same Flow, same `hris:` and
`calendar:` claims, so a late arrival is indistinguishable from a founder.

The read surface is deliberately read-only. A block created in Cumulus would
be a fact the mirror could not defend, and the next sync would delete it;
booking time is a Flow that calls the provider, and it does not exist yet. A
principal with no Employee record — every local-dev session — gets a demo
persona's calendar with a `persona_note` saying whose it is, rather than a
silent substitution.

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

A good deal of **ABox** is declared in Python — every eval
[fixture](#evals-a-per-flow-spec-over-the-shared-model), and the demo seed's
feed deliveries — and it used to be observable only through its effects.

`GET /api/v1/worlds` lists them; `GET /api/v1/worlds/{id}` **materializes**
one. Materializing is not expanding the payload: it builds a throwaway
in-memory world, replays the deliveries through the real sync flows, and reads
the graph back. That distinction is the whole value, because what comes back
includes the work establishing it *did* — which vendor each source row resolved
to, at what score, by what method, and the steward Tasks it escalated — none of
which is in the fixture text. Every object carries both halves of the
natural-key question (the key as authored and the id it resolves to), every
claim says whether it won under precedence, and the TBox invariant sweep runs
over the result, so a world that would violate the ontology says so before a
case uses it. It is also why the fixture editor needed no building: it was
already the world browser, and both establish a world by the same route — one
that showed something a graded run never sees would be worse than none.

`GET /api/v1/graph/instances` returns the **live** ABox in the same shape, so
the running graph and a declared world are peers in one renderer rather than
two views that merely resemble each other. A `type` filter pulls in that type's
one-hop neighbours too — a screen of boxes with no edges answers less than a
smaller one showing how they join. Both are plain reads; nothing real is
touched by looking.

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

**Milestones** are the other half — *where in the run* a spec asserts. This
used to be `after_step="z1"`, a literal step id, and that made every case a
hostage of one flow's internal naming: rename `z1`, split a ModelCall in two,
insert a step, and every case that mentioned the id failed without a single
fact about the world having changed. A milestone instead says what the point
*is*, as a predicate over the run journal resolved at grading time:

| Anchor | Selector | Reads as |
| --- | --- | --- |
| `last_of_kind` | a step kind | "once the resolver has run" |
| `first_of_kind` | a step kind | "the first time it stopped for a person" |
| `wrote` | a ref prefix | "once the invoice exists" |
| `terminal` | — | "however it ended, if it did" |

Four, and each is the only way to name a point some declared case depends on: a
resolver that links an *already known* vendor writes nothing, so the point
after it can only be named by kind, and a suspended run has no terminal, so the
case asserting nothing is committed before the first gate needs
`first_of_kind`. The AP spec's four resolve against a real journal to exactly
the places the step ids named — `vendor-settled → z1`, `invoice-retained → a1`,
`held → k2_1`, `outcome → final`. What changed is how a point is *named*, not
which point it is; it stays a resolver rather than a second grading path, so
everything downstream still takes a step id and is untouched.

**An unreachable milestone fails loudly.** Declaring one is itself the claim
that the run reaches it, so there is no path through the grader on which a
milestone quietly contributes nothing — one with no expectations at all still
fails when it cannot be resolved, and the failure names what the run *did* do:

    vendor-settled   expected  the last step of kind `resolve`
                     actual    trigger, modelcall, action
                     detail    this flow ran no step of kind 'resolve' —
                               it ran: trigger, modelcall, action.

That `actual` line is what lets a reader tell a changed flow from a stale
milestone.

Asserting mid-run works without snapshots because claims and links record the
step that wrote them, so the state as of a step is the resolution of the claims
whose step came no later — point-in-time reconstruction, and the direct payoff
of recording provenance. Trajectory rules are scoped the same way, so
`step_absent` at a mid-run milestone means "not yet" rather than "never".

Because the flows write the graph once, at commit, a run held at a gate never
reaches `invoice-retained` — so declaring it in a case *is* the assertion that
this invoice should have posted, and a case for a held invoice asserts the
entities are **absent** at `held` instead. That pins the rule that nothing is
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
nothing a graded run writes touches the real shelf), and the revisions of that
copied corpus are recorded on the result — two pass rates are only comparable
when they were graded against the same knowledge.

**A `Fixture` is a first-class, named, stored object**, and a case names the
world it runs in rather than carrying one. It used to be an anonymous literal
inside the one case that needed it, so two cases over one world were two copies
of it. Nine cases now share seven fixtures — three AP worlds, three expense
worlds, and the demo seed, which belongs to no flow — so re-authoring a world
brings every case naming it along. `GET /api/v1/evals/fixtures` lists them;
`GET /api/v1/worlds/{id}` materializes one, so a world is browsable before any
case runs in it.

A fixture is expressed as **the deliveries the real feeds would have made**, so
a precondition is established the way production established it and the sync
flows are exercised on the way in. Fixtures and expectations are written in
**natural keys, never ids** — that is the part that would be hard to undo, and
it is what lets a case promoted from a real run need no rewriting. `$run` and an
empty key cover the keys that are minted rather than authored.

A fixture also declares the `read_set` it was written against
(`knowledge:invoice-posting-policy@rev1`). That is a claim rather than an
enforcement, and it is worth being plain about which: nothing compares it to
what the graded run actually read, so the pin is documentation until something
does.

**`EvalResult.measured` is a fingerprint of what was graded** — a hash over the
spec's own content plus the ids of the cases that ran. The promotion gate (no
regressions, and at least as many cases passing as the run before it) **refuses
to compare across a mismatch**, reporting rather than judging: declining to
promote is right, and calling it a regression would name the wrong problem. A
declared version says as much only when whoever edited the spec remembered to
bump it; a hash says it either way, and folding the case ids in stops a run
over three cases posing as the baseline for a run over all of them. Results are
held in memory, one per spec, and deliberately not persisted — a pass rate only
means something against the knowledge it read.

The TBox invariant sweep runs per case at no authoring cost, so a structural
bug fails a case even when no expectation named it. The AP suite is 5 cases
and 80 assertions over 4 milestones, and it grades by running the flow: real
run ids, real graph refs, real gates answered by scripted decisions.

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

`expense-reimbursement` is the first of the two seeded definitions and the
prototype's first **live** execution path. `POST /api/v1/reimbursements` runs
the same steps the Tier-2 export declares, synchronously:

1. **extract-receipt (ModelCall)** — with `MODEL_BACKEND=vertex`, a
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
read, what the rule said, who decided. The table seeds twelve rows on first
start — nine approved, two pending, one denied; live submissions append.

## Accounts payable (the second live flow)

`accounts-payable` extracts, resolves, matches, screens and posts. **A vendor is
an entity, not a slug** — the previous version matched a slug against per-vendor
knowledge assets, which made the knowledge shelf the primary key and meant one
company in two systems was two vendors.

1. **extract-invoice (ModelCall)** — one pass over the document returns the
   vendor as printed, the invoice number, dates, PO, totals and the billed
   lines, with a confidence. One governed model call reads the page; there is
   no separate classifier pass and no per-vendor template to keep in step with
   it.
2. **resolve-vendor (resolve)** fuzzy-matches the printed name against the
   vendor master. A confident match links; an unknown name mints an
   `onboarding` vendor; an ambiguous one **suspends the run** for a clerk,
   because posting against the wrong vendor is not recoverable. The model
   contributes text it read; the resolver decides identity, against the master
   and the governed thresholds.
3. **three-way-match (check)** pairs each invoice line against the PO line that
   ordered it and the receiving line that booked it in — both landed by their
   own sync Flows — and **retains the comparison as a `MatchSet` object** with
   the variance and the tolerance that judged it. Three misses are modelled
   distinctly because they mean different things: a goods line with no PO line
   is a variance, a freight line under the governed allowance is normal, and
   goods not booked in yet is a *sequencing* state (`blocked`), not an error.
4. **anomaly-screen (check)** replaces the fraud placeholder. It scores the
   invoice against *this vendor's own history* from the derived
   `vendor_payment_profile` — amount outlier, first-time-large, suspected
   duplicate, out-of-cadence, round-number — because "unusual" only has a
   useful definition per vendor. Derived statistics are data; the thresholds
   that judge them are governed knowledge in the policy document.
5. **post-or-review (Rule)** reads governed values from the
   `invoice-posting-policy` asset at run time (auto-post limit $2,500,
   extraction-confidence floor 0.75, match tolerances, anomaly thresholds).
   Under the limit and clean, it posts; otherwise a review Task, and a clerk's
   correction **re-runs the match** rather than overriding it.

**A vendor still graduates through use.** One seen for the first time is minted
`onboarding` and stays there until a person posts one of its invoices — that
decision, and nothing automatic, is the confirmation it is real. The seeded
world demonstrates it end to end (`run_ap_seed04` mints Meridian and a clerk
vouches for it at the posting gate; `run_ap_seed05` links to the same object
rather than minting a second one).

**Where an invoice comes from.** In a deployed estate, from the customer's own
filing tree: the **`ap-intake`** Flow wakes on the same `DocumentsDelivered`
fact `dms-sync` does, reads the AP folders named by the governed
`ap-intake-policy` asset, finds the documents under them whose pointer resolves
to a revision nobody has read, fetches the bytes through that pointer and
stores them under their content hash — then emits one `InvoiceReceived` per
invoice. It is windowed: a file that will not fetch is a count on the run and
never a Task the other thirty-nine queue behind, and the only Task it raises is
the aggregate, when nothing in a window answered at all.

The **Accounts Payable** workspace keeps one side door for the invoice somebody
has in their hand: **upload** a document, and with no extraction model
configured the screen says plainly that the stub reads the filename, not the
page. There is no second door. The screen used to offer a *composer* — a vendor
picker, an order picker, line steppers and four deviation checkboxes that
serialized to a hint string — and every control was a real graph row and the
thing it produced was still an invoice nobody wrote. A submission with no bytes
is now refused at both doors.

The hint string survives as what it always was: the **case-authoring language**
of this flow, used by the seeded world and the eval triggers, quarantined in
[server/app/tasks/accounts_payable/scenarios.py](server/app/tasks/accounts_payable/scenarios.py)
where exactly two kinds of caller may reach it — the opt-in stub reader
(`MODEL_BACKEND=none` or `stub`) and the fixtures that declare cases:

| Hint | What it exercises |
| --- | --- |
| `vendor=acme-industrial` | a known vendor, resolved against the master |
| `newvendor=meridian-supply` | an unknown vendor, stably named — run it twice to watch the second invoice link rather than mint |
| `po=PO-7741 lines=STL-0250:2,FAS-M12:3` | bills that PO exactly, so the three-way match really matches |
| `pricebump` | a unit price 8% off the PO — price variance |
| `overbill` | bills more than was received — quantity variance |
| `nopo` | no purchase order — `unmatched` |
| `blurry` | drops confidence below the governed floor |
| `total=9800.00` | over the auto-post limit, and an amount outlier once the vendor has history |
| `taxid` | prints a tax id — the resolver's strongest signal |

With `MODEL_BACKEND=vertex` the extractions are real multimodal calls over the
stored bytes, and a Vertex client that will not construct **stops the service
from starting** rather than falling back to the reader that invents.

Nothing on that screen predicts the outcome. The governed value the run will
read sits *beside* the door — `auto-post ≤ $2,500.00 ·
knowledge:invoice-posting-policy@rev1` — and the run answers.
`_posting_check` is the rule; a second implementation in a screen is a second
rule. Submitting returns a screen rather than a sentence: where the run
stopped, whether *this* persona may release that gate and why not ("this is in
ap-clerk; you are bound to no queue" — `require_queue`, legible before the 403
rather than after it), the clauses it read with the deciding one marked, the
match, the screen and the journal — with the run, the Task and the artifact
each one click away. It is the same widget a row in the table expands into,
because they are the same thing seen at two moments.

The AP world is seeded the same way as the expense history: four vendors
arrive through a real `vendor-sync` delivery (plus a byte-identical
redelivery, a cross-system match that auto-links, and a misspelling that waits
for a steward), the purchase orders and receiving reports arrive through their
own sync Flows, and every invoice is an actual run whose Tasks are decided
through the path a clerk would use.

## Employee Hub

Opened from the account menu (upper right): the one-stop personal page —
PTO balance and a request form, benefits, expense submission, and the **HR
Agent**. Three behaviors worth knowing:

- **"Me" is the signed-in email.** An `Employee` is keyed on their email in
  the graph, so that is the only key the hub resolves a person by — switch
  persona and the hub is that person's, with the title, team and manager the
  org chart holds. A principal the org chart has never heard of (Naviant
  Support, a machine credential) is told so instead of being minted a blank
  profile with a balance nobody accrued.
- **PTO follows the flow pattern in miniature.** Requests of 3 working days
  or fewer auto-approve (the window is a governed value parsed from the
  `pto-policy` asset); longer requests raise a Task to the `hr` queue, and
  the HR decision resumes the request and deducts the balance.
- **The HR Agent is grounded and cited.** With `MODEL_BACKEND=vertex` it
  plans with tools over both planes of the knowledge layer — documents *and*
  the entity graph, which is where "what have I spent this quarter" actually
  lives; locally it degrades to retrieval plus the caller's own structured
  facts (balance, placement, pending requests, benefits) and labels itself
  `"backend": "retrieval"` rather than pretending. Either way the question is
  an `HrQuestionAsked` event and the `answer-question` Flow owns the call, so
  the run, the read-set and the cost land in the journal like everything else.

  Four rules it now keeps, each of which it used to break:

  - **It reads the whole corpus.** It was scoped to the `people/` taxonomy,
    which made "how do I get reimbursed for a flight" unanswerable — the
    travel guideline and the expense policy were out of reach — while letting
    the onboarding checklist answer "what is our expense approval policy",
    which it does not. Travel, expenses and equipment are employee questions
    filed under other desks. `people/` is now a one-place tiebreak, not a cage.
  - **It will say it does not know.** It named `hits[0]` "the governing
    document" unconditionally, so nonsense got a governing document and an
    employee had no way to tell a real answer from the top of a bad ranking.
    It applies the governed coverage bands above instead.
  - **It cites documents, never machinery.** A prompt, a config and a
    generated schema export carry no authority by construction; the agent used
    to cite `source-mappings` at people.
  - **It says which rule it ranked under.** Every reply carries a `grounding`
    block — the precedence, the `retrieval-authority` revision it was read at,
    the floor, how many documents fell below it, and the query actually run —
    because "the governing document is X" is a claim about precedence that a
    reader otherwise has no way to check.

  The conversation is the client's. The console replays the transcript on each
  send (so "what about dental?" is a question rather than two words) and the
  server keeps no session: a surface that answers from somebody's health plan
  and PTO balance is the last place to accumulate a record of who asked what,
  and **New chat** is only an honest control if the history it drops was never
  anyone else's to keep.

- **A principal outside the workforce gets a page, not a wall of zeros.** The
  hub used to render the whole personal column for the operator too: two stat
  tiles reading `0`, an empty third, a request form that would deny whatever
  it submitted, and a Benefits card with nothing in it — and an HR Agent that
  opened every reply with "PTO balance: 0 days (accruing 0/month)" and "Next
  company holiday: .". The personal half is not drawn at all now; what stands
  in its place says why, and shows the governed PTO rule, which is a fact
  about the deployment rather than about the reader.

## Flutter admin app

```bash
cd admin_app
flutter pub get
flutter run -d chrome --dart-define=API_BASE_URL=http://localhost:8080
```

Inside the dev container there is no display, so use the web-server device and
your host browser instead — see [.devcontainer/README.md](.devcontainer/README.md).

The left rail reads in two groups, separated by a hairline, and the order is an
argument about what a console is for. The first group is **yours**: **Home**
(today's schedule and where accounts payable stands), **Calendar** (your week,
mirrored from the provider), **My Tasks** (the queue, badged with its open
count), and **Workspaces** — a dropdown, because a workspace is a business
view onto rows a flow wrote rather than a plane of the map. The second is the
platform: **Knowledge layer** first, because everything after it either writes
into the layer (**Flows**) or governs who may touch it (**Identities, access &
keys**). The Employee Hub hangs off the account avatar, since it is about you
rather than about a workstream.

The Knowledge layer itself is **three tabs, one question each**: **Ontology**
(what this system knows, how it relates, which flows touch it), **Library**
(what people wrote down that agents must obey), and **Sources** (did the
outside world deliver, and was it clean). Ontology is where the section lands,
because the diagram *is* the model — the Overview it replaced was the tab bar
redrawn as cards. Entering the section fires exactly three requests, one
composed call per tab; switching between them fires none. For an own-scoped
persona the rail stop is dropped and the section says so in words, because
every route behind it answers 403 and a Retry button on a refusal that can
never succeed reads as an outage.

The diagram is the whole model in one frame: 17 types and 24 links, clustered
into named Collection frames, composition in the accent, the identity link
dashed, self-loops drawn as corner glyphs rather than tangles, and an instance
count on every node. Every box and every line is a target, and hovering either
dims everything it does not touch — which is what answers "if I change this,
what else moves". Selecting a type and choosing **Browse instances** replaces
the canvas with the instance explorer scoped to that type, with a back link:
the model and what exists of it are one view at two zoom levels.

The flow overlay is why the diagram is worth having rather than merely looking
at: pick a flow and its **observed** footprint, read off its own runs, is drawn
beside its **declared** one on the same boxes. Declared but never observed is a
stale declaration or an untested path; observed but never declared is a
governance gap. Both are live on the seeded data — `accounts-payable` reads
`PurchaseOrder`, which no Collection declares it a reader of, and
`expense-reimbursement` writes `Employee`, which only `workforce-sync` and
people-ops are declared to write.

A generated schema export is no longer filed as a `how-to` next to the Expense
Approval Policy: assets carry `kind` (document / schema-export / prompt /
config) and `origin` (authored / flywheel / derived / synced / flow-written),
`authority_level` is None for anything that isn't a document, and search takes
a `kind` filter so governed-context retrieval and generated-export listing
cannot cross.

Each workspace shows the **Flow runs** list above its table: a run carries the
gates, the read-set and the replay count, which a row cannot. Clicking a run
opens its journal step by step. A task with something structured to edit opens
a **full review screen** rather than being decided from the pane beside the
queue — correcting an amount, splitting a receipt into two lines, adding a line
the model missed, or picking the right vendor — because doing that in a side
panel would be a worse job badly done.

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
- The A2A agent answers with retrieved documents and citations rather than LLM synthesis; swap `_answer()` in [server/app/a2a.py](server/app/a2a.py) for a model call when synthesis is wanted.
- Retrieval keeps an inverted index (term -> {doc_id: tf}) so a query scores
  only documents containing a query term, and a write updates a single document
  rather than rebuilding the corpus — appropriate while the corpus fits in
  memory. Callers that post-filter results must ask `SearchIndex.search` for
  `limit=None` and truncate afterwards: truncating in the index would discard
  candidates the filter would have kept. The ingest pipeline — chunking, other
  document types — is intentionally deferred; assets are embedded whole. See
  [Search](#search).
