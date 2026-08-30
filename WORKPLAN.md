# Workplan — Cumulus against the live estate

Supersedes the rebuild workplan (folded in below as "Carried debt"; the old
log's findings live in git history at fa37208 and README.md). Unlike its
predecessor this file is **committed**: fleet agents work in worktrees and
must be able to read it.

**Mission.** Build Cumulus against a live simulation of a customer
environment — the `naviant` namespace of `/workspaces/cumulus-demo-ecosystem`
— improving both sides at once: the simulation gets more realistic, Cumulus
gets real. Naviant's own back office is the customer (the dogfood story).

---

## Decisions of record — 2026-08-30 (Mitchell)

1. Baseline committed (`fa37208`); normal commits from here on, both repos,
   current branches (`workflows-management` here, `initial-ecosystem-import`
   in the ecosystem).
2. Agents may modify **both repos** — the ecosystem toward realism, Cumulus
   toward how the platform should function. The ecosystem's own doctrine
   holds: it never learns Cumulus exists; its "ask before adding" list
   (auth, webhooks, MCP, CI) still requires asking.
3. **Pull, for simplicity.** The connector pulls `?updatedSince=` deltas.
   A completed pull is an **Event**; a **Trigger** binds it; a **Flow** does
   everything after — mapping to the ontology, embeddings, processing raw
   estate data, dedup only where strictly necessary. Delivery goes through
   the **HTTP door** (`POST /api/v1/sources/deliver`), not in-process.
4. **The mock world dies.** All current Cumulus seed data (acme-industrial,
   customer.example, the hardcoded deliveries) is replaced by data synced
   from the estate — which first needs to be built out (Track A). Eval
   fixtures keep their isolated worlds.
5. **Naviant is ~130 people**: Marketing, Sales, Delivery,
   Backoffice/Finance/IT as the core buckets. The HRIS is populated (and
   improved) to that scale. Personas become Naviant people.
6. **The Action Gateway is core.** Read plane first for sequencing, but
   Stage 2 (write-back through the Gateway) is planned now, not later.
7. Process coverage should be **rich and extensive** — workforce, identity
   resolution, and AP all matter; Cumulus must work in a real environment.
8. Ontology grows **slowly and generally** — reusable across customer
   environments, never hyper-specific. Custom ontologies are the product;
   brittleness is not.
9. Simulation controls (pulse intensity, time compression) belong in the
   ecosystem's **admin console**, not in config files.
10. Console effort goes to admin/observability **and** knowledge-curation
    UX: who gets delegated curation work, how it lands in My Tasks, and a
    data dashboard showing the current state of the whole system.
11. Extensive on core functionality, no frivolous features.
12. Model: **`gemini-3.5-flash-lite`** for every ModelCall (config default
    and seeded exports switched). ⚠ Not yet verified live — container gcloud
    credentials need reauth (`gcloud auth login` and `gcloud auth
    application-default login` on the host). First live run must verify;
    `gemini-3.7-flash` is the verified fallback. `gemini-3-flash` 404s.
13. **The estate gains a PSA app** (`psa`) — the project-management tool
    Naviant's Delivery org runs its implementation projects in. Designed
    into the namespace docs first (A1), built as a new demo app in Wave 2
    (A4), synced as a sixth source once live. Naming hazard, stated once:
    `psa` (projects) vs `pas` (the insurance policy system) is a
    one-transposition collision; they never co-occur in a namespace.
14. **Subagents run Opus 5** (`model: opus` on every Agent spawn), not
    Fable.

## The estate (facts)

- Firebase project `cumulus-demo-mitchell-suter`. APIs:
  `https://cumulus-demo-mitchell-suter-<app>.web.app/api/naviant/v1/<resource>`
  for `crm · erp · office · hris · dms`. Unauthenticated, CORS-open.
- Discovery: `GET https://…-admin.web.app/admin/namespaces` (industry, apps,
  processes, pulse state). Nothing hardcodes the namespace list.
- Change detection: `?updatedSince=<ISO>` on every list route, ordered by
  `updatedAt`. Deletes are invisible to it — accepted for this phase.
  The HRIS `GET /employmentEvents` ledger is append-only.
- The pulse is ON at `low`; `sim_naviant.py` drives employee_lifecycle,
  deal_to_order, procure_to_pay. `SIM_DAY_MINUTES = 15`.
- The roster's designed drift records (stale duplicates, bait vendors,
  Thomas Berg, five extra mailboxes, the DMS tree) are **not built** —
  `docs/namespaces/naviant/roster.md`'s answer key is a design target until
  Track A lands them.

