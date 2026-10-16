> **Archived 2026-10-16.** B1-B5 landed; the read plane was complete by 2026-08-31. The prototype itself is superseded by cumulus-vision.

# Track B — the read plane: accepted implementation plan

Architect's plan, accepted 2026-08-30 with all three risk recommendations
adopted (Company/Vendor stay separate types through B2/B3; the connector
stays mechanical — no scoring, no precedence, no minting in `estate.py`;
mail lands as a Mailbox/MailMessage mirror capped at `body_preview`).
Builders implement their stage exactly; a stage that contradicts reality
gets flagged to the orchestrator, not improvised around. See WORKPLAN.md
for decisions of record and gates.

## Verified ground truth (file:line)

**Cumulus (`server/app/`)**

- Ingest is push-only: `POST /api/v1/sources/deliver` (`data_api.py:245-265`,
  `require_role(principal, "admin")` at :256) → `SyncService.deliver`
  (`sync.py:319-396`) builds one Event per feed shape → `Dispatcher.emit`
  (`dispatch.py:261-309`) appends the Event **before** starting runs →
  `TRIGGERS` (`dispatch.py:113-131`) binds five events to five windowed
  flows: `vendor-sync`, `purchase-order-sync`, `receiving-sync`,
  `workforce-sync`, `calendar-sync` (`sync.py:66-70`). No outbound HTTP
  anywhere in `server/app/` (`httpx 0.28.1` is in the venv only as a
  transitive dep of `google-genai`, not in `server/requirements.txt`).
- Seven fictional Mappings in `mappings.py:83-287`: `erp-vendors`,
  `procurement-coupa`, `legacy-ap-extract`, `erp-purchasing`,
  `hris-workday`, `calendar-m365`, `wms-receiving`. `validate_mappings`
  (`mappings.py:290-334`) refuses duplicate `source`, unknown
  types/collections/precedence.
- Hardcoded seed deliveries: `payables.py:2180-2333`
  (`_seed_vendor_deliveries`, `_seed_po_delivery`,
  `_seed_receiving_delivery`), `_seed_invoices` at `payables.py:2350+`;
  `workforce.py:49-296` (TEAMS/EMPLOYEES/capabilities) with
  `seed_workforce_if_empty` at :354; `calendars.py` mock delivery
  (`build_delivery` :362, `seed_calendars_if_empty` :428). Seeds fire from
  `deps.py:380` (calendars), `:511` (payables), `:514` (workforce).
  **Eval fixtures import the payables seed helpers**:
  `eval_specs.py:218-241` (`_demo_world`).
- Wire shapes and idempotency: `SyncRequest` (`sync.py:216-238`) carries
  `source`, `watermark`, `idempotency_key`; idempotent redelivery
  re-enters the same run (`sync.py:370-386`); byte-identical rows
  suppressed by payload hash (`_land`, `sync.py:1268-1314`).
- Resolution machinery is already type-generic:
  `resolve(graph, object_type, names, thresholds, tax_id=, source=,
  source_external_id=)` (`resolution.py:251-304`); vendor-only pieces are
  `STEWARD_QUEUE="vendor-stewardship"` (`sync.py:71`) and
  `apply_match_decision` which hard-validates `VendorRecord`
  (`sync.py:1318-1352`).
- Precedence is one global authority order (`ontology.py:678-680`):
  `human, erp, hris, calendar, procurement, flow, model`; feed name →
  authority mapping in `_authority` (`sync.py:1483-1507`).
- Ontology: 17 types (`ontology.py:187-591`), root types must declare
  competency questions + tags (enforced, `ontology.py:730-748`). Links
  `member_of`/`reports_to`/etc. were declared **before** their writer
  existed (`ontology.py:604-606`) — precedent for B3-before-B2.
- Tier-2 FlowDefinitions: only `expense-reimbursement` and
  `accounts-payable` are seeded (`flows.py:1129+`); the five sync flows
  have none (carried debt).
