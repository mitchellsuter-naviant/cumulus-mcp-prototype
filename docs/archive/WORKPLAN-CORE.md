> **Archived 2026-10-16.** Lanes P, S, A, E and F done and verified (its own §9 log), 2026-09-03. The prototype itself is superseded by cumulus-vision.

# WORKPLAN-CORE — Two flows that actually work

**Status: EXECUTING 2026-09-03.** Author: orchestrator session. Inputs: three
recon audits (flow trace, mock inventory, estate health) against the working
tree on `workflows-management`, baselines green (pytest 1337/1 skipped,
Flutter 344, analyzer clean).

## 1 · The brief (user's words, made operational)

Strip the placeholder and mock code; make the basics happen per DOCTRINE;
confirm the demo ecosystem works; then simplify the two core flows so that a
person can **submit an AP invoice and an expense report, watch each process,
and review it themselves**:

- **Expense reimbursement:** submit → extraction on **gemini-3.7-flash** →
  human review **by the submitter** to confirm values → graph update.
- **Accounts payable:** far fewer steps; custom HITL review screen; review
  **routed back to the person who submitted** the invoice.
- Both flows genuinely read and build the knowledge graph.

## 2 · What recon established

- **The estate is live and healthy** (10/11 feeds `unchanged`/`delivered`;
  pulse on). Two deployable breaks, both already fixed in the estate's
  uncommitted working tree: `erp-receiving` 404 (`functions/erp.py`) and
  Vertex-rejecting PDFs (`functions/dms.py` `_pdf` + coupled
  `sim_naviant.py`). Functions-only deploy — the Dart/melos blocker does not
  apply.
- **AP's real machinery is v8, which is not live** (`DEFAULT_VERSION = 6`).
  v6 live means: no arithmetic checks, no field verdicts, stub regions
  persisted, two clerk gates, a $0.00 invoice posted as "matched".
- **ER v9 keeps every door AP closed**: submitter's `note` steers the stub
  extractor; synthetic regions persisted onto real receipts; zero-evidence
  submissions accepted; 4 executed step families undeclared in `STEPS`;
  declared event name wrong.
- **Submitter routing almost exists**: `queues.ensure_personal` + ER's
  `manager-of(submitter)` resolver are the template. Missing: a `submitter`
  resolver, a first-class submitter field on `Task`, any routing step in AP,
  and the Gateway's queue pins accepting the task's resolved desk.
- Remaining mock: fabricated employee benefits quoted as the employee's "own
  record"; fabricated flow-version history; ~20 orphaned endpoints; dead
  `Asset.scope`/`supersedes` dials; `wms-receiving` name drift in eval
  fixtures; semantic-search chip that silently returns nothing.

## 3 · Decisions

| # | Decision |
|---|---|
| **C1** | **AP v9**, simplified from v8's real parts, becomes `DEFAULT_VERSION` and the activation target. One human gate. Declared `STEPS` = executed steps (loop suffixes aside). The separate vendor-confirm gate folds into the single review (vendor candidates ride the task proposal; the decision may pick one). `x3 project-lines` (theater) and the anomaly screen `c2` are cut — the anomaly capability remains selectable in v8. |
| **C2** | **ER v10**: evidence required (400 on no documents, mirroring AP); `note` loses the steering channel and lands as the document's memo (shown in review); synthetic regions never persisted; extraction model **gemini-3.7-flash** (new `EXPENSE_MODEL` setting; declared step config must equal it, test-pinned); `STEPS` declares every executed step family; event name fixed; review task **always raised** and routed to the submitter's personal desk; graph write on confirm. Reviewer corrections emit the correction event so the flywheel appends to the correction set (same mechanism as AP). |
| **C3** | **Submitter routing is platform work**: `routing.py` gains a `submitter` target; `Task` gains `subject_email`; `post-invoice` and `record-expense` Actions accept the task's resolved desk (the `queue_from_task` pattern `confirm-identity-link` already uses); persona principals union registry desks so local demo personas can decide personally-routed tasks. Routing targets stay **governed knowledge** (`## Approval routing` sections in both policy assets; AP falls back to `ap-clerk` when a case has no human submitter — the sim-filed intake path). |
| **C4** | **Strip list** (server): fabricated employee facts → honest empty states (HRIS is the source or nothing); fabricated version archives + the fake ER draft go; orphaned routes A1–A3 go; dead dials `Asset.scope`/`supersedes` go; `wms-receiving` → `erp-receiving` in eval fixtures; stale renamed-module docstrings fixed. (Flutter halves land with the screen lane.) Stub readers stay, quarantined, per WORKPLAN-REAL §7. |
| **C5** | **Deploys stay user-run** (ship.sh + estate functions + reseed + version activation), delivered as a runbook; the orchestrator attempts the estate functions deploy once and hands off if refused. |