---

## Track A — the estate becomes real (ecosystem repo)

- **A1 · Namespace redesign (docs only).** `/design-namespace naviant` →
  dossier/roster/processes rewritten for a ~130-person firm (buckets per
  decision 5), existing seeded records immovable, drift design preserved and
  extended at scale, answer key recomputed. *Running.*
- **A2 · Seed build.** From the A1 docs: HRIS to 130 workers (named cast
  authored, the rest expanded deterministically from a generation spec),
  CRM book scaled (~30–45 accounts, contacts, opportunity history), ERP
  (customers, ~10–15 vendors, items, order history), Office mailboxes for
  the named cast with day-zero traffic, a DMS seed (tree + documents), and
  **every designed drift record** (duplicates, bait pairs, missing-field
  budget). Module improvements the design flags (e.g. seed shape for
  multiple mailboxes) are in scope.
- **A3 · Simulation scale + controls.** `sim_naviant.py` cadences for a
  130-person firm; `SIM_DAY_MINUTES` becomes a per-namespace pulse setting
  editable from the admin console beside enabled/apps/intensity. The
  `project_delivery` process joins the sim once A4 exists (spawned by
  deal_to_order wins).
- **A4 · The PSA app.** A new demo app per the ecosystem's "New demo app"
  checklist (Flutter app, `functions/psa.py`, hosting site + target,
  authorized domain, firebase-options script, `namespaces.json` apps list):
  projects, phases, tasks, assignments, time entries — the entity/route
  sketch comes from the A1 design's "Gaps for build time". Deliberate
  drift: its own customer spellings, display-name assignees, time entries
  overlapping HRIS time with no shared id.

## Track B — the read plane (this repo)

Accepted implementation plan: **WORKPLAN-TRACK-B.md** (stages 1–6, file-level).

- **B1 · The estate connector.** A registered estate (project/base URL +
  namespace, discovered via `/admin/namespaces`) with one connector per
  (app, resource-family): watermark state, `?updatedSince=` pull, page
  through, then `POST /api/v1/sources/deliver`. Pull completion emits the
  Event; the Dispatcher starts the sync Flow. Scheduling: a "Sync now"
  control plus an in-server interval loop locally; the deployed form is a
  sourced scheduler hitting an endpoint (never a warm box of our own).
- **B2 · Mappings for the real sources.** The seven fictional Mappings
  (Coupa, Workday, M365, WMS…) are replaced by the estate's five, each with
  identity bindings, precedence, freshness, custody. `SyncRequest` grows
  record shapes for: CRM accounts/contacts/opportunities; ERP customers/
  vendors/items/salesOrders(+lines)/purchaseOrders(+lines); Office
  mailboxes/messages/events; HRIS workers/org/employment events; DMS
  folders/documents (bytes referenced, fetched on demand — custody
  decision per Mapping).
- **B3 · Ontology growth, general-first.** Likely additions: `Company`
  (canonical counterparty; CRM account / ERP customer / mail domain are
  claims), external `Person`, `Opportunity`, `SalesOrder`(+lines),
  `Item`, mail as evidence (shape decided by the architect — a
  Correspondence/Document treatment, not a full mail store). Existing
  types (Vendor, PurchaseOrder, Employee, Calendar…) are reused, not
  duplicated. Every addition justified by a synced feed or a process.