- `SourceStatus` is assembled from run journals, flow list hardcoded at
  `sync.py:1441-1446`; BFF row `SourceRow` at `ui_api.py:488-516`, joined
  to Mappings at `ui_api.py:552-596`.
- Background-thread precedent: `service.py:322-332`
  (`warm_embeddings_async`, plain `threading.Thread`).
- Test world = eval world: `tests/world.py` installs
  `eval_world.build_world` into `deps`; one session-scoped `TestClient`
  (`tests/conftest.py`). Nothing in `eval_world.py` may grow an estate
  dependency.

**Estate (`/workspaces/cumulus-demo-ecosystem`)** — verified live:

- Discovery: `GET https://cumulus-demo-mitchell-suter-admin.web.app/admin/namespaces`
  returns namespaces with `apps` + `pulse` (naviant runs
  `crm, erp, office, hris, dms`, pulse on/low).
- Every list route: `?updatedSince=<ISO>` filters `updatedAt >` (strict
  greater-than) and orders ascending by `updatedAt` — `crm.py:215-238`
  (`_list`), same pattern in every app. **No paging** — a delta streams
  whole. List envelope is `{"<entity>": [...]}`. Deletes invisible.
- CRM entities `crm.py:59+` (accounts/contacts/opportunities,
  Salesforce-ish ids `001/003/006`); ERP `erp.py:88-142`
  (customers/vendors/items/salesOrders/purchaseOrders; lines
  `{itemId, quantity, rate, amount}` with server-computed `total`,
  `erp.py:233-272`; **line `itemId` is the internal record id, not the
  SKU** — live SO shows `"itemId": "3000"`); Office `office.py:73-119`
  (mailboxes/messages/events; messages carry
  `from/toRecipients/conversationId/receivedDateTime`); HRIS
  `hris.py:235-282` (workers keyed `EMP-xxxx`, `workEmail`,
  `managerId`/`departmentId`/`jobId` refs; `/employmentEvents` append-only
  ledger rows carry new **and** previous values, `hris.py:1067-1090`);
  DMS `dms.py` (folders/documents; document metadata embeds versions;
  `GET /documents/<id>/content` for bytes).
- The estate has **no receiving app** — `wms-receiving` has no real
  counterpart. PO statuses ("Partially Received") are the closest fact.
- A sixth app (`psa`) is coming. Nothing below hardcodes "five sources" —
  see the FEEDS registry extension point.

---

## 1. Where estate registration lives

**Machinery-config (Connector, not map):** env in `config.py` —

```
ESTATE_PROJECT            # e.g. cumulus-demo-mitchell-suter → https://{project}-{app}.web.app
ESTATE_URL_TEMPLATE       # optional override of the URL shape
ESTATE_NAMESPACE          # e.g. naviant; empty → the discovery default
ESTATE_PULL_INTERVAL_SECONDS  # 0 = loop off (default; tests and deployed form)
CONNECTOR_STATE_COLLECTION    # default "connector_state"
CONNECTOR_DELIVER_URL     # default settings.public_base_url — the HTTP door
CONNECTOR_API_KEY         # machine credential for /sources/deliver; empty in dev (AUTH_BACKEND=none)
```

**Map (Mapping, portable):** per feed — source id, system description,
payload shape name, identity bindings, precedence authority, freshness
("pull, delta by updatedAt watermark"), custody, collection. The Mapping
never names a URL, a project id, or a credential (Lens 8 invariant:
adapter choices stay below the portability line).

**Watermark/cursor state:** a dedicated machinery store, `ConnectorState`
— one document per feed: `{feed, watermarks: {resource: iso},
last_pull_at, last_outcome, last_error, next_pull_at}` in the
`connector_state` collection (memory/Firestore, the usual dual pattern).
Explicitly **not** a Dataset and not in `data_collections.COLLECTIONS`
(DOCTRINE.md:203 — machinery/operational state is not a Dataset). Why not
derive it from runs like `sources()` does: a pull that returns an empty
delta starts **no run** (`deliver` skips empty shapes, `sync.py:346-347`),
and a pull that **failed** has no run at all — the cursor and the failure
must survive both. `SourceStatus` stays run-derived; C1 joins the two.