## 4 · Contracts (the seams between lanes)

- **Task**: new optional `subject_email: str | None` — who the case is about
  (the submitter). Set by both flows at gate time. Consoles may render it;
  nothing re-parses evidence rows for it.
- **AP v9 review proposal**: v8's `fields: list[FieldVerdict]` unchanged
  (`admin_app/test/field_verdict_contract_test.dart` stays green), plus
  optional `vendor: {resolved: str|null, printed: str, candidates:
  [{id, name, score}]}` when resolution wanted review. Decide payload may add
  `vendor_id`.
- **ER v10 confirm proposal**: existing v9 line shape unchanged, plus
  `documents: [{document_id, filename, memo}]` and per-document extraction
  summary `{vendor, date, total_usd, category, confidence}`. Decide payload:
  corrected lines keyed as today.
- **Screen ids**: AP v9 declares `accounts-payable/invoice-review` (reuse);
  ER v10 declares `expense-reimbursement/confirm-values` (new widget).
- **Routing keyword**: `submitter` (beside `manager-of(submitter)`); resolves
  via `queues.ensure_personal(subject_email, display_name)`; every failure
  falls back to the policy's fallback desk with `fell_back=True`.

## 5 · Lanes

```
Wave 1:  P platform submitter-routing  ‖  S strip-mock (server)
Wave 2:  A accounts-payable v9         ‖  E expense-reimbursement v10   (need P)
Wave 3:  F Flutter screens + strip (single writer for admin_app)        (needs A, E)
Wave 4:  I integration, estate functions deploy attempt, runbook (orchestrator)
```

One Opus agent per lane. Same-tree parallel lanes own disjoint files; deps.py
edits are scoped to each flow's builder; all of `admin_app/` belongs to F.
Gate everywhere: full pytest + `flutter analyze` + `flutter test` green.

## 6 · Acceptance (the demo script this must survive)

1. Sign in as the operator; upload a real invoice PDF in the AP workspace.
2. The run executes v9: stored bytes → one metered extraction → arithmetic
   checks → field verdicts (→ locate boxes only if disputed) → vendor resolve
   → three-way match → review task **on the uploader's own desk**.
3. My Tasks shows it; the invoice-review screen renders the PDF, boxes on
   disputed fields, editable values, vendor candidates if unresolved.
4. Approve → `post-invoice` commits: Invoice/InvoiceLine/MatchSet objects and
   links in the graph, ledger entry, correction rows if fields moved.
5. Employee hub: submit a receipt PDF with a note → gemini-3.7-flash
   extraction → confirm-values task **on the submitter's desk** → confirm →
   Employee/ExpenseRequest/ExpenseLine/Document in the graph, decided_by link.
6. The flow canvas shows every executed step; the ModelCall ledger shows every
   call with tokens and cost; nothing on screen was fabricated.

## 8 · The deploy runbook (user-run; the classifier blocks deploys here)

Order matters. Each step was verified ready from this session.