- **B4 · Seed-world replacement.** Delete the hardcoded deliveries and the
  customer.example workforce; the live world fills only from the connector.
  Personas rebuilt from the Naviant cast (the operator stays
  `masuter@naviant.com`). This also retires the Tomas-Berg-persona vs
  Tom-Berg-Northwind-contact name collision.
- **B5 · Identity resolution / MDM at scale.** Company/Person resolution
  flows: typed identity Links with confidence and method, steward Tasks
  for ambiguous matches, retained claims under precedence. **The identity
  eval**: the roster answer key (which records ARE the same entity, and
  which bait pairs are NOT) becomes an EvalSpec — fixtures snapshotted
  from estate data, graded on proposed links, bait pairs asserted as
  must-not-link.

## Track C — operate, observe, curate (console)

- **C1 · Connectors screen.** Sources evolves: register an estate, per-feed
  status/watermark/last-delta/sync-now, the Mapping contract, freshness.
- **C2 · Identity workbench.** Proposed merges with confidence and
  human-visible evidence; confirm/reject as curation Tasks through the
  Gateway into the ledger; un-merge honored (`reversibleWith`).
- **C3 · Data dashboard.** Current state of the whole system: objects by
  type, claim conflicts, feed freshness, queue depths, ledger activity,
  declared-vs-observed governance gaps.
- **C4 · Curation delegation.** Which queue gets which curation work, how
  it appears in My Tasks per persona, and how an admin rebinds it.

## Stage 2 — the write plane

- **S2a · Disagreement detection.** Cross-system state conflicts (the CRM
  Closed Won / ERP Pending Approval window is designed into deal_to_order)
  detected by a windowed Flow and raised as Tasks with evidence.
- **S2b · Write-back through the Gateway.** Outbound Actions against the
  estate APIs (e.g. release a sales order), committed only through the
  Action Gateway, Task-gated per policy. Echo suppression is watermark/
  value-based (the estate carries no origin field — deliberately; it does
  not know Cumulus exists).
- **S2c · ModelCall governance completed.** `ctx.step(cost_usd=, tokens=)`
  wired at the two real call sites; `/ask`, hr-chat and A2A calls journaled.

## Sequencing

    Wave 0  (done)      baseline commit · model switch · this plan
    Wave 1  (now)       A1 design docs ▸ B1 connector + first feed (HRIS
                        workforce against the current 16-person seed) ▸ C1
    Wave 2              A2/A3 estate at scale ▸ A4 PSA app ▸ B2 all live
                        sources ▸ B3 ontology growth ▸ B4 world
                        replacement + personas (the PSA feed joins B2 as a
                        sixth source when A4 lands — the connector is
                        shaped for N apps, never a hardcoded five)
    Wave 3              B5 identity resolution ▸ C2/C3/C4
    Wave 4              S2a/S2b/S2c ▸ the end-to-end demo script

The demo script (the "done" test): connect the estate → five feeds sync
live → the ontology shows Northwind as one Company with typed identity
links into four systems → the Thomas Berg duplicate is confirmed as a
curation Task → the Closed-Won/Pending-Approval disagreement raises a Task
→ approving it writes back through the Gateway and the ERP changes.

## Gates

`pytest` green (from `server/`) · `flutter analyze` clean · connector work
additionally smoke-tested against the live estate · clarity/code critics
after UI items · prune pass at the end of each wave. Commit messages end
with the Claude co-author line; container git needs
`-c commit.gpgsign=false` (host-only signing key).

## The agent system (carried forward)

| Role | Agent | Brief |
| --- | --- | --- |
| Surveyor | `Explore` | Facts, no opinions. |
| Architect | `Plan` | One cut; names what gets deleted. |
| Builder | `general-purpose` | One item to green gates, in a worktree. |
| Clarity critic | `general-purpose` | 10-second screen test; names concepts to cut. |
| Code critic | `general-purpose` | Simplicity/cohesion/duplication findings only. |
| Pruner | `general-purpose` | Deletes from an inventory, end of wave only. |