## 2. Connector module design (`server/app/estate.py`, new)

One class `EstateConnector` + one module-level **`FEEDS` registry — the
extension point the psa app lands in**:

```python
@dataclass(frozen=True)
class FeedSpec:
    feed: str                # the SyncRequest.source, e.g. "hris-workforce"
    app: str                 # estate app id from /admin/namespaces: crm|erp|office|hris|dms|psa…
    resources: tuple[str, ...]   # list routes pulled together, e.g. ("departments", "jobs", "employees")
    build: Callable[[dict[str, list[dict]]], SyncRequest]  # mechanical field mapping only

FEEDS: tuple[FeedSpec, ...] = (...)   # closed table, same idiom as dispatch.TRIGGERS / gateway.ACTIONS
```

At startup (and on demand) the connector calls `GET
{admin}/admin/namespaces`, finds `ESTATE_NAMESPACE`, and activates only
the FeedSpecs whose `app` is in that namespace's `apps` list. Adding
`psa` later = one FeedSpec + wire shapes + one Mapping + one flow; zero
connector redesign.

- **Pull:** per feed,
  `GET {base}/{app}/api/{ns}/v1/{resource}?updatedSince={watermark[resource]}`
  for each resource in the family. Multi-resource feeds are
  all-or-nothing: any GET failure aborts the feed without advancing any
  watermark.
- **Watermark semantics:** the estate filters strictly `>` and orders
  ascending, so next watermark = max `updatedAt` observed per resource. No
  overlap window needed for correctness at this scale; redelivery is safe
  anyway (payload-hash `unchanged` suppression + upserts), so the
  connector may re-pull the same watermark freely. Empty delta → no
  delivery, no run, state's `last_pull_at` still advances. Deletes
  invisible — accepted.
- **Chunking (the "paging" that actually exists):** estate lists are
  unpaged; cap a delivery at ~500 records, split on `updatedAt`
  boundaries, one POST per chunk, watermark advances per acknowledged
  chunk.
- **Idempotency:** `idempotency_key = f"{feed}:{max_updated_at}:{chunk_ordinal}"`
  — a retried identical pull re-enters the same runs (`sync.py:370-386`)
  and reports `replayed: true`.
- **Delivery through the HTTP door, never in-process:** `httpx.Client`
  POST to `{CONNECTOR_DELIVER_URL}/api/v1/sources/deliver` with
  `X-API-Key: CONNECTOR_API_KEY`. The key is a minted machine credential
  (identity role `admin` is assignable, `identities.py:37`; `deliver`
  requires `admin`, `data_api.py:256`). Dev (`AUTH_BACKEND=none`) needs no
  key. Add `httpx>=0.28` to `server/requirements.txt`.
- **Error handling:** estate down / 5xx / timeout → record in
  `ConnectorState.last_error`, do not advance, next tick retries; a
  non-202 from the door → same. Sync-now surfaces the error as a 502 with
  detail. No Task is raised per failure — a feed that stays red for N
  ticks is a C3 dashboard fact first (per-row escalation would violate the
  windowed-flow default, DOCTRINE.md:201).
- **Event emission:** the connector deliberately emits nothing itself. The
  door's `deliver()` → `Dispatcher.emit` already appends the `…Delivered`
  Event before the run exists — "a completed pull is an Event" is
  satisfied at the chokepoint.
- **Scheduling (Lens 8 language):** Scheduling is a sourced capability
  (DOCTRINE.md:435, :445 — no continuously-warm component of our own). The
  deployed form is Cloud Scheduler POSTing `/api/v1/estate/pull` on an
  interval — the scheduler is the adapter, the endpoint is the port. The
  in-server `threading.Thread` loop (pattern: `service.py:322`) exists
  **only** as the dev adapter for that port, gated by
  `ESTATE_PULL_INTERVAL_SECONDS > 0`, default off, never on in tests.
  "Sync now" = `POST /api/v1/estate/feeds/{feed}/sync-now` (admin) runs
  one pull synchronously and returns the delivery outcomes.

