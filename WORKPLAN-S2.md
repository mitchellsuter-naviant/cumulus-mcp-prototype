# Stage 2 — the write plane: accepted implementation plan

Architect's plan, accepted 2026-08-31. Builders implement their stage
exactly; contradictions get flagged, not improvised around. See WORKPLAN.md
for decisions of record and gates. The live-smoke stage (S2-S) has a strict
may/may-not-touch list — the ONE live write goes through the console Task
approval as the controller persona, never by curl.

## Verified ground truth

**The Voss case is live and unique.** ERP `id=5037`, `tranId=SO5037`,
`status="Pending Approval"`, `total=34960`, `tranDate=2026-08-17`, memo
"Title and service records capture — phase 1", `customerId=1007` (Voss
Automotive Group LLC), unchanged since the reseed. **The PATCH URL keys on
`id`, not `tranId`**: `PATCH /api/naviant/v1/salesOrders/5037`. The graph
holds both: `SalesOrder` natural key is `order_number` and `external_id` is
`"5037"` (`sync_commerce.py:812-836`).

Live counts (local graph + estate):
- Companies carrying a `pending-approval` SalesOrder: **2** — Voss (SO5037),
  Blue Harbor (SO5003).
- Of those, with any `closed-won` Opportunity on the same Company: **1** —
  Voss (3 wins: 2025-11-14 $61k · 2026-03-20 $44k · **2026-08-14 $128k "Voss
  — Title & Service Records Capture"**).
- `company_same_as` standing: 9, all `in_review` (incl. Voss Auto ↔ Voss
  Automotive Group at 0.95).

Consequences:
1. **The join is expressible on today's graph directly** —
   `Opportunity —pursued_with→ Company ←sold_to— SalesOrder` (both land on
   one Company for Voss). A name-based control join failed on Yahara/Dane
   County/Prairie Records — identity resolution already solved what names
   cannot.
2. **Amount proximity does not correlate Voss** ($128k deal vs $34,960
   phase-1 order). The honest amount rule is one-sided: an order may be a
   slice of a deal, never larger.
3. **PSA evidence (`ENG-0105`, on-hold) sits behind an `in_review`
   `company_same_as`** — status-gating keeps it inadmissible until a steward
   confirms. Feature, not blocker: counted as `blocked_on_identity`.

**The estate will not stop a bad write.** `functions/erp.py:275-322`
validates the picklist and nothing else — no transition validation, no
If-Match, no optimistic concurrency. `_update` (:378-392) is a partial PATCH
stamping `updatedAt` server-side, so **our write comes home on the next
delta**. READ_ONLY fields are ignored, not rejected. Legality is Cumulus's
to own.

**The pulse will not race SO5037** — `sim_naviant._p2_status` moves only
order ids its own instance minted; SO5037 is seeded. The race is real for
sim-generated orders and is designed for; it is not a hazard for the one
live smoke.

---

## 1 · S2a — disagreement detection

### The wave-1 class set — measured, not listed

| Candidate class | Two-ended? | Live instances | Verdict |
|---|---|---|---|
| **won-not-released** — Opportunity(closed-won) ⟷ SalesOrder(pending-approval) | yes | **1** (+ the pulse makes more; processes.md designs the window open) | **ship** |
| terminated worker still holding a mailbox | yes | 0 (all 34 mailboxes belong to active staff) | cut — Track A must seed it first |
| confirmed-duplicate counterparty, open exposure both sides | yes | 0 | cut |
| mail promises an order the ERP has not released | yes | 0 | cut |
| completed PSA project vs unbilled order | yes | ~2 after a similarity floor, noisy | defer |
| won deal with no order at all | no (one-ended) | several | defer — needs a reified finding type, not a Link |

Wave 1 detects one class properly and ships the class table so the second is
a data change.

### The finding is a typed Link (no new Object, no minting, reversible)

```
LinkType("deal_disagrees_with_order", "Opportunity", "SalesOrder", "many-to-many", …)
```

Named by its endpoints (the validator refuses duplicate names — a later
Project↔SalesOrder class gets its own). Props: `class="won-not-released"`,
`status ∈ {standing, resolved, dismissed, superseded}`,
`correlation_confidence`, `signals[]` (the `identity_sweep.Signal` shape),
`detected_at`, `policy_ref`, `run_id`,
`state_when_found={"opportunity_stage","order_status"}`.

### Evidence — all three required, or it is not a finding

1. **One counterparty**: `pursued_with` and `sold_to` reach the same Company,
   or two Companies joined by a `company_same_as` at **`confirmed`**. An
   `in_review` link does not carry it — counted `blocked_on_identity`, never
   raised.
2. **States**: stage `closed-won` AND order `pending-approval`.
3. **Time window**: `ordered_date ∈ [close_date − lead_days, close_date +
   lag_days]`; defaults `lead_days=21, lag_days=30`, in the governed policy
   asset, derived from processes.md's own day offsets.

At least one corroborator or the pair is `ambiguous`, not raised:
4a. name proximity ≥ 0.60 (`resolution.name_similarity`, opportunity name
minus account prefix vs order memo — Voss scores);
4b. amount containment `0.10 ≤ order/deal ≤ 1.15` (one-sided; Voss 0.27);
4c. sole candidate in the window.

Explicit non-findings, each live: SO5003/Blue Harbor (no won deal → counted
`order_without_deal`); Voss's two older wins (outside window — assert they
do NOT link); the `in_review` Voss PSA join (blocked_on_identity). **Demo
beat: confirming the Voss merge in the workbench makes a third system's
evidence appear on the disagreement Task.**

