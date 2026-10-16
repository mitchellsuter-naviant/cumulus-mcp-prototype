# WORKPLAN-REAL — Make the system work

**Status: RATIFIED 2026-09-02 — D1–D6 accepted with no overrides. Wave 1 launched.**
Author: orchestrator session · Inputs: two read-only audits (backend data
seams; console/DAG rendering seams) run against the tree as of the
versioned-task-packages reorg.

---

## 1 · Framing

The audit's headline: **the spine is real, the edges are fake.** The estate
connector pulls ten real feeds from the demo ecosystem; the sync flows land
them without invention; vendor resolution, the three-way matcher, the anomaly
screen, the document store (with a working bytes-serving route), the Vertex
readers, and ModelCall metering into the run journal all exist and work. What
is fabricated is concentrated in exactly the places a demo watcher looks:

- **Intake.** Invoices are composed in the console from a scenario grammar
  (`InvoiceScenario` hint strings) instead of arriving from the customer's
  systems. The composer path stores a **zero-byte** document.
- **Extraction (locally).** `MODEL_BACKEND` defaults to `none`
  ([config.py](server/app/config.py) `:264`), under which `StubInvoiceReader`
  fabricates the whole extraction from a hash of filename+hint —
  `_AP_VENDORS` and `CATALOG` are its invented vocabulary. Deploys default to
  `vertex` ([deploy.sh:114](deploy/deploy.sh#L114)) — but `deps.py:510,550`
  **silently fall back to the stub** if Vertex construction throws.
- **The receiving leg.** No receiving reports exist anywhere: the ecosystem
  ERP has no such entity and `estate.py`'s `FEEDS` has no receiving feed. The
  three-way match is structurally two-way in production.
- **Vocabulary.** `_AP_VENDORS`, `CATALOG`, `SCENARIO_TWISTS`, expense
  categories, and the personas/queue roster are hard-coded copies of what the
  graph (synced `Vendor`/`Item` objects), the ontology, and the HRIS feed
  already provide.
- **Evidence.** Real bytes, when they exist, are served by
  `GET /api/v1/documents/{id}/content` — but the console has no PDF viewer,
  step tiles don't link to their evidence, region boxes are fetched and never
  drawn, and the only regions ever written are the stub's synthetic geometry
  (the Vertex readers emit none).

### The golden thread (the acceptance test for "works")

One invoice, end to end, with no fabricated step:

1. The ecosystem's `procure_to_pay` pulse files a vendor invoice as a **real
   PDF** into the DMS Accounts Payable folder (real bytes, the vendor's own
   drifted spelling).
2. `dms-sync` lands the document reference; a new **`ap-intake`** flow starts
   one `accounts-payable` run per new AP-folder invoice, fetching the real
   bytes through the customer-resolvable reference and storing them
   (content-hash id).
3. `extract-invoice` is a real, metered ModelCall over those bytes, and it
   reports **a confidence per field**.
4. Deterministic **line-item and total arithmetic checks** run over the
   extraction — a pure Rule, no fetching — and every failure names the field
   path it implicates.
5. Low-confidence ∪ arithmetically-inconsistent fields become the
   **field verdicts** — the only fields a human is asked about. A second,
   cheap ModelCall (`gemini-3.7-flash`) locates **only those fields** and
   stores their bounding boxes as Regions.
6. `resolve-vendor` matches the printed name against the **ERP-synced vendor
   master** — the drifted spelling exercises real identity resolution.
7. `three-way-match` pairs the lines against the **synced purchase order and
   the synced receiving report**.
8. The governed posting policy (revised: auto-post limit **$0**) routes
   **every** invoice to `review-invoice`.
9. The clerk opens the **new invoice-review screen**: the rendered PDF with
   boxes drawn on exactly the disputed fields, beside the editable
   extraction, and decides. Their corrections append to the **correction
   set**.
10. The posting lands in the graph and the AP ledger; the run detail draws the
    DAG with the run path over **the version that ran**, every step's evidence
    is clickable, and every model invocation appears in the **ModelCall
    ledger** with real tokens and cost.

---

## 2 · Ratified decisions

| # | Decision | Ruling |
|---|---|---|
| **D1** | Where invoices come from | **DMS AP folders** via a new `ap-intake` flow. Console upload stays a manual side door; the scenario composer moves to the eval/dev world and leaves the production console. |
| **D2** | Receiving reports | **Build them** — ERP entity + API in the ecosystem, a `goods received` sim step, one `FeedSpec` in `estate.py`. |
| **D3** | Extraction honesty | Silent stub fallback becomes **loud**; the stub requires explicit `MODEL_BACKEND=stub`. Synthetic regions never ship in production — regions come from the locate step (§WP-J) or are absent. |
| **D4** | Vocabulary source of truth | Composer and screens read the **graph** (`Item`, `Vendor`); categories single-sourced; personas/queues from the queue registry + HRIS-landed `Employee` objects; bounded literal-string sweep to `ONTOLOGY`. |
| **D5** | Dynamic screen registration | Version packages declare `UI = {kind → screen_id, label, hint}`; a server manifest does **server-side version resolution**; Dart keeps compile-time `screen_id → widget factory` and degrades gracefully. |
| **D6** | Every invoice to review | A **governed knowledge revision** (auto-post limit → $0). No code change; v7's perfect-match allowance stays shipped but dormant. |

### 2b · Review answers (2026-09-02)

**Q1 — overrides:** none.

**Q2 — canvas polish (back-edges, barycentric ordering): parked.** WP-E ships
the version-trace fix, clickable step evidence, and the PDF/region viewer
only. Cycle rendering and crossing minimisation move to a follow-up.

**Q3 — ModelCall ledger + trajectories + the ontology.** Own collection,
approved. On "does all of this make its way to the ontology?" — **no, and
that is the design.** Three homes:

- *Run journals / task-run trajectories* stay **machinery state** in the
  engine's store. Doctrine, Lens 2: "machinery/operational state (flow run
  journals, the trigger index, queues) is not a Dataset." They never become
  graph Objects.
- *The ModelCall ledger* is a **sibling of the curation ledger**: append-only,
  customer-owned, exportable, keyed by `run_id`/`step_id`/`case_ref`. Its own
  collection, not graph Objects.
- *What does reach the map*: the business facts a run wrote (`Invoice`,
  `InvoiceLine`, `MatchSet`, vendor links), the human judgment (the curation
  ledger — `gateway.LedgerEntry`, which exists), and the **correction set**,
  which **does not exist today**. Doctrine Lens 4 calls it a crown-jewel
  eval/tuning asset; WP-J's field verdicts produce exactly the right
  raw→corrected pairs, so WP-J closes that gap (see J4).

The join is by reference, not by containment: the graph is the referencing
spine, so an `Invoice` names the run that produced it and the run names the
documents and knowledge it read — traversable both ways without stuffing
journals into the graph.

**Q4 — volume:** **seed depth over pulse speed.** The pulse rate is already a
dial; what is thin is history. WP-A deepens the seed (more vendors, items,
POs, receiving reports, and *historical* invoices), which also gives the
anomaly screen genuine per-vendor payment history to profile against.

---

## 3 · Work packages

**Gate** = what must be demonstrably true before the lane merges (suites green
is implied everywhere).

### WP-A · Estate truth — repo: `cumulus-demo-ecosystem`
- **A1** ERP receiving reports: `erp_receiving_reports` entity + REST +
  `updated-since`, a `goods received` sim step in `procure_to_pay` (before the
  invoice files), and a seed backfill against existing POs.
- **A2** Real invoice artifacts: the sim files **text bytes with a `.pdf`
  name** today (`sim_naviant.py:1161`). Generate a real, plainly-laid-out PDF
  per filed invoice (number, PO, lines, totals consistent with the ERP items
  the sim reads back). Plain layout on purpose — it is what the extractor
  reads.
- **A3** Seed depth (Q4): more vendors/items/POs, receiving reports, and a
  back-dated invoice history per vendor.
- **A4** Deploy the estate; verify the DMS content endpoint serves
  `application/pdf`.
- **Gate:** every AP vendor folder holds ≥1 real PDF invoice; the ERP serves
  receiving reports with watermarks; each AP vendor has a payment history.

### WP-B · Real intake — repo: cumulus
- **B1** New `app/tasks/ap_intake/v1/`: woken by `DocumentsDelivered`, finds
  new documents under the AP folder taxonomy, fetches bytes through the stored
  customer-resolvable reference, stores them (`put_bytes`), emits
  `InvoiceReceived{document_id}` per case.
- **B2** `accounts-payable` **v8** package: the body starts from a
  `document_id` — no hint, no scenario; extraction reads stored bytes;
  synthetic region geometry leaves the production path.
- **B3** Scenario composer becomes eval/dev-world-only; upload endpoint stays.
- **B4** `deps.py:510,550` silent stub fallbacks become loud.
- **Gate:** a synced or uploaded PDF drives a run whose extraction is a real
  metered ModelCall; no production path can fabricate an extraction.

### WP-C · Receiving feed — repo: cumulus (pairs with A1)
- One `FeedSpec` in `estate.py` (`erp-receiving`, rename-only builder);
  `receiving-sync` and the matcher untouched; `data_collections` updated.
- **Gate:** the golden-thread invoice matches on all three legs.

### WP-D · Ontology as the reference — repo: cumulus
- Delete `_AP_VENDORS` + `CATALOG` from production paths (stub/test fixtures
  only); composer reads `graph.find("Item")` / `graph.find("Vendor")`; expense
  categories single-sourced; personas/queues from the queue registry +
  `Employee` objects; `ONTOLOGY` sweep over the audit's bounded list.
- **Gate:** no invented business vocabulary in `app/` outside explicitly
  stub-marked modules (grep-checkable).

### WP-E · Run evidence — repo: cumulus (mostly Flutter)
- **E1** `FlowRun`/`RunSummary` carry `flow_version`; run tracing overlays
  **the run's own version** (today it traces over the *active* version —
  `flow_detail_screen.dart:439`).