New API surface (`estate_api.py`): `GET /api/v1/estate` (registration +
discovery echo + per-feed state), `POST /api/v1/estate/feeds/{feed}/sync-now`,
`POST /api/v1/estate/pull` (pull all due feeds — the scheduler's target).

## 3. SyncRequest extension, feeds, and flows

Feed table (each row: one Mapping, one Event, one Trigger, one flow, one
Tier-2 FlowDefinition):

| Feed (source id) | App/resources | Event | Flow | Strategy |
|---|---|---|---|---|
| `hris-workforce` | hris: departments, jobs, employees | `WorkforceDelivered` (existing) | **existing `workforce-sync`**, extended | bind |
| `hris-employment-events` | hris: employmentEvents | `EmploymentEventsDelivered` (new) | new `employment-event-sync` (landing-only) | reify → SourceRecord |
| `erp-vendors` | erp: vendors | `VendorRecordsDelivered` (existing) | **existing `vendor-sync`**, unchanged | reify |
| `erp-purchase-orders` | erp: purchaseOrders | `PurchaseOrdersDelivered` (existing) | **existing `purchase-order-sync`** | reify |
| `erp-sales` | erp: customers, items, salesOrders | `SalesRecordsDelivered` (new) | new `sales-sync` | reify (customers→Company), bind (items, orders) |
| `crm-book` | crm: accounts, contacts, opportunities | `CrmBookDelivered` (new) | new `crm-sync` | reify (accounts→Company, contacts→Person) |
| `office-calendar` | office: mailboxes, events | `CalendarsDelivered` (existing) | **existing `calendar-sync`** | bind |
| `office-mail` | office: mailboxes, messages | `MailDelivered` (new) | new `mail-sync` | bind |
| `dms-library` | dms: folders, documents | `DocumentsDelivered` (new) | new `dms-sync` | bind, custody=**reference** |

`receiving-sync` keeps its flow and wire shape (evals/AP tests exercise
it) but loses its Mapping at B4 — the estate has no receiving system; an
honest gap, not a mock to fake.

**New wire shapes on `SyncRequest`** (`sync.py:216-238` grows; all lists
default-empty so the door stays backward compatible):

- CRM: `AccountRecord {external_id, name, industry, website, phone}`;
  `ContactRecord {external_id, first_name, last_name, title, email, phone,
  account_external_id}`; `OpportunityRecord {external_id, name,
  account_external_id, stage, amount, close_date}` — travel together
  (refs), like teams+employees (`sync.py:230-233`).
- ERP sales: `CustomerRecord {external_id, name, email, phone,
  payment_terms}`; `ItemRecord {external_id, sku, display_name, item_type,
  base_price}` (SKU is the natural key, `external_id` is the internal id
  the lines reference); `SalesOrderRecord {external_id,
  order_number(tranId), customer_external_id, ordered_date, status, memo,
  total, lines: [SalesOrderLineRecord {item_external_id, quantity, rate,
  amount}]}`.
- Office: `MailboxRecord {external_id, address, display_name, job_title,
  department, timezone}`; `MailMessageRecord {external_id,
  mailbox_address, subject, folder, from_email, from_name, to_emails,
  cc_emails, body_preview, importance, conversation_id, received_at,
  sent_at}` (body → preview only).
- HRIS: extend `EmployeeRecord` (`sync.py:153-169`) with
  `manager_external_id`, `department_external_id`, `status`,
  `employment_type`, `work_location` (optional, additive);
  `EmploymentEventRecord {external_id, employee_external_id, event_type,
  effective_date, payload}` (the row as-is, landed whole).
- DMS: `FolderRecord {external_id, name, parent_external_id}`;
  `DmsDocumentRecord {external_id, name, folder_external_id, content_type,
  size, latest_version, storage_ref}` where `storage_ref` is the
  customer-resolvable content URL (`…/documents/<id>/content?version=n`)
  — a portable reference, per DOCTRINE.md:326.

**Where the mapping logic sits:** the connector's `build` functions do
**mechanical** renames only (`companyName`→`name`, `tranId`→`order_number`);
everything semantic — identity scoring, external-id → object resolution,
org wiring, precedence — lives in the flows. One sanctioned exception,
documented per Mapping: dereferencing an id to the Mapping's declared
natural key when the pulled page doesn't contain the target (e.g. a delta
employee whose `managerId` isn't in the batch → one `GET /employees/{id}`
for the `workEmail`). Flow-side resolution of external ids reuses the
in-batch index + `source_external_id` path `resolve()` already has
(`resolution.py:259`, method "source-link").