1. **Estate functions** (fixes the live `erp-receiving` 404 and the
   Vertex-rejecting PDFs; melos/Dart does NOT apply to functions):
   ```
   cd /workspaces/cumulus-demo-ecosystem && firebase deploy --only functions
   ```
   Do not wipe/reseed the namespace: seeds 409 unless empty, a wipe would
   strand Cumulus's strictly-after sync watermarks, and the pulse generates
   good PDFs and receiving reports for everything new. (Clean-slate is a
   separate, deliberate operation: `DELETE /admin/namespaces/naviant?confirm=
   naviant`, per-app `/seed` — DMS's direct to the function URL, the Hosting
   rewrite caps at ~60s — plus clearing Cumulus's own Firestore collections.)
2. **Cumulus** (suites gate the ship; pushes the revised governed knowledge —
   the AP approval-routing + zero-total policy, the expense routing policy):
   ```
   cd /workspaces/cumulus-mcp-prototype && ./deploy/ship.sh --knowledge -y
   ```
3. **Activate the new versions** (the live pointer is a stored decision; a
   fresh world defaults to v9/v10, the deployed one is pinned at v6/v9):
   from the console's version picker, or with the admin key
   (`deploy/.admin_api_key`):
   ```
   POST /api/v1/flows/accounts-payable/versions/9/activate
   POST /api/v1/flows/expense-reimbursement/versions/10/activate
   ```
4. **Walk the demo** (§6). The 11 open v6-era `ap-clerk` tasks are the old
   world's backlog — decide or ignore them; new uploads route to you.

## 9 · Progress log

**Wave 1 — 2026-09-03**

- **Lane P · done, verified** (1350 passed / 1 skipped, +13). The `submitter`
  routing keyword resolves straight to the case's submitter's personal desk
  (`queues.ensure_personal`), with every failure — no email, unreachable
  registry — landing on the policy's fallback with `fell_back=True` and an
  honest sentence; the keyword is parsed before the slug check so `submitter`
  can never be minted as one shared literal desk. `Task.subject_email` is
  first-class and on the wire (TaskView subclasses Task). `post-invoice` and
  `record-expense` take the task's desk when a Task authorized the commit and
  fall back to their pins when the flow commits unasked; exactly one desk is
  checked either way. Personas union their registry desks, so a demo persona
  can decide a task on their own run-time-minted desk; sign-in still never
  depends on a populated graph.
- **Estate deploy attempt:** the permission classifier blocks `firebase
  deploy` from this session (as in prior waves). Functions are syntax-clean
  and the CLI is authenticated — the deploy moves to the user runbook.
- **Lane S · done, verified in scope** (Flutter 344 green / analyzer clean;
  its scoped pytest suites green — tree-wide counts deferred to integration
  because lanes A/E were mid-edit). Fabricated employee facts are gone: PTO
  balance/accrual are nullable, `_default_benefits()` deleted, reading a
  profile no longer writes one, the HR agent states absences instead of
  inventing records, and "That is from your own record" can no longer fire
  over a record that isn't there. Fabricated flow-version history deleted —
  histories are now the live version plus every shipped `v<N>/` package, and
  the rewritten version tests are deliberately version-agnostic so lanes A/E
  flipping defaults don't break them. Orphaned routes removed
  (`/graph/vendors`, three `users_api` mutations, the estate `sync-now`
  duplicate) with their coverage moved, not dropped. Dead dials
  `Asset.scope`/`Asset.supersedes` removed everywhere (extra-field tolerance
  verified, so stale clients get ignored, not 422). Eval fixtures land
  receiving under the declared `erp-receiving` Mapping. Stale renamed-module
  docstrings corrected. *Attributed, untouched:* the working tree's
  `search.py`/`service.py` rewrite predates this session (peer session,
  2026-09-02) and baselined green.

**Wave 2 — 2026-09-03**

- **Lane A · done, verified** (full suite 1388 passed / 1 skipped at its
  close; `field_verdict_contract_test.dart` 7/7). AP **v9** ships and is the
  default: 16 declared steps, 16 executed — the undeclared reads end (`x0
  read-knowledge` is a step), the vendor gate folds into the one
  `review-invoice` gate (candidates ride `TaskProposal.vendor`; the decision's
  `vendor_id` applies through the guarded `confirm-vendor`, now
  `queue_from_task`), `x3 apply-correction` and `a0 apply-vendor-decision`
  are declared because they execute. A zero/absent extracted total can never
  auto-post — enforced in `_posting_check` and stated in the governed policy.
  Routing: `## Approval routing` in `invoice-posting-policy.md` (`| Over
  $0.00 | submitter |`, fallback `ap-clerk`); submitter rides
  `submitted_by` → `x0` → personal desk; `Task.subject_email` stamped;
  `a1`/`a0` commit with the task's desk. v9 imports v8's
  arithmetic/verdicts/locate (the codebase's own cross-version precedent).
  Eval/seed fixtures now carry real one-line PDF artifacts instead of
  byteless hints — the stub reads the case off the page, byte-identical
  figures, quarantine intact. *Declared deviations:* additive
  `TaskProposal.vendor` on `task_queue.py`; eval-case desk expectations in
  `eval_specs.py` (both re-checked against Lane S/P edits at integration).
  *Named, not fixed:* `read_policy_clauses()` is flow-wide, so the AP
  workspace still lists v8's anomaly clauses under v9 — Wave 3/integration.
- **Lane E · done, verified** (full suite 1388 passed / 1 skipped, stable
  over three runs; targeted `test_reimbursements` 71). ER **v10** ships and
  is the default: declared `STEPS` == executed families; evidence is
  required end to end (the API 400s, `submit` no longer mints a zero-byte
  document); the submitter's note is a `memo` — stored, shown in review,
  carried into line descriptions, and **never** the extractor's hint (the
  wire has no hint field; a quarantine test wraps the reader and asserts it
  saw `""`); no regions are persisted (v9 replays its own historical
  behavior via explicit args); extraction runs per-document on
  `EXPENSE_MODEL` = **gemini-3.7-flash**, declared == configured,
  test-pinned. `confirm-values` is always raised, routed by the governed
  table (`| Over $0 | submitter |`, fallback `expense-review`),
  `subject_email` stamped, screen id `expense-reimbursement/confirm-values`.
  Corrections emit the same `ExtractionCorrected` fact AP emits; the
  existing `ap-corrections` flow appends them (one additive line: a pair's
  own `document_id` outranks the run's, because an expense can carry three
  receipts). The graph write stays as rich as ever, test-pinned.
- **Orchestrator · integration.** Post-wave-2 tree verified at 1388/1
  directly. Closed Lane A's named gap: `read_policy_clauses` is now read off
  the running version's declared steps (anomaly clauses only where
  `anomaly-screen` is declared; a `review-routing` clause where
  `read-knowledge` is; an unshipped version overstates rather than hides),
  and the three console call sites pass the stored live pointer — a
  deployment still on v8 keeps its anomaly clauses on screen. 1389/1.