- **E2** Step tiles get clickable evidence: `RefChips` for refs/writes, a
  collapsible output block, document click-through.
- **E3** Document drawer: PDF rendering (blob URL from the fetched bytes),
  region boxes drawn from the normalized coordinates, duplicate no-bytes
  drawer in the AP workspace collapsed onto the real one.
- **E4** *(parked per Q2)* canvas back-edges, barycentric ordering.
- **Gate:** click a run's extract step → the invoice PDF renders, with its
  regions drawn when present.

### WP-J · Field verdicts: confidence, arithmetic, targeted boxes — repo: cumulus
**Task-specific logic, not platform.** It lives in
`app/tasks/accounts_payable/v8/` and is developed in tandem with WP-F's
screen, against the contract in §4. The platform contributes only the
`Region` type, ModelCall metering, and the Task envelope.

- **J1** `extract-invoice` reports **per-field confidence** (prompt revision —
  governed knowledge — plus the parse).
- **J2** New deterministic step `check-arithmetic` (kind `check`, a pure Rule
  — gathers nothing): per-line `qty × unit_price ≈ amount`, lines sum ≈
  subtotal, `subtotal + freight + tax ≈ total`, required fields present, dates
  sane. Every failure names the **field path** it implicates. Tolerance is
  governed knowledge, not a literal.