**Flow bodies:** don't grow `sync.py` past ~1,500 lines. Keep `sync.py`
as wire shapes + `SyncService` + landing + the five existing bodies; new
bodies in `sync_commerce.py` (crm-sync, sales-sync), `sync_office.py`
(mail-sync), `sync_dms.py` (dms-sync; the employment-event body may sit
with it or in sync.py). Each exposes `register(service: SyncService)`
called from `SyncService.__init__` (`sync.py:294-299`); they use
`service._act`/`service.graph` so every write stays gatewayed. Replace the
hardcoded `flows` dict in `sources()` (`sync.py:1441-1446`) with a
`FEED_FLOWS` registry declared beside the event names — one place new
feeds register for status.

**Tier-2 FlowDefinitions (carried debt paid):** `flows.py:_seed_definitions`
(`flows.py:1129`) gains a definition per sync flow — trigger, land,
resolve, apply, event steps matching the real `ctx.step` ids
(`t1/x1/s1/z1/a1/e1`, `sync.py:20-25`). Nine definitions total.

## 4. B3 ontology outline (general-first)

New root types (each justified by a feed or the demo script; keys are
natural keys, ids minted):

- **`Company`** — canonical counterparty. Natural key: normalized legal
  name (same normalization as Vendor). Claims from `crm` (accounts:
  industry, website, phone) and `erp` (customers: terms, email).
  Competency questions: "Which systems hold a record for {label}?", "What
  have we sold {label} and what is open?". Collection: new
  `company-master` (mirror of `vendor-master`, `data_collections.py:137`).
  Justified by: crm-book + erp-sales feeds, the identity demo.
- **`Person`** — an external human (CRM contact, mail correspondent).
  Natural key: lowercased email; fallback `source:external_id` when email
  is absent (the Thomas Berg duplicate **must** mint separately so B5 can
  propose the merge). Link `works_at` Person→Company (many-to-one,
  association). Deliberately distinct from Employee (internal,
  HRIS-authoritative) — an invitation is not employment
  (`sync.py:996-998` precedent).
- **`Opportunity`** — natural key: crm opportunity id (source-scoped).
  Fields: stage, amount, close_date. Link `pursued_with`
  Opportunity→Company. Justified by deal_to_order and S2a.
- **`SalesOrder`** + part **`SalesOrderLine`** (composition `has_so_line`,
  mirroring `has_po_line`, `ontology.py:649`). Natural key: tranId. Link
  `sold_to` SalesOrder→Company. Justified by erp-sales + the S2b
  write-back target.
- **`Item`** — natural key: SKU. Lines carry `item_external_id` resolved
  via source-link. Justified by order lines in two feeds.
- **`Mailbox`** + part **`MailMessage`** (composition `has_message`) — the
  mail-as-evidence shape: mirror the provider the way
  Calendar/CalendarEvent already does (`ontology.py:297-334`), headers +
  `body_preview` only, **not** a full mail store; `sent_by`/`addressed_to`
  links to Person/Employee resolved by email, unresolved counted never
  minted. Collection: new `correspondence`.