### Shape (rhymes with identity_sweep.py line for line)

- `dispatch.TRIGGERS` += `Trigger(id="state-window-closed",
  event="StateWindowClosed", starts="disagreement-sweep", wakes="window")`.
- `SyncService.sweep_disagreements(actor, window)` mirroring
  `sweep_identities`; route `POST /api/v1/data/state/sweep` (admin).
- Body: t1 trigger · x1 read-policy · z1 resolve scan (pure read, findings +
  counts) · a1 action `record-disagreements` (writes edges only) · k1 raise
  tasks · e1 event `DisagreementsFound`.
- `gateway.ACTIONS` += `Action(id="record-disagreements",
  target="entity-layer")` — **no queue** (finding is nobody's work, the
  `propose-identity-link` reasoning).
- New governed asset `server/seed/state-agreement-policy.md` (windows, name
  floor, amount band, transition table), parsed by
  `resolution.read_state_policy()`. **Zero ModelCalls.**
- Task idempotency exactly as `raise_merge_tasks` (id derived from the edge;
  a decided Task left alone; `dismissed` never re-raised).
- New queue **`order-desk`** bound to the `controller` persona only — Raymond
  releases orders; Connie must not release revenue (testable scoping).

## 2 · S2b — the outbound Action

### `estate_writes.py`, a sibling — not a method on estate.py

(1) `test_estate.py`'s import boundary stays intact; the writer gets its own
(no `sync`, no `ConnectorState`). (2) Cursor state and write journal are
different machinery with different lifetimes. (3) `pull_all` runs on a
timer; it must never be one refactor from a loop that writes.

Contents:
- **`WRITABLE`** — closed table, one row per permitted write:
  `WritableTarget(id="sales-order-status", app="erp", resource="salesOrders",
  field="status", action="release-sales-order",
  transitions={"Pending Approval": ("Pending Fulfillment",)})`.
  A generic patch() is the governance hole; the estate enforces no
  transitions, so this table is where legality lives.
- **`EstateWriter(connector)`** — borrows the connector's address helpers;
  adds `read(app, resource, id)` and `patch(app, resource, id, body)`;
  injectable callables like the connector's.
- **`EstateWriteJournal` / `EstateWriteRecord`** — machinery, own collection,
  never a Dataset.
- `ESTATE_WRITES_ENABLED` (config, default false): no real writer without it.

### The Action and the per-case flow

`gateway.ACTIONS` += `Action(id="release-sales-order",
target="sales-records", queue_from_task=True)`.

The sweep raises Tasks and does not suspend (windowed default); the approval
starts an independent per-case Flow `order-release` via Trigger
`OrderReleaseApproved` — the tasks_api resume hook does exactly one
`dispatcher.emit(...)`.

```
t1 trigger  OrderReleaseApproved
x1 transform read-write-policy
z1 resolve  check-preconditions   (re-read the estate row; five outcomes)
a1 action   release-sales-order   (journal → PATCH → verify → resolve finding)
s1 action   apply-source-records  (regime=observation: land the ERP's answer)
e1 event    SalesOrderReleased
```

**Two Actions, two regimes — load-bearing.** a1 is the decision; s1 is an
observation of what the ERP now says. **Cumulus never writes an authority
claim it did not read back from the ERP** — no `human:` claim on
`SalesOrder.status` (it would outrank `erp` forever and freeze the order).
Assert the claim sources on the object after release.

### Preconditions (z1), idempotency, verify-after-write

| Estate says | Journal says | Action |
|---|---|---|
| Pending Approval | — | proceed |
| Pending Fulfillment | applied entry for this order | already ours — no PATCH, finding resolved |
| Pending Fulfillment | nothing | resolved_externally — no PATCH, no noise |
| anything else / 404 | — | escalate to order-desk, no write |
| transition not in WRITABLE | — | refused before HTTP |