- **J3** New step `field-verdicts` (transform): low-confidence ∪ inconsistent
  ∪ missing → the fields a human is asked about. Then `locate-fields`, a
  second ModelCall on **`gemini-3.7-flash`**, which receives the document plus
  *only those field paths* and returns their bounding boxes → stored as
  `Region`s (which already carry a canonical `field` path). **Skipped
  entirely when nothing needs review** — no boxes, no call, no cost.
- **J4** The clerk's corrections append to a **correction set** (knowledge-role
  Dataset): `{field path, raw value, corrected value, document ref, decided
  by, at}`. Closes the flywheel gap named in Q3. Appending is automatic;
  promotion into the prompt/config stays a governed decision, never automatic.
- **Gate:** a clean invoice raises no field verdicts and makes **one** model
  call; a bad-arithmetic invoice raises verdicts naming exact field paths and
  makes **two**, the second returning boxes only for those fields.

### WP-F · The invoice review screen — repo: cumulus (Flutter + seed)
- `lib/tasks/accounts_payable/v8/invoice_review.dart`: the rendered PDF with
  boxes on exactly the disputed fields, beside the editable extraction and the
  match panel; the disputed fields are what the screen focuses, the rest is
  collapsed context. Approve / correct-and-recheck / reject.
- Ships with D6's governed policy revision (auto-post limit $0).
- **Gate:** every new invoice raises a review task that opens this screen, and
  a field verdict highlights the matching input **and** its box on the page.

### WP-G · UI manifest — repo: cumulus (both sides; after WP-F names its screens)
- Version packages declare `UI`; manifest endpoint; Dart boot-fetch, cached
  manifest, `screen_id → factory` map, fallback chain (manifest → local packs
  → generic pane).
- **Gate:** removing a hardcoded pack entry changes nothing user-visible; an
  unknown `screen_id` degrades to the generic pane.

### WP-H · ModelCall ledger — repo: cumulus
- One shared governed model client (`app/modelcall.py`): constructs the
  client, makes the call, meters usage, writes a ledger record `{id, at,
  flow_id, flow_version, run_id, step_id, case_ref, model, prompt_ref,
  input_tokens, output_tokens, cost_usd, latency_ms, outcome}` to **its own
  collection** (Q3). The four hand-rolled call sites migrate — invoice reader,
  receipt reader, answering, embeddings; embeddings become metered for the
  first time.
- `GET /api/v1/modelcalls` (filterable by run/flow/model) + a per-call table
  in the console spend panel.
- **Gate:** every model invocation in a golden-thread run appears in the
  ledger with real token counts; `StepRecord` accounting still agrees with it.

### WP-I · Integration & deploy — orchestrator
- Full suites; `ship.sh --knowledge` (policy + prompt revisions) and the
  estate deploy; run the golden thread against the deployed pair; walk the
  acceptance checklist (§5).

---