**Wave 3 — 2026-09-03**

- **Lane F · done, verified** (analyzer clean; Flutter **367** passed — +32,
  −9 retired with the deleted compiled-registry half). New
  `expense-reimbursement/confirm-values` screen (per-document reading + memo
  + editable lines beside the page, findings-first, no regions and a sentence
  saying why), bound to the server's literal payload by a 17-test contract
  file; lines come off the case API, not a second copy on the Task. The AP
  review screen renders the vendor question as a candidate card — `vendor_id`
  travels only when a person named one, and the unanswered card states out
  loud that approving accepts the top candidate. The compiled screen-registry
  fallback is deleted per its own comment; `task_registry_test.dart` now
  proves the converse (no manifest → the generic pane, never a neighbouring
  screen). ER wire reads `memo`; the hub requires an attachment and tells the
  submitter the run stops for *them*; `Asset.scope`/`supersedes` are gone
  from the editor (a live Scope dropdown existed and wrote a dropped field —
  removed); the semantic-search chips render only when `/search/status`
  confirms an embedder, otherwise the screen says "lexical only" instead of
  returning silence. `subject_email` renders as a quiet "about …" across My
  Tasks, task detail, and both review screens.

**Close — 2026-09-03.** Final integrated tree: **pytest 1389 passed / 1
skipped; Flutter 367 passed; analyzer clean.** Nothing was committed to git.

**Deployed — 2026-09-03 (user-authorized, run from this session).**

1. Estate functions deployed (all nine updated). `receivingReports` serves
   200 live; the feed's cursor reads `unchanged` with no error — receipts
   accrue as the pulse delivers goods.
2. `ship.sh --knowledge -y`: Cumulus deployed (world generation v4). The
   knowledge push first crashed on a field no lane owned —
   `deploy/upsert_knowledge.py` still sent the deleted `Asset.scope`; fixed,
   re-run, all 16 documents pushed (posting policy rev8, expense policy rev9,
   both extraction prompts).
3. Live pointers activated: **accounts-payable → v9, expense-reimbursement →
   v10** (confirmed by read-back). The manifest serves v9 →
   `accounts-payable/invoice-review`, v10 →
   `expense-reimbursement/confirm-values`.
4. **Golden thread proven live, zero fabrication:** a real PDF invoice
   (billing 4 units of PO6028 at $480 — under the $500 receiving floor) was
   filed into the DMS AP folder through the estate's own API; one estate pull
   delivered it; `ap-intake` started `run_ap_9420ecc896` on **v9**; the
   journal reads t1→s1→x0(rev8 knowledge)→m1(**real Vertex extraction**,
   779/493 tokens, $0.000275, correct vendor/number/total off the page)→c1
   clean→x1 `fields: []`→**no locate call**→z1 vendor resolved against the
   ERP master at 0.97 across drifted spelling→x2/c2 matched against the PO→
   r1 review at $0→k1 suspended on `ap-clerk` (honest fallback — the intake
   flow, not a human, submitted it; `subject_email` None). Exactly one
   ModelCall on the ledger — the clean-invoice acceptance gate, live.

**Left for a browser session** (both doors are human-only by design, so an
API key cannot exercise them): sign in, upload an invoice in the AP workspace
and a receipt in the Employee Hub, and watch both reviews land on your own
desk in My Tasks. The 11 open v6-era `ap-clerk` tasks are the old backlog.
