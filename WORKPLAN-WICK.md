# WORKPLAN-WICK — a second customer namespace, and three RPA designs become Flows

**Status: PLAN FOR REVIEW, 2026-09-07. Nothing is built.** Author: orchestrator
session with two Opus 5 planning agents (one per repo). Inputs: three Naviant RPA
Solution Design Documents for Wick Buildings (digest committed at
`cumulus-demo-ecosystem/docs/namespaces/wick/sources/sdd-digest.md`), `DOCTRINE.md`,
`WORKPLAN.md` decisions 8 (ontology grows slowly and generally) and 14 (subagents run
Opus 5), and both repos' code as of this date.

This file has three parts. **Part I** (this one) is the plan of record: the brief,
the binding principle, the decisions to confirm, the seam between the two repos, the
waves, acceptance, the runbook, and the open questions. **Part II** is the estate-side
plan in full (repo `cumulus-demo-ecosystem`). **Part III** is the Cumulus-side plan
in full (this repo). Parts II and III are the planners' v2 texts; where Part I
corrects them, Part I wins.

---

## 1 · The brief (user's words, made operational)

Create a `wick` namespace in both repos. In the prototype, evolve it for the Wick
use case while **keeping the accounts-payable and expense flows as they are**. From
the three SDDs, produce the ontology, the Flows, and the requisite workspaces. Plan
first, with Opus 5, then execute.

**Correction, given mid-planning and binding:** do not re-create what the SDDs
describe. Much of it happens in Excel or in SharePoint only because Wick has no
system for it; in Cumulus that state is Datasets and part of the knowledge graph.

## 2 · The binding principle — what lives where

The SDDs describe how Wick works around missing systems. The estate models only
Wick's **real systems of record**; the working state the bots were going to keep in
spreadsheets and folder trees becomes graph Objects and governed Datasets; Cumulus
writes nothing of it back. **v1 makes no outbound write to the estate at all.**

| SDD artefact | Where it lives now |
|---|---|
| AXIS job record: number, customer, builder, status, load date, week position, Out-to-Yard, Do-Not-Fab, dimensions, ten per-department Done checkboxes | **Estate** — ERP `jobs` (NEW entity). Synced → graph `Job`. |
| The fleet: trucks, trailers, lifts and their last/next inspection dates | **Estate** — ERP `assets` (NEW). Synced → graph `Equipment` + `Inspection`. |
| Training records; CDL, medical card, PSP, clearinghouse dates | **Estate** — HRIS `trainingRecords` (+ `result`), HRIS `driverQualifications` (NEW). Synced → graph `Credential`. |
| Crews and foremen; plant departments | **Estate** — HRIS departments (crew = department, foreman = head). Graph `Team` / `Employee`, reused. |
| Shipping lists (pages A1/B/C/D/E/F/LL), cut sheets, the portal's four scanned job documents, signoff sheets, medical cards, inspection certificates | **Estate** — DMS documents that really are documents. Graph `Document` / `Folder`, reused. |
| Mail between real people | **Estate** — Office mailboxes for the cast. Unchanged feed. |
| The per-foreman folder tree (`Construction Details`, `JobSite Pictures`, `Archive`) and its create/copy/move/delete | **Graph** — `Job —documented_by→ Document` at the latest revision, `Job —assigned_to→ Team` with validity; a packet is a query, "no longer this crew's" is a withdrawn edge. No folder is created, moved or deleted. |
| The weekly crew-schedule PDF and the mail that carried it | **Nowhere.** Crew assignment is a column on the AXIS job. No PDF, no parse, no ModelCall for SDD 1. |
| The daily department scheduling workbooks, the bonus cell, the typed headcount | **Graph** — `ScheduleDay` + `ScheduleEntry` parts, `authoritative=True`: Cumulus is the system of record for a plant schedule. Headcount = count of `member_of` the department Team. Incentive = a pure Rule over two governed values. The "workbook" is a derived Dataset export (CSV) of an approved day. |
| The Excel training matrix, DOT tracker, inspection tracker, "Last Notified" and "Last Run Status" cells | **Graph** — `Credential` / `Inspection` with `last_notified_on`, `last_notice_stage`, `status`, `retain_until`; history on the ledger. |
| Truss_Standards_2026.xlsx; min/max/fudge table; lead times; farthest-out windows; headcount→capacity ladder; labor-hour formulas; eligibility clauses; the AXIS vocabulary (OTY/DNF/Week 1–3); the folder-naming convention | **Governed Datasets** (`server/seed/*.md`): `plant-scheduling-parameters`, `labor-hour-formulas`, `truss-standards`, `field-documents-policy`, `certification-alert-policy`, plus two prompts. Arithmetic in one pure module with no literal. |
| Action Center reviews and approvals | **Tasks** routed to Queues (`plant-supervisor`, `field-operations`, `hr-safety`). |
| Exception / Failure / Stats e-mails | **Run journals, workspace state, aggregate Tasks.** No mail in v1. |
| UiPath Orchestrator, the Builder Portal as an app, Box | **Not modelled.** The portal is a read over ERP jobs + DMS `Job Documents/<job>`. |

## 3 · Decisions of record (proposed — confirm or amend before any lane starts)

| # | Decision | Recommendation |
|---|---|---|
| **W1** | The principle in §2 | Binding on every lane in both repos. The estate's negative grep (Part II §E.3) and the one-row `WRITABLE` assertion (Part III §F-2) enforce it structurally. |
| **W2** | Namespace | `id: wick`, `name: Wick Buildings`, `industry: post-frame-construction`, `apps: [erp, dms, office, hris]`, `processes: [employee_lifecycle, plant_week, crew_assignment_week, certification_cycle]`; `default` stays `naviant`. No `crm`/`pas`/`psa`. No new demo app (the Builder Portal is a read). |
| **W3** | AXIS = the ERP, narrowed | `erp.py` gains `jobs` and `assets` only, plus a `filters` spec key. Flags are `"Yes"`/`"No"` picklists (the ERP has no boolean type); Cumulus maps them via the governed vocabulary table. |
| **W4** | Cast per namespace | `personas.py` becomes a selector over `server/app/casts/{naviant,wick}.py` on `settings.estate_namespace`; `""`/unknown selects naviant so nothing existing changes. Desks stay one global tuple, plus `field-operations`, `hr-safety`, `plant-supervisor`. |
| **W5** | A Wick deployment is its own Cumulus project | `./deploy/ship.sh -p <wick-project> -e <estate-project> -n wick --knowledge`. Cheaper alternative: same project, `--generation wick-v1`. |
| **W6** | No outbound estate writes in v1 | `estate_writes.py` is not edited. Deferred, recorded: `send-estate-mail` (notifications via Office) and `publish-job-packet` (mirror a packet into SharePoint for crews). Both are second-wave decisions. |
| **W7** | The schedule is Cumulus's | `ScheduleDay`/`ScheduleEntry` `authoritative=True`; approval Task is the permanent control point (SDD 2.5.10); `GET /api/v1/schedule/export?week=&department=` answers CSV for an approved day and 409 naming the Task otherwise. Consequence to accept: if the deployment goes dark, the exports are the copy. |
| **W8** | Numbers are governed knowledge | Every rate, threshold, window and the incentive's start + per-hour rate live in assets; `formulas.py` holds no literal (test-pinned). The four departments Chris Robson owes numbers for carry the SDD's interim rule verbatim under an `## Open` heading. |
| **W9** | Incentive model | Two governed values per department (start, rate), stated as arithmetic on the approval Task so a supervisor can dispute it on day one. Non-linear → a table in the asset, no schema change. |
| **W10** | Packet label convention | `<job number> <first keyword of customer name>`, with governed stopwords (`the, a, an, of`) and a collision rule (append the next keyword). Seeded traps: `The Willows Equine LLC`; two customers beginning `Evermore`. |
| **W11** | Compliance cadence | Daily window; the asset carries `run cadence: daily` and the 60/30/15 stages; `last_notice_stage` makes an empty day a no-op. Resolves the SDD's monthly-vs-daily contradiction out of code. |
| **W12** | Staging | Build **SDD 3's spine first** (`plant-eligibility-sweep` → `shipping-list-extraction` → `department-scheduling`, Lanes O·N·K·S·4·5 + partial 6): it exercises every plane. Then `field-documents` (cheapest, no ModelCall), then `credential-tracking`. All three are in scope; this is the order. |
| **W13** | Real names | Keep the seven SDD names and `wickbuildings.com` (the naviant precedent uses the real domain); everyone else invented; externals on `*.example.com`. A judgment about a real customer's staff, so it is yours. |
| **W14** | Abby Wick holds `ap-clerk` and `expense-review` | Not read off any SDD; it exists so the kept AP and expense flows are testable as a customer persona. Move it if there is a better answer. |

## 4 · The seam between the two repos

### 4.1 The four estate contracts Cumulus reads (Part III §C-5), with the field mapping

Connectors rename and never judge. Left column is what the estate serves; right is what lands.

| Estate (Part II §C) | Cumulus wire shape (Part III §C-4) |
|---|---|
| ERP `GET /jobs`: `jobNumber`, `customerId` (ref), `builder`, `jobName`, `status`, `plantSite`, `foreman`, `crewId`, `loadDate`, `shipmentDate`, `weekPosition`, `outToYard`, `doNotFab`, `shipmentType`, `combinedWith`, `trailerNumber`, `buildingWidthFt/LengthFt/HeightFt`, `wingsTotalWidthFt/LengthFt`, `trussCutDone` … `loadingDone` | `JobRecord`: `job_number`, `customer_name` (**joined** from `customers`, which the feed loads first), `builder_name`, `name` (← `jobName`), `status`, `plant_site`, `foreman`, `crew_id`, `load_date`, `shipment_date`, `week_position`, `out_to_yard`/`do_not_fab` (the literal `"Yes"`/`"No"`), `shipment_type`, `combined_with`, `trailer_number`, `width`/`length`/`height`, `secondary_dimensions` (the two wings fields, verbatim), `upstream_done` (the Done fields that read `"Yes"`, as slugs per §4.2). No `site_address` in v1. |
| ERP `GET /assets`: `assetTag`, `description`, `assetType`, `serialNumber`, `plateNumber`, `homeSite`, `assignedTo`, `vendorId`, `lastInspectionDate`, `nextInspectionDueDate` (null for skid steers), `status` | `EquipmentRecord`: `equipment_id` (← `assetTag`), `name` (← `description`), `category` (← `assetType`), `identifier` (← `serialNumber`, else `plateNumber`), `home_site`, `last_inspection_date`, `next_inspection_due_date`. `make`/`model`/`year` unset (the estate does not carry them). |
| HRIS `GET /trainingRecords` (+ `result`), `GET /driverQualifications`, `GET /departments`; both credential resources carry `employeeId` only | `CredentialRecord`: the `hris-credentials` feed **also pulls `employees`** and the connector joins `employeeId → workEmail` (a rename, not a judgment) to fill `holder_email`; `holder_external_id` = `employeeId`. One `Credential` per training row (`category` certification/training) and per driver date (`licence`/`medical`/`clearance`). |
| DMS `folders` + `documents` (existing shape): `Job Documents/<job>` with the four types, revisions on one job, none on one job; `PlantCutSheets/<site>/<job>` multi-page shipping list + cut sheets; compliance documents | Unchanged `dms-library` feed. `field-documents-policy` names the folder root, the four filename patterns and "latest `latestVersion` wins". |

Not a contract: Office mailboxes for the cast — the existing `office-mail` feed, no attachments, no crew-schedule mail.

### 4.2 Seam corrections (these override Parts II and III where they differ)

| # | Correction |
|---|---|
| **S1** | **Crew identity.** HRIS crew departments carry `costCenter` = the **bare crew id** (`"12"`, `"14"`, `"18"`, later `"21"`), the same string the ERP writes in `jobs.crewId`. The designed drift stays where it belongs, in `jobs.foreman` (`"D. Prellwitz"` vs HRIS `Dale Prellwitz`). The `erp-jobs` Mapping binds `crew_id → Team` by `Team.external_id` = that cost centre. |
| **S2** | **The subcontractor crew never collides with a real one.** Jobs erected by Kickapoo Valley Builders carry `crewId: "SUB-KVB"` and `foreman: "KICKAPOO VALLEY BUILDERS"`; there is no HRIS department for it, so the id resolves to no `Team` and lands in `field-documents`' second aggregate Task — the SUBCONTRACTOR case as an identity question. Cory Meinholz's fourth crew keeps `"21"`. (Part II §B.3 wrote `crewId:"21"` for the subcontractor; that is superseded.) |
| **S3** | **Department identity is one governed table.** `plant-scheduling-parameters` carries a `## Departments` table with three columns — HRIS department name (`Truss Cutting`, `Truss Fabrication`, `Steel Fabrication`, `Trim Fabrication`, `Hardware & Trim Packaging`, `Verticals`, `Components`, `Laminates`, `Stack Out`, `Loading & Yard`), ERP Done field (`trussCutDone` … `loadingDone`), Cumulus slug (`truss-cut`, `truss-fab`, `steel-fab`, `trim-fab`, `hardware-trim-pack`, `verticals`, `components`, `laminates`, `stack-out`, `loading`). `JobTask.department`, `ScheduleDay.department` and `upstream_done` use the slug; `scheduled_by` resolves to the `Team` through the HRIS name. Neither repo hardcodes the other's spelling. |
| **S4** | **Customer name reaches the job by a join, not a field.** The ERP keeps `customerId` (its own convention); the `erp-jobs` feed loads `customers` before `jobs` and the connector fills `customer_name` from it. Part III §C-5 row 1's `customerName` is satisfied this way, not by an estate field. |
| **S5** | **Yes/No stands on both sides** (Part II A5, Part III §C-4). The `## Source vocabulary` table in `plant-scheduling-parameters` maps `outToYard: "Yes"` → `release_state: released`, `doNotFab: "Yes"` → `hold_reason: DNF`, and `weekPosition` → `week_position` 1/2/3/0. |
| **S6** | **`DocumentRecordsDelivered` is the existing `DocumentsDelivered`** (`connectors/sync.py`). One fact, one spelling. |

### 4.3 Answer keys the estate seeds for Cumulus to be graded against

The packet-label key (job → expected label), the expected-hours key (seeded
job-department → hours the governed formulas should produce from the seeded PDF),
the identity key (roster answer key incl. `Jeramy`/`Jeremy`, `D. Prellwitz`,
`Hoffmann Ridge Farms`), the two Evermores and `The Willows`, a job that moves crews
mid-week, a document at `latestVersion > 1`, a job with no portal documents, a job
with no shipping list, a Saturday load date, a job DNF'd after it queued, and two
skid steers with a null due date. All in `roster.md` / `processes.md` (Part II §B).

## 5 · Waves across both repos

```
Estate  A  L0 design docs (/design-namespace wick)                    M
        B  L1 erp ‖ L2 dms ‖ L3 hris ‖ L4 office  →  L5 sim_wick     M M L S · M
        C  L6 functions deploy + seed + smoke (user-run)               S
Cumulus 0  O ontology ‖ N casts+desks ‖ K assets                      M S S
        1  S sync + mappings + the three sync Triggers                 L   (needs O, N; fixtures first, live after C)
        2  4 plant demand ‖ 2 field-documents ‖ 3 credential-tracking  L M M (need S, K)
        3  5 department-scheduling + schedule export                   L   (needs 4)
        4  6 console ‖ E evals                                         L M (need 2–5)
        5  I integration: scheduler jobs, sweep endpoints, README, log M
```

Per W12 the first pass is A → B → C → 0 → 1 → 4 → 5 → 6(partial) → I, then Lanes 2
and 3 and their screens. Lane S can be built against recorded fixtures
(`server/tests/estate_fixtures.py`) before Wave C lands; live verification needs it.