## 4 · The field-verdict contract (owned here; WP-J and WP-F code against it)

Written in the plan rather than by either lane, because it is the seam
between them.

```python
# app/tasks/accounts_payable/v8/verdicts.py
FieldStatus = Literal["ok", "low_confidence", "inconsistent", "missing"]

class FieldVerdict(BaseModel):
    path: str            # canonical fact path: "invoice.total_usd",
                         # "line_items[2].amount_usd" — the same vocabulary
                         # Region.field, the FlowContract and golden sets use
    label: str           # "Invoice total", "Line 3 — amount" (screen copy)
    value: str           # as read, rendered for display
    confidence: float    # 0..1 from the extractor; 0.0 = it did not say
    status: FieldStatus
    reason: str          # one sentence, shown to the clerk verbatim
    region: Region | None = None   # filled by locate-fields, non-ok only
```

- The review Task carries `fields: list[FieldVerdict]` in its proposal; the
  screen renders boxes for every verdict whose `region` is set.
- `status="ok"` fields are never sent to `locate-fields` and are collapsed in
  the screen — the point is that a clerk is asked about three fields, not
  thirty.
- The decide payload returns corrected values keyed by the same `path`, which
  is what makes J4's correction set a clean raw→corrected pair.

---

## 5 · Sequencing & MAS execution model

```
Wave 1 (running):  WP-A estate ·  WP-H modelcall ·  WP-E run evidence
Wave 2:            WP-B intake (needs A) ·  WP-C receiving (needs A) ·  WP-D vocabulary
Wave 3:            WP-J field verdicts (needs B) ‖ WP-F review screen (needs E, J's contract)
Wave 4:            WP-G manifest (needs F) ·  WP-I integration + deploy
```

Wave 1 is A/H/E rather than the draft's A/D/E/H: WP-D and WP-H both edit the
two task-domain modules, so the vocabulary sweep is sequenced behind the
ModelCall migration instead of racing it.

- One Opus agent per lane; at most three concurrent; same-repo lanes in a
  wave get **worktree isolation**; I integrate and run `pytest`,
  `flutter analyze`, `flutter test` between waves.
- WP-J and WP-F are one wave and two agents, both against §4's contract.
- Deploys stay user-run (`./deploy/ship.sh -y`, plus the estate deploy) — the
  permission classifier blocks them from this session, and they are
  outward-facing anyway.

## 6 · Acceptance checklist

- [ ] DMS AP folder holds a real PDF invoice filed by the sim
- [ ] `ap-intake` started an `accounts-payable` run from it (no console touch)
- [ ] The extract step is a metered ModelCall over the stored bytes, with
      per-field confidence
- [ ] Arithmetic checks ran and named field paths on failure
- [ ] `locate-fields` ran **only** for disputed fields (and not at all on a
      clean invoice)
- [ ] The vendor resolved against a synced ERP vendor (drifted spelling)
- [ ] The match paired invoice ↔ PO ↔ receiving report from the graph
- [ ] The run suspended into a `review-invoice` task (policy at $0)
- [ ] The screen showed the rendered PDF with boxes on the disputed fields
- [ ] The correction appended to the correction set
- [ ] The run DAG shows the true path over the true version; step evidence is
      clickable
- [ ] The ModelCall ledger shows both calls with tokens and cost

## 7 · What deliberately stays fake

- **Eval worlds and fixtures** — grading runs in isolated worlds by design;
  `_demo_world` stays a browsable fixture.
- **The stub readers** — under explicit config, for tests and offline dev.
- **The pulse itself** — the demo ecosystem *is* the pretend customer; its
  simulation is the stage, not a mock inside Cumulus.

## 8 · Progress log

**Wave 1 — 2026-09-02**

- **WP-E · done, verified.** `flutter analyze` clean; `flutter test` 294 (was
  253); `pytest` unchanged at baseline. The run-version trace bug is fixed and
  states its fallback out loud; step tiles carry clickable refs/writes, a
  folded output block, and a `stepDocuments()` union that reaches the document
  named only in a step's *output* — which is how `extract-invoice` names its
  invoice, previously reachable from nowhere. PDF rendering via blob URL +
  platform view, no new pub dependency; region boxes drawn from the normalized
  coordinates with page filtering. The duplicate no-bytes drawer is gone.
  Two honest states kept: a composed run's zero-byte document still says so,
  and a new `looksLikePdf` check names the estate's current text-under-a-`.pdf`
  condition rather than rendering an empty frame (WP-A/A2 fixes the cause).
  *Known limit, stated in source:* `StoredDocument` carries page count but not
  page dimensions, so the PDF frame assumes US Letter with `view=Fit`; other
  proportions letterbox. The image path has no such assumption.