a1: **journal the intent first** (write-ahead) → PATCH → re-GET → compare →
journal `applied` + finding `resolved`; mismatch → `unverified` + Task. The
estate is the source of truth for whether the write took. `retries=1` is
safe ONLY because the value-based precondition exists — never wire retries
before it (double-PATCH). No `updatedAt` travels on the wire today; the Task
carries `status_when_raised` and z1 compares on value. Carrying
`updated_at` on wire shapes is a named follow-up, not smuggled in.

### Echo suppression with no origin field

The two-regime split means the graph needs no suppression (the echo lands an
identical `erp:` claim). What needs recognizing is the reporting: **one call
in `SyncService.deliver` (~:881), before the plan loop** — walks delivered
rows against the open journal:
- match (record+field+value = applied entry) → journal `echoed`, outcome
  carries `echoes: n`; the row still lands (an identical claim IS the no-op)
- divergence (our record, third value) → journal `diverged` **and a Task**
  ("the ERP no longer holds the value you approved" — the re-elevating
  discrepancy)
- stale (no echo after N pulls) → `unechoed`, dashboard fact

Site it beside `_land`'s hash suppression with a comment distinguishing the
two facts (re-delivery vs our-own-change-coming-home).

### Failure

Retry-then-escalate everywhere; no silent rollback; **no inverse Action** —
moving the order back is a second decision, and the review screen says so.

### The Gateway bug that lands FIRST

`gateway.py:302-336`: `PendingAction.commit` catches only `TBoxError`; the
ledger append is after `write()`. A `write()` raising anything else appends
**no ledger entry** — tolerable for graph writes, a governance hole for an
outbound HTTP write. Fix: `LedgerEntry.outcome` gains `"failed"`; commit
appends before re-raising. Companion: `ui_api._ledger_window` +
`RegimeCount` + the Dart reader gain the third bucket or a `failed` entry is
silently dropped.

## 3 · S2c — ModelCall cost and tokens

- **The lie**: `reimbursements.py:915` / `payables.py:1081` pass
  `model=getattr(self.reader, "name", "")` — the backend ("vertex"/"stub"),
  not the model. The real id already exists on
  `ReceiptExtraction.model`/`InvoiceExtraction.model`. Deletion, not
  addition.
- **Mechanism**: `ctx.step` gains a `meter:` callable resolved from the
  output after success (beside `writes:`) returning
  `ModelUsage(model, input_tokens, output_tokens)`. Additive.
- **Plumbing**: extraction results gain token fields; the Vertex readers read
  `usage_metadata` they currently discard; stub/none record zeros honestly
  (`cost_basis` reports `unrecorded`, never "cheap").
- **Price**: `flows._MODEL_RATES` moves to governed
  `server/seed/model-pricing.md`; `_blended_rate` reads it.
- **Ungoverned calls**: one flow `answer-question` (Trigger `QuestionAsked`,
  case) wraps the existing answering loop unchanged; a2a comes free (routes
  through AnswerService.ask); hr-chat emits `HrQuestionAsked` into the same
  flow. **One modelcall step per question** with summed usage — not per
  turn.

## 4 · The console surface

Not a new Workspace (one class, one live finding — add the Seeing Space when
the class table has three rows). Not the Governance panel (that is about
Cumulus's own flows; a disagreement is a fact about the customer's estate).
**The Judgment panel**: a `DisagreementState` sibling beside
`IdentityState` — `standing`, `awaiting_decision`, **`blocked_on_identity`**
(the number that sells the identity work; links to the Counterparty
workspace), `resolved_30d` split by_cumulus/externally.

The work lives in My Tasks: a `state_disagreement` review screen —
what-the-CRM-says / what-the-ERP-says, the Signal table, a before/after
naming what changes AND what does not. The approve button:

> **Release SO5037 in the ERP**
> This writes to Naviant's ERP. Sales order SO5037 moves from Pending
> Approval to Pending Fulfillment. Nothing else on the order changes.
> Cumulus re-reads the order afterwards to confirm the change took, and
> tells you if it did not. There is no undo here — moving it back is a
> second decision.

Reject = "Leave it as it is": the finding stays `dismissed` and is not
re-raised unless the two systems change (must be true — idempotent sweep).

## 5 · Eval

Write-back asserted against a fake estate, never live — enforced three
ways: eval_world always injects a FakeEstate-backed writer (test: no httpx
client); `ESTATE_WRITES_ENABLED` default false; a boundary test on
estate_writes.py. `FakeEstate.patch` reproduces erp.py faithfully —
picklist, unknown-field 400, READ_ONLY ignored, updatedAt bumped, **no
transition validation** (a tidier fake tests a system nothing serves).