Rules for every builder (from the project's decisions of record): Opus 5; a worktree
must be verified as based on `workflows-management` (`git merge-base
--is-ancestor origin/workflows-management HEAD`) before the first edit; in the
estate repo, `erp.py`, `dms.py`, `sim_naviant.py`, `apps/erp/lib/api_docs.dart` and
`docs/namespaces/naviant/processes.md` carry another session's uncommitted work —
append-only, never revert (Part II §G). In this repo a Trigger row and its Flow's
`v1` package land in the same commit (Part III §D-0: `Dispatcher.emit` does not
guard a missing body, so an orphan Trigger 500s the next delivery).

## 6 · Acceptance

- **Estate:** `import main, sim_wick` clean; the negative grep finds no
  `plantSchedule|bonusPerHour|lastNotified|Crew Schedule Reports|Foremen/|Truss_Standards|Trackers`
  under `functions/` for `wick`; deployed smoke per Part II §E incl. the seeded
  shipping list having seven page objects and `crm…/api/wick/v1/accounts` → 404.
- **Cumulus:** full pytest green (1,389 today + the new files in Part III §F-2);
  `flutter analyze` clean, `flutter test` green; every new `v1` package passes
  "declared STEPS == executed journal"; `test_estate_writes.py` asserts `WRITABLE`
  still has exactly one row; naviant suites byte-identical after Wave 0.
- **The central claims, each an eval:** nothing leaves the graph before a
  department-day is approved and the export 409s until then; both label traps
  resolve; a crew reassignment withdraws an `assigned_to` edge and keeps it on the
  record; a null-due-date asset never alerts; 60/30/15 fire once each per cycle; a
  department whose grid is missing is skipped and the job is not; `m2` is skipped
  when nothing is in doubt; editing `labor-hour-formulas` moves the computed number.
- **Live:** `GET /api/ui/v1/sources` shows the three Wick feeds active and
  `crm-book`/`psa-book`/`erp-sales` absent rather than failed; `graph/invariants`
  reports zero violations after the first pull.

## 7 · Runbook (user-run; deploys are refused from an agent here)

1. Estate: `firebase deploy --only functions --project cumulus-demo-mitchell-suter`;
   seed each app once via the direct function URLs
   (`…cloudfunctions.net/{hris,erp,dms,office}_api/wick/v1/seed` — Hosting caps
   rewrites at ~60 s); pulse on via `PUT /admin/namespaces/wick/pulse`. Reset with
   `./scripts/purge-namespace.sh wick`. Anything under `apps/` is host-only (Dart
   3.9.2 vs melos ^3.13) and optional — the namespace, folders and mail are visible
   without it.
2. Cumulus: `./deploy/ship.sh -p <wick-project> -e cumulus-demo-mitchell-suter -n wick --knowledge`;
   two scheduler jobs guarded on the namespace (`cumulus-compliance-window` daily,
   `cumulus-plant-window` 11:00 CST Mon–Fri); no field window — `field-documents`
   wakes on deliveries. `ESTATE_WRITES_ENABLED` is not part of this runbook.
3. Verify in the order Part III §G-7 gives. Flow-version activation stays a console act.

## 8 · Risks and open questions (consolidated; detail in Part II §H and Part III §I)

| # | Risk / question | Position |
|---|---|---|
| R1 | Cumulus becomes the system of record for a plant schedule (W7) | Honest consequence of not re-creating the workbook. Mitigation: the export is a Tier-1 route. **Open:** a standing weekly export so a cold deployment is never the only copy? |
| R2 | The incentive curve may not be linear (W9) | Stated as arithmetic on the Task so it is disputed on day one; a table replaces two values with no schema change. |
| R3 | Chris Robson's missing min/max for four departments | Interim rule verbatim in the asset under `## Open`; one console edit and a ledger row when he answers. Modelled in the estate only as his unanswered mail. |
| R4 | Field crews may have no Cumulus access | `publish-job-packet` is the recorded second-wave decision (W6). **Open:** workspace + share link, or a SharePoint mirror? |
| R5 | Notifications: the SDDs promise supervisor e-mail; v1 sends none | `send-estate-mail` is the recorded second-wave decision (W6), with recipients as governed knowledge per notice class. |
| R6 | `Dispatcher.emit` does not guard a missing flow body | Co-landing rule (§5). Platform follow-up, deliberately not in this plan: should `emit` record the fact with an empty `starts` instead of raising? |
| R7 | A thin app reads as a healthy feed with zero rows (`erp-vendors` in Wick) | Accept for v1; Lane 6 decides whether the Sources screen can say "the app runs, the resource is empty" without a server change. |
| R8 | The 11:00 window runs every eligible job's extraction synchronously | `ap-intake`'s known cost; bound it in the asset (`jobs per eligibility window: 15`) and watch the first live day. |
| R9 | `dms._pdf` writes one page; off-page text is invisible to a vision model | D1/D2 in Part II are mandatory; the seven-page check is the guard. |
| R10 | Another session's uncommitted work in four estate files | Append-only, never revert; run L1/L2 last or in the main tree (Part II §G). |
| R11 | Real customer names in a demo estate (W13) | Yours to decide. |
| R12 | Temptation to "finish the picture" in the estate — a schedule table, a notified-on stamp, a foreman folder | Each one re-implements what Cumulus is being shown doing and makes the demo circular. Enforced by the dossier's "not needed" list, `sim_wick.py`'s docstring, the negative grep, and the one-row `WRITABLE` test. |

## 8a · Execution log — 2026-09-07

Appended during the build. Part I §3's decisions were confirmed by the user
before any lane started, with the four answers: **all three SDDs, spine first**
(W12); **run straight through** the design gate; **keep the seven SDD names**
(W13); and **tests green, deploys the user's**. Everything below is what the
lanes actually landed and what the orchestrator decided in flight.

### Landed

| Lane | Repo | What landed |
|---|---|---|
| **L0** | estate | `docs/namespaces/wick/{dossier,roster,processes}.md` (596/984/340 lines). Drift measured at 59% clean / 29% near / 12% hard against dials of 60/30/10; 2 duplicates, 2 bait pairs, 15% missing an optional field, 100% of ids distinct. The **interface appendix closes `roster.md`** and is authoritative for every entity, field, picklist and route name, the authored job table, the asset table, and three answer keys (packet label, expected hours, identity). |
| **O** | cumulus | 7 ObjectTypes, 11 LinkTypes, 3 Collections, 7 `names.py` constants; `PRECEDENCE` unchanged; a 28-type snapshot test proving no existing declaration moved. |
| **N** | cumulus | `server/app/casts/{__init__,naviant,wick}.py` selected on `settings.estate_namespace` (`""`/unknown → naviant); `personas.py` keeps its whole public surface as a selector; 3 desks added. |
| **K** | cumulus | 7 governed Datasets in `server/seed/`, holding every number from the three SDDs. |
| **S** | cumulus | 3 feeds, 3 Mappings, `connectors/{production,compliance}.py`, 3 thin flow packages, 3 Triggers, a recorded Wick estate in `estate_fixtures.py`. |
| **L1** | estate | ERP `jobs` + `assets`, the `filters` spec key, `_references` over both, two seed loops, the Saturday-snap date helper; 22 jobs and 14 assets diffed field-by-field against the interface appendix. |
| **L2** | estate | Multi-page `_pdf` (verified: 7 page objects, 7 content streams, text extractable from every page) with the single-page path **byte-identical**; 48 folders / 74 documents; grids derived from each job's ERP dimensions and checked against the expected-hours key with zero mismatches. |
| **L5** | estate | `namespaces.json` gains `wick`; `sim_wick.py` (3,612 lines) drives the four processes. Verified by three independent 2,000-tick runs: ~5,850 steps each, **zero failures**, no instance abandoned or spinning, every process reaching a terminal state, one PDF write and at most one job mint per tick, and the sequencing invariants asserted (truss fab never before the saw, hardware pack never before trim fab, skid steers never touched, `assignedDate` never moved). Apps-off runs degrade cleanly. **The estate half is complete.** |
| **L4** | estate | 9 mailboxes, 39 messages, 20 events; 88 of 88 facts quoted in prose verified against `roster.md`; the negative greps clean. |

| **2** | cumulus | `field-documents` — the whole Field Documents SDD as one windowed Flow. Both packet-label traps pass and are proven governed: dropping `the` from the asset's stopword list turns the label into `250661 The`. A crew move withdraws the old `assigned_to` edge and keeps it on the record. 40 tests. |
| **3** | cumulus | `credential-tracking` — all five sub-processes of the Training SDD as one windowed sweep. 44 tests. |

| **4** | cumulus | `plant-eligibility-sweep` + `shipping-list-extraction` + `formulas.py` + `parameters.py`. The arithmetic module holds **no literal** — proven by an AST walk and behaviourally, by editing five shapes of constant on the governed pages and watching the figures move. 64 tests. |
| **5** | cumulus | `department-scheduling` + the CSV export. The plant spine now runs end to end from one timer. 33 tests. |
| **6** | cumulus | The console: three workspaces, four review screens, three BFF endpoints, and resume hooks for all four task-raising flows. Flutter 367 → **403** tests. |
| **E** | cumulus | Six `EvalSpec`s, every fixture built from **deliveries** rather than objects — which is stronger than the plan asked for and necessary: `seed_fixture` runs objects after deliveries, so a `Team` written as an object would land after the job that binds a crew to it, and every crew would resolve to nobody. |
| **I** | cumulus | The two sweep doors (`POST /api/v1/compliance/sweep`, `/api/v1/plant/sweep`) with a `sweep()` on the compliance service to match the plant one, their tests, the two namespace-guarded scheduler jobs, and the corrected `job-sync` map description. |

### Decisions taken in flight (not in the plan as approved)

1. **Katy Tiller holds `hr` as well as `hr-safety`.** Lane N found the Employee Hub files a person's own requests to `hr` (`employee.HR_QUEUE`), which no Wick persona held — so a Wick time-off request would have arrived on a desk only the operator can see. Wick has an HR function and she runs it.
2. **The A-11 AP/expense asset edits are cancelled.** `server/seed/` is namespace-agnostic and shared across deployments, so pointing `ap-intake-policy`'s invoice folder at Wick or re-banding expenses would break the naviant deployment. If a Wick deployment ever needs different AP numbers the honest mechanism is a per-namespace override or a separate corpus, not an edit there. The user's instruction to keep AP as it is settles it for now.
3. **`deploy/upsert_knowledge.py` was pushing every asset as `kind: document`.** Its `FIELDS` list omitted `kind`, `origin`, `parent` and `related`, so a deployed corpus ranked its own prompts as if they were policy while the seeded corpus was correct. Fixed. The other half cannot be fixed there: `AssetUpdate` has no `kind`, so a re-push cannot correct an asset already stored wrong — recorded in the script.
4. **`Team` gains a declared `cost_center` field** (Lane S2). Seam correction S1 binds `crew_id → Team` by the HRIS cost centre, but the workforce feed lands `external_id` as the department id and `Team` had no cost-centre field, so there was nothing to resolve against. A department whose primary id is the bare string `"12"` is not what an HRIS looks like; the graph should hold the fact it joins on.
5. **`job-sync` claims `release_state`, `hold_reason` and `week_position`** by reading the `## Source vocabulary` table (Lane S2). The estate connector rightly refuses to translate — that would put a governed decision below the Mapping — but a flow body may read governed knowledge, and leaving the fields empty would have forced the eligibility Rule to read raw strings out of a `SourceRecord` payload.
6. **The equipment site vocabulary needs a rename table** (Lane S2): `assets.homeSite` is `Mazo` / `Fork West` while the HRIS departments are `Plant — Mazomanie` / `Plant — Fork West`, so `operated_by` resolved to nothing for every asset.

7. **`hris.py` keeps its new optional `assignedDaysAgo` seed key** (Lane L3 asked). It is additive, defaults to the previous expression, leaves naviant and insurance byte-identical, and it is what lets a seeded certification show an original award date years before its latest sitting — without it the recert rule the module documents is a rule the data never demonstrates.

8. **`release_state` carries the governed page's own two words**, `released` and `not released`, and the speculative `held` / `staged` from the plan are written by nothing and no longer named in the ontology. Lane S2 chose to claim the page's spelling verbatim rather than translate a second time invisibly, which is right: the governed table is where a customer declares these words, and a synonym minted in code would be a translation nobody could read. Eligibility therefore reads as two clauses — released, and no hold reason — because out-to-yard and do-not-fab are independent flags in the source.

9. **Supersession is the withdrawn link, not a field on `Document`.** Lane 2 found `Document` declares no status field and asked whether to add one. It should not be added: the packet's previous `documented_by` edge is **withdrawn and kept on the record**, exactly as `assigned_to` handles a crew move, so "which revision did this replace, and when" is already a query over link validity rather than a flag needing its own lifecycle. The extra claim Lane 2 writes on the superseded row to carry the determination is redundant under that reading and is a cleanup candidate once the workspace shows what it actually needs.

10. **The two plant scheduler jobs are guarded on the namespace**, and there is deliberately no third for `field-documents`. A naviant deployment has no jobs and no credentials, so scheduling the sweeps there would run two passes a day over an empty graph and put two flows on the fleet page whose every run found nothing — noise that reads exactly like a broken feed. `field-documents` needs no timer at all: it wakes on `JobRecordsDelivered` and `DocumentsDelivered`, which the estate pull already produces every fifteen minutes, so a timer would ask a question two deliveries had just answered.
11. **`ScheduleDay.exported_at` was dropped from the ontology.** It was declared and written by nothing, and it could not honestly be written: a GET must not write, and an export is regenerated on request and stored nowhere, so "when was this last handed out" would be a record of reads. The graph does not keep those.
12. **Trim Fab's incentive contradiction is stated in the governed page rather than smoothed away.** Its crossing (29.03 h) sits above its own few-specials ceiling (28 + 1), so that department can never get out of the hole on a light day. The incentive parameters were reconstructed from the shape of a spreadsheet formula rather than copied from one, so either they are wrong or a light trim day genuinely never pays — and the page now says so and asks. The schedule fills to the ceiling and the approval task reports the shortfall meanwhile.

13. **The people system's course taxonomy is a rename, and the connector had never learned it.** Found by the fixture re-recording: the HRIS files a course by the department that runs it (`Safety`, `Equipment`, `Compliance`, `Technical`, `Professional`) while the ontology files a credential by what it *is*. Every training credential in the namespace was landing with no category at all, so the compliance window — which selects on the governed `categories tracked` list — could not see one, which is half of what that flow exists to do, failing silently. `CREDENTIAL_CATEGORIES` now holds all five words: `Equipment` is a certification (awarded, expiring, and what the 60/30/15 notices were designed around), the rest are training. The unmapped counter stays asserted, at zero, because it is the thing that would notice a sixth course category arriving.

### Queued integration items

| # | Item | Owner |
|---|---|---|
| **I1** | **RESOLVED.** The `wick` block was re-recorded from the estate's own seed — it had disagreed on *every* list, not just jobs and customers. A two-way script now checks 180 fixture rows against `erp`/`hris`/`dms` `_SEED` and reports **no disagreement**: every job number, customer, crew, person and asset tag in the fixture is a row the estate seeds. Six assertions changed because the estate contradicted what the fixture used to claim — among them that no Wick department lacks a cost centre, and that an unpairable equipment site cannot arrive at all, because the ERP picklists `homeSite` to two values and both are paired. | a reconciliation lane, after L5 |
| **I2** | **RESOLVED** (L5). `dms.py`'s wick compliance documents now compute their dates from the offsets that drive them, via a local helper, with `_SEED["wick"]` built at seed time rather than module import so a warm instance cannot date a certificate from its own start day. Filename token and body agree on all six, and each medical card's "24 months from the exam" now reproduces the HRIS expiry exactly. | L5 |
| **I3** | **RESOLVED** (L5). The cell was corrected, not the rule: `260478` reads `livestock = no`. The premise that no code depended on it was wrong — the cut-sheet builder reads the same flag, and the seeded sheet carries no Perma Column bases, so adding `cow` to the keywords would have put the answer key at odds with the paper the estate holds. | L5 or a docs pass |
| **I4** | **RESOLVED** (L5). `POST /wick/v1/seed` is routable on all four apps once functions are deployed. | L5 |
| **I7** | **`flows.py`'s `job-sync` description is stale in two places** — it says the crew id "is the HRIS department's own id" and that "Nothing here translates the ERP's `Yes`/`No` flags". Lane S2 made both false. Left alone because two lanes were editing that file; fix in the integration pass. | Lane I |
| **I8** | **RESOLVED** by inspection, not deferred to the first pull. The estate's HRIS serves `departments.costCenter`, its `id_prefix` is `DEPT`, and L3 seeded the crew cost centres as the bare ids — which is exactly what Lane S2's fixture and the `erp-jobs` Mapping assume. A cross-repo contract check also confirms **every column Cumulus reads is served**: the job feed's 21, the asset feed's 8, and the credential feed's 9, with nothing missing on any of them. | Lane I / first pull |
| **I10** | `eval_specs.py`'s Wick read-sets are still built on the pre-recording synthetic ids (`DEPT-2031`, `EMP-2003`, `TR-3001`, `AST-7001`…). They are self-contained deliveries so nothing is broken and every spec grades green, but they describe a world the estate does not seed. The natural follow-on to I1. | a later pass |
| **I9** | **No `EvalSpec` exists for any Wick flow.** Lanes 2 and 3 each added none (it was in neither's file list), so §F-1's specs are all open. Every claim they name is covered by unit tests today, but the fleet page will report a flow with runs and no declarative export to audit — which is the finding that page exists to make. | Lane E |
| **I6** | `Hardware & Trim Packaging` had no supervisor in the design (the four named plant supervisors cover nine of ten departments). L3 assigned Lonnie Frei, so the dossier's cast row for him is one department short of what the seed holds. Docs nit. | a docs pass |
| **I5** | Three governed `## Open` items stay open by design and are carried in the assets rather than resolved: Chris Robson's min/max for Verticals, Laminates, Stack Out and Components; whether the incentive curve is linear; and whether the invented holiday calendar is the plant's real one. | the user, or Wick |

## 8b · The runbook, as built

Deploys stay yours; everything below was verified locally and is the sequence to
run. The estate half must land first — Cumulus cannot be verified against a
namespace that does not exist.

### 1 · The estate
```bash
cd /workspaces/cumulus-demo-ecosystem
firebase deploy --only functions --project cumulus-demo-mitchell-suter
```
Then seed each app once, through the **direct function URLs** rather than the
Hosting rewrite, which caps at about 60 seconds (the DMS seed writes 74 PDFs and
the HRIS seed builds 71 workers):
```bash
BASE=https://us-central1-cumulus-demo-mitchell-suter.cloudfunctions.net
for app in hris erp dms office; do
  curl -X POST "$BASE/${app}_api/wick/v1/seed"
done
```
Each `/seed` answers 409 unless the namespace is empty and 404 without a `_SEED`
entry, so a partial build fails loudly rather than half-filling. Reset with
`./scripts/purge-namespace.sh wick`. Turn the simulation on from the admin
console, or `PUT /admin/namespaces/wick/pulse` with `enabled`, `intensity` and
`simDayMinutes`.

Anything under `apps/` is **host-only** — the container's Dart is 3.9.2 and
melos wants ^3.13 — and it is optional: the namespace, the document tree and the
mailboxes are visible in the deployed apps without it. Only the new ERP and HRIS
entity tabs need that build.

### 2 · Cumulus
Into its own project, because `ESTATE_NAMESPACE` is a per-deployment pin and the
corpus holds the customer's own governed knowledge (W5):
```bash
cd /workspaces/cumulus-mcp-prototype
./deploy/ship.sh -p <wick-cumulus-project> -e cumulus-demo-mitchell-suter \
                 -n wick --knowledge
```
`--knowledge` pushes the seven new governed Datasets. `deploy.sh` adds the two
namespace-guarded scheduler jobs on the way through. `ESTATE_WRITES_ENABLED`
does not appear anywhere in this runbook: v1 makes no outbound write to the
estate, and the one-row `WRITABLE` assertion in `test_estate_writes.py` is what
keeps that true by accident-proofing rather than by discipline.

### 3 · Verify, in this order
1. `GET /api/ui/v1/sources` — the three Wick feeds active, and `crm-book`,
   `psa-book` and `erp-sales` **absent rather than failed**. Assert the
   sentence; do not assume it.
2. `POST /api/v1/estate/pull`, then `GET /api/v1/graph/invariants` — zero
   violations.
3. The **Field Documents** workspace: both packet-label traps resolved
   (`250661 Willows`, and the two Evermore companies disambiguated), and at
   least one job showing a former crew beside its current one.
4. `POST /api/v1/compliance/sweep` — the 60/30/15 stages, and the four exception
   classes as at most four Tasks.
5. `POST /api/v1/plant/sweep` — eligible jobs, one extraction each, department
   days opened.
6. Review one extraction and approve one department-day as
   `customer-user:plant-manager`, then pull
   `GET /api/v1/schedule/export?week=…&department=…`. It must 409 before the
   approval and answer CSV after.

## 8c · Deployed 2026-09-08, and what the first live run taught

Both halves are deployed and verified against each other. The estate is in
`cumulus-demo-mitchell-suter` (nine functions, all four apps seeded, pulse on at
`low`); Cumulus is in `cumulus-mcp-prototype`, revision
`cumulus-knowledge-00134-r9l`, pinned `ESTATE_NAMESPACE=wick` on collection
generation `wick-v1` with writes disabled. Naviant's `v4` collections are
untouched and readable; `./deploy/ship.sh -n naviant` switches back.

Live results on the first pull and the two windows:

| | |
|---|---|
| Graph | 23 Jobs · 80 Credentials · 71 Employees · 24 Teams · 14 Equipment · 12 Inspections · 74 Documents · 48 Folders · 12 Companies. **Zero invariant violations.** |
| Compliance window | 92 records considered, 41 due, alerted at 60/30/15 in 7/11/23. Two aggregate Tasks. The two skid steers correctly untracked. It found the designed cases: Josh Ripp's medical card ten days lapsed, and a worker terminated months ago still holding live credentials. |
| Plant window | 23 jobs considered, 8 eligible, 15 excluded each with its own named reason (`load-date-past` 6, `not-a-business-day` 7, `held` 1, `not-released` 1). |
| Extraction | **Real Gemini calls over real seven-page PDFs.** `gemini-3.5-flash-lite` read job 260431's A1–LL at confidence 0.98 across 63 field scores; the arithmetic check caught a genuine disagreement (a components grid whose 18 lines add to 486 against a printed total of 18); the governed formulas computed eight departments' hours; and `gemini-3.7-flash` located the one disputed field of 109 — the second call skipped for every field that was not in doubt. |
| Packets | **Both label traps resolved on production data**: `250661 Willows` through the governed stopwords, and `250774 Evermore Farms` through the collision rule. One unresolvable crew, which is the subcontractor. |
| Queue | Five real Tasks: two extraction reviews on `plant-supervisor`, an unresolvable crew on `field-operations`, and two compliance classes on `hr-safety`. |

### Three things the deploy taught, which local testing could not

1. **The knowledge push refused all seven governed documents**, because an asset's `owner` must name a team the deployment holds and `field-operations`, `hr-safety` and `plant-operations` did not exist. The **seeded** path does not validate the owner and the **API** does, so the suites were always going to pass and the deploy was always going to fail — the same seeded-versus-deployed divergence as the `kind` bug in `upsert_knowledge.py`, found the same way. Fixed by creating the three teams (the precedent is the existing `system:team-bootstrap`, which imported teams from asset ownership); the seven documents then landed at revision 1. **A fresh Cumulus project will hit this again** unless the teams are created before the first `--knowledge` push, or the bootstrap is taught to read owners off `server/seed/`.
2. **Six of the eight eligible jobs have no plant shipping list**, so the extraction skipped them and said so, exactly as the governed page instructs. The estate files `PlantCutSheets/<site>/<job>` for seven jobs, but only two of those are currently eligible — the rest are already In Progress or their load date has passed. Nothing is wrong, and it self-corrects: `plant_week` files a cut sheet and a shipping list for every job it mints, so the overlap grows as the pulse runs. Worth knowing before demoing on a freshly seeded namespace.
3. **PSP and clearinghouse dates read as expiries, and they are not.** The HRIS dates them as *when the query was last run* — they run backwards — so the sweep reports them "expired 402 days ago" and alerts on them. Defensible, since the SDD lists both under the DOT dates to track, but it reads oddly and it is the largest single contributor to the 42-row unreadable-date exception class. Either the connector should not emit a backwards-dated column as an expiry-bearing credential, or the governed policy should treat `clearance` on its own terms. **A modelling question for Wick, not a bug.**

### Not exercised, and why

No `department-scheduling` run yet: both extractions that had paper suspended on their review Task, so no `JobTask` demand exists and the department days opened with nothing to schedule. That is the human gate working. Answer either review in the console as `customer-user:plant-manager` and the day follows, ending at an approval Task — after which `GET /api/v1/schedule/export` answers CSV where it currently answers 409.

## 9 · Where the detail is

- **Part II** below — estate plan v2: code-truth findings, decisions A1–A19, the
  three design documents' outlines (cast, roster, four process step tables), the
  `erp.py`/`dms.py`/`hris.py`/`office.py` changes and seed scope, `namespaces.json`
  + `sim_wick.py`, validation, deploy, lanes L0–L7, risks H1–H20.
- **Part III** below — Cumulus plan v2: decisions A-1…A-11, ontology (7 types, 11
  links, 3 collections), feeds/mappings, the five business Flows step by step, five
  Gateway Actions, three workspaces with BFF shapes, evals and tests, runbook, lanes
  O·N·K·S·2·3·4·5·6·E·I, risks I-1…I-15.
- The SDD digest: `cumulus-demo-ecosystem/docs/namespaces/wick/sources/sdd-digest.md`.
- Estate `/design-namespace wick` output (Lane L0) will land at
  `cumulus-demo-ecosystem/docs/namespaces/wick/{dossier,roster,processes}.md`.

---


# Part II — Wick Buildings (`wick`): the estate-side plan (v2)

Repo: `/workspaces/cumulus-demo-ecosystem`. Read-only planning output; nothing in
either repo was modified. For two readers: Mitchell (review before approving)
and the Opus builder agents that execute it without re-asking.

**v2 supersedes v1.** v1 modelled the SDDs' *working state* — daily scheduling
workbooks, a bonus cell, a typed headcount, three tracker spreadsheets, a
"Last Notified" column, per-foreman SharePoint folder trees, a truss standards
sheet — inside the estate. That was wrong, and the correction is binding.

---

## 0. The binding principle

The three SDDs describe **how Wick works around systems it does not have.**
Excel trackers, daily department workbooks, a "Last Notified" cell, a headcount
typed into a sheet, a bonus-per-hour formula, a truss standards spreadsheet,
per-foreman folder trees in SharePoint — that is **RPA-era working state**, not
a system of record. In Cumulus it becomes graph **Objects** (Job, JobTask,
ScheduleDay/ScheduleEntry, Credential, Equipment/Inspection) and governed
**Datasets** (parameters, formulas, truss standards).

**The estate therefore models only Wick's real systems of record**, as ordinary
line-of-business apps would hold them:

| System | Plays | Holds |
|---|---|---|
| `erp` | **AXIS** | jobs with flags, dimensions and the ten per-department Done checkboxes; the fleet/asset register; ordinary purchasing |
| `hris` | the people system | employees, crews as departments, training records, driver qualifications |
| `dms` | SharePoint | **documents that really are documents** — engineering plans, shipping lists, cut sheets, site maps, signoff sheets, medical cards, inspection certificates, employee files |
| `office` | Outlook | mail between real people |

Two consequences, both absolute:

1. **The estate leaves all working state undone.** No schedule, no computed
   hours, no notification history, no assembled job packet, no derived
   parameters. That absence *is* the demo: it is what Cumulus supplies.
2. **Cumulus does not write working state back to the estate.** Estate writes
   from Cumulus are writes to a system of record — a Done flag, a recert row, an
   inspection date — never a schedule row or a notified-on stamp.

### What was removed from v1, and where it now lives

| v1 estate artefact | Status in v2 | Where it lives instead |
|---|---|---|
| ERP `plantSchedules` entity, `_clean_rows`, `totalHours`, `bonusPerHour`, `crewCount`, the "hand-written fallback" step, 60 seeded schedule records | **REMOVED** | Cumulus graph Objects (ScheduleDay / ScheduleEntry) + workspace + a Dataset export |
| DMS `Plant Schedules/2026/Week NN/<Dept>` folders and documents | **REMOVED** | as above |
| DMS `Plant Standards/Truss_Standards_2026.pdf` | **REMOVED** | Cumulus governed knowledge (a Dataset) |
| DMS `Compliance/Trackers/*.csv` (the three Excel trackers) | **REMOVED** | Cumulus Objects: Credential, Equipment/Inspection |
| `lastNotifiedDate` on `trainingRecords`, `driverQualifications`, `assets` | **REMOVED** | Cumulus notification history |
| DMS `Foremen/<name>/{Construction Details,JobSite Pictures,Archive}` tree, its job subfolders, stale/archived folders | **REMOVED** | Cumulus assembles the packet as Job→Document links; a crew reads it in Cumulus. A later "publish packet to SharePoint" is a Cumulus-side decision, not an estate build |
| Gap D3 (`PATCH /folders/<id>`, move/rename) and D4 (`?parentId=`) | **WITHDRAWN** | nothing needs them now |
| Crew-schedule PDFs, `Crew Schedule Reports/2026/Week NN`, decision A7 (mail attachments), the `uipath@` shared mailbox, the weekly `no-reply@` mail | **REMOVED** | crew assignment is **data in AXIS**; there is no bot to model |
| Process `crew_schedule_week` | **RENAMED** `crew_assignment_week` | see B.3 |
| `jobDepartments` child entity (v1 alternative to A4) | **not needed** | upstream state = the ten Done flags; department *demand* comes from Cumulus's own extraction of the shipping list |

### What stands from v1, unchanged

ERP `jobs` exactly as designed (job number, `customerId`, `builder`, name,
status, `plantSite`, `foreman` + `crewId` as typed drift, `loadDate`/
`shipmentDate`, `weekPosition`, `outToYard`/`doNotFab` as Yes/No picklists,
`shipmentType`/`combinedWith`/`trailerNumber`, building + wings dimensions, the
ten `…Done` picklists) plus the `filters` spec key · ERP `assets` · HRIS
`driverQualifications` · `trainingRecords.result` (Pass/Fail — HR data) · crews
as HRIS departments with the foreman as head, plant departments spelled the
HRIS way · DMS `Job Documents/<job>` and `PlantCutSheets/<site>/<job>` with
multi-page shipping lists · compliance documents that are genuinely documents ·
Office mailboxes for the named cast with realistic mail · the four processes
(one renamed) · the answer keys · decisions A1, A2, A5, A6, A8, A9, A11, A13,
A15, A16, A17, A18 · gaps D1, D2 · the deploy path.

---

## Code-truth findings (facts about the code, v1 verified, v2 relevance marked)

| Finding | Bearing on v2 |
|---|---|
| **ERP has no job/production/plant concept** (`customers, vendors, items, salesOrders, purchaseOrders, receivingReports`), but `ENTITIES` is metadata-driven and the router is generic (`segments[0] in ENTITIES`) | `jobs` and `assets` are mostly table data, not code |
| **ERP has no boolean type and no arbitrary array type** — only the special-cased `lines`/`receipt_of`; `_clean` demands `isinstance(value, str)` outside `numbers` | flags stay `"Yes"`/`"No"` picklists (A5). **The array limitation no longer matters — nothing in the estate stores rows** |
| **`erp._singular("equipment")` → `"equipmen"`** (it drops the last char) | name the entity `assets` (gap E3) |
| **`erp._list` filters only on `refs` fields** | add a `"filters"` spec key for `?status=&weekPosition=&foreman=&plantSite=` and `?assetType=&status=` (gap E1) |
| **`erp._references` walks only `TRANSACTIONS`** | extend it so `jobs.customerId` / `assets.vendorId` guard deletes (gap E2) |
| **`dms._pdf` is single-page**, lines at `y = 720 − leading·i`; past ~36 lines text falls off the MediaBox and **off-page text is invisible to a vision model** | multi-page is mandatory for the shipping-list ModelCall (gap D1) |
| **`dms._SeedUpload` hardcodes `application/pdf`; `_seed_document` knows only `body`/`revision`** | needs `pages`/`font`/`leading` passthrough (gap D2) |
| **DMS has no `PATCH /folders/<id>`**; `DELETE /folders` requires empty; folders have no `path` | **moot in v2** — no folder is ever moved or renamed |
| **Office `hasAttachments` is a bare bool with no content**; `from`/`fromName` are writable; `_SEED` already takes a *list* of mailboxes | **attachment gap moot in v2**; the mailbox list is free |
| **HRIS `trainingRecords`** carries `assignedDate`/`completionDate`/`expirationDate`/`status`/`required`/`provider`/`category` — mapping exactly onto "original cert date unchanged, expiration derived from last cert". **Nothing exists for CDL/DOT.** `managerId` is a real supervisor relation; `departments` have parent + costCenter + head | certifications free; DOT = one new entity; crews = departments |
| **HRIS `_seed` reads every dataset key** (`reviewCycle`, `goals`, `timeOff`, …) and puts `employees[5]` on parental leave | Wick's dataset must fill all keys |
| **No Python tests anywhere** (Flutter widget tests only). `functions/venv/bin/python -c "import main"` works; `flake8` installed; `melos` blocked (Dart 3.9.2 vs ^3.13) | validation = design review + import/flake8 + greps + deployed curl |
| **Apps fetch the namespace list over HTTP** at startup | `wick` appears in the deployed ERP/DMS/Office/HRIS banners after a **functions-only** deploy; only new *entity tabs* need a host build |
| Another session holds uncommitted work in `erp.py`, `dms.py`, `sim_naviant.py`, `apps/erp/lib/api_docs.dart` | append-only edits; see G |

---

## A. Decisions the user must confirm

Numbering is stable against v1 so the two can be diffed. Withdrawn rows are
kept and marked.

| # | Decision | Recommendation | Alternative + cost |
|---|---|---|---|
| A1 | **Builder Portal (wickbuilder.com)** | **No new app.** The portal is a *read*: `GET /jobs` (job + "Customer tab") plus DMS folder `Job Documents/<jobNumber>` (the "Scanned Job Documents" panel). | A 9th Flutter app + hosting site + Terraform + auth domain: L, an "ask before adding" item, **and unbuildable in this container** (Dart 3.9.2 vs melos ^3.13). |
| A2 | **AXIS lives in the ERP** | **Yes, narrowed:** `erp.py` gains `jobs` and `assets` only. No scheduling entity of any kind. | A separate `axis` app: same L cost and Flutter blocker. |
| ~~A3~~ | ~~Scheduling workbook representation~~ | **WITHDRAWN — the estate stores no schedule.** | — |
| A4 | **Per-department Done checkboxes** | **Ten `…Done` picklist fields on `jobs`.** One `GET /jobs` gives Cumulus every upstream-dependency fact it needs. | `jobDepartments` child entity — **explicitly not needed** (§0). |
| A5 | **Flag typing** (`outToYard`, `doNotFab`, Done ×10) | **Picklist `"Yes"`/`"No"`.** `erp.py` has no bool type; zero new machinery; reads like a green-screen job system. | A `booleans` spec key (~10 lines, copies HRIS). Cleaner Flow predicates — **confirm with the Cumulus planner; it changes their expressions.** |
| A6 | **Fleet / equipment register + inspections** | **New ERP entity `assets`** (`assetTag`, `assetType`, `lastInspectionDate`, `nextInspectionDueDate`, `vendorId` → the inspecting vendor). This is a real register a builder keeps, not RPA working state: the *inspection programme* is Cumulus's, the *asset and its last/next dates* are Wick's. | (a) HRIS — a trailer is not a person; (b) documents only — no expiry query; (c) reuse `items` — those are priced SKUs. |
| ~~A7~~ | ~~Mail attachments~~ | **WITHDRAWN — moot.** No crew-schedule mail, no attachments. | — |
| A8 | **Shipping lists / engineering documents** | **Real generated PDFs via `dms._pdf`** — Cumulus runs a Gemini ModelCall over them. Requires D1. Never pre-parse the grids into estate fields. | Placeholder bytes: kills the ModelCall demo and breaks §0. |
| A9 | **Where the structured labor-hour inputs live** | **Nowhere in the estate.** Grids, board hours, CondenStop, trim weight and member counts exist only in PDF text. AXIS keeps only Plant-Ops/Wings facts. | Adding extracted values as fields — rejected, twice over. |
| ~~A10~~ | ~~The three Excel trackers~~ | **WITHDRAWN — RPA-era working state; Cumulus Objects.** | — |
| A11 | **DOT / CDL data** | **New HRIS entity `driverQualifications`** (`id_prefix "DQ"`), one record per driver with the four CDL-tab dates. A driver's qualification file is real HR data; only the *chasing* was the spreadsheet. | Four `trainingRecords` rows per driver (`category:"DOT"`) — no new entity, but a CDL is not a course and PSP/Clearinghouse are not expiries. |
| A12 | **`trainingRecords` fields** | **Add `result` (`Pass`/`Fail`) only** — Steve Woodbury's in-person determination is HR data. **`lastNotifiedDate` withdrawn** on all three entities: notification history is Cumulus's. | No `result`: the pass/fail rule becomes unrecordable. |
| A13 | **Crews and foremen** | **Crews are HRIS `departments`**: `Field Construction` parent, `Crew 12/14/18` beneath, `costCenter` = the Crew ID, `headEmployeeId` = the foreman. Renders in the existing Org tab. ERP `jobs.foreman` stays a **typed display name** — designed drift. | A `crewId` employee field: new field, no UI, weaker. |
| ~~A14~~ | ~~`uipath@` shared mailbox~~ | **WITHDRAWN — there is no bot to model.** | — |
| A15 | **Real names / real domain** | Keep the seven SDD names + `wickbuildings.com` (the naviant precedent uses the real `naviant.com`); everyone else invented; externals on `*.example.com`. **Flagged because it is a judgment call about a real customer's staff, not a technical one.** | Invent surnames, or invent everyone (loses the "these are the people in your SDD" punch). |
| A16 | **id / name / default** | `id: wick`, `name: "Wick Buildings"`, `industry: post-frame-construction`; **`default` stays `naviant`**; module `sim_wick.py`. | `wick-buildings` works (`-`→`_`) but is longer everywhere. |
| A17 | **Size** | **Medium**: 9 shared external entities, 4 processes, ~68 employees, ~22 seeded jobs. | Small loses an SDD; large is unjustified. |
| A18 | **`crm`, `pas`, `psa` excluded** | Confirmed. No CRM (builder-dealers are AXIS customers; no SDD touches a pipeline), no PAS (not an insurer), no PSA (crew and plant labour is Cumulus's model, not a PS tool's). | Adding `crm` doubles roster work for no demo value. |
| **A19** *(new)* | **Does the weekly `no-reply@` "Crew Schedule Reports" notice survive as colour?** | **Drop it.** Crew assignment is data in AXIS; a mail nobody's Flow reads is set dressing that invites the reader to think the bot design still holds. | Keep one plain one-line notice per week in Heather's Inbox, explicitly non-load-bearing (2 lines of seed). Reversible either way. |

---

## B. Design step — `/design-namespace wick`

Skill Phase 1, pre-answered: (1) Wick Buildings, Inc., a Wisconsin post-frame
manufacturer and builder fabricating in Mazomanie ("Mazo") and a Fork West
facility, erecting through foremen-led crews plus a builder-dealer network;
(2) demo purpose = *Cumulus replaces three RPA bot designs with governed Flows
over Wick's own systems* — **and supplies, as governed graph Objects and
Datasets, the working state those bots were going to keep in spreadsheets**;
(3) medium.

### `dossier.md` outline

| Section | Contents |
|---|---|
| The business | Post-frame manufacturer/builder; jobs sold via builder-dealers and direct to farm/commercial owners; a plant week across ten departments; field crews erecting what the plant ships; ~300 people in life, ~68 modelled. A normal week: ~12 jobs/day of plant work, three crews, a rolling compliance calendar. |
| Demo purpose | The sentence above. Name the three SDDs and what each Flow must do. Then the §0 rule, stated for the reviewer: the estate holds systems of record; the schedule, the parameters, the truss standards, the notification history and the assembled job packet are **Cumulus's**, and their absence here is the point. |
| Systems | Row per app incl. exclusions: `erp`=**AXIS** (jobs, flags, dimensions, Done checkboxes, fleet register, purchasing); `dms`=**SharePoint** (documents that are documents); `office`=**Outlook**; `hris`=people, crews-as-departments, training, driver qualifications. `crm`/`pas`/`psa` **No**, with reasons. Then a short **"modelled as a read, not a system"** row — Builder Portal = ERP jobs + DMS `Job Documents/` — and a **"deliberately not modelled"** block naming every artefact in §0's removal table with the reason "RPA-era working state; Cumulus Object/Dataset". UiPath Orchestrator / Action Center: not modelled — it is what is being replaced. |
| The cast | `Name/Role/Dept/Appears in/Voice`, managers first. Real: **Katy Tiller** (HR & Safety Manager), **Steve Woodbury** (Safety & Training Supervisor, Fork West — pass/fail at in-person training), **Abby Wick** (Operations Coordinator; direct line 608-795-1578 in her signature), **Chris Robson** (Plant Superintendent, Mazo — owes the Verticals/Laminates/Stack Out/Components min-max), **Heather Danegelis** (Construction Services Coordinator), **Marlea Knox** (Construction Plan Designer / Agile Coach), **Jeramy Bollig** (Systems Administrator — HRIS `Jeramy`, Office `Jeremy Bollig`, **the SDD's own misspelling kept as drift**). Invented: foremen **Dale Prellwitz** (Crew 12), **Marv Tessmann** (14), **Ryan Hupf** (18), and **Cory Meinholz** (Crew 21) arriving mid-demo; supervisors **Doug Zwettler** (Truss), **Lonnie Frei** (Steel/Trim), **Denise Kalscheur** (Components/Laminates/Verticals/Stack Out), **Wade Ballweg** (Loading & Yard); drivers **Terry Nachreiner, Vern Oimoen, Josh Ripp, Cody Statz, Arlen Breunig**; an Engineering & Design lead, a Builder Services manager, a Controller, an HR coordinator. No first+last pair may collide with `naviant` or `insurance`. |
| Content voice | Flat rural-Wisconsin plant English; bare first name, first-name sign-off; only Abby uses a signature block. Job numbers quoted bare ("260431 loads Thursday", "DNF'd 260418 this morning"). Use: post-frame, truss, girts, purlin, ply, laminate, precut, stack out, OTY, DNF, crib, CondenStop, trim pack, load date, Mazo, Fork West, builder. Avoid: "solution", "reach out", exclamation marks, emoji, and any mention of AI, RPA, Cumulus or integration platforms. |
| Frozen values | Every ambiguity the SDDs leave open, with the chosen value — **including v1's H4/H5/H6/H9, which are frozen values in v2 rather than risks**: the packet-label keyword rule and its traps; `Jeramy`/`Jeremy`; three crews plus a fourth arriving; the 07:00 SLA (now a Cumulus-side concern, recorded for context). |
| Seed scope (derived) | Exactly the counts in §C, plus "not needed" lines — which must name the removals in §0 explicitly, so a later builder does not helpfully add them back. |
| Gaps for build time | D1, D2, E1, E2, E3 from §C, verbatim, as decisions. |

### `roster.md` outline

Nine shared external entities; dials measured against `references/drift.md`
(~60% clean / ~30% near / ~10% hard, 1–2 duplicates per app, 1–2 bait pairs,
~15% missing an optional field, 100% of ids differing).

| # | Entity | Class | Renderings and drift |
|---|---|---|---|
| 1 | **Evermore Barns LLC** | clean | ERP customer; job `250553` (the SDD's own number, a 2025 carryover); mail "Evermore"; **the packet label Cumulus derives is `250553 Evermore`** |
| 2 | **Evermore Ridge Estates** | **bait pair with #1** | the first-keyword label collides (`260418 Evermore`); disambiguated by job number, customer id, address |
| 3 | **The Willows Equine LLC** | hard | naive first keyword = `The`; answer key says the label should read `260435 Willows` |
| 4 | **Sauk Prairie Dairy LLC** | near | legal LLC vs mail "Sauk Prairie Dairy" vs portal panel "SAUK PRAIRIE DAIRY" |
| 5 | **Hoffman Ridge Farms** | **ERP duplicate** | current + stale `Hoffmann Ridge Farms` (2019, Net 60, no email) |
| 6 | **Kickapoo Valley Builders** | structural | ERP customer *and* a subcontractor crew that erects some jobs — jobs assigned to it carry `foreman: "KICKAPOO VALLEY BUILDERS"` and **must be excluded from crew packets** |
| 7 | **Badger Lift & Crane Service** | clean | ERP vendor; `assets.vendorId`; DMS inspection certificates; `service@badgerlift.example.com` |
| 8 | **Coulee Fleet Services** | near | ERP `…Inc.`, mail `fromName` "Coulee Fleet", DMS filenames "Coulee Fleet …" |
| 9 | **Mazo Occupational Health** | clean | ERP vendor; DMS medical cards; no CRM presence by design |
| — | **Blackhawk Equipment Rental** vs **Blackhawk Truss Supply** | **bait pair 2** | customer vs vendor, same first word |
| — | material vendors | clean | Cross Plains Lumber Co., Northern Steel & Trim Supply, Ridgeline Truss Connectors — POs + receipts only |

Closes with **Bait pairs**; the **Answer key** (real entity → every (app, record)
that is it, plus internal drift rows: `Jeramy`/`Jeremy`; `Dale Prellwitz` HRIS /
`D. Prellwitz` ERP `jobs.foreman`; HRIS `Truss Fabrication` vs the SDD's
`Truss Fab`; HRIS `Hardware & Trim Packaging` vs `Hardware & Trim Pack`; and
**the two homes of the same paper** — portal 1-page summary vs plant A1–LL set);
the **authored job table** (job number, builder/owner, dimensions, load-date
offset, week, OTY, DNF, foreman, crew, plant site, document set) as the single
written source that `erp._SEED`, `dms._SEED` and `sim_wick.py` all copy; the
**packet-label answer key** (job → the label a correct Flow derives); and the
**expected-hours answer key** per seeded job-department, so the Cumulus side can
check its ModelCall and arithmetic against ground truth.

### `processes.md` — four processes

`CADENCE` for `sim_wick`: `employee_lifecycle` live 3 / gap 20 · `plant_week`
live 8 / gap 1.5 · `crew_assignment_week` live 1 / gap 7 ·
`certification_cycle` live 4 / gap 4.

#### B.1 `employee_lifecycle` — the spine (unchanged from v1)

Cast: the HR coordinator opens the requisition; Katy Tiller welcomes and owns
the checklist; the hiring manager rotates (Chris Robson plant, Doug Zwettler /
Wade Ballweg departments, Heather Danegelis Builder Services, Abby Wick
Operations, Jeramy Bollig IT); Steve Woodbury runs safety orientation.

| Step | Day | App | Route / action | Writes | Content sketch |
|---|---|---|---|---|---|
| Open requisition | −28…−21 | hris | `POST /requisitions` → `/open` | Draft→Open | "Backfill — second-shift truss fab" |
| Applicants | −21…−10 | hris | `POST /applicants` ×3–4 | Applied | résumé lines in plant vocabulary |
| Screen / interview | −18…−8 | hris | `POST /applicants/<id>/advance` `{stage:"Screen"}`, `{stage:"Interview"}` | stage history | — |
| Interview on the calendar | −16…−8 | office | `POST /events` | 1–2 on the manager's mailbox | `Interview — <candidate> (<title>)` |
| Turn down the rest | −8…−5 | hris | `POST /applicants/<id>/reject` | reason | one clause |
| Offer | −7…−5 | hris | `POST /applicants/<id>/offer` | offer fields | inside the job band |
| Offer letter filed | −6…−4 | dms | `POST /folders` (`Employee Files/<Name>`) + `POST /documents` | folder + v1 | — |
| Hire | 0 | hris | `POST /applicants/<id>/hire` | `EMP-####`, Hire event, onboarding tasks, req Filled | — |
| Work email | 0 | hris | `PATCH /employees/<id>` `{workEmail}` | `first.last@wickbuildings.com` | `/hire` does not set it |
| Mailbox (office roles only) | 0 | office | `POST /mailboxes` | GUID | plant/crew hires get none — their mail leaves the building |
| Welcome mail | 0…+1 | office | `POST /messages` + `/send` | Sent + Inbox copies | Katy's voice, checklist in prose |
| PPE / laptop task | +1…+2 | hris | `POST /onboardingTasks/<id>/complete` | Complete | — |
| Safety orientation | +3…+10 | hris | `POST /trainingRecords` then `PATCH` `{status:"Complete", result:"Pass", expirationDate}` | record | provider "Wick Safety" |
| Equipment certification (plant/crew) | +10…+25 | hris | `POST /trainingRecords` | Telehandler / Forklift / Fall Protection, +1–3 y | **feeds B.4** |
| Driver qualification (driver hires) | +10…+20 | hris | `POST /driverQualifications` **(NEW)** | CDL + medical card + PSP + clearinghouse | feeds B.4 |
| First goal | +14…+25 | hris | `POST /goals` | In Progress | supervisor's voice |
| Benefits | +21…+35 | hris | `POST /benefitEnrollments` + task complete | enrolment | — |
| Payroll | semi-monthly | hris | `POST /payRuns` → `/calculate` → `/approve` → `/pay`, one per tick | ~68 payslips, balances accrued | preceded by a deadline mail |
| First time off | +25…+60 | hris | `POST /timeOffRequests` → `/approve` | balance drawn | "Deer season" |
| *(1 in 4)* resignation | +90…+400 | office | `POST /messages` + `/send` (skipped for mailbox-less hires) | Sent + Inbox | names a last day |
| *(1 in 4)* terminate | +2…+4 after | hris | `POST /employees/<id>/terminate` | Terminated + event | training rows and driver qualifications **stay** — that residue is real HR data, and reconciling it is Cumulus's job |
| *(1 in 4)* file the letter | same day | dms | `POST /documents` into `Employee Files/<Name>` | document | — |
| *(1 in 4)* backfill | +5…+15 | hris | `POST /requisitions` + `/open` | **spawns a new instance** | — |

**Ends when** the hire appears on a paid run. **Evidence:** `EMP-####` + an
Office mailbox joined only by address; an ERP `jobs.foreman` display name only a
person can line up with the HRIS legal name; a terminated worker whose
credential rows are still live. Salaried roles only (the ATS requires
`annualSalary` and hard-codes `payType:"Salary"`); hourly plant and crew
headcount arrives at seed time.

#### B.2 `plant_week` — a job walks Week 3 → the truck

Every schedule, bonus and headcount step from v1 is gone. What remains is the
**state of the job in AXIS** and **the paper that describes it**.

| Step | Day | App | Route / action | Writes | Content sketch |
|---|---|---|---|---|---|
| Job created | 0 | erp | `POST /jobs` **(NEW)** | `26####`, `status:"Scheduled"`, `weekPosition:"Week 3"`, `outToYard:"No"`, `doNotFab:"No"`, dimensions, `loadDate` ~+14 d, ten Done flags `"No"` | builder + owner from the roster |
| Cut sheet + shipping list filed | 0…+1 | dms | `POST /folders` (`PlantCutSheets/Mazo/<jobNumber>`) + `POST /documents` ×2 | the **7-page** shipping list (A1,B,C,D,E,F,LL) and a column cut sheet | grids derived deterministically from the job's own ERP dimensions, so the expected-hours answer key is checkable |
| Portal documents appear | +1…+3 | dms | `POST /folders` (`Job Documents/<jobNumber>`) + `POST /documents` ×2–4 | Shipping List / Engineering Plans / Job Site Map / Crew Hours | **1 job in 6 gets none**; one job gets two Shipping Lists with different `createdAt` (the "topmost = most recent" rule) |
| Out to yard | +3…+6 | erp | `PATCH /jobs/<id>` `{outToYard:"Yes"}` | flag | eligibility now passes |
| Week 3 → Week 2 | +5…+7 | erp | `PATCH /jobs/<id>` `{weekPosition:"Week 2"}` | — | — |
| Truss Cut done | +7…+9 | erp | `PATCH /jobs/<id>` `{trussCutDone:"Yes"}` | unblocks Truss Fab | the one upstream-dependency fact Cumulus reads |
| Week 2 → Week 1 | +9…+11 | erp | `PATCH /jobs/<id>` `{weekPosition:"Week 1"}` | — | — |
| Departments finish, in dependency order | +9…+14 | erp | `PATCH /jobs/<id>`, 2–3 `…Done` flags per step | Truss Fab after Truss Cut; Steel Fab independent; Trim Fab, then Hardware & Trim Pack the next simulated day | mirrors the real 1-business-day trim→pack dependency |
| *(1 in 8)* DNF'd after queueing | anywhere | erp | `PATCH /jobs/<id>` `{doNotFab:"Yes"}` | flag | a Flow that already queued the job must cope |
| *(1 in 10)* weekend load | at creation | erp | `loadDate` on a Saturday | — | the "ignore weekend loads" rule needs something to ignore |
| *(1 in 6)* shipping list missing | at filing | dms | *(no document written)* | — | the missing-paper exception |
| Loading + ship | +13…+16 | erp | `PATCH /jobs/<id>` `{loadingDone:"Yes", status:"In Progress"}` | job leaves the plant | hands off to B.3 |

**Estate does:** the job record and its flags, the week walk, the Done flags,
the paper, and the three authored defects.
**Cumulus does:** eligibility (load date ≥ today, Week 1–3, OTY set, not DNF,
business days), the A1–LL ModelCall, the labour-hour formulas, sequencing /
lead time / farthest-out, capacity and bonus reasoning, **holding the schedule
as graph Objects**, the exceptions, and asking a human before a day is final.
**Evidence:** the same job as an ERP record, a folder name and a PDF header with
only the job number in common; a job whose Done flags move under a schedule
Cumulus already built; paper that is missing exactly where the answer key says.

#### B.3 `crew_assignment_week` — crews get their work (renamed from `crew_schedule_week`)

No PDFs, no mail, no folders. Crew assignment is **data in AXIS**.

| Step | Day | App | Route / action | Writes | Content sketch |
|---|---|---|---|---|---|
| Assign crews | 0 | erp | `PATCH /jobs/<id>` `{foreman, crewId}` ×6–10 | jobs whose `loadDate` is 0–14 d out | `foreman` typed as "D. Prellwitz" — drift against the HRIS legal name |
| Jobs go live | +3…+6 | erp | `PATCH /jobs/<id>` `{status:"In Progress"}` ×1–2 | — | the crew is on site; the packet stops changing |
| Portal document re-issued | +2…+4 | dms | `POST /documents/<id>/content` | v2 of a Scheduled job's Shipping List or Job Site Map | a Flow holding a packet must notice the new version |
| A job moves crews | +4…+6 | erp | `PATCH /jobs/<id>` `{foreman:<other>, crewId:<other>}` ×1 | — | the reassignment case: the old crew's packet must stop, the new crew's must start |
| A job goes to the subcontractor | 1 week in 4 | erp | `PATCH /jobs/<id>` `{foreman:"KICKAPOO VALLEY BUILDERS", crewId:"21"}` | — | must be **excluded** from crew packets |
| *(1 week in 6)* a 4th crew | any | hris + erp | `POST /employees` (or `POST /employees/<id>/transfer`) + `POST /departments` (`Crew 21`) + assignments | Cory Meinholz becomes a foreman | the crew roster is not a constant |
| Week closes | +7 | — | — | **spawns the next week's instance** | — |
| *(A19, if kept)* weekly notice | 0 | office | `POST /messages` + `/send` | one plain line to Heather | **recommended: drop** |

**Cumulus does:** assemble each crew's packet as Job→Document links, keep it
current while the job is Scheduled, freeze it at In Progress, drop it on
reassignment, skip the subcontractor, derive the packet label (the
`Evermore`/`The Willows` traps), and present it to a crew.
**Evidence:** a job whose crew changed while a packet was open; a document at v2
after a packet was assembled from v1; a foreman name that exists in three
spellings across two apps.

#### B.4 `certification_cycle` — credentials, CDL dates, annual inspections

| Step | Day | App | Route / action | Writes | Content sketch |
|---|---|---|---|---|---|
| Pick a credential | 0 | — | *(no write)* reads a `trainingRecords` / `driverQualifications` / `assets` row inside 60 days | — | the clock creates the work, not a write |
| Session on the calendar | +2…+5 | office | `POST /events` on Steve Woodbury's mailbox | attendees = the crew's work emails | "Equipment Training — Telehandler (Fork West)" |
| Outcome recorded | +6…+8 | hris | `PATCH /trainingRecords/<id>` `{completionDate, expirationDate, result:"Pass", status:"Complete"}`; `assignedDate` (the **original** cert date) untouched | recert | 1 in 8 is `result:"Fail"` and `status` stays `In Progress` |
| Signoff filed | +7…+9 | dms | `POST /documents` → `Compliance/Training Signoffs/2026` | signoff sheet PDF | attendees with pass/fail — a real document |
| DOT renewal | +10…+14 | hris | `PATCH /driverQualifications/<id>` **(NEW)** `{medicalCardExpirationDate}` / `{cdlExpirationDate}` / `{pspDate, clearinghouseDate}` | dates move out | after a clinic visit |
| Card filed | +10…+14 | dms | `POST /documents` → `Compliance/DOT/<Driver>` | the card | Mazo Occupational Health letterhead |
| Annual inspection | +12…+18 | erp | `PATCH /assets/<id>` **(NEW)** `{lastInspectionDate, nextInspectionDueDate}` | next due = +365 d | skid steers are never touched (`nextInspectionDueDate: null`) |
| Certificate filed | +12…+18 | dms | `POST /documents` → `Compliance/Inspections/2026` | vendor certificate | filed under the vendor's *mail* spelling |
| *(1 in 5)* a lapse | anywhere | — | *(no write)* | a date passes with nothing done | `status:"Expired"` is left for Cumulus or a human |
| *(1 in 5)* termination | +20…+30 | hris | `POST /employees/<id>/terminate` | Terminated + event | live credential rows for a departed worker |

**Cumulus does:** the 60/30/15 date math, supervisor resolution via HRIS
`managerId`, the notifications **and their history**, exception and stats
reporting, retention flagging, and the terminated-but-still-credentialed
reconciliation.
**Evidence:** the same person as an HRIS worker, a supervisor relation and a
name on a signoff PDF; an asset whose ERP `vendorId` and DMS certificate
filename spell the vendor differently; credentials that lapse with no trace of
anyone having been told.

**Phase 4 must additionally confirm** that every route named exists today or is
listed in §C as NEW, and — the v2 check — that **no step stores working state or
does work attributed to Cumulus.**

---

## C. Build step — app extensions (functions only)

No Flutter change is needed for the namespace to work or to appear (apps fetch
the namespace list over HTTP). New ERP/HRIS entities are **API-only until a
host-side Flutter build**; DMS folders/documents and Office mail render
generically, so Wick's filing and mailboxes are visible immediately.

### C.1 `functions/erp.py` — two entities, three small machinery gaps

**NEW `jobs`** — `collection: "erp_jobs"`, `seq_start: 8000`, **no**
`tran_prefix` (that would add a `total`).

| Field | Type | Notes |
|---|---|---|
| `jobNumber` | required str | `"260431"`, `"250553"` — the cross-system business key; the record id (8000+) is unrelated (drift anti-rule 1) |
| `customerId` | ref `customers` | the portal "Customer tab" company; the packet-label keyword source |
| `builder` | str | the builder-dealer, typed |
| `jobName` | str | `"40x72x14 machine storage"` |
| `status` | picklist | NEW `JOB_STATUSES = ["Quoted","Scheduled","In Progress","Complete","Cancelled"]` |
| `plantSite` | picklist | NEW `PLANT_SITES = ["Mazo","Fork West"]` |
| `foreman`, `crewId` | str | typed display name + `"12"` |
| `loadDate`, `shipmentDate` | date | `dates` spec key |
| `weekPosition` | picklist | NEW `WEEK_POSITIONS = ["Week 1","Week 2","Week 3","Beyond"]` |
| `outToYard`, `doNotFab` | picklist | NEW `YES_NO = ["Yes","No"]` (A5) |
| `shipmentType`, `combinedWith`, `trailerNumber` | str | ST / consolidated-truck facts for loading |
| `buildingWidthFt`, `buildingLengthFt`, `buildingHeightFt`, `wingsTotalWidthFt`, `wingsTotalLengthFt` | numbers | the AXIS Wings/Building screen |
| `trussCutDone`, `trussFabDone`, `steelFabDone`, `trimFabDone`, `hardwarePackDone`, `verticalsDone`, `componentsDone`, `laminatesDone`, `stackOutDone`, `loadingDone` | picklist `YES_NO` | the Plant Operations Done checkboxes (A4) |

**NEW `assets`** — `collection: "erp_assets"`, `seq_start: 4000`: `assetTag`
(required, `"TRK-14"`), `description`, `assetType` (picklist NEW
`["Truck","Trailer","Lift","Skid Steer","Other"]`), `serialNumber`,
`plateNumber`, `homeSite` (picklist `PLANT_SITES`), `assignedTo` (str),
`vendorId` (ref `vendors`), `lastInspectionDate`, `nextInspectionDueDate`
(dates), `status` (picklist NEW `["In Service","Out of Service","Retired"]`).
**No `lastNotifiedDate`.**

| Gap | Change | Size |
|---|---|---|
| **E1** | A `"filters": [...]` spec key honoured in `_list` beside the existing `refs` filters → `GET /jobs?status=&weekPosition=&foreman=&plantSite=`, `GET /assets?assetType=&status=`. `?updatedSince=` keeps precedence, as everywhere in the estate | ~6 lines |
| **E2** | `_references` walks only `TRANSACTIONS`; extend the loop to `TRANSACTIONS + ["jobs", "assets"]` so `jobs.customerId` and `assets.vendorId` guard deletes | 1 line |
| **E3** | `_singular("equipment")` → `"equipmen"`; the entity is therefore named **`assets`** | naming only |
| — | Module docstring + route list | ~12 lines |

**Removed vs v1:** no `plantSchedules` entity, no `_clean_rows`, no
`totalHours` in `_record_json`, no `"rows"` spec flag, no `rows` seed loop.

**`_SEED["wick"]`** — every key the seeder reads must be present:

| Key | Count | Notes |
|---|---|---|
| `customers` | 12 | the roster's nine + `Hoffmann Ridge Farms` (stale duplicate) + `Blackhawk Equipment Rental` + `Evermore Ridge Estates` |
| `vendors` | 6 | Badger Lift & Crane Service, Coulee Fleet Services Inc., Mazo Occupational Health, Cross Plains Lumber Co., Northern Steel & Trim Supply, Blackhawk Truss Supply |
| `items` | 14 | post-frame catalogue (truss, steel roof/trim, laminated column, Perma Column, slide-door track, profile vent, 2x4 SPF, 7/16 OSB, ridge vent, house wrap, concrete mix, hardware pack, girts, purlin) — drives the POs and is echoed in shipping-list text |
| `salesOrders` | **0** (`[]`) | buildings are `jobs`; the empty list is deliberate and commented |
| `purchaseOrders` | 6 | material buys; 2 carry `received` receipts, reusing the existing receiving-report machinery |
| **`jobs`** (new loop) | 22 | 4 `In Progress`; 10 `Scheduled` across 3 crews + 1 subcontractor; 6 in Weeks 1–3 with OTY/DNF/weekend spread; 2 `Complete`. Includes `250553` (Evermore — the SDD's own job) and the `The Willows Equine LLC` label trap |
| **`assets`** (new loop) | 14 | 5 trucks, 4 trailers, 3 lifts, 2 skid steers (`nextInspectionDueDate: null` — the SDD exclusion, visible in the data); due dates laddered 15/30/60 days out |

`_seed()` needs two new blocks: masters (`assets`, resolving `vendor` →
`vendorId` the way the orders do) and `jobs` (resolving `customer` →
`customerId`). *Not needed:* sales orders, an invoice entity, multi-currency,
inventory transactions, **and no schedule of any kind.**

### C.2 `functions/dms.py` — two gaps, no route changes

| # | Gap | Recommendation | Size |
|---|---|---|---|
| **D1** | `_pdf` writes a single page, lines at `y = 720 − leading·i`; past ~36 lines text falls off the MediaBox and is **invisible to a vision model** | Extend `_pdf` to multi-page: accept `pages: list[list[str]]` (or a `None` page-break sentinel in `body`), emit one `/Page` per page under a shared `/Pages /Kids` with per-page content streams. **Keep the existing `(title, body)` signature working** — naviant's invoices depend on it | ~30 lines, **mandatory** |
| **D2** | `_SeedUpload` hardcodes `content_type="application/pdf"`; `_seed_document` knows only `body`/`revision` | Add optional `pages`, `font`, `leading` (and `contentType` for completeness) to the seed document spec and pass them through | ~15 lines, **mandatory** |
| ~~D3~~ | ~~`PATCH /folders/<id>` (move/rename)~~ | **WITHDRAWN** — no folder is ever moved in v2 | — |
| ~~D4~~ | ~~`?parentId=` on `GET /folders`~~ | **WITHDRAWN** | — |
| D5 | `DELETE /folders/<id>` refuses non-empty | **Keep**, untouched | none |
| D6 | Documents sort by name, not date, so "topmost = most recent" must be read off `createdAt` | **Keep**; document it in the process spec | none |

**Day-zero tree (`_SEED["wick"]`)** — ~45 folders, ~55 documents. Every folder
below exists because it holds documents that are genuinely documents.

| Path | Contents |
|---|---|
| `Job Documents/<jobNumber>` ×12 | the portal panel: `<job> Shipping List.pdf`, `<job> Engineering Plans.pdf`, `<job> Job Site Map.pdf`, `<job> Crew Hours.pdf`. **One job carries several revisions** (v2/v3 via `revision`), **one job has none** |
| `PlantCutSheets/Mazo/<jobNumber>` ×6 · `PlantCutSheets/Fork West/<jobNumber>` ×1 | the full **A1,B,C,D,E,F,LL** shipping list (multi-page, fixed-pitch) + a column cut sheet; **one job deliberately missing its shipping list** |
| `Compliance/Training Signoffs/2026` | 4 historical signoff sheets |
| `Compliance/Inspections/2026` | 3 vendor inspection certificates (Badger Lift, Coulee Fleet) |
| `Compliance/DOT/<Driver>` ×5 | 2 medical cards, 1 MVR |
| `Employee Files/<Name>` ×12 | offer letters for the named office/supervisor staff |

**Explicitly absent** (and named as absent in the dossier's "not needed" list so
nobody adds them back): `Foremen/…`, `Crew Schedule Reports/…`,
`Plant Schedules/…`, `Plant Standards/…`, `Compliance/Trackers/…`.

**Shipping-list representation** (A8): one document per job, 7 pages,
`font="Courier"`, `leading=18`, per-page header
`SHIPPING LIST   JOB 260431   PAGE B   MAZO`, grids as space-aligned columns —
**A1** truss drawings (span, ply, T/B/W member counts) · **B** components /
laminates / verticals / truss grids plus a handwritten-style
`TRUSSES W/GIRTS PLATED IN` note · **C** steel fab (colour grids, linear
footage, `CONDENSTOP: Y/N`, crib notes) · **D** hardware line items · **E** trim
cut/fab + trim pack (items, lengths, total weight, FJ/JJ flags) · **F**
slide-door hardware (track end caps qty, pull plate) · **LL** loader list (board
hours, lumber/plywood grids). All quantities **derived deterministically from
the job's ERP dimensions** by an authored seed formula, so the expected-hours
answer key is checkable. Nothing on these pages is duplicated as an estate
field (A9).

### C.3 `functions/office.py` — seed only, no module change

`_SEED["wick"]`: **9 mailboxes** (v1's `uipath@` removed) —
`heather.danegelis@`, `marlea.knox@`, `katy.tiller@`, `steve.woodbury@`,
`abby.wick@`, `chris.robson@`, `jeramy.bollig@` (**`displayName` `Jeremy
Bollig`** — the authored drift), `doug.zwettler@`, `wade.ballweg@`. ~36 messages
and ~20 events, including: **Steve chasing a lapsed medical card by hand** (the
"before Cumulus" picture, and the only notification anywhere in the estate);
**Chris Robson's unanswered mail** asking for the Verticals / Laminates / Stack
Out / Components min-max (the SDD's open decision, live in the data);
**Abby Wick's signature block** with 608-795-1578; **Heather asking a builder
for a corrected job site map**; Katy circulating a training date; a builder
chasing a delivery week. *Not needed:* mailboxes for foremen, crews, hourly
plant staff, drivers or external senders; no attachments; no automation mail.

### C.4 `functions/hris.py`

| Change | Detail | Size |
|---|---|---|
| `trainingRecords` + `result` | enum, new `TRAINING_RESULTS = ["Pass","Fail"]` (A12). **No `lastNotifiedDate`** | 1 line + 1 picklist |
| **NEW `driverQualifications`** | `collection: "hris_driver_qualifications"`, `id_prefix: "DQ"`; `employeeId` (ref employees, required), `cdlNumber`, `cdlClass` (enum `["A","B","C"]`), `cdlState`, `cdlExpirationDate`, `medicalCardExpirationDate`, `pspDate`, `clearinghouseDate` (dates), `status` (enum `["Qualified","Pending","Disqualified"]`); `QUERY_FILTERS["driverQualifications"] = ["employeeId","status"]`; add `("driverQualifications","employeeId")` to `REFERENCES["employees"]`; docstring route lines | ~22 lines |
| Recert semantics (no code) | `assignedDate` = the original cert date (never moves), `completionDate` = the latest recert, `expirationDate` derived from it — the SDD's rule with **zero new fields** | none |

**`_SEED["wick"]`** — the seeder reads `emailDomain`, `location`, `areaCode`,
`legacy?`, `departments`, `jobs`, `employees`, `promotions`, `transfers`,
`terminations`, `timeOff`, `requisitions`, `applicants`, `reviewCycle`, `goals`,
`training`; **all keys must be present**, and note that
`dataset["employees"][5]` is put on parental leave.

| Piece | Plan |
|---|---|
| Header | `wickbuildings.com` / `Mazomanie, WI` / `608` (the seeder writes mobiles as `(608) 555-0{100+i}`; keep external roster phones out of that block) |
| `departments` (~18) | Executive; Builder Services; Engineering & Design; HR & Safety; Operations; **Plant — Mazomanie** with the ten plant departments beneath it spelled the HRIS way (`Truss Cutting`, `Truss Fabrication`, `Steel Fabrication`, `Trim Fabrication`, `Hardware & Trim Packaging`, `Verticals`, `Components`, `Laminates`, `Stack Out`, `Loading & Yard`) — **deliberately not the SDD's short forms**; **Plant — Fork West**; Transportation; **Field Construction** with `Crew 12`/`Crew 14`/`Crew 18` (`costCenter` `CREW-12`…, `headEmployeeId` = the foreman) |
| `jobs` (~26 codes) | plant (`PLT-SUPT`, `PLT-SUP`, `PLT-LEAD`, `PLT-FAB`, `PLT-SAW`, `PLT-LOAD`), field (`FLD-FOREMAN`, `FLD-CARP`, `FLD-LABOR`), transport (`DRV-CDL`), office (`EXEC-PRES`, `OPS-COORD`, `CON-COORD`, `ENG-DES`, `HR-SAFETY`, `HR-COORD`, `IT-SYS`, `FIN-CTL`, `BLD-MGR`) with bands; hourly jobs carry no min/max salary (house convention) |
| `employees` (~68) | ~14 authored cast + ~54 from a `_wick_workforce()` cohort generator mirroring `_naviant_workforce()`: one fixed-seed `random.Random`, cohorts consumed in a fixed order, authored first/last pools, no collisions with `naviant` or `insurance`. Foremen, crews and plant floor hourly; office salaried |
| `promotions` / `transfers` / `terminations` | ~6 / ~4 / ~3 |
| `training` (~55 rows) | `New Employee Safety Orientation` for everyone; equipment certifications (Forklift, Telehandler, Scissor Lift, Fall Protection, Crane Signalling, First Aid/CPR) for plant and field staff, with `expiresInDays` laddered **61, 45, 30, 21, 15, 7, −10** so any Cumulus cadence finds work at every threshold (negative values pass straight through the seeder's `ahead()` — no code change) |
| **`driverQualifications`** (new loop) | 5 drivers × 4 dates, laddered the same way; two rows missing `pspDate` (the ~15% missing-optional dial) |
| `timeOff` / `requisitions` / `applicants` / `reviewCycle` / `goals` | ~8 / 2 / 5 / 1 / 8 |

---

## D. `namespaces.json` + `sim_wick.py`

Append after `insurance` (leave `"default": "naviant"`):

```
{ "id": "wick", "name": "Wick Buildings",
  "description": "Wick Buildings, a Wisconsin post-frame building manufacturer and builder.",
  "industry": "post-frame-construction",
  "apps": ["erp", "dms", "office", "hris"],
  "processes": ["employee_lifecycle", "plant_week", "crew_assignment_week",
                "certification_cycle"] }
```

`namespaces.json` is read at import, so **a functions deploy is required** for
the namespace to exist; after that it appears in the ERP/DMS/Office/HRIS banners
and the admin console with no Flutter build.

**`sim_wick.py`** copies `sim_naviant.py`'s architecture exactly (the repo wants
convergent copies, not an abstraction):

1. **Docstring** — Wick's week in Wick's voice; the four processes; and §0
   stated as a prohibition: *this file must never write a schedule, a computed
   hour, a notification, a notified-on date, an assembled packet, or a folder
   tree — that is the work being replaced.*
2. Imports `dms, erp, hris, office, namespaces, pulse as pulse_lib`
   (**no `crm`, `pas`, `psa`**).
3. `NAMESPACE = "wick"`, `INSTANCES`, `TERMINAL`, `MAX_STEPS`,
   `CENTRAL = timedelta(hours=5)`.
4. `CADENCE` — the four `{live, gap}` pairs from §B.
5. `CAST` (keyed dicts with `name`, `first`, `address`, `title`, `department`;
   every address is a seeded mailbox so `_mailbox` finds rather than creates)
   plus `FOREMEN`, `SUPERVISORS`, `PLANT_DEPARTMENTS` (label strings only, for
   Done-flag ordering), `DRIVERS`.
6. **The book of jobs** — builder/owner rotations, the building-size table, and
   `_shipping_list_pages(job)` deriving grids from dimensions. Job numbers
   minted as `max(jobNumber) + 1` inside the `26####` band, so pulse-minted jobs
   never collide with the seeded ones.
7. **Plumbing copied verbatim** — `_Gone`, `_Upload`, `_FileRequest`, `_req`,
   `_read`, `_now`, `_today`, `_fetch`, `_records`, `_pick`, `_requires`,
   `_mailbox`, `_send`, `_deliver`, `_event`, `_folder`, `_pdf_upload`,
   `_file_pdf`, `_revise_pdf`, `_on`, `_day0`, `_weekday`, `_slot`.
8. Step handlers `_p1_*` (lifecycle), `_p2_*` (plant_week), `_p3_*`
   (crew_assignment_week), `_p4_*` (certification_cycle).
9. Step tables via `_step(name, app, run, day, days)`, matching `processes.md`
   row for row; `PROCESSES = {...}`.
10. Instance store + tick copied verbatim (`_collection`, `_instances`, `_due`,
    `_save`, `_finish`, `_start`, `_opening_data`, `_new_day0`, `_index`,
    `_advance`, `_startable`, `tick`).

**Deliberately unsimulated** (say it in the docstring): every daily department
schedule and its rows, bonus and headcount; all labour-hour arithmetic; the
truss standards table; job-packet assembly and folder trees; every expiry
notification and its history; Action Center approvals; the Builder Portal as an
app; hourly hires through the ATS (salaried only); `crm`/`pas`/`psa`.

---

## E. Tests and validation

No Python tests exist (Flutter widget tests only), and `melos` cannot run in
this container. Four layers:

1. **Design review (skill Phase 4)** plus the two v2 checks: no step stores
   working state, and no step does work attributed to Cumulus.
2. **Static** — `cd functions && ./venv/bin/python -c "import main, sim_wick"`
   (the venv resolves `firebase_functions`/`firebase_admin`; verified), and
   `./venv/bin/flake8 <changed files>` matching surrounding style (~79 cols; the
   repo is not clean at 79 everywhere, so compare counts before/after).
3. **Consistency greps** — every job number in `dms._SEED["wick"]` folder paths
   exists in `erp._SEED["wick"]["jobs"]`; every `CAST` address in `sim_wick.py`
   exists in `office._SEED["wick"]["mailboxes"]`; every `PLANT_DEPARTMENTS`
   label used for Done-flag ordering maps to a real `…Done` field; **and a
   negative grep: `plantSchedule|bonusPerHour|lastNotified|Crew Schedule
   Reports|Foremen/|Truss_Standards|Trackers` must not appear anywhere under
   `functions/` for `wick`.**
4. **Deployed smoke:**

```
BASE=https://cumulus-demo-mitchell-suter-erp.web.app/api/wick/v1
curl -s $BASE/jobs | head -c 400
curl -s "$BASE/jobs?status=Scheduled&weekPosition=Week%202"
curl -s "$BASE/assets?assetType=Lift"
curl -s -X PATCH $BASE/jobs/8007 -H 'content-type: application/json' \
     -d '{"trussCutDone":"Yes"}'          # a write to a system of record
DBASE=https://cumulus-demo-mitchell-suter-dms.web.app/api/wick/v1
curl -s $DBASE/folders | python3 -m json.tool | head -40
curl -s "$DBASE/documents?folderId=<PlantCutSheets/Mazo/260431 id>"
curl -s "$DBASE/documents/<id>/content" -o /tmp/sl.pdf && head -c 200 /tmp/sl.pdf
HBASE=https://cumulus-demo-mitchell-suter-hris.web.app/api/wick/v1
curl -s "$HBASE/trainingRecords?status=Complete" | head -c 400
curl -s $HBASE/driverQualifications
OBASE=https://cumulus-demo-mitchell-suter-office.web.app/api/wick/v1
curl -s "$OBASE/messages?folder=Inbox" | head -c 400
curl -s https://cumulus-demo-mitchell-suter-crm.web.app/api/wick/v1/accounts  # expect 404
```

Then confirm the seeded shipping list really has **seven page objects**
(`python3 -c "print(open('/tmp/sl.pdf','rb').read().count(b'/Type /Page '))"`)
and that `?updatedSince=` returns a delta on `jobs`, `documents` and
`trainingRecords` after one pulse tick.

---

## F. Deploy

Container-safe:

```
cd /workspaces/cumulus-demo-ecosystem
firebase deploy --only functions --project cumulus-demo-mitchell-suter
```

Seed each app once, via **direct function URLs** (Hosting caps a rewrite at
~60 s; `dms_api` is `timeout_sec=300`, `hris_api` `540`):

```
P=cumulus-demo-mitchell-suter
F=https://us-central1-$P.cloudfunctions.net
curl -X POST $F/hris_api/wick/v1/seed
curl -X POST $F/erp_api/wick/v1/seed
curl -X POST $F/dms_api/wick/v1/seed
curl -X POST $F/office_api/wick/v1/seed
```

Each `/seed` 409s unless that app is empty for `wick` and 404s without a
`_SEED` entry, so a partial build fails loudly. Reset with
`./scripts/purge-namespace.sh wick`. Pulse on/off:

```
curl -X PUT $F/namespaces_api/namespaces/wick/pulse \
  -H 'content-type: application/json' \
  -d '{"enabled":true,"intensity":"demo","simDayMinutes":10,
       "apps":{"erp":true,"dms":true,"office":true,"hris":true}}'
curl -X PUT $F/namespaces_api/namespaces/wick/pulse -d '{"enabled":false}' \
  -H 'content-type: application/json'
```

**Host-only follow-up** (Dart 3.9.2 vs melos ^3.13): anything under `apps/` —
optional ERP tabs for Jobs and Assets, the matching
`apps/erp/lib/api_docs.dart` entries, and an HRIS `driverQualifications`
surface. **Nothing in A–F depends on them.**

---

## G. Lanes

| Lane | Unit | Files owned | Done when | Size (v1 → v2) |
|---|---|---|---|---|
| **L0** | Design assets | `docs/namespaces/wick/{dossier,roster,processes}.md` | the three files exist, Phase 4 + the two v2 checks reported, and an **interface appendix** freezes every entity/field/picklist/route name, the authored job table, the packet-label answer key and the expected-hours answer key | M |
| **L1** | ERP: jobs + assets | `functions/erp.py` | two entities + E1 + E2 + `_SEED["wick"]`; `import erp` clean; flake8 no worse | **L → M** (no `plantSchedules`, no `_clean_rows`, no 60 schedule records) |
| **L2** | DMS: documents | `functions/dms.py` | D1 + D2 + `_SEED["wick"]`; `dms._pdf("x",["y"])` still behaves for naviant's invoices | **L → M** (no folder PATCH, no foreman tree, no crew PDFs, no trackers, no standards sheet) |
| **L3** | HRIS workforce | `functions/hris.py` | `driverQualifications` + `result` + `_wick_workforce()` + `_SEED["wick"]`; `import hris` clean | L |
| **L4** | Office mail | `functions/office.py` | `_SEED["wick"]` (9 mailboxes, ~36 messages, ~20 events); **no module change** | M → S |
| **L5** | Config + simulation | `functions/namespaces.json`, `functions/sim_wick.py` | entry exists; `import sim_wick` clean; `tick()` walks all four tables against deployed data without abandoning an instance | L → M |
| **L6** | Deploy, seed, smoke | commands only | §F run, §E green (incl. the negative grep and the 7-page check), one `pulse: step applied namespace=wick` in Cloud Logging | S |
| **L7** | *(host only, optional)* UI | `apps/erp/lib/*`, `apps/hris/lib/*` | Jobs and Assets tabs render | M |

L1–L4 are file-disjoint and parallel once L0's appendix exists; L5 can be
written against the appendix and validated after they land.

**Worktree warning (unchanged):** `functions/erp.py`, `functions/dms.py`,
`functions/sim_naviant.py`, `apps/erp/lib/api_docs.dart` and
`docs/namespaces/naviant/processes.md` carry another session's uncommitted work.
Do not revert or stash it. A worktree branched from HEAD would *lack* that work
and collide in exactly L1's and L2's files. Prefer, in order: (a) wait for that
session to commit, then branch; (b) run L1/L2 in the main working tree,
**append-only** — new `ENTITIES` keys at the end of the dict, `_SEED["wick"]`
after `_SEED["insurance"]`, new helpers after the existing ones; (c) run
L0/L3/L4 in worktrees first and L1/L2 last.

---

## H. Risks and open questions

v1's H4/H5/H6/H9 are now **dossier frozen values**, not risks. H18 and H19 are
**dropped** (the hand-written fallback and the attachment seam no longer exist).

| # | Risk / question | Where the design freezes a value |
|---|---|---|
| H1 | Training SDD cadence contradicts itself (monthly 1st @05:00 vs "daily" vs an Oct–Nov peak) | The estate takes **no** position: credentials ladder at **61/45/30/21/15/7/−10 days** so any cadence finds work at every threshold. Cadence is a Cumulus-side decision. |
| H2 | Box vs SharePoint vs shared drive | **Moot by construction:** the estate holds no working state, so there is no tracker to place. The DMS holds documents that are documents; the "shared drive" simply is not modelled. |
| H3 | The label rule says `<Job Number> - <first keyword>`, the SDD's example says `250553 Evermore` | **Space, no dash** (the example wins). It is now the label **Cumulus derives** for a job packet, not a folder the estate creates; both spellings recorded, and the answer key gives the expected label per seeded job. |
| H7 | The AXIS REST API is "under development by Wick IT"; SharePoint paths, SMTP and DU extractors are TBD | The estate simply *has* AXIS and SharePoint — the repo's standing fiction. Noted in the dossier so nobody reads it as a claim about the real Wick. |
| H8 | Chris Robson's missing min/max hours for Verticals, Laminates, Stack Out, Components | Unresolvable here and **no longer an estate concern**: those parameters are a governed Cumulus Dataset. Modelled only as his **unanswered mail**, which is why the Dataset needs a human owner. |
| H10 | Wide `jobs` record (~28 fields incl. ten Done flags) | Deliberate (A4); `jobDepartments` is explicitly not needed. Object before L1 if the shape offends. |
| H11 | `_pdf` off-page text is invisible to a vision model — a real correctness trap for the ModelCall demo | D1 is mandatory; §E's 7-page check is the guard. |
| H12 | Pulse budget: `plant_week` at 8 live instances writes PDFs (GCS round trips) plus many small PATCHes; the `demo` preset allows 3 events/tick, 400/day | Keep every PDF-writing step on its own step so no tick carries two. Smaller than v1 (no schedule shells, no daily bonus PATCHes). Retune `pulse.INTENSITY` only if a tick times out — that file is shared plumbing. |
| H13 | ERP `_next_id` is "deliberately dumb" (`max(id)+1`, no counters); two creates in the same instant collide | Mint at most one job per tick. |
| H14 | The authored job table is duplicated across three modules (`erp`, `dms`, `sim_wick`) — one fewer than v1, since Office no longer names attachment filenames | Deliberate duplication per repo doctrine; `roster.md` is the single written source and §E.3's greps are the guard. |
| H15 | A concurrent session holds uncommitted changes in `erp.py`, `dms.py`, `sim_naviant.py`, `apps/erp/lib/api_docs.dart` | §G worktree warning; append-only; never revert. |
| H16 | New ERP/HRIS entities are invisible in the deployed web apps until a host-side Flutter build | Accepted; L7 is optional and host-only. The namespace, the DMS filing and the Office mailboxes are visible immediately. |
| H17 | Real people's names and the real `wickbuildings.com` domain in a demo estate | A15 — Mitchell's call, not the builders'. |
| **H20** *(new, standing)* | **The estate must leave all working state undone.** The temptation to "finish the picture" — a schedule table so the plant looks busy, a notified-on stamp so the audit trail looks complete, a foreman folder so the packet looks filed — will recur at every lane and in every later enrichment. Each one silently re-implements what Cumulus is being demoed doing, and makes the demo circular. | Enforced three ways: the §0 removal table is copied into `dossier.md` → *Seed scope (not needed)*; `sim_wick.py`'s docstring states the prohibition; §E.3 runs a **negative grep** (`plantSchedule|bonusPerHour|lastNotified|Crew Schedule Reports|Foremen/|Truss_Standards|Trackers`) that must find nothing. Any future request to add one of these is a Cumulus-side design change first. |

---

# Part III — Wick: the Cumulus-side plan (v2)

**Repo** `/workspaces/cumulus-mcp-prototype`, branch `workflows-management`. **Status: FOR APPROVAL.**
Inputs: the three Wick RPA SDDs (digest at `scratchpad/wick-sdd-digest.md`), `DOCTRINE.md`,
`README.md`, `WORKPLAN.md` (decisions 8, 14), `WORKPLAN-CORE.md` §3–§4 as the plan's shape.

## The binding principle (v2)

The SDDs describe **how Wick works around missing systems**: Excel trackers, daily scheduling
workbooks, a "Last Notified" cell, a headcount typed into a sheet, a bonus-cell formula, a truss
standards spreadsheet, a per-foreman SharePoint folder tree, a system-generated crew-schedule PDF.
**Cumulus does not re-create any of it, and does not mirror it back.** That working state *is* the
knowledge graph (`Job`, `JobTask`, `ScheduleDay`, `ScheduleEntry`, `Credential`, `Equipment`,
`Inspection`) plus governed Datasets (parameters, formulas, truss standards). The estate models only
Wick's **real systems of record** — AXIS as ERP `jobs` + `assets`, the HRIS's training and driver
records and its departments-as-crews, the DMS's real document trees, Office mailboxes.

Consequence, stated once: **v1 makes no outbound write to the estate at all.** Every Action commits
into the graph through the Gateway. `estate_writes.py` is untouched.

## What changed from v1, so you can diff it in your head

| Removed | Stands |
|---|---|
| `crew-schedule-intake` Flow, its ModelCall and `crew-schedule-parse-prompt` | `field-documents`, now **windowed** and the whole of SDD 1 |
| `field-folder-reconciliation` Flow | reconciliation is `assigned_to` link validity — a graph fact the workspace shows (current vs former crew) |
| `has_attachment` LinkType, `MailAttachmentRecord`, the Office attachment contract | `office-mail` unchanged; mailboxes exist for the cast |
| `create-estate-folder`, `copy-estate-document`, `move-estate-folder`, `delete-estate-folder` + their `WRITABLE` rows | one NEW graph Action, `link-job-documents` |
| `publish-schedule-rows`, `set-schedule-status` + their `WRITABLE` rows | `apply-schedule-day` → approval Task → `mark-day-approved`, all graph |
| `erp-schedule` feed, `schedule_sync` package, `ScheduleDayRecord`, `ScheduleEntryRecord`, schedule echo-suppression | `erp-jobs`, `hris-credentials`, `erp-equipment` |
| `external_id` / `source` on `ScheduleDay` and `ScheduleEntry` | both are `authoritative=True`: Cumulus is the system of record for a schedule |
| the `scheduleEntries` / `departmentDays` / `jobDepartments` estate contracts | four estate contracts (§C-5) |
| `Lane W` (the create/delete write door) and `Lane 1` | Lanes O · N · K · S · 2 · 3 · 4 · 5 · 6 · E · I |
| `cumulus-field-window` scheduler job | two scheduler jobs: compliance daily, plant 11:00 CST |
| `Job.foreman_report_seen_on`; `Truss_Standards` read from a DMS document | `Job.foreman_name` / `crew_id` as landed strings; `truss-standards` as a governed Dataset |
| risks I-5 (attachments) and I-6 (Excel) — both moot | risk I-15, restated as the staging question |
| `headcount` read from a sheet; `incentive_per_hour` read from a bonus cell | `headcount` = count of `member_of` the department Team; `incentive_per_hour` **computed** by a pure Rule from governed values |

Totals: **5 business Flows + 3 sync Flows** (was 7 + 4), **7 new ObjectTypes** (unchanged, minus
four fields), **11 new LinkTypes** (was 12), **5 new Gateway Actions** (was 9), **7 governed assets**
(was 9), **0 new estate write doors** (was 6).

---

## A · Decisions the user must confirm

### A-1 · Namespace-scoping of the cast
**Recommendation: split the *cast* per namespace; keep the *desks* declaration global.**

- `server/app/personas.py` keeps its entire public surface (`ENGINEER`, `SUPPORT`, `CUSTOMER_ADMIN`,
  `CUSTOMER_USERS`, `GROUPS`, `ALL_PERSONAS`, `DEFAULT_PERSONA`, `OPERATOR_EMAIL`, `ALL_QUEUES`,
  `get`, `for_email`, `queues_for_email`, `reach_for`) and becomes a **selector** over a new
  `server/app/casts/` package: `casts/naviant.py` (today's roster, verbatim), `casts/wick.py` (NEW),
  `casts/__init__.py` picking on `settings.estate_namespace`. **`""` and any unknown value select
  naviant**, so local dev and every existing test are unchanged.
- The two Naviant personas live in **both** casts — they are the FDE and support, not the customer's
  org chart, which is what `personas.py`'s docstring already says about them.
- `server/app/desks.py` stays one global `DESKS` tuple and gains the three Wick desks (A-6). Every
  `desks.X` constant must stay importable at module-import time, because flow packages name their
  desk at import; filtering `DESKS` per namespace would turn a naviant deployment's import of
  `desks.PLANT_SUPERVISOR` into an ImportError.

| Option | Why not |
|---|---|
| One roster with `Persona.namespaces` tags | Same code, and it puts two customers' real employees in the one file that decides who may decide a Task. A wrong tag is a Wick foreman holding a Naviant desk. A module boundary makes "who is in this deployment" a whole-file answer. |
| Leave naviant as-is, add Wick personas alongside | Breaks what `personas.py` exists to prevent: a persona whose email resolves to no `Employee` opens onto an empty hub and calendar, so every screen tested with it tests the fallback. In a Wick deployment, Raymond Fitzgerald is that persona. |

**A-1b · A Wick deployment is its own Cumulus project.** `ESTATE_NAMESPACE` is a per-deployment pin
and the corpus holds the *customer's* governed knowledge. Re-pointing the Naviant deployment would
leave Naviant's policies, tasks, runs and ledger in the store while the personas switched under
them. Recommend `./deploy/ship.sh -p <wick-project> -n wick`. Cheaper alternative: the same project
with `--generation wick-v1` — clean corpus and collections, shared Firebase Auth users and roles.

Also here: `server/app/eval_world.py:63 EVAL_ESTATE_NAMESPACE = "naviant"` becomes a parameter on the
world builder defaulting to `settings.estate_namespace or "naviant"`.

### A-2 · Auto-committed Action, or Task? — per act
Discriminator: DOCTRINE Lens 4 (*"a human is invoked only where judgment is the point"*), plus
*"deletes are decisions"*, plus *"prefer putting the gate on the Action itself"*. Every row below is
now a **graph** write, which changes the blast radius of the whole table: a bad write is reversible
by a steward and every claim it made is still on the record.

| SDD act | v2 form | Reasoning |
|---|---|---|
| Create the foreman's job folder / copy the four documents in / replace the older version | **auto Action** `link-job-documents` — `documented_by` to the latest revision, the previous superseded, `documents_complete` set, the packet label derived | There is no folder to create. The SDD's folder *was* the packet; the packet is now a graph fact. Additive, reversible, deterministic from a governed convention. Nobody's judgment. |
| Delete a job folder when the job leaves a foreman | **nothing — it is an observation** | `job-sync` withdraws the `assigned_to` edge (validity ended, never deleted), so "no longer this crew's" is a graph fact the workspace renders as *former crew*. There is no destructive act left to gate. This is the single biggest simplification in v2. |
| Move the pictures folder to `Archive` | **nothing** | Same. |
| Exception / Failure / Stats / Unknown-exception mail to Heather | **no Action, no Task by default** | *"Notify/record — that is just the Event in the log."* The Exception Report is the run journal plus the aggregate Task; the Stats Report is the run outputs the Flows page charts; the workspace is the standing view. |
| "No documents found for a job" (SDD 1, exc. 3) | **aggregate Task** to `field-operations`, only past the governed recurrence threshold | The SDD's own words: "log as information and continue; notify if recurring". |
| "Job not found / crew not recognized" (SDD 1, exc. 2 + the SUBCONTRACTOR crew) | **aggregate Task** to `field-operations` | A `crewId`/`foreman` string that resolves to no `Team` is one identity question, not N document questions. |
| Certification 60/30/15 alerts | **auto Action** `record-compliance-alert` — stamps `last_notified_on` + `last_notice_stage`, moves `status` | Deterministic date arithmetic. A supervisor's inbox is a *delivery surface*, not a decision; the alert becomes a row at the top of the Compliance workspace and, at the last stage, a Task. |
| "Records over 7 years flagged for deletion — no automatic deletion, reported for manual review" | **aggregate Task** to `hr-safety` | The SDD already says this. It is deletes-are-decisions arriving from the customer's own side; quote it in the asset. |
| Write a row into a department workbook | **`apply-schedule-day` into the graph at `pending_approval`, gated behind the approval Task** | 2.5.10 makes approval a permanent control point, so the Action is *proposed* on the Task and the day is only `approved` after. The doctrine's "put the gate on the Action itself". |
| Flag a day "Pending Approval" | **`ScheduleDay.status`** | *"A record's status is a governance gate, not a label."* |

### A-3 · Where the "workbook" went
**The graph is the schedule. The workbook is an export.**

`ScheduleDay` + its `ScheduleEntry` parts are `authoritative=True`: Cumulus is the system of record
for a plant schedule, because nothing else in Wick's estate is — the workbook was a workaround for
the absence of one. Nothing is written back.

What replaces the .xlsx, for people and for portability:
1. **The Plant Schedule workspace** (§E) is the operational surface: a department × day grid.
2. **A derived Dataset the customer owns** — `GET /api/v1/schedule/export?week=YYYY-Www&department=<slug>`
   answering `text/csv` (one row per `ScheduleEntry`: job number, label, hours, basis, sequence,
   row kind, vehicle ref, placed_by). A Tier-1 REST route, not a console BFF concern, because a
   portable export is a platform capability. Per DOCTRINE point 204 it is a **derived** Dataset —
   `{from: graph:ScheduleDay/<key>, recipe: "schedule-export-v1", producedBy: <run>}` — so it is
   **regenerated on request and stored nowhere**: *"sources are synced; derivatives are
   regenerated."* Only an `approved` day exports; a `pending_approval` day answers 409 with the
   Task id, because an export a supervisor has not signed reads as a schedule.

| Option | Why not |
|---|---|
| Write cells into an .xlsx in the DMS | Not a governed write: no field to name in a transition table, no value to verify by re-reading, no way for the estate to refuse an illegal one. And it re-creates the workaround. |
| A new estate `scheduleEntries` entity to mirror into | The estate models Wick's *real* systems, and Wick has no scheduling system. Inventing one would model the workaround in the place that is supposed to be the truth. |
| Store each export as a Dataset row | A snapshot of a graph you already hold, going stale the moment a supervisor adjusts a row. |

**Deferred decision, recorded not built:** an optional outbound `publish-job-packet` Flow that
mirrors a document packet into SharePoint for crews with no Cumulus access. Not v1. If adopted it is
the *only* reason `estate_writes.py` grows a create verb.

### A-4 · Ontology additions, kept general (WORKPLAN decision 8)
Full spec in §B. Reuse vs new:

| Concept | v2 | Why |
|---|---|---|
| Wick's customer on a job | **reuse `Company`**, resolved from `customerName` | Already the counterparty type with `company_identified_as`. |
| The builder on a job | **a landed string on `Job`**, not yet a `Company` | An observation awaiting promotion — the `vendor_name`-before-resolution pattern. Promote it the day a competency question needs to traverse it. |
| Plant department (Truss Cut, Steel Fab, …) | **reuse `Team`** | "An organizational unit", nests via `subteam_of`, has `led_by`. The HRIS lands the ten as departments. |
| Crew (a foreman and his people) | **reuse `Team`**, `led_by` the foreman, `external_id` = the HRIS `costCenter` (the crew id) | Same argument, and it makes `crew_id → Team` a Mapping binding rather than flow logic. |
| Foremen, supervisors, drivers | **reuse `Employee`** | And `member_of` gives the headcount for free (A-5). |
| Document trees and files | **reuse `Folder` / `Document`** | `Document`'s own summary argues against a second type; `storage_ref` / `latest_version` / `read_version` / `sha256` already model reference custody and "has anybody read this". |
| Certification / licence / medical card / clearinghouse | **NEW `Credential`** — *not* `Capability` | A `Capability` is an encoded skill with proficiency on the link, no expiry, no issuer, no renewal clock and no alert history. A thing that expires has its own identity and its own edges. |
| Vehicle / trailer / lift | **NEW `Equipment`** | An inspection and a due date hang off the asset, not off whoever drove it. |
| One dated inspection | **NEW `Inspection`**, a *part* of `Equipment` | Weak entity: means nothing without the asset, dies with it. |
| An AXIS job | **NEW `Job`** | Root of the production graph. |
| One department's share of a job | **NEW `JobTask`**, a *part* of `Job` | `250553/truss-cut` means nothing outside the job. |
| A department's day, with capacity and incentive | **NEW `ScheduleDay`** | The unit approval happens on, and the type that replaces a workbook tab. |
| One row of that day | **NEW `ScheduleEntry`**, a *part* of `ScheduleDay` | |
| `Department`, `Crew`, `Customer`, `WorkOrder`, `Truck`, `Trailer`, `TrussStandard`, `Alert` | **not added** | Each is a reuse or machinery (an alert is a claim plus a ledger row; a truss standard is a governed Dataset). |
| `PRECEDENCE` | **no change** | `human > erp > hris > crm > calendar > office > dms > psa > procurement > flow > model` already orders every Wick claim: AXIS as `erp` outranks a `model` grid reading, and a supervisor's correction as `human` outranks both. |

### A-5 · Which SDD process is which Flow, and its shape
Read off the Trigger, per DOCTRINE point 201 — never declared.

| Flow id | Shape | Woken by | SDD origin |
|---|---|---|---|
| `field-documents` | **windowed** | `JobRecordsDelivered` **and** `DocumentsDelivered` | **the whole of SDD 1** |
| `credential-tracking` | **windowed** | `ComplianceWindowClosed` (daily timer) | SDD 2, all five sub-processes |
| `plant-eligibility-sweep` | **windowed** | `PlantWindowClosed` (11:00 CST Mon–Fri) | SDD 3 Dispatcher |
| `shipping-list-extraction` | **per case** (job) | `JobEligible` | SDD 3 Performer 1 |
| `department-scheduling` | **windowed** (one run per department-day) | `DepartmentDayOpened` | SDD 3 Performer 2 |

Four shape calls worth confirming:

1. **SDD 1 is one windowed Flow, not three.** There is no PDF to parse, no folder to create and no
   folder to delete — so the Dispatcher, the Performer and the weekly reconciliation collapse into
   one standing question asked of the graph: *which scheduled job inside the horizon does not have
   its four documents linked at their latest revision?* It is windowed because the unit of work is a
   pass, and it raises **at most two aggregate Tasks**, never one per job.
2. **Two Triggers on one Flow, and the adjacency is load-bearing.** `field-documents` is declared
   immediately after `jobs-delivered` and after `documents-delivered-ap`, because it reads rows
   `job-sync` and `dms-sync` land, and Triggers on one event fire in declaration order. Both events
   wake it because either half can be the new fact: a job becoming `Scheduled`, or a revision
   arriving for a job already scheduled.
3. **`department-scheduling` is windowed per department-day.** The SDD's ten queues are a UiPath
   artifact. Capacity, incentive and headcount are properties of the day; "sort by load date and add
   until capacity" is a batch decision; 2.5.10's approval is per department-day. One run, one
   aggregate Task, one governed commit. A windowed Flow may still suspend — *"a response pauses in
   place for a human"* is available to every Flow.
4. **A dependency not met ends the run, it does not wait.** Cumulus records `deferred` with the
   reason and finishes; the next day's eligibility sweep re-raises the day. Nothing sits warm —
   Lens 8: *"whenever a component looks like it must run continuously … that is almost always a
   mis-named capability."* Same for a suspended extraction: the job simply has no `JobTask` rows
   yet, so the scheduling window does not schedule it and says so as a count.

### A-6 · Desks and personas
New rows in `server/app/desks.py`:

| slug | description | `owner` |
|---|---|---|
| `field-operations` | "Job document packets that need a person: a job the document system holds nothing for, and a crew id that resolves to no team." | `tasks.field_documents.domain` |
| `hr-safety` | "Certifications, licences and inspections that need a person: a date that will not parse, a holder nobody can be found for, a record past its retention horizon." | `tasks.credential_tracking.domain` |
| `plant-supervisor` | "A day's department schedule awaiting sign-off, and an extracted grid the run could not read confidently." | `tasks.department_scheduling.domain` |

Reused unchanged: `platform-operations`, `ap-clerk`, `expense-review`, `expense-review-exec`,
`knowledge-curation`, `eval-review`, `vendor-stewardship`. Declared and inert in Wick: `order-desk`
and `data-stewardship`-as-merge-desk (no `crm` → `disagreement-sweep` finds nothing) — the Sources
and Queues screens already read that honestly.

`server/app/casts/wick.py`. **Every customer persona is a real person named in the SDDs**, at
`@wickbuildings.com`:

| persona id | authority | scope | acts as | queues |
|---|---|---|---|---|
| `naviant-engineer` | owner | platform | Mitchell Suter · `masuter@naviant.com` | `ALL_QUEUES` |
| `naviant-support` | user | platform | — · `support@naviant.com` | () |
| `customer-admin` | admin | tenant | **[ESTATE] one Wick IT/systems owner** — until named, `email=""` and the hub opens on its not-in-the-workforce state, the honest degrade `ENGINEER` already demonstrates | `knowledge-curation`, `eval-review` |
| `customer-user:field-ops` | user | own | Heather Danegelis, Field Documents process owner | `field-operations` |
| `customer-user:plan-designer` | user | own | Marlea Knox, Agile Coach / Construction Plan Designer | `field-operations` |
| `customer-user:hr-safety` | user | own | Katy Tiller, Process Owner — HR & Safety | `hr-safety` |
| `customer-user:safety-sme` | user | own | Steve Woodbury, Process SME, Fork West | `hr-safety` |
| `customer-user:ops-coordinator` | user | own | Abby Wick, Operations Coordinator | `plant-supervisor`, `ap-clerk`, `expense-review` |
| `customer-user:plant-manager` | user | own | Chris Robson (the SDD's authority on department min/max) | `plant-supervisor` |
| `customer-user:foreman` | user | own | **[ESTATE] one named foreman** | () — the persona that proves scoping, as `ae` does today |

**Flagged:** the `ap-clerk` / `expense-review` binding on Abby Wick is the one binding *not* read off
the SDDs — they name no AP clerk. It is there so the kept AP and expense flows are testable as a
customer persona rather than only as the engineer. Move it if there is a better answer.

### A-7 · Labor-hour formulas, the min/max table, and the bonus cell
**Rules over governed parameters, with the arithmetic in one pure module that holds no literal.**
Modelled exactly on `server/app/tasks/accounts_payable/v8/arithmetic.py`
(`check_arithmetic` + `parse_arithmetic_tolerances`) — the repo's existing answer.

| Layer | Where | What is in it |
|---|---|---|
| **Numbers** | governed Datasets, parsed the way `parse_posting_policy` / `parse_match_tolerances` / `parse_routing` already parse markdown (tolerant, line- and table-oriented, degrading to a built-in default the run *says* it fell back to) | the 2.5.3 min/goal-max/abs-max/fudge table; 2.5.7 lead times; 2.5.6 farthest-out windows; the 2.5.5 headcount→capacity ladder; the span/member lookup; the eligibility clauses; the AXIS vocabulary map; **and the incentive's starting figure and per-hour rate** |
| **Arithmetic shape** | `server/app/tasks/department_scheduling/formulas.py` (NEW) — pure, **no literal**; every constant arrives as a parameter | `bundles = ceil(weight ÷ bundle_lb)`, `(total_lf × rate) + setup + colour_surcharge`, `doors = end_caps ÷ 2`, the Perma Column base subtraction, the NAILING/GIRTS row predicates, Loading's truck consolidation, and `incentive(hours) = start + rate × hours` |
| **Verdict** | the `rule` step — pure, fetches nothing | schedule today / next business day / defer / escalate as out-of-range |
| **Explanation** | `ScheduleEntry.basis` | the formula name plus the asset ref at revision (`knowledge:labor-hour-formulas@rev3`), so "why 27.4 hours" answers with a clause, not a stack trace |

Two v2 specifics:
- **`incentive_per_hour` is computed, never read.** The SDD's bonus cell is a spreadsheet formula
  that starts negative and rises with hours. Reverse-engineered as two governed values per
  department — a starting figure and a per-hour rate — it becomes a pure Rule, which means the
  "out of the hole" indicator is explainable (`start −$180.00 + $6.20/h × 29.0 h = +$0.80`) rather
  than a cell nobody can audit. Confirm the two-parameter model is faithful enough; if a department's
  curve is not linear, the asset gains a table and `formulas.py` gains a lookup — no schema change.
- **`truss-standards` is a governed Dataset, not a document read per run.** The estate holds no
  `Truss_Standards_2026.xlsx`, and a run whose numbers come from an unversioned spreadsheet cannot
  say which numbers it used. The asset names the source workbook and the year in its own header.

| Option | Why not |
|---|---|
| Python in the flow package, numbers included | Fails the transparency bar (*"no opaque steps whose logic cannot be inspected"*) and puts the four departments Chris Robson still owes numbers for behind a deploy. |
| A formula DSL in the asset | An interpreter for `ceil(bundles × 0.15)` is machinery nobody asked for. DOCTRINE point 199 reserves a Rule's *implementation* while requiring its *meaning* be inspectable; parameters-in-the-asset plus reviewed pure arithmetic is exactly that trade. |

### A-8 · Cadence (unchanged from v1)
SDD 2 says monthly-on-the-1st in §5.1 and "daily" elsewhere. **The scheduler fires
`ComplianceWindowClosed` daily; `certification-alert-policy` carries `- run cadence: daily` and the
notice stages.** A daily window with nothing due writes nothing and raises nothing — the
`last_notice_stage` guard is what makes that safe. Monthly is an asset edit plus one cron change,
and the asset is what decides whether work happens, so the two can never silently disagree about the
SLA.

### A-9 · The naming convention, and its two traps
The packet label is `<job number> <first keyword of the customer's company name>`. Two answer keys
the estate seeds deliberately, and the convention asset must handle both:
- **`The Willows`** — the first keyword is an article. The asset carries `- label stopwords: the, a,
  an, of` and the flow skips them, giving `250661 Willows`.
- **two customers beginning `Evermore`** — a collision. The asset carries
  `- on label collision: append the next keyword`, giving `250553 Evermore Ridge` and
  `250774 Evermore Farms`.
Both are pure-function behaviour over governed values, both are eval cases (§F-1), and neither is a
literal in code. Confirm the two rules; a third trap is one asset line.

### A-10 · Outbound mail: not in v1, and here is exactly what it would be
**Recommendation: no outbound mail in v1.** The three workspaces are the notification, and an email
Cumulus sends is a write nobody can un-send. Stated concretely so it is a decision and not a
discovery, as a **second-wave** item:

- A new Gateway Action `send-estate-mail` (regime `decision`, `queue_from_task=True` where a Task
  authorized it).
- A new `estate_writes.WRITABLE` row — `mail-message`, app `office`, resource `messages`,
  **verb `create`** (`POST /messages` then `POST /messages/{id}/send`), which is the one thing that
  would bring the create verb back into `estate_writes.py`.
- Recipients as **governed knowledge per notice class**, not per person: `certification-alert-policy`
  gains a `## Recipients` table (`certification alert → the holder's supervisor`,
  `dot compliance → the driver and the safety SME`, `inspection → the crew foreman and the safety
  SME`, `exception report → the HR & Safety desk`), resolved against the org chart the way
  `routing.resolve` already resolves `manager-of(submitter)`.
- Behind `ESTATE_WRITES_ENABLED`, so a deployment that has not deliberately turned it on holds no
  callable that could reach a mail server.

### A-11 · What changes in AP and expense for Wick
**Almost nothing.**
- `ap-intake-policy`'s `- invoice folder:` line edited to Wick's own AP folder path. Asset edit.
- `invoice-posting-policy`'s `## Approval routing` fallback stays `ap-clerk`; Abby Wick holds it.
- `expense-approval-policy`'s bands and the `expense-review-exec` threshold are Wick's numbers.
- `erp-vendors` / `erp-purchase-orders` / `erp-receiving` are **[ESTATE] confirm**: if Wick's `erp`
  carries only `jobs` and `assets`, those three feeds go inactive. See risk I-9.
- No version bump: `accounts-payable` stays v9, `expense-reimbursement` v10.

---

## B · Ontology plan

All additive. **No existing type, field, link, tag, competency question or precedence entry changes** —
the naviant-regression contract for Lane O.

### B-1 · New ObjectTypes

| name | natural_key | root/part | collection | tags | fields |
|---|---|---|---|---|---|
| **`Job`** | `source + job number` | root | `production-records` | `("jobs","production","scheduling")` | `job_number`, `name`, `customer_name`, `builder_name` (string — "the builder of record as the source names them; a landed observation, not yet resolved to a Company"), `plant_site`, `status` (enum — the source's own word: Scheduled \| In Progress \| …), `load_date`, `shipment_date`, `week_position` (number — "which week of the production window the source places it in; 0 when it places it in none"), `release_state` (enum — "released \| held \| staged, derived from the source's own flags by the governed vocabulary table"), `hold_reason` (string — "verbatim, because a hold code is the customer's vocabulary and not ours"), `foreman_name` (string — "as the source prints it; the display half of a crew reference nobody has resolved yet"), `crew_id` (string — "the source's crew identifier; bound to a Team by the Mapping"), `shipment_type`, `combined_with` (string — "the shipment this one consolidates with, as the source names it"), `trailer_number`, `width`, `length`, `height` (numbers — "the primary structure's dimensions as the source holds them"), `secondary_dimensions` (string — "dimensions of attached structures the source records separately, kept verbatim because which of them a labor formula sums is a governed decision and not ours"), `upstream_done` (string — "the production stages the source marks complete, as it names them"), `documents_complete` (enum), `site_address`, `external_id`, `source` |
| **`JobTask`** | `job key + department slug` | **part of `Job`** | `production-records` | — | `department` (string, the `Team` slug), `labor_hours` (number), `hours_basis` (string — "which governed formula produced the hours, and at which asset revision, named so a number can be checked"), `demand_summary`, `source_page` (string — "which page of the source document the demand was read off"), `confidence` (number), `upstream_state` (enum: `blocked \| ready \| scheduled \| done`), `out_of_band` (enum) |
| **`ScheduleDay`** | `department slug + date` | root | `production-records` | `("scheduling","capacity","production")` | `department`, `schedule_date`, `status` (enum: `proposed \| pending_approval \| approved \| rejected`), `total_hours`, `goal_max_hours`, `abs_max_hours`, `fudge_hours`, `headcount` (number), `headcount_as_of` (date — "when the workforce feed last landed; a headcount is only as current as the feed behind it"), `incentive_per_hour` (number — "computed: the governed starting figure plus the governed rate times the day's hours. Negative until the day clears the threshold"), `approved_by`, `approved_at`, `deferred_count`, `exported_at` (date — "when this day was last handed out as a Dataset") |
| **`ScheduleEntry`** | `schedule day key + job number` | **part of `ScheduleDay`** | `production-records` | — | `job_number`, `job_label` (string — "the per-department abbreviation convention's output"), `ticket_number`, `labor_hours`, `sequence`, `basis`, `row_kind` (enum: `job \| nailing \| girts \| consolidated \| placeholder`), `vehicle_ref` (string — "the truck or trailer a loading row consolidates onto, where the schedule has one"), `placed_by` (enum: `flow \| human` — "`human` only ever comes from a supervisor's Task correction, and is what the flow must never overwrite") |
| **`Credential`** | `source + the source's record id` | root | `compliance-records` | `("people","compliance","credentials")` | `credential_type`, `category` (enum: `certification \| licence \| medical \| clearance \| training`), `holder_email` (denormalized from `holds_credential`), `assigned_on` (date — "the original award date, which does not move when a renewal is recorded"), `completed_on`, `expires_on`, `result` (enum: `pass \| fail \| pending`), `provider`, `status` (enum: `valid \| expiring \| expired \| withdrawn`), `last_notified_on`, `last_notice_stage` (string — "which governed notice stage was last sent, so a cycle does not repeat one"), `retain_until`, `external_id`, `source` |
| **`Equipment`** | `source + equipment id` | root | `equipment-register` | `("equipment","operations","compliance")` | `equipment_id`, `name`, `category` (enum: `vehicle \| trailer \| lift \| machine \| other`), `make`, `model`, `year`, `identifier` (string — "VIN, serial, whatever the source keys the physical asset by"), `home_site`, `inspection_tracked` (enum: `yes \| no` — "derived once, on the way in, from whether the source holds a next-due date at all. A skid steer with a null due date is *not tracked*, which is a fact about the asset and not a gap in the feed"), `external_id`, `source` |
| **`Inspection`** | `equipment key + inspection date` | **part of `Equipment`** | `compliance-records` | — | `inspected_on`, `next_due_on`, `result` (enum: `pass \| fail \| conditional`), `inspector`, `notes`, `last_notified_on`, `last_notice_stage`, `external_id` |

**`authoritative=True` on `ScheduleDay` and `ScheduleEntry` only** — and in v2 that is the load-bearing
statement, not a flourish: Cumulus is the system of record for a plant schedule because nothing else
in Wick's estate is. `Job`, `Credential`, `Equipment` are `False`; AXIS and the HRIS own their rows,
and the alert stamps Cumulus writes on them are `flow` claims on a non-authoritative type, exactly as
`Document.sha256` already is.

### B-2 · New LinkTypes

| name | from → to | cardinality | nature | why this shape |
|---|---|---|---|---|
| `has_job_task` | `Job → JobTask` | one-to-many | **composition** | the department shares of a job |
| `job_for` | `Job → Company` | many-to-one | association | the customer a job is built for — one, and a second is a resolution bug |
| `assigned_to` | `Job → Team` | **many-to-many** | association | the crew a job is scheduled with. Many-to-many is now doing real work: a job moving between crews **withdraws** one edge (validity ended, never deleted) and draws another, and the pair *is* the reconciliation the SDD wanted a weekly bot for |
| `documented_by` | `Job → Document` | many-to-many | association | the artifacts a job's facts were read out of — mirrors `evidenced_by_document` on `Invoice` |
| `job_folder` | `Job → Folder` | many-to-many | association | the document-system folders holding this job's artifacts (`Job Documents/<job>`, `PlantCutSheets/<site>/<job>`) — two roots, so many-to-many |
| `has_schedule_entry` | `ScheduleDay → ScheduleEntry` | one-to-many | **composition** | the rows on the day |
| `scheduled_for` | `ScheduleEntry → Job` | many-to-one | association | the job a row is about |
| `scheduled_by` | `ScheduleDay → Team` | many-to-one | association | the department whose day this is — what makes "which departments is Chris responsible for" a traversal, and how the headcount is counted |
| `holds_credential` | `Employee → Credential` | one-to-many | **association, not composition** | a credential outlives the currency of the employment record: the SDD retains a terminated employee's certification for seven years, and killing it with the `Employee` is exactly what that forbids |
| `has_inspection` | `Equipment → Inspection` | one-to-many | **composition** | |
| `operated_by` | `Equipment → Team` | many-to-one | association | how "which equipment at Fork West is out of inspection" becomes a traversal |

### B-3 · `validate_ontology`, checked
- Roots declare tags **and** competency questions: `Job`, `ScheduleDay`, `Credential`, `Equipment`.
- Parts declare **neither**: `JobTask`, `ScheduleEntry`, `Inspection`.
- Each part has exactly one compositional owner: `has_job_task`, `has_schedule_entry`,
  `has_inspection` — three two-node trees, no cycle.
- Composition cardinality `one-to-many` on all three.
- No duplicate link name — grep `LINK_TYPES` before landing; `assigned_to` and `documented_by` are
  the two most likely to collide with a future feed.
- **No `ref:` field on any new type.** `JobTask.department` stays a plain string naming a `Team`
  slug, because the department's `Team` is reachable through `ScheduleDay.scheduled_by`; and
  `Job.builder_name` / `Job.crew_id` stay plain strings because nothing traverses them yet. The
  doctrine's rule is that a ref field a competency question traverses must become a Link — the day
  one does, they are promoted.

Competency questions, for the record (this is the admission test):
- `Job`: "Which jobs are scheduled to load in the next two weeks?" · "What is {label}'s production
  state, and which departments still have work on it?" · "Which crew is {label} assigned to now,
  which crews held it before, and are its four documents current?"
- `ScheduleDay`: "Is {label} approved, and who signed it?" · "How far is {label} from its goal, and
  from its absolute maximum?" · "What did {label} defer, and why?"
- `Credential`: "When does {label} expire, and who has been told?" · "Whose {label} expires in the
  next 60 days?"
- `Equipment`: "When was {label} last inspected, and when is it next due?" · "Which equipment at
  {label}'s site is out of inspection?"

### B-4 · `names.py`, Collections, PRECEDENCE, Datasets
- `server/app/ontology/names.py` gains `JOB`, `JOB_TASK`, `SCHEDULE_DAY`, `SCHEDULE_ENTRY`,
  `CREDENTIAL`, `EQUIPMENT`, `INSPECTION` via `require_type(...)`. **No link constants** — the file's
  docstring restricts itself to lookup keys and declarations, and today's traversals pass link names
  as strings (`ap_intake` passes `"filed_under"`). Follow the convention.
- `server/app/data_collections.py` gains three:

| id | object_types | custody | authoritative | retention |
|---|---|---|---|---|
| `production-records` | `Job`, `JobTask`, `ScheduleDay`, `ScheduleEntry` | copy | **mixed — and the mix is the point:** the jobs are AXIS's, the schedule is ours | 7 years — a schedule is the record of what a plant was told to build |
| `compliance-records` | `Credential`, `Inspection` | copy | no | **7 years**, the SDD's own number, and the horizon the retention Task is raised against |
| `equipment-register` | `Equipment` | copy | no | while operated + 7 years |

- **`PRECEDENCE` unchanged.**
- **Governed Datasets (7 assets, all `server/seed/*.md`, pushed by `--knowledge`):**
  `field-documents-policy` · `certification-alert-policy` · `plant-scheduling-parameters` ·
  `labor-hour-formulas` · `truss-standards` (kind `config`) · `shipping-list-extract-prompt`
  (kind `prompt`) · `shipping-list-locate-prompt` (kind `prompt`).
  Dropped from v1: `crew-schedule-intake-policy`, `crew-schedule-parse-prompt`.
- **The schedule export** is a *derived* Dataset (A-3), regenerated from the graph, stored nowhere,
  and therefore in no Collection.

---

## C · Sync / Mapping plan

### C-1 · New FEEDS in `server/app/estate.py`

| feed const | id | app | resources (order is load order) | build fn |
|---|---|---|---|---|
| `ERP_JOBS` | `erp-jobs` | `erp` | `("customers", "jobs")` | `_build_erp_jobs` |
| `HRIS_CREDENTIALS` | `hris-credentials` | `hris` | `("trainingRecords", "driverQualifications")` | `_build_hris_credentials` |
| `ERP_EQUIPMENT` | `erp-equipment` | `erp` | `("assets",)` | `_build_erp_equipment` |

Three feeds, not four: **`erp-schedule` is gone** because the estate has no schedule. Each is its own
feed rather than a resource on an existing one, for the reason `erp-receiving` states — they move on
different clocks, and a shared cursor would let one failure hold back rows that were already safe to
keep. `DMS_LIBRARY` and `OFFICE_MAIL` need **no change at all**.

### C-2 · New Mappings in `server/app/mappings.py`

| id | strategy | lands_as | resolves_to | identity bindings | precedence | steward queue | escalation |
|---|---|---|---|---|---|---|---|
| `erp-jobs` | **reify** | `Job` | `("Company","Team")` | `customer_name → Company` (normalized legal name); `job_number → Job` (job number); **`crew_id → Team` (the HRIS department cost centre)** | `erp` | `data-stewardship` | per-row task (for the Company match) |
| `hris-credentials` | **bind** | — | `("Employee","Credential")` | `holder_email → Employee` (work email) | `hris` | — | aggregate count |
| `erp-equipment` | **reify** | `Equipment` | `("Team",)` | `equipment_id → Equipment` (equipment id); `home_site → Team` | `erp` | — | aggregate count |

Three notes that carry the design:
- **`crew_id → Team` is a Mapping binding, not flow logic.** The HRIS lands a crew as a department
  whose head is the foreman and whose `costCenter` is the crew id; the ERP's job names the crew by
  that same id. Declaring the binding here is what makes "this crew id is that team" a portable,
  customer-owned statement — and what makes a crew id that resolves to nothing a **countable
  aggregate** rather than a per-row failure.
- **`hris-credentials` binds** because a work email is exact and nobody adjudicates it — the HRIS
  feed's own argument. Both resources become `Credential` rows distinguished only by `category`
  (`trainingRecords` → `certification` / `training`; `driverQualifications` → `licence` / `medical`
  / `clearance`), because a thing that expires and gets renewed is one type whatever department
  tracks it.
- **`erp-jobs` reifies** because a customer name is fuzzy and a steward rules on the match.

`validate_mappings` requires: no duplicate `source`; `reify` names `lands_as`; `bind` must **not**;
every `lands_as`/`resolves_to`/`identity.object_type` declared; `collection` declared;
`precedence_source` in `PRECEDENCE`; `steward_queue` a declared desk; and one desk per resolved type
across every per-row-task feed — hence `data-stewardship` for `Company`, matching `crm-book` /
`erp-sales`.

### C-3 · New connector flow packages

| package | FLOW_ID | lands | body |
|---|---|---|---|
| `server/app/tasks/job_sync/` | `job-sync` | `Job` + `job_for` + `assigned_to` (drawn, and **withdrawn** when a job's crew changes) + `job_folder` | NEW `server/app/connectors/production.py::job_body` |
| `server/app/tasks/credential_sync/` | `credential-sync` | `Credential` + `holds_credential` | NEW `server/app/connectors/compliance.py::credential_body` |
| `server/app/tasks/equipment_sync/` | `equipment-sync` | `Equipment` + `Inspection` + `operated_by` | NEW `compliance.py::equipment_body` |

Each is a thin package on `vendor_sync`'s model: `__init__.py` re-exporting
`FLOW_ID`/`DEFAULT_VERSION`, `v1/flow.py` with `STEPS` + `build_body`. Registered in `SyncService`,
wired in `server/app/deps.py::get_sync_service`. All three commit through the existing
`apply-source-records` observation Action — no new Action needed.

`job_body`'s one interesting decision: **withdrawing an `assigned_to` edge is an observation, not a
deletion.** The link's validity is ended and the edge stays on the record, which is exactly what
lets the Field Documents workspace show *former crew* beside *current crew* — and what retires the
SDD's weekly reconciliation bot.

`equipment_body`'s one interesting decision: **`inspection_tracked` is derived once, on the way in,**
from whether the source holds a `nextInspectionDueDate` at all. A skid steer with a null due date is
*not tracked*, and saying so on the row is what stops the compliance sweep from reporting it forever
as overdue. `Inspection` parts are derived one per landed inspection date.

### C-4 · Wire shapes to add in `server/app/connectors/sync.py`

```
JobRecord             external_id, job_number, name, customer_name, builder_name,
                      plant_site, status, load_date, shipment_date, week_position,
                      out_to_yard: str, do_not_fab: str,        # the estate's "Yes"/"No"
                      foreman: str, crew_id: str,
                      shipment_type, combined_with, trailer_number,
                      width, length, height, secondary_dimensions,
                      upstream_done: list[str]                  # the ten <dept>Done flags
CredentialRecord      external_id, holder_email, holder_external_id, credential_type,
                      category, assigned_date, completion_date, expiration_date,
                      status, result, provider
EquipmentRecord       external_id, equipment_id, name, category, make, model, year,
                      identifier, home_site,
                      last_inspection_date, next_inspection_due_date   # null ⇒ untracked
```

`SyncRequest` gains `jobs`, `credentials`, `equipment` — each with the same "these travel together
and why" comment the existing pairs carry. **Dropped from v1:** `JobDepartmentRecord`,
`ScheduleDayRecord`, `ScheduleEntryRecord`, `InspectionRecord` (folded into `EquipmentRecord`'s two
dates), `MailAttachmentRecord`.

`out_to_yard` / `do_not_fab` arrive as the estate's own `"Yes"`/`"No"` strings and are mapped onto
`release_state` / `hold_reason` **by the flow**, against the `## Source vocabulary` table in
`plant-scheduling-parameters` — the precedent being `connectors.sync._event_status` and
`connectors.office._importance`, which are already where "this estate's word becomes our enum" is
decided. The connector renames and never judges.

### C-5 · The four remaining [ESTATE] contracts

| # | App · resource | Shape Cumulus reads |
|---|---|---|
| 1 | `erp` · `GET /api/wick/v1/jobs?updatedSince=` | `{id, jobNumber, name, customerId, customerName, builder, plantSite, status ("Scheduled"\|"In Progress"\|…), loadDate, shipmentDate, weekPosition, outToYard ("Yes"\|"No"), doNotFab ("Yes"\|"No"), shipmentType, combinedWith, trailerNumber, buildingWidth, buildingLength, buildingHeight, wings (verbatim), siteAddress, foreman, crewId, trussCutDone…loadingDone (ten "Yes"\|"No" picklists), updatedAt}` · plus `GET /customers` (already exists) for `Company` |
| 2 | `erp` · `GET /api/wick/v1/assets?updatedSince=` | `{id, assetId, name, category, make, model, year, identifier, homeSite, lastInspectionDate, nextInspectionDueDate (**null for skid steers**), updatedAt}` |
| 3 | `hris` · `GET /trainingRecords`, `GET /driverQualifications`, `GET /departments` | training: `{id, employeeId, employeeEmail, courseName, category, provider, assignedDate, completionDate, expirationDate, status, result ("Pass"\|"Fail"), updatedAt}` · driver: `{id, employeeId, employeeEmail, cdlExpirationDate, medicalCardExpirationDate, pspDate, clearinghouseDate, updatedAt}` · departments: the **ten plant departments spelled the HRIS way**, plus the crews with `headEmployeeId` = the foreman and **`costCenter` = the crew id** |
| 4 | `dms` · `folders` + `documents` (existing shape, no change) | `Job Documents/<job>` holding the four governed types **with revisions** (`latestVersion` > 1 on at least one job, so version-replace is demonstrable) · `PlantCutSheets/<site>/<job>` holding the multi-page shipping list (A1/B/C/D/E/F/LL) and column cut sheets · the compliance documents as real documents |

Plus one non-contract: `office` · mailboxes for the cast, served by the **existing** `office-mail`
feed with **no shape change** — no attachments, no crew-schedule mail.
And one discovery line: `admin` · `namespaces.json` gains `wick` with
`apps: ["erp","office","hris","dms"]` — no `crm`, no `psa`, no `pas`.

Cumulus needs nothing else for the naviant-only feeds: `pull` already refuses a feed whose app the
namespace does not run, and `ui_api._sync_now_refusal` already prints the sentence. Verify that on a
Wick deployment as an acceptance step rather than assuming it.

---

## D · Flow plan

Step types are exactly `flows.StepType`:
`trigger · modelcall · transform · rule · task · action · event · sync · check · resolve`.

### D-0 · Trigger table (`server/app/dispatch.py`, Lane S lands all of it)

| Trigger id | event | starts | wakes | lands in |
|---|---|---|---|---|
| `jobs-delivered` | `JobRecordsDelivered` | `job-sync` | window | **S** |
| `credentials-delivered` | `CredentialRecordsDelivered` | `credential-sync` | window | **S** |
| `equipment-delivered` | `EquipmentRecordsDelivered` | `equipment-sync` | window | **S** |
| `jobs-delivered-field-documents` | `JobRecordsDelivered` | `field-documents` | window | **2** |
| `documents-delivered-field` | `DocumentsDelivered` | `field-documents` | window | **2** |
| `compliance-window-closed` | `ComplianceWindowClosed` | `credential-tracking` | window | **3** |
| `plant-window-closed` | `PlantWindowClosed` | `plant-eligibility-sweep` | window | **4** |
| `job-eligible` | `JobEligible` | `shipping-list-extraction` | case | **4** |
| `department-day-opened` | `DepartmentDayOpened` | `department-scheduling` | window | **5** |

**A Trigger and its Flow's `v1` package land in the same commit, in the same lane.** This is a hard
rule, not tidiness. `RunEngine.start` raises `ValueError("No registered body for flow …")` for an
unregistered flow id, and `Dispatcher.emit` does **not** catch it — it starts every matched Trigger in
a list comprehension. So a `documents-delivered-field` row landed in Wave 1, before Lane 2 registers
a body, would make the **next estate delivery 500** and take `POST /api/v1/sources/deliver` down with
it. The three sync Triggers are Lane S's because their bodies are Lane S's; every other row belongs
to the lane that builds the flow it names. This is the one place the plan gives `dispatch.py` to
more than one lane, and the `lands in` column above is the whole coordination.

Note on names: the coordinator's `DocumentRecordsDelivered` is the **existing** `DocumentsDelivered`
(`connectors/sync.py:138`) — one fact, one spelling, and a second name for it would be the drift the
constant exists to prevent.

**Adjacency is load-bearing, twice.** `jobs-delivered-field-documents` is declared immediately after
`jobs-delivered`, and `documents-delivered-field` immediately after `documents-delivered-ap`, because
`field-documents` reads rows `job-sync` and `dms-sync` land and Triggers on one event fire in
declaration order (`_BY_EVENT`). `DocumentsDelivered` now has **three** Triggers — `dms-sync`,
`ap-intake`, `field-documents`, in that order — and `keyed_run_ids` already derives a distinct run id
per Trigger, so an idempotent redelivery re-enters all three rather than starting copies. Put the
same comment `documents-delivered-ap` carries.

Dropped from v1: `mail-delivered-crew-schedule`, `job-scheduled`, `field-window-closed`,
`schedule-delivered`.

### D-1 · `field-documents` (windowed) — `server/app/tasks/field_documents/`
`FLOW_ID = "field-documents"`, `DEFAULT_VERSION = 1`, `POLICY_ASSET_ID = "field-documents-policy"`.
**The whole of SDD 1, and no ModelCall at all** — nothing here reads a page, because the fact the
SDD's PDF carried is now a column on the job.

| id | type | name | what it does · config |
|---|---|---|---|
| t1 | trigger | `JobRecordsDelivered \| DocumentsDelivered` | one run per delivery of either half; `job-sync` / `dms-sync` run first so the rows are in the graph · `{events: "JobRecordsDelivered, DocumentsDelivered"}` |
| x1 | transform | `read-field-policy` | the governed values in one step: the four document types and their order, the scheduling horizon, the naming convention with its stopword list and collision rule, the recurrence threshold, the per-job folder roots, the `## Approval routing` table · `{source: knowledge:field-documents-policy}` |
| x2 | transform | `select-scheduled-jobs` | the standing question, asked of the graph: `Job` rows with `status=Scheduled`, load date inside the horizon, and an `assigned_to` crew — plus the ones whose `crew_id` resolves to no `Team`, kept separately because they are a different answer. A backlog past the window bound is the next window's work, reported not dropped · `{source: "graph:Job,Team"}` |
| x3 | transform | `resolve-document-packet` | per job: walks `Job Documents/<job>` to the `Document` rows `dms-sync` landed, takes the **latest revision of each of the four governed types** (the SDD's "topmost is most recent", now a `latest_version` comparison rather than a screen position), and derives the packet label from the convention — job number plus the customer's first non-stopword keyword, next keyword appended on collision · `{source: "graph:Folder,Document"}` |
| a1 | action | `link-job-documents` | **one guarded Action for the whole window** (a windowed flow lands bulk observations; forty guarded writes would be forty ledger rows saying the same thing about one pass): `documented_by` drawn to each latest revision, the previous revision's edge withdrawn and its `Document` marked `superseded`, `documents_complete` claimed, the packet label claimed, `job_folder` drawn · `{target: production-records, gateway: action-gateway}` |
| c1 | check | `screen-packets` | pure: which of the four types each job is missing; how many consecutive windows it has been missing all four; which jobs have an unresolvable crew · `{rule_type: precedence-list, source: knowledge:field-documents-policy}` |
| k1 | task | `report-document-gaps` | **at most two aggregate Tasks per window**, both to `field-operations`: (i) jobs with no documents past the governed recurrence threshold — the SDD's exception 3, "log as information and continue; notify if recurring"; (ii) jobs whose `crew_id`/`foreman` resolves to no `Team` — the SUBCONTRACTOR-crew case, one identity question rather than N document questions · `{queue: field-operations}` |
| e1 | event | `JobPacketsLinked` | with the counts. Nothing binds it; it is the run's own fact |

**Journal must show:** the policy ref at revision; jobs considered, in-horizon, and each exclusion
reason with its count; per job, which four revisions were linked and which were superseded, with both
refs; the derived label **and which convention rule produced it** (so `250661 Willows` reads as "the
article was a governed stopword" and `250553 Evermore Ridge` as "a collision, so the next keyword was
appended"); the window bound and the deferred remainder; the two aggregate counts.

**What is *not* a step, and why:**
- *"Once the job moves to IN PROGRESS, stop checking"* — a job whose `status` is not `Scheduled` is
  not selected in `x2`. Nothing to switch off.
- *"Delete the folder when the job leaves the foreman"* — the withdrawn `assigned_to` edge is
  `job-sync`'s observation. There is no destructive act.
- *"Move the pictures folder to Archive"* — same.
Say all three in `NOTES`, because a reader comparing the Flow to the SDD needs to see that the steps
were removed rather than forgotten.

### D-2 · `credential-tracking` (windowed) — `server/app/tasks/credential_tracking/`
`POLICY_ASSET_ID = "certification-alert-policy"`. Unchanged from v1.

| id | type | name | what it does |
|---|---|---|---|
| t1 | trigger | `ComplianceWindowClosed` | one run per window over the credential and inspection book |
| x1 | transform | `read-alert-policy` | notice stages (60/30/15), the SLA floor (≥30), the categories tracked, the retention horizon (7 years), the cadence |
| x2 | transform | `gather-expiring` | `Credential` and `Inspection` rows inside the widest stage, joined to holder / equipment and to the supervisor each would notify — the fetching half, so the Rule need not. Rows on `Equipment` with `inspection_tracked=no` are **excluded here and counted**, not carried to the Rule to be rejected |
| r1 | rule | `stage-due` | pure, per row: which stage is due given `expires_on` / `next_due_on`, today, and `last_notice_stage`. A stage already sent this cycle is not re-sent · `{rule_type: threshold}` |
| a1 | action | `record-credential-alerts` | one guarded `record-compliance-alert` for the window (regime **decision** — no source reported this; Cumulus determined it, which is `record-disagreements`' reasoning exactly): stamps `last_notified_on` + `last_notice_stage`, moves `status`, claims `retain_until` |
| c1 | check | `screen-exceptions` | pure, four classes: a missing or unparseable date; a holder nobody can be resolved; a terminated holder still on the active roster; a retained row past the horizon |
| k1 | task | `report-compliance-exceptions` | **one aggregate Task per exception class per window** — at most four, never one per row — each carrying its rows, to `hr-safety` |
| e1 | event | `CredentialAlertsRecorded` | with the per-stage counts |

**Explicitly out of scope, because the SDD scopes it out:** downloading a DOT document, updating a
date in a spreadsheet, retrieving inspection photos. Cumulus tracks the row and asks; it does not
widen the automation past what the customer signed off. Say so in `NOTES` and in the asset.
**Journal must show:** the stage each row was at and moved to; a row skipped because its stage was
already sent, named as such; the untracked-asset exclusion count; the four exception counts.

### D-3 · `plant-eligibility-sweep` (windowed) — `server/app/tasks/plant_eligibility/`
`POLICY_ASSET_ID = "plant-scheduling-parameters"`.

| id | type | name | what it does |
|---|---|---|---|
| t1 | trigger | `PlantWindowClosed` | the 11:00 CST window |
| x1 | transform | `read-plant-parameters` | the eligibility clauses, the business-day calendar and holidays, the department roster, the `## Source vocabulary` table, the jobs-per-window bound |
| x2 | transform | `select-candidate-jobs` | `Job` rows from the graph, sorted by load date ascending |
| r1 | rule | `first-pass-eligibility` | pure, per job: load date ≥ today; `week_position` in the governed set; `release_state` released (from `outToYard`); no hold (from `doNotFab`); load date a business day. The verdict and its reason are on the record · `{rule_type: match-condition}` |
| k1 | task | `report-plant-fault` | the aggregate: **no** `Job` rows at all for the namespace — the feed stopped — to `platform-operations`. An empty *eligible* set is informational (the SDD's exception 5) and raises nothing |
| e1 | event | `JobEligible` | one per eligible job, bound to `shipping-list-extraction`; run synchronously inside the governed bound, and the cost is named where it is paid, in `ap-intake`'s words |
| e2 | event | `DepartmentDayOpened` | **after** e1: one per (department, target business day), bound to `department-scheduling` — so every extraction has run or suspended before any day is filled |

**Journal must show:** the parameters ref; candidates considered, eligible, and each exclusion reason
with its count; the bound and the deferred remainder; how many extraction runs started, succeeded,
suspended and failed; how many department-days opened.

### D-4 · `shipping-list-extraction` (per case) — `server/app/tasks/shipping_list_extraction/`
`PROMPT_ASSET_ID = "shipping-list-extract-prompt"`, `LOCATE_ASSET_ID = "shipping-list-locate-prompt"`.
v9's shape, applied to a different document. **The only ModelCall pair in the plan.**

| id | type | name | what it does |
|---|---|---|---|
| t1 | trigger | `JobEligible` | one run per eligible job |
| x0 | transform | `read-knowledge` | one step, the whole read-set: the extraction prompt, the page→department map (`A1 · B · C · D · E · F · LL`), the confidence floor, the per-department expected hour band, the labor-hour parameters, the truss standards lookup, the approval table resolved for this case |
| x1 | transform | `select-source-documents` | the job's shipping list and column cut sheets out of `PlantCutSheets/<site>/<job>`. **None found** is the SDD's exception 1: the run ends with the job skipped and a count, not a Task |
| s1 | sync | `fetch-pages` | guarded `record-document-read` (existing Action); bytes stored under content hash, the rows stamped `sha256` + `read_version` |
| m1 | modelcall | `extract-shipping-list` | one metered pass over the pages: the grids per page family, with a confidence **per field** · `{model: settings.model, prompt: knowledge:shipping-list-extract-prompt}` |
| c1 | check | `check-grids` | pure: required-field presence; a grid's line amounts against its own total; linear footage against the colour grid; the page→department map. Every failure names the field path it implicates, because those paths become the boxes |
| c2 | check | `check-labor-hours` | pure: the hours the governed formula computes per department against that department's governed expected band. **Out of band is the SDD's exception 4** (">50 h single job-dept → supervisor review *before writing*") and becomes a field verdict rather than a second gate |
| x2 | transform | `field-verdicts` | the union a person is actually asked about — low-confidence, contradicted, absent-but-required, out-of-band. Branches: `m2` when something is in doubt, `a1` when nothing is |
| m2 | modelcall | `locate-fields` | a second narrow look: only the disputed paths, where each is printed; boxes stored as Regions. **Skipped entirely** when nothing is in doubt — no boxes, no call, no cost · `{model: settings.locate_model, skipped_when: "no field verdicts"}` |
| k1 | task | `review-extraction` | the one gate: the fields in doubt with a box on each, the computed hours beside each grid, the page itself. `plant-supervisor`, routed by the approval table. A correction re-runs `c1` and `c2` |
| x3 | transform | `apply-correction` | the corrected values applied, keyed by the same fact paths the verdicts were raised under |
| a1 | action | `apply-job-demand` | guarded: one `JobTask` per department whose grid is present, with the hours, `hours_basis` naming the formula and the asset revision, `demand_summary`, `source_page`, `confidence`, `out_of_band`. **`JobTask` rows are created here and nowhere else.** A department whose grid is absent is skipped — that department only, never the job |
| e1 | event | `JobDemandExtracted` | the run's own fact; nothing binds it, and the scheduling window reads `JobTask` rows out of the graph instead — `ap-intake`'s argument, that a work list scoped to one delivery skews and a standing question does not |

**Journal must show:** which pages were read and which page families were absent; the per-field
confidences; the verdict set; whether `m2` ran, and why not when it did not; the departments written
and the departments skipped for a missing grid; per row, the formula and the asset revision.

### D-5 · `department-scheduling` (windowed, one run per department-day) — `server/app/tasks/department_scheduling/`
**Graph-only. Three Actions, none of them outbound.**

| id | type | name | what it does |
|---|---|---|---|
| t1 | trigger | `DepartmentDayOpened` | one run per (department, business day) · `wakes: window` |
| x0 | transform | `read-knowledge` | this department's min / goal-max / abs-max / fudge row for the day's crew size; its lead time; its farthest-out window; the sequencing and dependency table; the headcount→capacity ladder; **the incentive's starting figure and per-hour rate**; the abbreviation convention; the row-shaping conditions (nailing, girts, consolidation); the labor-hour parameters; the truss standards; the approval table |
| x1 | transform | `read-day-state` | the `ScheduleDay` in **our own graph** for (department, date), if a previous window opened one: its rows, and specifically the `placed_by: human` rows a supervisor's correction created, which this flow **must not overwrite** (2.5.10). Plus `headcount` = **the count of active `Employee`s `member_of` the department's `Team`**, and `headcount_as_of` = the `hris-workforce` feed's last landed time. Nothing is read from any spreadsheet, and nothing is read from the estate |
| x2 | transform | `gather-candidate-work` | the `JobTask` rows for this department whose job is eligible, sorted by load date, each with its job's upstream state read **live** off `Job.upstream_done` — the ten `<dept>Done` flags as the latest `erp` claim holds them |
| r1 | rule | `dependency-and-lead-time` | pure, per candidate: is the upstream department done; is the date inside the lead-time window and the farthest-out window; is the load date a business day. Not-met is `deferred` with a reason, never a failure · `{rule_type: cross-reference}` |
| r2 | rule | `fill-to-capacity` | pure: adds candidates in load-date order until the governed goal max — or, for the two incentive departments, **until the computed incentive crosses zero** — inside the fudge band, with the absolute max as the hard stop. Below capacity is reported and never forced · `{rule_type: accumulation}` |
| a1 | action | `apply-schedule-day` | guarded: the `ScheduleDay` at `pending_approval` and its `ScheduleEntry` parts, each with `basis`, `row_kind` and `placed_by: flow`; `total_hours`, `incentive_per_hour` (computed), `headcount`, `headcount_as_of`, `deferred_count`. **This is the write. There is no second one.** |
| k1 | task | `approve-department-day` | the permanent control point. One Task per department-day to `plant-supervisor` (routed), carrying: the rows with hours and basis; the total against goal and absolute max with the fudge band; the incentive position stated as its own arithmetic (`−$180.00 + $6.20/h × 29.0 h = +$0.80`) so the out-of-the-hole indicator is auditable; what was deferred and why; a **headcount staleness warning** where `headcount_as_of` is older than the governed window (exception 7: warn and proceed); and the Dataset export the approval unlocks. The run suspends |
| a2 | action | `mark-day-approved` | guarded: `status → approved`, `approved_by`, `approved_at`. The day becomes exportable at this instant and not before |
| e1 | event | `DepartmentDayScheduled` | with the totals |

A `corrected` decision (rows removed, hours edited, a row hand-placed) re-runs `r2` over what is left,
stamps the touched rows `placed_by: human`, and re-raises — **capped** the way `MAX_REVIEW_ROUNDS`
caps AP's, so it stops asking rather than looping.

**Journal must show:** the parameters ref and **which capacity row** was chosen (department + crew
size), because that is the number a supervisor will dispute; the headcount, its source
(`count of member_of`) and its age; every candidate with its verdict and reason; the fill sequence,
where it stopped, and against which limit; the incentive before and after, with the arithmetic; the
`placed_by: human` rows it left alone; and that **nothing left the graph before `a2`** — the day is a
Dataset only once approved.

### D-6 · Gateway Actions in v1 (`server/app/gateway.py::ACTIONS`)

| Action id | target | regime | queue | why |
|---|---|---|---|---|
| `link-job-documents` (NEW) | `production-records` | decision | — | the packet. No queue: linking a document nobody disputed is nobody's work, and a Flow principal owns only what its declaration binds — `record-document-read`'s reasoning |
| `apply-job-demand` (NEW) | `production-records` | decision | `plant-supervisor`, `queue_from_task=True` | committed under the desk of the Task where a person answered, or the standing desk when nothing was in doubt |
| `apply-schedule-day` (NEW) | `production-records` | decision | `plant-supervisor`, `queue_from_task=True` | Cumulus determined this — a rule over a governed policy, not something a source reported |
| `mark-day-approved` (NEW) | `production-records` | decision | `plant-supervisor`, `queue_from_task=True` | a human approved this; the desk comes from the Task, so the queue that raised the work and the queue that commits it are the same queue by construction |
| `record-compliance-alert` (NEW) | `compliance-records` | decision | — | `record-disagreements`' reasoning exactly: no source reported it, it is the product of a rule over a governed policy, and noticing is nobody's work |

Reused unchanged: `apply-source-records` (the three sync feeds), `record-document-read` (the shipping
list fetch), `link-company-record` (a steward's Company match on `erp-jobs`).
**`server/app/estate_writes.py` is not edited.** `WRITABLE` keeps its single `sales-order-status` row,
which is inert in a Wick namespace because the sweep that drives it needs a `crm`. Note that in the
runbook so nobody reads it as a gap.

---

## E · Workspaces plan (Flutter + BFF)

### E-1 · The three workspaces

| Workspace | slug | desk gate | what it answers |
|---|---|---|---|
| **Field Documents** | `field-documents` | `field-operations` | **packets, not folders**: per scheduled job — the crew it is assigned to *now* and the crews that held it *before*, the four document types each with its version and filed date, what was superseded, completeness, the derived packet label and the convention rule behind it, and how many windows a job has been empty |
| **Compliance** | `compliance` | `hr-safety` | credentials and inspections by days-to-expiry ascending; which notice stages have been sent and when; the four exception classes with their rows; untracked assets named as untracked; the retention horizon |
| **Plant Schedule** | `plant-schedule` | `plant-supervisor` | **the system of record**: a department × day grid — status, hours against goal and absolute max with the fudge band, the incentive position with its arithmetic and an explicit out-of-the-hole indicator, the rows with basis and `placed_by`, what was deferred and why, and the **Download CSV** control that is live only on an approved day |

### E-2 · BFF endpoints (`server/app/ui_api.py`, prefix `/api/ui/v1`)
One composed call per screen, `payables_screen`'s shape: rows, stats, the governed clauses that
judged them, and the runs.

```
GET /api/ui/v1/field-documents
  → FieldDocumentsScreen {
      jobs: [FieldJobRow{ job_number, packet_label, label_rule,
                          customer, builder, plant_site, status, load_date,
                          current_crew:{team_slug,name,foreman}|null,
                          former_crews:[{team_slug,name,until_iso}],
                          unresolved_crew_id: str,
                          documents:[{kind, filename, version, filed_at, superseded}],
                          missing:[kind], complete: bool, empty_windows: int,
                          last_run_id, last_run_at_iso }],
      crews:  [CrewRow{ team_slug, name, foreman, jobs: int, complete: int }],
      stats:  { jobs, in_horizon, packets_complete, missing, unresolved_crews },
      policy_clauses: [Clause], runs: [RunSummary],
      gap_task_ids: [str] }

GET /api/ui/v1/compliance
  → ComplianceScreen {
      rows: [ComplianceRow{ kind: "credential"|"inspection", subject, subject_kind,
                            credential_type, category, expires_on, days_to_expiry,
                            status, last_notified_on, last_notice_stage,
                            stages_sent:[str], supervisor, exception, refs:[str] }],
      stats: { expiring_60, expiring_30, expiring_15, expired, untracked,
               exceptions_by_class:{…}, retention_flagged },
      policy_clauses: [Clause], runs: [RunSummary] }

GET /api/ui/v1/plant-schedule?week=YYYY-Www
  → PlantScheduleScreen {
      week, departments:[str], dates:[str],
      cells: [ScheduleDayCell{ department, date, status,
                               total_hours, goal_max_hours, abs_max_hours, fudge_hours,
                               incentive_per_hour, incentive_basis, out_of_the_hole: bool,
                               headcount, headcount_as_of, headcount_stale: bool,
                               entries:[{job_number, job_label, labor_hours, basis,
                                         sequence, row_kind, vehicle_ref, placed_by}],
                               deferred:[{job_number, reason}],
                               pending_task_id, exported_at, export_url, run_id }],
      stats: { days, pending_approval, approved, deferred, below_capacity },
      policy_clauses: [Clause], runs: [RunSummary] }
```

Plus the **Tier-1 export** (A-3), deliberately not a BFF route because a portable Dataset is a
platform capability:

```
GET /api/v1/schedule/export?week=YYYY-Www&department=<slug>   → text/csv
    200  one row per ScheduleEntry, with a header naming the derivation
         (from, recipe, produced_by run id, approved_by, approved_at)
    409  the day is not approved, and the body names the open Task
```

Each screen gets a `get_*_principal` door on `get_payables_principal`'s exact model: **own the desk,
or read past yourself** (`principal.reads_beyond_self or principal.owns_queue(<desk>)`), with the
refusal sentence naming the desk. Reads are `user`+; nothing on these screens writes, so there is no
second gate — the decision happens on the Task, through `require_queue`. The export is `user`+ and
`require_queue(plant-supervisor)`-free on purpose: handing a customer their own approved schedule is
a read.

### E-3 · Honest empty states when the namespace lacks the app
**The BFF answers availability; the console does not compile a third copy of the rule.** `Session`
gains

```
workspaces: [WorkspaceAvailability{ slug, available: bool, reason: str }]
```

derived server-side from (a) whether this deployment's cast declares anybody on the desk and
(b) whether estate discovery says the namespace runs the app the workspace rests on — reusing
`_estate_view`'s `namespace_detail.apps` and `_sync_now_refusal`'s exact wording. `home_shell.dart`
builds its `workspaces` rail list from the session rather than from three more compiled booleans, and
the section body renders the `reason` when `available` is false.

Smaller fallback if that is too much for one lane: keep `ownsWorkspace(session, queue)` and add
`session.estate_apps: [str]` — three lines of Dart and one field, at the cost of the rule existing in
Dart as well as Python.

### E-4 · Rail wiring (`admin_app/lib/screens/home_shell.dart`, `widgets/command_palette.dart`)
Indices are declared in `command_palette.dart` and copied as private consts in `home_shell.dart`.
Append rather than insert — the rail's *order* is built explicitly in the `workspaces` list and does
not follow index order:

```
const int fieldDocumentsWorkspaceIndex = 12;
const int complianceWorkspaceIndex     = 13;
const int plantScheduleWorkspaceIndex  = 14;
const String fieldOpsQueue        = 'field-operations';
const String hrSafetyQueue        = 'hr-safety';
const String plantSupervisorQueue = 'plant-supervisor';
```

Plus three `_sectionSlugs` entries (`field-documents`, `compliance`, `plant-schedule`); three
`RailChild`s; three `_lazy` guards mirroring the two that exist for expenses and payables; three
`sections` tuples; three `CatalogEntry`s in the palette. The `assert` that "a section is not in the
slot its index constant names" must be extended, not bypassed.

### E-5 · Task review screens
`screen_id → widget` in `admin_app/lib/tasks/registry.dart::taskScreenFactories`, and `TaskUi` in the
version package, so the manifest resolves them:

| screen_id | widget file | raised by | the shape it edits |
|---|---|---|---|
| `field-documents/gap-review` | `admin_app/lib/tasks/field_documents/v1/gap_review.dart` | `field-documents` k1 | the jobs in one gap class. For the missing-documents class, an acknowledge/dismiss per row. For the unresolved-crew class, a **crew picker** per row — the decision names a `Team`, and a `corrected` payload of `{crew: {job_number: team_slug}}` is what lets `job-sync` draw the edge on the next window |
| `compliance/exception-review` | `admin_app/lib/tasks/compliance/v1/exception_review.dart` | `credential-tracking` k1 | the rows of one exception class, each with an editable date; a date corrected here is a `human` claim, which outranks `hris` in `PRECEDENCE` — worth saying on the screen |
| `plant-schedule/extraction-review` | `admin_app/lib/tasks/plant_schedule/v1/extraction_review.dart` | `shipping-list-extraction` k1 | **reuses the v8 `FieldVerdict` contract** (`admin_app/test/field_verdict_contract_test.dart` stays green): the page with boxes, the disputed grid fields, the computed hours beside each |
| `plant-schedule/day-approval` | `admin_app/lib/tasks/plant_schedule/v1/day_approval.dart` | `department-scheduling` k1 | the day's rows; the total as a bar against goal and absolute max with the fudge band drawn; the incentive stated as its arithmetic; the deferred list with reasons; the headcount and its age; row removal and hour editing, which stamp `placed_by: human` |

All four sit inside `admin_app/lib/tasks/shared/review_frame.dart`. New models under
`admin_app/lib/tasks/{field_documents,compliance,plant_schedule}/models.dart`; new workspaces at
`.../workspace.dart` each, on `tasks/accounts_payable/workspace.dart`'s model — the **Flow runs** list
above the table, a run opening its journal step by step.

`admin_app/lib/screens/api_docs_data.dart` gains the four new endpoints **in the same commit** — the
README says a drifted reference is worse than none, because people code against it.

---

## F · Evals and tests

### F-1 · One `EvalSpec` per flow (`server/app/eval_specs.py`), fixtures in natural keys
Five specs, not seven — `crew-schedule-intake` and `field-folder-reconciliation` are gone.

| spec id | flow_id | milestones (anchor) | fixture | the claim it grades |
|---|---|---|---|---|
| `field-documents-core` | `field-documents` | `first_of_kind action`, `wrote graph:Job`, `terminal` | `wick/scheduled-jobs-week` | the label rules, both traps: `The Willows` → `250661 Willows` (stopword), two Evermores → `250553 Evermore Ridge` / `250774 Evermore Farms` (collision). The latest revision is linked and the previous superseded. An `In Progress` job is not selected. A job with no documents is a count until the recurrence threshold. A `crew_id` matching no `costCenter` lands in the second aggregate Task, and **only** the second |
| `field-documents-crew-move` | `field-documents` | `wrote graph:Job`, `terminal` | `wick/job-moves-crew` | after a `job-sync` delivery that reassigns a job, the old `assigned_to` edge is **withdrawn and still present** with an ended validity, the new one is live, and nothing was deleted — the claim that retired the reconciliation Flow |
| `credential-tracking` | `credential-tracking` | `last_of_kind rule`, `wrote graph:Credential`, `terminal` | `wick/compliance-clock` | 60/30/15 fire once each per cycle; `last_notice_stage` prevents a repeat; a `driverQualifications` row and a `trainingRecords` row both become `Credential`s distinguished by `category`; four exception classes are four Tasks, not N; an asset with a null due date is `inspection_tracked=no` and never alerts |
| `plant-eligibility` | `plant-eligibility-sweep` | `last_of_kind rule`, `terminal` | `wick/plant-day` | a weekend load, a `doNotFab: "Yes"`, an `outToYard: "No"`, a past load date and a `weekPosition` outside the governed set are each excluded for their own named reason; an empty eligible set raises nothing |
| `shipping-list-extraction` | `shipping-list-extraction` | `last_of_kind modelcall`, `first_of_kind task`, `wrote graph:JobTask`, `terminal` | `wick/shipping-list-pages` | a department whose grid is absent is skipped and the job is not; `m2` is skipped when nothing is in doubt; out-of-band hours become a verdict, not a second gate; a correction re-runs both checks |
| `department-scheduling` | `department-scheduling` | `first_of_kind task`, `wrote graph:ScheduleDay`, `terminal` | `wick/plant-day` | **the central claim, restated for v2: nothing leaves the graph before approval, and the day is a Dataset only once approved** — an expectation that `ScheduleDay.status` is `pending_approval` at `first_of_kind task`, that the export answers 409 there, and that `exported_at` is unset until after `mark-day-approved`. Plus: goal max stops the fill and fudge does not extend past absolute max; the incentive crossing zero stops the two incentive departments; a `placed_by: human` row survives a re-run; a dependency not met ends `deferred`; `headcount` equals the `member_of` count |

Fixtures use `deliveries` (the same push payloads the real feeds send, so the sync flows are exercised
on the way in) with `objects` only for the `Employee`/`Team` rows no Wick feed provides in the
fixture. Every key is a natural key: Wick job numbers, `@wickbuildings.com` emails, department slugs.
`EVAL_ESTATE_NAMESPACE` becomes a parameter (A-1).

### F-2 · Server tests, by lane

| file | lane | pins |
|---|---|---|
| `server/tests/test_ontology_wick.py` | O | all three `validate_*` clean; parts declare no tags or CQs; each new type claimed by exactly one collection; `ScheduleDay`/`ScheduleEntry` are `authoritative`; `PRECEDENCE` unchanged; **no existing declaration changed** (a snapshot over the naviant types) |
| `server/tests/test_casts.py` | N | `ESTATE_NAMESPACE=wick` selects the Wick cast; `""` and `naviant` select naviant; every persona's queues are declared desks; `for_email` / `queues_for_email` / `reach_for` unchanged for naviant |
| `server/tests/test_personas.py` (extend) | N | unchanged assertions still pass — the regression contract |
| `server/tests/test_sync_wick.py` | S | each of the three feeds lands its wire shape; `crew_id → Team` resolves via `costCenter` and an unmatched one is an aggregate count; a reassignment **withdraws** rather than deletes; a null `nextInspectionDueDate` lands `inspection_tracked=no`; `"Yes"`/`"No"` map to `release_state`/`hold_reason` per the asset |
| `server/tests/test_field_documents.py` | 2 | as F-1, both specs |
| `server/tests/test_credential_tracking.py` | 3 | as F-1 |
| `server/tests/test_plant_eligibility.py` | 4 | as F-1 |
| `server/tests/test_shipping_list_extraction.py` | 4 | as F-1 |
| `server/tests/test_department_scheduling.py` | 5 | as F-1, **plus** a test that edits `labor-hour-formulas` and watches the computed number move, and one that edits the incentive parameters and watches the fill stop somewhere else — the proof that `formulas.py` holds no literal |
| `server/tests/test_schedule_export.py` | 5 | the export answers 409 on a `pending_approval` day naming the Task, 200 with the derivation header on an approved one, and stores nothing |
| `server/tests/test_estate_writes.py` (extend, one assertion) | I | **`WRITABLE` still has exactly one row** — the structural proof that v1 makes no outbound Wick write. A future `send-estate-mail` or `publish-job-packet` has to change this line on purpose |
| `server/tests/test_ui_wick.py` | 6 | the three screen doors; the honest empty state when discovery says the namespace lacks the app |
| `server/tests/test_task_screens.py` (extend) | 6 | the four new `screen_id`s resolve for the shipped versions and fall back to the generic pane for an unknown one |
| `server/tests/estate_fixtures.py`, `demo_world.py` (extend) | S | a recorded Wick estate and a seeded Wick world |

Dropped from v1: `test_crew_schedule_intake.py`, `test_field_reconciliation.py`,
`test_estate_writes_create.py`.

### F-3 · The "declared STEPS == executed journal" pattern
Every new `v1` package ships it, copied from
`server/tests/test_payables_v9.py::test_the_declared_steps_are_exactly_the_steps_a_full_run_executes`
plus its `_families()` helper (which trims `_<n>` loop suffixes so a corrected run does not fail it).
The assertion is a **set** comparison over a real journal **and** an ordered list, because a map is a
sequence and not a set. A per-lane done-criterion, not an integration nicety: a flow whose declared
map does not match its journal is the exact divergence the transparency bar forbids.

### F-4 · Flutter
`cd admin_app && flutter analyze && flutter test`, clean. New:
`admin_app/test/plant_schedule_grid_test.dart` (the grid, the out-of-the-hole indicator, the fudge
band, the export control disabled until approved), `admin_app/test/compliance_rows_test.dart`
(days-to-expiry ordering, stages-sent, untracked rendering),
`admin_app/test/field_documents_packet_test.dart` (four types with versions, current vs former crew),
`admin_app/test/day_approval_contract_test.dart` (the k1 proposal shape, on
`field_verdict_contract_test`'s model).

---

## G · Deploy and runbook (user-run; the classifier blocks deploys from an agent)

1. **Estate first**, in `/workspaces/cumulus-demo-ecosystem` — `wick` in `functions/namespaces.json`,
   the four contracts in §C-5, the seed with its answer keys (the two label traps, the null-due-date
   skid steer, a job that moves crews, a document at `latestVersion` > 1), the pulse.
2. **Cumulus, into its own project** (A-1b):
   ```
   cd /workspaces/cumulus-mcp-prototype
   ./deploy/ship.sh -p <wick-cumulus-project> -e <estate-project> -n wick --knowledge
   ```
   `ship.sh` already takes `-n/--namespace` and threads it to `ESTATE_NAMESPACE` (line 301); the
   shipper needs no change. `--knowledge` runs `deploy/upsert_knowledge.py`, which is the only way
   new assets reach a corpus `seed_if_empty` will not touch.
3. **Seven new `server/seed/` documents** (Lane K writes them): `field-documents-policy.md`,
   `certification-alert-policy.md`, `plant-scheduling-parameters.md`, `labor-hour-formulas.md`,
   `truss-standards.md`, `shipping-list-extract-prompt.md`, `shipping-list-locate-prompt.md`.
   Edited for Wick: `ap-intake-policy.md`'s `- invoice folder:` line and the two approval-routing
   tables' desks.
   Note, so nobody discovers it: `server/seed/` is namespace-agnostic, so a Wick deployment also
   seeds `state-agreement-policy.md`, which governs a sweep that finds nothing without a `crm`. Leave
   it — a policy with nothing to judge is inert, and pruning the seed per namespace is a second
   selection mechanism for no gain.
4. **Reseed implications.** Do **not** wipe the estate namespace: seeds 409 unless empty, and a wipe
   strands Cumulus's strictly-after watermarks. A clean slate is the deliberate operation
   (`DELETE /admin/namespaces/wick?confirm=wick`, per-app `/seed` — the dms's direct to the function
   URL, because the Hosting rewrite caps at ~60s — plus clearing this deployment's own Firestore
   collections).
5. **Two new scheduler jobs** in `deploy/deploy.sh`, **guarded on the namespace** so a naviant
   deployment does not run windows over an empty graph and pollute the fleet page:
   | job | cron (Etc/UTC) | path |
   |---|---|---|
   | `cumulus-compliance-window` | `0 10 * * *` (daily — A-8) | `/api/v1/compliance/sweep` |
   | `cumulus-plant-window` | `0 17 * * 1-5` (11:00 CST Mon–Fri) | `/api/v1/plant/sweep` |
   **No field window:** `field-documents` wakes on `JobRecordsDelivered` and `DocumentsDelivered`, and
   the estate pull already runs every 15 minutes. A third timer would ask a question two deliveries
   had already answered.
   Both call the direct Cloud Run URL with OIDC, not through Hosting, for the reason the existing
   three do: a sweep over the whole book runs past Hosting's ~60s rewrite cap.
6. **Two new endpoints** in `server/app/data_api.py`, on `sweep_identities`' exact shape —
   `require_role(admin)` because each raises work into somebody's queue, and each emits its window
   Event through the Dispatcher so the run's `caused_by` points at a logged fact rather than at
   whoever called: `POST /api/v1/compliance/sweep`, `POST /api/v1/plant/sweep`.
   Plus the export route (E-2) in a new `server/app/schedule_api.py`.
7. **Verify, in this order:** `GET /api/ui/v1/sources` — three Wick feeds registered and active,
   `crm-book` / `psa-book` / `erp-sales` **absent rather than failed** (assert the sentence, do not
   assume it) → `POST /api/v1/estate/pull` → `GET /api/v1/graph/invariants` zero violations →
   the Field Documents workspace, checking both label traps and one job showing a former crew →
   `POST /api/v1/compliance/sweep` → `POST /api/v1/plant/sweep` → one extraction reviewed → one
   department-day approved → the export answering 409 before approval and CSV after.
8. **What stays user-run:** every deploy, the estate seed, and flow-version activation (the console
   flips `FlowDefinition.version`). **`ESTATE_WRITES_ENABLED` is not part of this runbook at all** —
   v1 makes no outbound Wick write, and the one-row `WRITABLE` assertion in F-2 is what keeps that
   true by accident-proofing rather than by discipline.

---

## H · Lanes for parallel Opus builders

Every builder: **Opus 5** (WORKPLAN decision 14). If a worktree is used, **verify it is based on
`workflows-management`** before the first edit — a known gotcha:
`git -C <worktree> merge-base --is-ancestor origin/workflows-management HEAD`.
Gate for every lane: full `pytest` green, `flutter analyze` clean, `flutter test` green.

```
Wave 0:  O ontology  ‖  N cast+desks  ‖  K assets
Wave 1:  S sync + mappings + the whole Trigger table            (needs O, N)
Wave 2:  2 field-documents  ‖  3 credential-tracking  ‖  4 plant demand   (need S, K)
Wave 3:  5 department-scheduling                                (needs 4)
Wave 4:  6 console (single writer for admin_app/ + ui_api.py)  ‖  E evals  (need 2–5)
Wave 5:  I integration, scheduler, endpoints, export route, runbook (orchestrator)
```

**Lane W is gone.** With no v1 estate write, `estate_writes.py` needs no create or delete verb. It
returns as a Wave-6 lane if and only if `send-estate-mail` (A-10) or `publish-job-packet` (A-3) is
adopted.

| Lane | Size | Files owned | Inputs from | Done when |
|---|---|---|---|---|
| **O · Ontology** | M | `server/app/ontology/tbox.py`, `ontology/names.py`, `server/app/data_collections.py`, `server/tests/test_ontology_wick.py` | — | all three `validate_*` clean; the `enterprise-ontology` export renders the seven types; **no existing declaration changed**; naviant suite byte-identical |
| **N · Cast & desks** | S | `server/app/personas.py`, NEW `server/app/casts/{__init__,naviant,wick}.py`, `server/app/desks.py`, `server/app/eval_world.py` (namespace param only), `server/tests/test_casts.py`, `test_personas.py` | — | `ESTATE_NAMESPACE=wick` selects the Wick cast; `""`/`naviant` unchanged; the three desks declared with owners |
| **K · Assets** | S | the seven `server/seed/*.md` files, plus the AP/expense asset edits (A-11) | — | every asset parses under its lane's parser (integration gate in Wave 5); the label rules, the vocabulary table and the incentive parameters are all present and commented for a customer |
| **S · Sync & mappings** | L | `server/app/estate.py`, `server/app/connectors/sync.py`, NEW `connectors/production.py`, NEW `connectors/compliance.py`, `server/app/mappings.py`, `server/app/dispatch.py` (**only the three sync Triggers — see the co-landing rule in D-0**), `server/app/tasks/{job_sync,credential_sync,equipment_sync}/`, `server/tests/test_sync_wick.py`, `estate_fixtures.py` | O (type names), N (desk slugs) | three feeds land against a recorded estate; `crew_id → Team` resolves; a reassignment withdraws; the Trigger table validates and every flow id it names is in D-0 |
| **2 · Field documents** | M | `server/app/tasks/field_documents/**`, `server/app/deps.py` (its builder only), `server/tests/test_field_documents.py` | S, K, O | both label traps pass; at most two Tasks per window; STEPS == journal |
| **3 · Credential tracking** | M | `server/app/tasks/credential_tracking/**`, `deps.py`, `server/tests/test_credential_tracking.py` | S, K, O | four exception classes are four Tasks; an untracked asset never alerts |
| **4 · Plant demand** | L | `server/app/tasks/plant_eligibility/**`, `server/app/tasks/shipping_list_extraction/**`, **`server/app/tasks/department_scheduling/formulas.py`** (owned here, imported by 5), `deps.py`, two test files | S, K, O | `formulas.py` holds no literal, proven by test; `JobTask` created here and nowhere else |
| **5 · Department scheduling** | L | `server/app/tasks/department_scheduling/**` except `formulas.py`, `deps.py`, `server/app/schedule_api.py`, `server/tests/{test_department_scheduling,test_schedule_export}.py` | **4** (formulas + `JobTask`), K | nothing leaves the graph before approval, asserted; the export 409s before and 200s after |
| **6 · Console** | L | **all of `admin_app/`**, `server/app/ui_api.py`, `server/app/task_screens.py` shared entries, `server/tests/test_ui_wick.py` | 2–5 | three workspaces, four review screens, `api_docs_data.dart` current, analyze+test clean |
| **E · Evals** | M | `server/app/eval_specs.py`, `server/app/eval_world.py` fixtures, `server/tests/test_evals.py` | 2–5 | six specs (five flows, `field-documents` has two), every fixture in natural keys, all graded green |
| **I · Integration** | M | `deploy/deploy.sh`, `server/app/data_api.py`, `server/tests/test_estate_writes.py` (the one-row assertion), `README.md`, `WORKPLAN.md` log | everything | §G executes on paper; the fleet page shows eight new flows each with an export and a spec |

**Seams, stated once so no lane re-asks:**
1. Type, field and link names → §B (O → all).
2. Desk slugs `field-operations` · `hr-safety` · `plant-supervisor` → §A-6 (N → 2,3,5,6).
3. Event names → §D-0 (S names them all up front so no lane invents a spelling; each lane
   lands **its own** Trigger row beside its own `v1` package, per the co-landing rule).
4. Landed row shapes and the `crew_id → Team` binding → §C-4, §C-2 (S → 2,4,5).
5. Gateway Action ids → §D-6 (O/S → 2,3,4,5). **No estate write door exists; do not add one.**
6. `formulas.py` signatures — pure functions `(grid: dict, params: Parameters) -> HoursResult` and
   `incentive(hours: float, params: Parameters) -> float`, every constant on `params` (4 → 5).
7. Screen ids and Task proposal shapes → §E-5 (2,3,4,5 → 6).
8. Asset ids → §B-4 (K → 2,3,4,5).

**Files with more than one interested lane, and who owns them:** `server/app/deps.py` — each flow lane
edits **only its own builder function**, and Lane I resolves any conflict; `server/app/dispatch.py` —
**shared, and the only shared file in the plan**: Lane S lands the three sync Triggers, each flow lane
appends its own adjacent row with its `v1` package, and Lane I resolves any conflict; `server/app/data_api.py` — **Lane I only**;
`server/app/schedule_api.py` — **Lane 5 only**; `admin_app/**` — **Lane 6 only**.

---

## I · Risks and open questions

| # | Risk / the SDD's own TBD | How the plan freezes it as governed knowledge rather than code |
|---|---|---|
| 1 | **AXIS REST API "under development by Wick IT"** | The estate's `erp` app *plays* AXIS; Cumulus reads `job-sync` and never speaks to AXIS. If the real AXIS never ships an API, the same Mapping binds to whatever does — a file drop, a nightly extract — with **no Flow change**. The Mapping-carries-binding / Flow-carries-logic split earning its keep. |
| 2 | **Chris Robson's min/max for Verticals, Laminates, Stack Out, Components (2.5.14)** | Those four rows in `plant-scheduling-parameters` carry the SDD's interim rule verbatim (`goal max: —`, `note: schedule whatever Loading requires`) plus an `## Open` section naming him. The Rule reads "no goal max" as "fill to the absolute max or to the demand" and journals which clause it used. His numbers are one console edit and a ledger row. |
| 3 | **Truss Fab headcount staleness (exception 7)** | `headcount` is the count of `Employee`s `member_of` the department's `Team` — so the "supervisor must update the list before the run" problem is **gone**: the HRIS is the list. What remains is feed lag, so `headcount_as_of` is the workforce feed's last landed time, and older than the governed window becomes a **warning on the approval Task**, not a failure. "Warn and proceed", the SDD's own answer, with the number and its age in front of the person approving. |
| 4 | **Training cadence: monthly-at-5am vs daily vs an Oct–Nov peak** | Resolved out of code (A-8): the scheduler fires daily; the asset carries `- run cadence: daily` and the notice stages; the `last_notice_stage` guard makes a daily window with nothing due a genuine no-op. Monthly is an asset edit plus a cron change. |
| 5 | **The incentive curve may not be linear** | `incentive_per_hour` is computed from two governed values per department (a negative start and a per-hour rate) — a reverse-engineering of a spreadsheet formula nobody has shown us. It is stated as arithmetic on the approval Task precisely so a supervisor can say "that is not how our bonus works" on day one. If a department's curve is a table, the asset gains a table and `formulas.py` gains a lookup; no schema change either way. **Open for the user: is the two-parameter model faithful enough to demo?** |
| 6 | **The packet label conventions are answer keys, not guesses** | `The Willows` and the two Evermores are seeded traps (A-9). Both rules — a stopword list and a collision rule — are asset lines and eval cases. A third trap costs one line. What would be wrong is slugifying prose in code, the mistake `routing._clean_target` already refuses to make. |
| 7 | **`crew_id → Team` rests on the HRIS `costCenter`** | A crew id the HRIS does not carry resolves to nothing, and the plan's answer is deliberate: the `Job` keeps `crew_id` and `foreman_name` as landed strings, the job appears in the second aggregate Task, and a person picks the Team on the review screen. The SUBCONTRACTOR case the SDD handled with a string match is now an identity question with a human answer and a ledger row. **[ESTATE] the seed must include at least one such crew.** |
| 8 | **Nothing reaches SharePoint, and some crews may have no Cumulus access** | The recorded deferred decision (A-3): an optional `publish-job-packet` Flow. Not v1, and the one-row `WRITABLE` test is what makes adopting it a deliberate act. **Open for the user: do field crews need the packet in SharePoint, or is the workspace plus a share link enough?** |
| 9 | **"The namespace does not run this app" is the wrong sentence for an app present but thin** | If Wick's `erp` runs `jobs` and `assets` but no vendors, `erp-vendors` pulls and lands nothing — which reads as a healthy feed with zero rows, indistinguishable from one nobody has asked yet. Accept for v1, note it, and let Lane 6 decide whether the Sources screen can say "the app runs, the resource is empty" without a server change. |
| 10 | **The 11:00 window emits `JobEligible` synchronously per job** | `ap-intake`'s named cost, inherited: the sweep is not acknowledged until every extraction it raised has run or suspended. A Wick day is ~12 jobs against `ap-intake`'s 25-invoice bound, so the arithmetic works — but an extraction is a multi-page ModelCall. Bound it in the asset (`- jobs per eligibility window: 15`) and watch the duration on the first live day; if it strains, the honest fix is two timers rather than a queue Cumulus does not have. |
| 11 | **A Trigger landed before its Flow breaks the delivery door** | Found while writing this plan, and it changed the lane cut: `Dispatcher.emit` does not guard `RunEngine.start`, so an orphan Trigger 500s the next estate pull rather than logging. The plan's answer is the co-landing rule in D-0 plus a test in each flow lane that emits the flow's own event through the Dispatcher — which fails loudly in the lane that owns the fix. Worth a platform follow-up the plan does **not** take on: should `emit` catch a missing body and record the fact with an empty `starts`? Probably yes, and it is a change to shared machinery that should not ride in on a customer namespace. |
| 12 | **Naviant regression** | Every Wave-0 lane's done-criterion is "the naviant suite is byte-identical". The three riskiest edits, named: `personas.py` (a behaviour-preserving refactor, with `test_personas.py` unchanged as the proof), `tbox.py` (additive only — a snapshot test over the naviant declarations), `dispatch.py` (additive Triggers only, and one existing event gaining a third Trigger, which `keyed_run_ids` already handles). |
| 13 | **Fleet honesty** | Eight new flows will appear on the Flows page. Each must ship its `STEPS` **and** its eval spec in the same lane, or the page earns a "n run(s) with no declarative export to audit" row — a real finding, and the one that page exists to make. |
| 14 | **Cumulus becomes the system of record for a schedule** | `ScheduleDay` is `authoritative=True`, which is the strongest claim in this plan: if the deployment goes dark, Wick has no plant schedule unless it has the exports. That is the honest consequence of not re-creating the workbook, and the mitigation is the export being a **Tier-1 REST route** rather than a console feature — a customer can pull every approved week with a script they own. **Open for the user: should the runbook include a standing weekly export, so a cold deployment is never the only copy?** |
| 15 | **The minimum coherent demo, restated** | SDD 3's spine — `plant-eligibility-sweep` → `shipping-list-extraction` → `department-scheduling` — exercises every plane in one line: a multi-page ModelCall, a per-case gate, pure Rules over governed numbers, a windowed aggregate, an approval control point, a graph that becomes the system of record, and a Dataset the customer walks away with. It is Lanes O · N · K · S · 4 · 5 plus a partial 6, and it is **materially smaller in v2** than in v1 because nothing has to build a write door. `field-documents` is the cheapest second act (one windowed Flow, no ModelCall, two aggregate Tasks) and `credential-tracking` the third. **Recommend deciding the staging before any lane starts.** |