- **WP-H · done, verified.** `pytest` 1206 (was 1182), +24 tests. One
  `GovernedModelClient` holds the wire, the ledger and the price sheet; the
  four hand-rolled Vertex clients collapse to one cached construction site.
  Ledger rows carry the ratified field set, price off the *same* governed
  `model-pricing` asset the journal uses, and a failed or refused call is
  appended before the error re-raises. Run/step/prompt coordinates reach the
  ledger through `modelcall.metered(body)` applied at the **version-registration
  loop** — so a new `v<N>/` package inherits metering automatically and no flow
  file changed. Embeddings are metered for the first time, deliberately with
  empty run coordinates (an embedding under a step is retrieval; borrowing the
  step id would make the ledger and the journal disagree about that step's
  cost) and unpriced until the rates asset carries an embedding row.
- **WP-A · done, emulator-verified** (estate repo, uncommitted, not deployed).
  All six gate checks pass against the Firestore emulator across 14 driven
  `procure_to_pay` instances. No new dependency: the repo already had a
  hand-rolled PDF writer (`dms._pdf`), extended with a fixed-pitch font and a
  Latin-1 transliteration table — which incidentally fixes mangled dashes and
  quotes in *every* previously seeded document. Receiving reports are a sixth
  ERP entity on the generic machinery, so they inherit `?updatedSince=` and
  the router for free; the PO's status is now **derived** from its receipts
  (`_apply_receipts`) instead of PATCHed by the simulator, and only within the
  receiving band — `Pending Approval`, `Fully Billed` and `Closed` stay where
  a person put them. `goods_received` / `balance_received` steps land before
  the invoice files, with short shipments (~1 in 3) and late deliveries that
  shift the whole tail so an invoice can never predate its delivery. Seed
  depth: items 19→24, POs 30→54 (appended, never inserted — PO ids are list
  position and two are quoted by name in other apps), 20 seeded receipts, DMS
  documents 62→106 including four historical invoices for each of the 11 AP
  vendors. Non-disputed runs bill **received** quantities; disputed runs
  overbill and reissue at v2.
  *Deliberately left:* no Receiving tab in the ERP Flutter app (API and API-docs
  page only) — worth asking for separately if the demo needs it on screen.
  *Pre-existing, not caused by this lane:* `melos run analyze|test` cannot run
  in this container (workspace wants Dart ^3.13.0, installed SDK is 3.9.2);
  verified identical on a stashed tree. `dart analyze` on `apps/erp/lib` and
  `pyflakes` over `functions/` are clean.

  The receiving contract WP-C codes against: `GET /api/<ns>/v1/receivingReports`,
  ids from `"7000"` with `tranId` `RR7000`, `purchaseOrderId` + ERP-copied
  `vendorId`, `tranDate`, `receivedBy`, `memo`, and `lines[]` of
  `{line, itemId, quantityOrdered, quantityReceived}`. **No money on a
  receipt.** Watermark `updatedAt`, strictly-after, ascending.

- **WP-D · running** (vocabulary → ontology). Started ahead of the wave
  because it was sequenced behind WP-H only for shared files, and does not
  depend on WP-A.
- **WP-C · done, with one declaration owed.** `erp-receiving` FeedSpec +
  rename-only builder in `estate.py`, ordered **below** `erp-purchase-orders`
  so the order is in the graph before its receipts and `receives_against` is
  drawn on the first pull. The load-bearing decision: a receipt names its
  order by internal id, but `PurchaseOrder`'s natural key is the ERP's
  `tranId`, so the builder dereferences the order row rather than rebuilding
  the key from the `"PO" + id` convention — a key rebuilt from a naming
  convention fails silently the day the convention changes. Two fields land
  empty rather than defaulted, because the source has no answer: a receipt has
  no `status` (completeness is the *order's* property, and the wire default
  `"complete"` would assert of every partial delivery exactly what is untrue
  about it) and a line has no `condition` (good/damaged/short is a judgment; a
  dock that counts has not made one). No money on a receipt, none invented.
  12 tests covering split delivery, partial receipt, the service order billed
  with no receipt, the watermark re-pull, and the gate itself: an invoice
  matching `verdict == "matched"` on all three legs through the real HTTP
  door. It also documented a real matcher limit it did not paper over —
  `match_invoice` pairs an invoice line to a *single* best receipt line and
  does not sum a split delivery, so billing the full quantity across two
  receipts reads as a quantity variance.

  **Corrected my assumption:** `data_collections.py` needs *nothing* —
  `receiving-sync` is already in `written_by` for both `source-landing` and
  `ap-records`. The declaration actually missing is a **Mapping** in
  `mappings.py` (which WP-D holds). WP-C validated it out-of-tree and handed
  back ready-to-paste code; it is stashed in the scratchpad as
  `pending-erp-receiving-mapping.py` together with two prose-staleness fixes
  and the `estate_fixtures.py` wart, all to be applied when WP-D releases.
  Until then `test_every_feed_in_the_registry_declares_a_contract` fails —
  known, owed, and not a defect in the lane.