- **`Folder`** — DMS tree; `filed_under` Folder→Folder and
  Document→Folder (the doctrine's own example, DOCTRINE.md:203).
  Collection: new `document-library`.
- **Document is reused, not duplicated** for DMS files: rows carry
  `collection_id="document-library"` (the per-instance field exists,
  `ontology.py:394-396`), custody=reference via a new optional
  `storage_ref` field; natural-key note widens to "content hash; or
  source+external id for referenced-in-place originals".

**Deliberately NOT added:** Department (Team absorbs it — slug from name),
Job/Position (a `title` claim resolved from the delivered jobs list
in-flow), EmploymentEvent-as-type (lands as SourceRecord),
PayRun/Payslip/BenefitPlan/TimeOff/Applicant/Requisition (no feed, no
process this wave — and Cumulus's own PTO store in `employee.py` must not
collide), Lead/Campaign, MailAttachment, a "Customer" type (Company + a
`sold_to` edge is the general form).

**Precedence** (`ontology.py:678-680`) grows two authorities:
`human, erp, hris, crm, calendar, office, procurement, flow, model` — ERP
over CRM for commercial facts (terms, totals), CRM the voice for
relationship facts (it usually speaks unopposed: industry/website only
come from `crm`); `office` low: mail-derived facts are hints. `_authority`
(`sync.py:1483-1507`) gains `crm`/`office` branches. When CRM and ERP
disagree on a Company field both claims are retained and the order
resolves (`graph.py:288-296`) — the demo's disagreement story is a read of
retained claims, not a data loss.

**Identity links:** `identified_as` is currently typed Vendor→SourceRecord
(`ontology.py:636-639`). Add sibling declarations `Company→SourceRecord`
and `Person→SourceRecord` rather than widening one link's from_type —
keeps cardinality checks honest.

## 5. Identity-resolution seams for B5

Generalize, don't parallel. `resolve()` is already type-parameterized;
B2's crm-sync and sales-sync call `resolve(graph, "Company", …)` exactly
as vendor-sync does, landing SourceRecords + identity links. Steward
queue: keep `vendor-stewardship` for vendors, add `data-stewardship` for
Company/Person review Tasks; the queue is named per Mapping (a new
`Mapping.steward_queue` field) so B5/C4 can rebind it.
`apply_match_decision` (`sync.py:1318-1352`) is the one vendor-hardcoded
piece — B2 does *not* fix it wholesale but routes on `record_kind` from
the landed SourceRecord so a Company match task can resolve; B5 owns full
generalization plus the cross-**source** company-match proposer (a
windowed flow scoring Company↔Vendor and Person↔Person candidates,
emitting in_review identity links + Tasks) and the EvalSpec built from the
roster answer key (`docs/namespaces/naviant/roster.md`, bait pairs
included as must-not-link). Nothing in B2 asserts cross-source merges
automatically — it lands per-source claims and reified rows, which is
exactly the substrate B5 grades against.

## 6. Console read-back (C1 outline)

`SourceRow` (`ui_api.py:488-516`) gains `pull: PullState | None` —
`{registered, estate_app, namespace, watermark(s), last_pull_at,
next_pull_at, last_error, sync_now_allowed}` joined from `ConnectorState`
by feed id in the `/api/ui/v1/sources` BFF (`ui_api.py:552-596`). One new
BFF action: `POST /api/ui/v1/sources/{source}/sync-now` proxying
`estate_api`. Estate registration renders as a header card from
`GET /api/v1/estate`. The existing "contract=None means an undeclared
feed" finding (`ui_api.py:505-507`) is preserved — it is the screen's job.

## 7. Test strategy

- **Fake estate, not the network:** `tests/estate_fixtures.py` — recorded
  response dicts per (app, resource), shapes copied from live probes.
  `EstateConnector` takes an injectable `fetch` (or `httpx.MockTransport`);
  tests drive pulls against the fixture and a `deliver` callable pointed
  at the session `TestClient` — the HTTP door is exercised, not bypassed.
- **Connector unit tests (written first):** watermark advance per
  resource; strict-`>` semantics (a re-pull of the same watermark lands
  all-`unchanged`); empty delta → no delivery, state still stamped; chunk
  splitting + per-chunk idempotency keys; failure → no watermark advance +
  `last_error`; discovery filters feeds by the namespace's `apps` list
  (the psa extension point, tested by adding a fake sixth app).
- **Flow tests:** per new flow, the `test_workforce.py`/`test_calendar.py`
  pattern — deliver a fixture SyncRequest through `world.sync.deliver`,
  assert objects/links/claims/authorities, unresolved counted not minted,
  redelivery unchanged.
- **Live smoke (gate, not CI):** `pytest -m estate_live` (skipped unless
  `ESTATE_PROJECT` set) — discovery returns naviant; one `updatedSince`
  pull per app asserts envelope keys; plus a `sync-now` of
  `hris-workforce` against a dev server landing ≥16 employees.
- **eval_world untouched:** `estate.py` is imported by `main.py`/`deps.py`
  only; `eval_world.build_world` gains no connector. The interval loop is
  off by default, so the session TestClient never pulls.
- **Boundary guard:** a test asserting `estate.py` imports nothing from
  `graph.py`/`resolution.py`.

## 8. What gets deleted, staged

| Stage | Deleted | ~lines |
|---|---|---|
| B1 | nothing | 0 |
| B2 | `mappings.py` rows `erp-vendors`/`erp-purchasing`/`hris-workday`/`calendar-m365` **rewritten in place** to the real systems; `procurement-coupa`, `legacy-ap-extract`, `wms-receiving` marked `notes="retires at B4"` but kept (their seed deliveries still run) | ~40 net |
| B4 | `payables.py:2180-2506` seed deliveries + seed invoices (~330); `workforce.py:49-352` TEAMS/EMPLOYEES/capability seed (~300 of 413 — `OPERATOR_EMAIL` and `sync_workforce_additions` survive or move); `calendars.py` mock delivery + seeds (~450 of 734); fictional Mapping leftovers (~75); `deps.py:380,:511,:514` seed calls; the Tomas-Berg persona collision in `personas.py` | ~1,200 |
| B4 (moves) | `eval_specs.py:_demo_world` (:212-241) stops importing payables helpers — the delivery dicts are **copied into `eval_specs.py`** as fixture data (eval worlds keep working verbatim); `test_payables.py` re-seeds through that fixture; `test_workforce.py`/`test_calendar.py` seed via explicit in-test fixture deliveries | ~150 |

---

## Stages (one builder each; parallel where marked)

**Stage 1 — B1: connector + first feed (Wave 1, now; parallel with Track
A/C1).** Files: new `server/app/estate.py` (~350), new
`server/app/estate_api.py` (~120), `config.py` (+8 env keys), `main.py`
(router + lifespan hook after `get_sync_service()`, before "Startup
complete", `main.py:95-134`; loop start only when interval > 0), `deps.py`
(+`get_estate_connector`), `server/requirements.txt` (+`httpx>=0.28`),
`mappings.py` (append one `hris-workforce` Mapping, transport `pull` —
additive, zero conflict with Stage 3's rewrite), new
`tests/estate_fixtures.py`, new `tests/test_estate.py`. First feed:
`hris-workforce` translated to the **existing** `TeamRecord`/
`EmployeeRecord` shapes (departments→teams, jobs→title, managerId→email
dereferenced in the pulled page) so nothing in `sync.py` changes and it
lands against the live 16-person estate today. Tests first (§7). Deletes:
nothing.
**Gate:** `pytest` green; live smoke: `sync-now` on `hris-workforce` → 16
employees + org links in the graph beside the untouched mock world;
Sources data shows the feed under its contract with a watermark.

**Stage 2 — B3: ontology + collections + precedence (parallel with Stage
1; blocking for 3a/3b/3c; one builder).** Files: `ontology.py` (+9 types,
+~10 links, precedence +`crm`/`office`), `data_collections.py`
(+`company-master`, `correspondence`, `document-library`,
`sales-records`), `sync.py:_authority` (+2 branches),
`tests/test_graph.py`/`test_mappings.py` conformance additions. Declared
ahead of their writers — the validated precedent at `ontology.py:604-606`.
Deletes: nothing.
**Gate:** `validate_ontology() == []`, `validate_mappings() == []`, pytest
green, ontology export renders.

**Stages 3a/3b/3c — B2 feeds (parallel after Stage 2; merge order 3a → 3b
→ 3c, each appends to `SyncRequest`, `TRIGGERS`, `MAPPINGS`, `FEEDS`,
`FEED_FLOWS`):**

- **3a — commerce:** `sync.py` (+CRM/ERP-sales wire shapes, deliver plan
  rows), new `sync_commerce.py` (crm-sync, sales-sync bodies),
  `dispatch.py` (+2 Triggers), `mappings.py` (rewrite `erp-vendors`/
  `erp-purchase-orders` rows to the real ERP; +`crm-book`, +`erp-sales`),
  `estate.py` FEEDS (+3 FeedSpecs incl. erp-vendors/erp-purchase-orders
  onto the existing flows), `flows.py` FlowDefinitions (vendor, po, crm,
  sales), `sync.py:sources()` registry refactor lands here. Tests:
  `tests/test_sync_commerce.py`. **Gate:** fixture pull of the live
  CRM/ERP shapes lands Companies with retained crm+erp claims; a PO delta
  links vendors by source-link; pytest green + live smoke.
- **3b — office:** `sync.py` (+mail shapes), new `sync_office.py`
  (mail-sync), `mappings.py` (+`office-mail`, replace `calendar-m365` with
  `office-calendar`), connector FeedSpecs (+2; office-calendar builds
  `CalendarRecord`s from mailboxes), FlowDefinitions (calendar, mail).
  Tests: `tests/test_sync_office.py`. **Gate:** live mailbox pull lands
  Mailbox/MailMessage + Calendar/CalendarEvent, externals counted not
  minted.
- **3c — dms + hris ledger + workforce hardening:** new `sync_dms.py`
  (dms-sync reference-custody, employment-event landing), `sync.py`
  EmployeeRecord extension + workforce body reads the new bind columns
  (retiring Stage 1's connector-side manager dereference to the
  fallback-only case), FlowDefinitions (workforce, receiving,
  employment-events, dms). Tests: `tests/test_sync_dms.py`,
  `test_workforce.py` additions. **Gate:** DMS pull renders the folder
  tree as Folder objects + filedUnder; `storage_ref` resolves live;
  employment events land as SourceRecords.

**Stage 4 — C1 read-back (parallel with 3b/3c once Stage 1 is merged;
Track C owns it):** `ui_api.py` SourceRow + sync-now proxy + estate card;
Flutter Sources screen.

**Stage 5 — B4 mock-world replacement (Wave 2 close; one builder):** the
deletion table in §8; personas rebuilt from the Naviant cast (operator
stays `masuter@naviant.com`, `workforce.py:42`); eval fixtures re-homed
into `eval_specs.py`. **Gate:** a cold boot with `ESTATE_PROJECT` set
fills the world only from pulls; pytest green with eval fixtures
byte-identical in behavior; prune pass.

**Stage 6 — B5 (Wave 3; per §5):** company/person match-proposer flow,
generalized `apply_match_decision`, identity EvalSpec from the roster
answer key with bait pairs as must-not-link.

## The three risk decisions (adopted)

1. **Company and Vendor stay separate types through B2/B3.** Vendor is the
   AP-side role object; Company is the counterparty book; B5 proposes
   typed identity links between them. Revisit subsumption only if a real
   dual-role entity appears in Track A's seed.
2. **The connector does mechanical renames + declared-key dereference;
   flows do everything semantic.** No scoring, no precedence, no minting
   in `estate.py`; every FeedSpec dereference appears in its Mapping's
   notes; enforced by the import-boundary test.
3. **Mail is a Mailbox/MailMessage mirror (headers + preview).** Reuses
   the Calendar/CalendarEvent weak-entity pattern; custody capped at
   `body_preview`; if 3b runs long, land mailboxes-only first.