- Spec `disagreement-sweep`, fixture `state/naviant-disagreement` (Voss +
  Blue Harbor non-finding + the two older wins + the in_review PSA join).
  Milestone `terminal`. Link-count rules (counted, not asserted-to-exist);
  trajectory reads the policy asset; `model_calls_at_most: 0`. One case
  asserts `blocked_on_identity == 1` and the PSA link absent; a second case
  over the same fixture with the merge pre-confirmed asserts it present.
- Spec `order-release`: seven cases, one per branch (exactly-one-PATCH ·
  idempotent zero-PATCH · resolved_externally · escalate-on-Billed ·
  ledger `failed` on a 400 · `unverified` on a lying 200 · echo at written
  value vs third value). `ScriptedDecision` drives approval; evals.py may
  need a `task_raised` TrajectoryKind.

## 6 · Deleted or simplified

1. `getattr(self.reader, "name", "")` at both call sites — gone.
2. `flows._MODEL_RATES` → `seed/model-pricing.md`.
3. `tasks_api.py:174` lambda tuple → keyed dispatch table (approved).
4. Nothing in `estate.py` — the read-only contract stays.
5. Checked and rejected: deleting `receiving-sync`/its Trigger — the missing
   Mapping is itself the Sources screen's finding; evals exercise the leg.

## Stages

- **S2-0 · Shared machinery** (one builder, BLOCKS everything, small):
  the Gateway `failed` outcome + write-ahead ledger append; RegimeCount's
  third bucket (server+Dart); `meter:` on ctx.step. Tests first (failed
  ledger entry on ConnectError; Refused vs failed distinguishable; meter
  records off output; replay re-meters nothing).
- **S2a · The sweep** (∥ S2b-1, after S2-0): disagreement_sweep.py + the
  edits listed in §1. Tests first: Voss found / older wins not / Blue Harbor
  counted not raised / in_review blocks and confirmed unblocks (same
  fixture both ways) / one-sided amounts / links-only writes / idempotent /
  dismissed stays dismissed / thresholds from the asset (mutation-proof) /
  zero ModelCalls / order-desk scoping (ap refused, controller decides).
  Gate: pytest green; Tier-2 export exists; local sweep vs live-synced graph
  finds exactly 1 standing + 1 blocked.
- **S2b-1 · Writer + journal** (∥ S2a, after S2-0): estate_writes.py, fake
  patch, config, deps. Tests per §5's branch list; NO live estate contact.
- **S2b-2 · Release flow + echo pass** (after S2a + S2b-1): order_release.py,
  the Action, the Trigger, the deliver-side echo pass, the hook, the export.
  Fake estate only; the eval spec green.
- **S2c · ModelCall governance** (∥ anything, after S2-0): per §3. Gate
  includes ONE live vertex verification — blocked on host gcloud reauth
  (workplan decision 12); land the code, name the verification as a
  separate user-unblocked step.
- **S2-C1 · Console** (after S2a for the panel, S2b-2 for the screen):
  DisagreementState, the review screen, dashboard model. Clarity critic
  after.
- **S2-E · Evals** (after S2a + S2b-2).
- **S2-S · The live smoke** (LAST): may touch read-only everything; may
  write ONCE — `PATCH …/salesOrders/5037 {"status":"Pending Fulfillment"}`
  issued by the order-release run started by approving the Task as the
  controller in the console. Never by curl, never any other record. The
  eight-step reported sequence per the architect (sync → confirm the Voss
  merge → sweep → read 5037 verbatim → approve → re-read → sync-now shows
  `echoes: 1` → report before/after JSON + ledger ids). No rollback from
  Cumulus, by design.

## Risks (adopted recommendations)

1. Gateway swallows failed external writes → S2-0, non-negotiable.
2. `human:` claim would freeze the order → two-regime split, asserted.
3. Pulse races sim-generated orders → value preconditions, never write on a
   miss.
4. `retries=` before the precondition = double-PATCH → land z1+journal with
   a1; retries only after idempotency case is green.
5. The estate accepts illegal transitions → WRITABLE's table, checked before
   HTTP; the highest-blast-radius surface in S2.
6. `updatedAt` not on the wire → value comparison now; wire change named
   separately.
7. One class is thin → honest count; ship the table; Track A seeds the
   terminated-mailbox drift to unlock class two.
8. A run per /ask → accept; retention window later if needed.
9. vertex unverified → land code; live check is a named user-unblocked step.
10. The demo needs the Voss merge confirmed first → the feature; the
    dashboard tile says so in words; the demo script sequences it.