- **WP-D · done** (after a resume — a transient API 529 killed the first run
  mid-edit, leaving the tree unable to import; the agent was resumed with its
  context intact rather than patched from outside, and finished its own
  half-written thought). The sweep's shape: **named constants for lookup keys,
  a validation pass for declarations** — not a mechanical rewrite of hundreds
  of literals. The reasoning is exact: a graph *write* already fails loudly on
  an unknown type, but a *read* does not — `find("Vendors")` returns `[]`,
  indistinguishable from a feed that has not landed. New `app/ontology/names.py`
  checks every constant against the TBox at import; 14 modules use it; three
  registries gained conformance passes (`validate_mappings` extended,
  `validate_collections` and `validate_specs` new). `validate_collections`
  also checks a direction nothing was checking: `ObjectType.collection` and
  `DataCollection.object_types` are the same fact from two ends.
  The fakes are renamed `_STUB_*` and moved under a banner directly above the
  stub reader, with a test asserting only those two modules touch them; the
  composer reads `graph.find(onto.ITEM)`; `CLOSED_PO_STATUSES` became the tail
  of a declared `PO_STATUS_LADDER` in the domain that owns payables. New leaf
  module `app/desks.py` single-sources the queue slugs — a leaf both sides
  read, because a roster importing the desk-owning modules is an import cycle
  (`employee`, `task_queue`, `auth`, `queues` all import `personas`). Persona
  titles now prefer the HRIS-landed `Employee`, with the declared title as the
  cold-deployment fallback and a test pinning that sign-in never depends on a
  populated graph.
  *Deliberately left:* `department` (it is a `member_of` Team link, and
  overlaying it would add eight traversals to a first-paint call),
  `OPERATOR_EMAIL` (deployment identity, not business vocabulary),
  `graph_api.py`'s three reads and the version packages' `entity` step
  configs — both outside the audited list, both worth a follow-up.

- **Orchestrator · debts paid, waves 1–2 closed.** Applied WP-C's
  `erp-receiving` Mapping verbatim (WP-D confirmed `mappings.py` still uses
  raw type strings, so no translation was needed); rewrote the now-false prose
  in the `mappings.py` docstring and `test_mappings.py` that said this estate
  has no receiving system, and added `erp-receiving` to that test's asserted
  set; closed the fixtures wart by serving one clean receipt (`RR7000`,
  against the already-present `PO6013`) from the shared fake estate. That last
  one deliberately does **not** pull WP-C's split-delivery and partial-receipt
  orders into the shared fixtures: those are scenario fixtures and belong
  beside the test that asserts on them, and hoisting them would have forced a
  re-teach of every test pinning the purchase-order set. Verified: **1231
  passed, 1 skipped** and **294 Flutter tests**, analyzer clean.