Critics report; the orchestrator adjudicates. Builders never widen scope
silently — a brief that contradicts the data gets flagged, not obeyed.
Every subagent is spawned with `model: opus` (Opus 5) — decision 14.

## Carried debt (folded from the deprecated workplan)

- Persona-aware Home (today hardcoded to AP; wrong the moment personas are
  Naviant people). → Wave 2/B4.
- `read_set` declared but unenforced — compare a fixture's declared
  read-set to `CaseResult.knowledge_revisions`. → Wave 3, with B5 evals.
- Retire the stale Dart `AuthoredWorld` model (parses fields the server no
  longer sends). → next Flutter touch.
- Flow-emitted Events are appended, never dispatched (fan-out needs a
  queue); `RunEngine.start` is a discipline, not a chokepoint. → revisit
  with B1 (the connector adds the first real second producer).
- Five feeds have no `FlowDefinition` Tier-2 export ("five flows write the
  graph with no declarative export to audit"). → B2 gives the real five
  their exports.
- Knowledge writes (`service.py`) and PTO approval commit outside the
  Gateway (named deferrals). → S2 window.
- Eval authoring paths still missing: promote-from-run, upload-and-label,
  synthesize variants; document three states (bytes/facsimile/unread) —
  real DMS bytes make "unread" reachable at last. → Wave 3.
- `x3` means different work in AP vs expense (step-id hygiene). → any
  builder touching either flow.
- Prune leftovers: `Persona.person`/`title` duplicating graph `Employee`
  (moot after B4); `deps.get_employee_service` ordering call;
  `tasks_api` `mine` param. → wave-end prunes.

## Log

- **2026-08-30** — Wave 0: venv repaired (pytest module was missing);
  baseline committed (`fa37208`, 621 passed / 1 skipped, analyze clean);
  model switched to `gemini-3.5-flash-lite` (config default, seeded
  exports, README/deploy.sh; downgrade-ladder tests moved onto probe
  flows so the seeded exports stay truthful); live model verification
  blocked on host gcloud reauth. A1 namespace redesign agent launched.
  Estate probed live: pulse on, 15 sales orders (10 sim-generated),
  designed drift records confirmed absent.
- **2026-08-30 (Wave 1 lands)** — A1 committed (`7a55e03`, ecosystem): 130
  people, 4 buckets over the 7 immovable departments + Marketing/Bureau/IT,
  35 named cast + generation spec, PSA designed in with `project_delivery`,
  drift 60/30/10 over 30 matched entities, gaps re-derived (ATS is
  salaried-only; HRIS phone formula collides past index 16). B1 committed
  (`2e49db7`): estate connector live-smoked — 25 workers pulled, 22
  Employees landed (3 real duplicate-email pairs in the live HRIS collapse
  under the natural key — B5 substrate), watermarks strict->, replay
  idempotent. B3 merged (`da4fec8`): 26 types / 38 links, 4 new
  collections, precedence `human,erp,hris,crm,calendar,office,procurement,
  flow,model`; §4's same-name sibling links were a validator trap — landed
  as `company_identified_as`/`person_identified_as`/`subfolder_of`/
  `sent_by_employee`/`addressed_to_employee` + a uniqueness rule. Combined
  gates: 657 passed / 1 skipped, analyze clean, 54 Dart tests. Known:
  Agent worktrees may branch from the initial commit — builders must
  verify their base. `_authority` has no `dms` bucket (3c confirms).
- **2026-08-30 (later)** — Decisions 13/14 recorded: the PSA app joins the
  estate (A4), subagents run Opus 5. Both in-flight Wave-1 agents briefed
  mid-run (A1 designs the PSA in; the architect shapes the connector for N
  apps).