- **WP-B · done, verified** (1266 passed / 292 Flutter / analyzer clean). An
  AP invoice is recognised by **where the customer filed it** — `dms-sync`
  already lands the folder tree, so intake walks the AP folders, and *which*
  folders is a governed asset rather than code. Segment-wise prefix matching,
  because `Accounts Payable Archive` is not under `Accounts Payable` and a
  `startswith` would have started a run per invoice the company already paid.
  The work list comes from **the graph, not the delivery payload**: the
  delivery is the wake, and the flow then asks the standing question "which
  AP-filed documents has nobody read yet" — self-healing, and boundable by a
  governed `window_limit`, which is not optional because every invoice raised
  runs a whole AP case inside the delivery's HTTP request. "Nobody has read
  it" became a fact in the map: `sha256` **plus** a `read_version`, because a
  hash alone cannot tell an unread file from one the vendor reissued — which a
  disputed invoice does, at `?version=2`, under the same id. Windowed
  escalation is counts-on-the-run, with exactly one Task when a window reached
  for files and got *nothing* (forty failures at once is one address that
  stopped answering — platform-operations, not the clerk's desk).
  Two Trigger-level changes were needed: `DocumentsDelivered` is the first
  fact with two responses, so declaration order is now asserted, and
  `Dispatcher.emit` only honoured a caller's idempotency key when exactly one
  Trigger matched — `keyed_run_ids` now derives one id per response while
  keeping the single-Trigger id verbatim, so no deployment's run store moves.
  `EstateConnector.fetch_content` **refuses an address outside the registered
  estate's hosts**: a `storage_ref` is a string an external system wrote into
  our graph, and following it anywhere hands the customer's DMS the ability to
  aim our credentials.
  *Scope expansion, accepted:* the composer was removed **wholesale** (server
  and console, ~730 lines) rather than merely de-scenarioed, on the grounds
  that a vendor/order/SKU picker whose payload nothing renders is a screen
  that lies about what it does. The cost is mine, not the lane's: WP-D had
  just finished pointing that composer at real graph `Item`s, and sequencing D
  before B meant polishing a doomed feature. **Consequence for demos:** with
  the estate undeployed, the only way to exercise AP is uploading a real PDF
  (filename steering still works under an explicitly-configured stub).
  *Named, not fixed:* `reimbursements_api.py` still maps a submitter's `note`
  into the stub's steering channel, so the **expense** extractor stays
  request-steerable. Un-wiring it properly needs the note a real home first,
  since that mapping is currently the only place it goes. Outside the AP
  golden thread; worth its own small change.

**Wave 3 — 2026-09-02**

- **WP-F · done, verified** (analyzer clean; **315** Flutter tests, was 292).
  Work pane left, page right, and the ordering is the argument: a document
  viewer with a form bolted beside it is a tool for *reading* invoices, and
  this is a tool for answering three questions about one. Disputed fields
  render worst-first (`missing` → `inconsistent` → `low_confidence`),
  stable-sorted so two equally urgent fields never swap between builds, each
  carrying the check's `reason` verbatim and a footer naming what was read,
  the confidence, the canonical path, and whether it is boxed. The match and
  the full extraction are folded below. Only the *disputed* fields' boxes are
  drawn — never the document's own stored regions — because the point of the
  locate step running on three fields is that three boxes mean something.
  The distinction it insisted on, correctly: `fields` **absent** ≠ `fields:
  []`. Absent means "this version computes no verdicts" and falls back to the
  full form; empty means "computed, nothing in doubt" and reads as a fast
  approve naming the $0 policy. Collapsing null into empty would turn "we did
  not look" into "we looked and it is fine", over an invoice about to be
  posted. An unknown `status` reads as **disputed** — showing one field too
  many costs a glance; hiding a flagged one costs the invoice.
  `DocumentPageView` was extracted from the drawer so box-drawing and every
  honest no-render state have one implementation; the invoice draft helpers
  were lifted to flow level so v8 does not depend on v6.
  *Coordination relayed to WP-J mid-flight* rather than left to a report:
  always emit `[]`; zero-based line indices with three accepted path
  spellings; the corrected payload keys by the path *the run named* and
  carries only values that actually moved; and a defensive duplication of
  `total_usd`/`tax_usd`/dates/currency under both patch key and path, because
  v6's form has no total input and a reused patch applier would recompute the
  total and silently discard the clerk's number.
- **WP-J · done, verified** (1297 passed). Per-field confidence is keyed by
  canonical fact path, re-keyed from the model's own vocabulary rather than
  asking a model to emit bracket indices while reading a page. **Zero means
  silence, not doubt** — an unscored field is never flagged, and out-of-range
  scores are dropped rather than clamped. `check-arithmetic` is a pure Rule in
  its own module, five checks each naming the path it implicates, tolerances
  governed and read by a separate transform so the Rule stays pure; it
  **reconciled** with `_posting_check` rather than duplicating it (a
  `check_line_sum=False` kwarg v8 passes), so a clerk gets one sentence naming
  the field instead of two, the vaguer one second. `locate-fields` returns
  before existing when nothing is in doubt — the journal shows the step
  *absent* rather than present-and-empty — and its acceptor refuses a box for
  a path nobody asked about, coordinates outside 0–1, a page the document does
  not have, and duplicates, counting every refusal in the step summary.
  J4 became **its own flow**, `ap-corrections`, because the doctrine's flywheel
  clause is explicit that a correction flows back decoupled via an Event: the
  decision emits `ExtractionCorrected` and a separate run appends one row per
  moved field, into the `eval-datasets` Collection the registry had described
  long before anything wrote it. Nothing promotes; a test pins that the
  extraction prompt's revision is unchanged after a correction.
  D6 is a two-line knowledge edit, and all 19 auto-post tests were re-stated to
  say something true rather than relaxed — the sharpest now asserts the dial in
  **both** directions ($0 holds; $5,000 posts with nobody involved).
  *Consequence worth knowing:* at a $0 limit a correction can never release an
  invoice — it settles the non-limit objection and returns to the desk — and
  with `MAX_REVIEW_ROUNDS = 2`, correct→approve consumes both rounds.
  *Not built, deliberately:* no read API for the correction set; the rows carry
  invoice values and the scoping decision was not this lane's to make.

- **Orchestrator · the seam pinned.** Both wave-3 lanes coded against §4's
  contract and never ran against each other — the Python tests assert on Python
  objects and the Dart tests build Dart fixtures, so both suites could stay
  green while the JSON between them disagreed. `field_verdict_contract_test.dart`
  feeds WP-J's literal documented payload through the console's parser and
  asserts the geometry, the zero-based-path/one-based-label convention, and
  that absent ≠ empty. 7 tests.

**Wave 4 — 2026-09-02**

- **WP-G · done, verified** (1312 passed / 335 Flutter / analyzer clean).
  Version packages declare `UI` beside `STEPS`; `app/task_screens.py` holds the
  **only** copy of "newest declaration at or below the task's version", and
  `GET /api/ui/v1/task-screens` serves every shipped version *already resolved*
  — accounts-payable serves 6, 7 and 8, where 7 carries v6's screens verbatim
  — plus a `fallback` for a version this deployment never shipped. The client
  does zero version arithmetic. A version list rather than an int-keyed map,
  because JSON object keys are strings and re-parsing them is a place to be
  wrong. The kind-keyed screens moved server-side too: keeping them client-side
  would have split the copy a clerk reads across the wire and the binary.
  Dart keeps `taskScreenFactories` as a `const` map — a constructor is code and
  cannot cross a wire — fetched at boot after sign-in and **not** awaited before
  first paint, so a cold Cloud Run start cannot hold the shell.
  The fallback chain is per-key, and the sharp edge is deliberate: an unknown
  `screen_id` degrades to the generic pane rather than falling through, because
  the manifest is right about what the task needs and opening a neighbouring
  widget would answer a different question with a clerk's signature on it. A
  flow the manifest *does* describe has answered even when the answer is "no
  screen for this kind" — falling through there would reinstate a screen the
  deployment dropped.

## 9 · Where the acceptance checklist actually stands

Everything in §6 is proven by tests except two lines that cannot be proven
without a deploy, and both are marked honestly:

| Checklist line | Status |
|---|---|
| Sim files a real PDF into the AP folder | emulator-verified in the estate repo; **uncommitted, undeployed** |
| `ap-intake` starts a run from it, no console touch | verified against the recorded estate fixture (3 invoices out of `Accounts Payable/GSF/2026`) |
| Extract is a metered ModelCall over stored bytes, per-field confidence | structurally done and tested on the stub; **real Vertex extraction needs the deploy** |
| Arithmetic checks name field paths | tested |
| `locate-fields` runs only for disputed fields, not at all when clean | tested — this is WP-J's gate |
| Vendor resolves against the synced ERP master | tested |
| Match pairs invoice ↔ PO ↔ receiving report | tested — WP-C's gate, `verdict == "matched"` through the real HTTP door |
| Run suspends into review at the $0 policy | tested |
| Screen shows the PDF with boxes on disputed fields | built and unit-tested; **the visual check needs a browser** |
| Correction appends to the correction set | tested |
| Run DAG traces the true version, evidence clickable | tested |
| ModelCall ledger shows the calls with tokens and cost | tested |

## 10 · The deploy (user-run)

Both repos are uncommitted and nothing has been deployed. The permission
classifier blocks deploys from the agent session, and they are outward-facing,
so they are the user's to run:

```
./deploy/ship.sh --with-estate --knowledge -y
```

That runs both suites as a gate, deploys Cumulus, pushes the governed knowledge
(the $0 posting policy, the revised extraction prompt, the new locate prompt),
and builds and deploys the estate. Two notes from the estate lane: the DMS
function timeout change needs `firebase deploy --only functions` to take
effect, and a full DMS `/seed` should go to the function URL directly rather
than through the Hosting rewrite, which still caps at ~60s.

### Ratified sub-decision — the ledger is not a declared Dataset

WP-H left this open rather than deciding it in-lane, correctly. Ruling: the
ModelCall ledger stays **out of `data_collections.COLLECTIONS`**. That registry
declares what *flow logic* reads and writes in the knowledge/data layer, and
`flow_projection` grades conformance against it; the ledger is written by the
engine underneath the flow, exactly like the run journal and the connector
cursor, so declaring it there would put a machinery write into a business
flow's footprint. This does not contradict §2b Q3: the ledger is still
customer-owned, append-only audit history and exports as such at the
portability layer — "not a declared Dataset" and "not the customer's" are
different claims, and only the first is being made.

## 9 · Risks

- **Vertex extraction quality on generated PDFs** — keep the artifact layout
  plain; the prompt is governed knowledge and tunes without a deploy.
- **Two-repo coordination** — WP-A gates two lanes; its gate is checked
  against the *deployed* estate, not local code.
- **Concurrent sessions** — this repo sometimes has parallel Claude sessions;
  waves land through the suites, and unexplained diffs get attributed before
  anything is reverted.
- **Scope creep in WP-D** — the sweep is bounded to the audit's list.
- **Two model calls per reviewed invoice** — accepted deliberately: the second
  is a cheap flash call, is skipped on clean invoices, and buys the boxes that
  make review fast. The ledger will show exactly what it costs.
