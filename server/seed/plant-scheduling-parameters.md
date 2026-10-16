---
id: plant-scheduling-parameters
path: operations/policies/plant-scheduling
title: Plant Scheduling Parameters
authority_level: policy
owner: plant-operations
tags: plant, scheduling, capacity, eligibility, departments, incentive
related: labor-hour-formulas, truss-standards, field-documents-policy
status: published
---
# Plant Scheduling Parameters

Which jobs are ready to be scheduled, which department does what and in what
order, how many hours a department can take in a day, and how far ahead of a
load date its work is placed.

Two flows read this page as governed values — the eligibility sweep that decides
which jobs are in play, and the department scheduling that fills a day. Edits
here change what both do. No code changes, every edit is logged, and every run
cites the revision it read, so a supervisor disputing a number is disputing a
line on a page with a date on it rather than a decision nobody can find.

The companion pages are the **Labor Hour Formulas** (what a job costs a
department) and the **Truss Standards** (the span and member lookup). Nothing
below computes an hour; everything below decides what to do with the hours those
two produce.

## Eligibility

The first pass, run once per plant window, over every job the job system holds.

- load date on or after: today
- eligible week positions: 1, 2, 3
- release required: out to yard
- hold excludes: do not fab
- business days only: yes
- sort candidates by: load date ascending
- jobs per eligibility window: 15
- empty eligible set: informational

A job must clear **all** of the clauses above. A job that fails one is excluded
for that named reason and the reason is recorded on the job, so "why was 260118
not scheduled" has an answer that is a clause rather than an opinion.

Weekend and holiday loads are excluded **even when the plant operations screen
shows them**. The screen is a list of load dates and does not know the plant is
shut; that is what the calendar below is for.

Nothing eligible is not a fault. An eligible set of zero is informational — it
means a slow week, and the original design says so: *"if not enough eligible work
to fill a day, do NOT generate a schedule — notify the supervisor."* The only
thing that raises an alarm is finding **no jobs at all**, which means the feed
stopped rather than that the plant is quiet.

The window bound is a cost control, not a filter. Each eligible job is answered
by a full extraction — a multi-page model read — before the window that raised it
is acknowledged, so fifteen is what one window will take on. What it does not
take on is still eligible, is reported as deferred, and is picked up by the next
window. Raise it once the first live days show the duration; the honest fix if
fifteen strains is a second window, not a bigger number.

## Business days and holidays

- business days: Monday, Tuesday, Wednesday, Thursday, Friday
- holidays: 2026-01-01, 2026-04-03, 2026-05-25, 2026-07-03, 2026-09-07, 2026-11-26, 2026-11-27, 2026-12-24, 2026-12-25, 2026-12-31

The plant calendar. Every "next business day", every lead time and every
farthest-out window on this page counts business days using exactly these two
lines. The original design names no calendar at all, so this one is **frozen
here** — a ten-day Wisconsin manufacturing year with Good Friday, the observed
Fourth (the fourth falls on a Saturday in 2026), the Thanksgiving Friday and the
two Christmas–New Year closures. It is a plant's own list and the plant should
correct it; a wrong holiday shows up as a schedule written for a day nobody was
in the building.

## Departments

The ten plant departments, in the three spellings that exist. The left column is
what the workforce system calls the department, the middle is the job system's
own "Done" checkbox, and the right is the slug this platform uses everywhere —
on a job's demand rows, on a schedule day, and in the upstream-done flags.

Neither system's spelling is written into any code. This table is the only place
the three names are tied together, and a plant that renames a department renames
it here.

| HRIS department name | ERP Done field | Cumulus slug |
|---|---|---|
| Truss Cutting | trussCutDone | truss-cut |
| Truss Fabrication | trussFabDone | truss-fab |
| Steel Fabrication | steelFabDone | steel-fab |
| Trim Fabrication | trimFabDone | trim-fab |
| Hardware & Trim Packaging | hardwarePackDone | hardware-trim-pack |
| Verticals | verticalsDone | verticals |
| Components | componentsDone | components |
| Laminates | laminatesDone | laminates |
| Stack Out | stackOutDone | stack-out |
| Loading & Yard | loadingDone | loading |

## Source vocabulary

The job system says "Yes" and "No" in picklists and calls a job's position
"Week 1". This table is where those words become facts this platform can reason
over. A connector renames and never judges; the judging is this table plus the
eligibility clauses above.

| Source field | Source value | Fact | Value |
|---|---|---|---|
| outToYard | Yes | release_state | released |
| outToYard | No | release_state | not released |
| doNotFab | Yes | hold_reason | DNF |
| doNotFab | No | hold_reason | (none) |
| weekPosition | Week 1 | week_position | 1 |
| weekPosition | Week 2 | week_position | 2 |
| weekPosition | Week 3 | week_position | 3 |
| weekPosition | anything else, or absent | week_position | 0 |

**Out to yard** is the plant's word for released to be built. **DNF** is
"do not fab" — a hold somebody set deliberately, and the reason is kept as the
hold reason so a held job reads as held rather than as missing. A week position
of 0 is not an error; it is a job the screen puts beyond Week 3, and the
eligibility clause above simply does not include it.

## Source documents

- cut sheet folder root: `PlantCutSheets/<plant site>/<job number>`
- plant sites: Mazo, Fork West
- shipping list filename pattern: `<job number> Shipping List*.pdf`
- column cut sheet filename pattern: `<job number> Column Cut Sheet*.pdf`
- missing shipping list: skip the job and count it

A job with no shipping list is the original design's first business exception —
*"missing shipping list file → skip job, notify owner with job number"* — and it
is a **count on the run**, not a task. There is nothing for a supervisor to
decide about a document that has not been filed yet; the office files it and the
next window finds it.

## Page families

The shipping list is one document with seven page families, and each one feeds
particular departments. **A department whose page family is absent is skipped —
that department only, never the whole job.** A building with no trusses is a
building with no trusses.

| Page | What it carries | Departments it feeds |
|---|---|---|
| A1 | truss drawings: span, ply, top / bottom / web member counts | truss-cut, truss-fab |
| B | components, laminates, verticals and truss grids; the handwritten "Trusses w/Girts Plated In" note | components, laminates, verticals, truss-fab |
| C | steel fab: colour grids, linear footage, the CondenStop flag, crib notes | steel-fab |
| D | hardware line items | hardware-trim-pack |
| E | trim cut / fab and trim pack: items, lengths, total weight, FJ and JJ flags | trim-fab, hardware-trim-pack |
| F | slide door hardware: track end caps quantity, pull plate | hardware-trim-pack |
| LL | loader list: pre-calculated board hours, lumber and plywood grids | stack-out, loading |

Hardware & Trim Packaging is fed by three page families and Truss Fabrication by
two, which is why "absent" is judged per page family rather than per department:
a ticket carrying a hardware grid on page D but no trim on page E still has
hardware work to do, and the original design says exactly that.

## Field verdicts

What a supervisor is asked about, rather than being handed a seven-page PDF.
The extraction is checked against itself before anybody sees it, and every
disagreement names the field it is about so the review screen can box three
numbers on a page instead of thirty.

- minimum extraction confidence: 0.75
- minimum field confidence: 0.70
- grid line total tolerance: 1.0%
- linear footage tolerance: 1.0%
- required field rule: a field a governed formula reads is required when its page family is present

Extraction confidence is about the document as a whole; field confidence is per
field, and they answer different questions — a page can read cleanly overall and
still have one smudged quantity. A field below the field floor goes in front of
the supervisor with a box around it on the page.

The two tolerances are the arithmetic checks: a grid whose line quantities do not
add up to its own printed total was misread somewhere, and the colour grid's
linear feet must agree with the printed total linear footage. Both are tight on
purpose. Widening them does not make a misread grid right; it decides how much
disagreement is worth a person's attention.

## Expected hours

The out-of-range guard. A computed figure outside its department's band is put in
front of a supervisor **before it is written**, which is the original design's
fourth business exception: *"labor calculation out of expected range (e.g. >50 h
single job-dept) → supervisor review before writing."*

- default expected hours band: 0.1 to 50 hours

| Department | Low (h) | High (h) |
|---|---|---|
| truss-cut | 0.1 | 30 |
| truss-fab | 0.1 | 80 |
| steel-fab | 0.1 | 25 |
| trim-fab | 0.1 | 30 |
| hardware-trim-pack | 0.1 | 50 |
| verticals | 0.1 | 50 |
| components | 0.1 | 15 |
| laminates | 0.1 | 30 |
| stack-out | 0.1 | 15 |
| loading | 0.1 | 8 |

The design gives one example and no table, so the highs above are **derived
rather than quoted**: each is that department's own goal max from the capacity
table below — one job may not quietly consume a whole day of a department — and
where the department has no goal max, it is 50 hours, the design's own figure.
The low is 0.1 everywhere: a computed zero is what an unread grid looks like, not
a small job.

Out of band is a **field verdict**, not a second gate. It arrives in the same
review as the low-confidence fields, beside the grid it was computed from, so a
supervisor confirms the reading and the hours in one sitting.

## Capacity

How many hours a department will take in one day. The row is chosen by department
plus the condition in the second column — crew size for the saw and the truss
tables, CondenStop for steel, how many specials for trim, spliced or solid for
laminates. **Which row was chosen is recorded on every schedule day**, because it
is the first number a supervisor will dispute.

- fudge applies to: goal max and absolute max
- exceeding the absolute max within the fudge: accepted, not an exception
- minimum out of the hole: the incentive crossing zero
- below capacity: reported, never forced
- supervisor-placed rows: never overwritten

| Department | Capacity row | Min (h) | Goal max (h) | Absolute max (h) | Fudge (h) | Note |
|---|---|---|---|---|---|---|
| truss-cut | saw-1-man | out of the hole | 17 | 20 | 3 | job mix and precuts move this |
| truss-cut | saw-2-man | out of the hole | 30 | 35 | 5 | |
| truss-fab | table-3-man | out of the hole | — | 35 | 5 | |
| truss-fab | table-4-man | out of the hole | — | 45 | 5 | |
| truss-fab | table-5-plus | out of the hole | 80 | 100 | 0 | over 100 is a heavy day |
| steel-fab | no-condenstop | 12 | 20 | 22 | 1 | |
| steel-fab | condenstop | 12 | 25 | 26 | 1 | |
| trim-fab | few-specials | out of the hole | 25 | 28 | 1 | |
| trim-fab | many-specials | out of the hole | 30 | 32 | 1 | |
| hardware-trim-pack | any | out of the hole | — | none | 1 | as far out as needed |
| verticals | any | out of the hole | — | none | 1 | whatever Loading requires |
| laminates | spliced-or-over-2x6 | out of the hole | 25 | none | 1 | |
| laminates | solid-2x6 | out of the hole | 30 | none | 1 | |
| stack-out | any | out of the hole | 15 | none | 1 | |
| components | any | out of the hole | 15 | none | 1 | |
| loading | any | out of the hole | — | none | 1 | the truck decides |

**How to read the three limits.** The fill adds jobs in load-date order until the
**goal max**; the **fudge** is the latitude either side of it; the **absolute
max** is the hard stop, and going past the absolute max by no more than the fudge
is explicitly *not* an exception — the design's own words. A row with `—` for a
goal max has no target, so the fill runs to the absolute max or to the demand,
whichever comes first, and the run journals which of the two clauses it used. A
row with `none` for an absolute max has no ceiling at all.

**"Out of the hole"** is a minimum stated as money rather than hours: the day's
incentive figure starting negative and rising with hours, and the floor being the
point where it crosses zero. It is a real floor and not a target, and a day below
it is **reported to the supervisor, never forced** — who assigns keep-home, scrap
or indirect work is a person's decision and out of scope here.

**Two numbers were frozen, and both were ranges.** The design writes the fudge as
`+1..+3` for the one-man saw and `+1..+5` for the two-man, and writes several
goal maxes as bands (`20–22` for steel, `80–100` for a five-man truss table). A
parameter is one number, so the fudge is frozen at the **top** of the range the
design allows and a banded goal max at the **bottom** of its band — which is what
a band means in practice: aim for the low figure, and the latitude above it is
already the fudge. Lower a fudge here and the schedule accepts less.

**Loading has no row in the source design.** It is the only department that does
not, because a truck is the constraint rather than a number of hours. Its row is
frozen with no goal and no ceiling, filling to the demand.

## Headcount

- headcount source: count of active employees member_of the department team
- headcount freshness window: 24 hours
- stale headcount: warn and proceed

The design had a supervisor typing today's crew into a spreadsheet and a bot
counting the names, with a business exception for the day nobody remembered:
*"stale Truss Fab headcount → warn, proceed, supervisor to confirm."* The
workforce system is the crew list here, so the counting problem is gone and only
feed lag remains. A headcount older than the window above is a **warning printed
on the approval task, with the number and its age**, and the day is still
scheduled — the design's own answer, in front of the person approving it.

The ladder below is what turns a headcount into a capacity row for the truss
tables:

| Employees present | Capacity row | Capacity (h) |
|---|---|---|
| 1 to 3 | table-3-man | 40 |
| 4 | table-4-man | 50 |
| 5 or more | table-5-plus | 100 |

The capacities in the third column are **the absolute max plus the fudge** from
the capacity table (35 + 5, 45 + 5, 100 + 0), which is why the two tables do not
disagree even though they look like they might: the ladder states the most a
crew of that size will ever be given, and the capacity table states where the
fill aims and where it stops.

## Lead times and farthest out

Two different distances, both counted in business days back from the job's load
date. **Lead time** is where the work is normally placed. **Farthest out** is the
earliest it may be placed at all; `none` means there is no earliest.

| Department | Condition | Lead time (days) | Farthest out (days) | Note |
|---|---|---|---|---|
| truss-cut | any | 21 | 21 | measured from the first truss load date |
| truss-fab | any | 21 | 21 | measured from the first truss load date |
| steel-fab | any | 14 | none | as far out as needed |
| trim-fab | any | 14 | none | as far out as needed |
| hardware-trim-pack | any | 14 | none | one business day after trim-fab for the same job |
| verticals | any | 7 | 7 | |
| laminates | treated | 7 | 21 | treated stock is handled two to three weeks out |
| laminates | non-treated | 2 | 2 | |
| stack-out | any | 2 | 2 | the two-business-day window on the loader list |
| components | any | 7 | 21 | |
| loading | any | 1 | 1 | always the next business day |

**Truss cutting and fabrication are measured differently from everything else**,
and this is the one clause worth reading twice. Their three-week window runs back
from the **first truss load date** of the job, not from the job's own load date:
*"no earlier than first truss load − 21 days; e.g. loads 6/22 → earliest 6/1."*
Trusses typically ship in the second load, so the two dates are not the same, and
using the job's load date would let the saw run a month early.

Two ranges were frozen. Treated laminates are *"2–3 weeks"* and components are
*"2–3 week lead"*; both farthest-out figures are frozen at **21 days**, the outer
end, because a farthest-out window is a permission and the design's outer figure
is what it permits. The lead times stay at the inner figure, which is where the
work is actually placed.

## Sequencing

The fixed daily order, and what each department waits for. A department is
considered in this order every window; an unmet dependency is not a failure.

- unmet upstream dependency: deferred to the next window
- do not schedule when: the load date is past, the load is a weekend or holiday, the department's page family is absent, the job is held (DNF), or an upstream dependency is unmet

| Order | Department | Upstream done required | Offset | Note |
|---|---|---|---|---|
| 1 | truss-cut | — | — | the bottleneck department; prioritized, and may pull further out |
| 2 | truss-fab | truss-cut | — | skipped this pass while truss-cut is not done; mirrors truss-cut once unblocked |
| 3 | steel-fab | — | — | independent; branches on CondenStop |
| 4 | trim-fab | — | one business day before hardware-trim-pack | |
| 5 | hardware-trim-pack | trim-fab | one business day after trim-fab | independent when the ticket carries a hardware grid and no trim grid |
| 6 | verticals | — | — | driven by what Loading needs; no fixed cap |
| 7 | components | — | — | same |
| 8 | laminates | truss-cut for precut items | — | a precut item must be cut before its laminates date; it rolls to the next business day if the saw is full, but must finish first |
| 9 | stack-out | — | — | inside the two-business-day window, using the loader list's board hours |
| 10 | loading | — | one business day ahead | reads shipment type with combined-with to find consolidated trucks |

The dependency is read **live** off the job's own Done flags at the moment the
department is considered, not off a snapshot taken when the job became eligible.
A job whose truss cutting finished this morning is fabricable this afternoon.

## Incentive

- incentive model: start + rate × hours
- incentive decides the fill: trim-fab, hardware-trim-pack

Each department's day carries a bonus-per-hour figure that begins negative and
rises as hours are added; the plant's own phrase for filling a day is getting
*"out of the hole"*, and the goal is to add hours until the figure crosses zero.

In the spreadsheets this was a formula in a cell. Here it is **two governed
numbers per department** — a starting figure and a per-hour rate — so the figure
can be stated as its own arithmetic on the approval task instead of being a
cell nobody can audit. Trim Fab at twenty-nine hours reads
`−$180.00 + $6.20/h × 29.0 h = −$0.20` — still in the hole, by twenty cents,
which is the kind of thing a supervisor should be able to see and argue with
rather than take on faith.

| Department | Starting figure (USD) | Rate (USD per hour) | Crosses zero at (h) |
|---|---|---|---|
| truss-cut | -96.00 | 6.00 | 16.0 |
| truss-fab | -168.00 | 6.00 | 28.0 |
| steel-fab | -72.00 | 6.00 | 12.0 |
| trim-fab | -180.00 | 6.20 | 29.03 |
| hardware-trim-pack | -150.00 | 6.00 | 25.0 |
| verticals | -84.00 | 6.00 | 14.0 |
| components | -84.00 | 6.00 | 14.0 |
| laminates | -144.00 | 6.00 | 24.0 |
| stack-out | -84.00 | 6.00 | 14.0 |
| loading | -60.00 | 6.00 | 10.0 |

The fourth column is not a parameter — it is `−start ÷ rate`, printed so the
table can be read at a glance and checked. Trim Fab is the one row where that
division does not land on a whole hour, which is why its crossing is written to
two places: the rate is the governed number, and the crossing follows from it.

**Trim Fab cannot reach its crossing on a light day, and that is a question for
the plant rather than something this platform should quietly round away.** The
crossing is 29.03 hours; the *few specials* capacity row allows 28 plus a fudge
hour, so on a day of ordinary trim the schedule stops at 29 with the bonus still
a few cents under water. On a *many specials* day (32 plus a fudge hour) it is
comfortably reachable. Either the starting figures below are not Wick's real
ones — they were reconstructed from the shape of a spreadsheet formula, not
copied from it — or a light trim day genuinely never pays the bonus, which the
supervisors would already know. Until somebody says which, the schedule fills to
the ceiling and the approval task reports the shortfall. Steel Fab's crossing lands on 12
hours, which is the same figure the capacity table gives as its numeric minimum;
that agreement is intentional and is a useful sanity check on the pair.

**Two things about this table are assumptions, and both should be confirmed by a
supervisor before anyone relies on them.**

1. **The numbers are invented.** The source design describes the cell's behaviour
   — *"starts negative and rises as hours are added"* — and quotes no figures at
   all. Every start and rate above is a plausible reconstruction chosen so the
   crossing lands near the department's own goal max. They are stated in the open
   precisely so the first supervisor who sees one can say it is wrong.
2. **The model is linear.** `start + rate × hours` is the simplest curve that
   behaves as described. If a department's real bonus steps, or flattens, or
   depends on the mix rather than the total, that department's row becomes a
   small table on this page and the arithmetic gains a lookup. Nothing else
   changes — no schema, no deploy.

**Two departments fill by this figure rather than by a ceiling**, per the line
above and the design's own reading: Trim Fabrication and Hardware & Trim
Packaging pull urgent work until the figure crosses zero, and whether to add
stock trim beyond that stays the supervisor's call. Every other department fills
to its goal max and shows the figure for information.

## Row shaping

A department-day is a list of rows, and a job does not always contribute exactly
one.

- row kinds: standard, nailing, girts, consolidated
- nailing row when: any truss on the job is multi-ply
- girts row when: the shipping list B page carries the note "Trusses w/Girts Plated In"
- loading consolidates: tickets sharing a truck become one row
- loading row key: the four-digit RT number with a letter suffix
- loading trailer number: left blank for the supervisor

Every row carries one of the four kinds above. `standard` is a job's own row in
its department; the other three are the shapes below. The nailing and girts rows
belong to truss cutting and fabrication and carry their own hours from the truss
standards lookup. The girts condition is a
**handwritten note** on a printed page, which is why it is a field the extraction
is asked about explicitly and a field a supervisor is likely to be asked to
confirm.

Loading is the opposite shape: two tickets on one truck are one loading job, so
they become one row keyed on the truck rather than two rows keyed on the tickets.
The trailer number is deliberately left blank — which trailer goes out is the
yard's call on the day, and writing a guess into that column would be this system
making an operational decision.

## Job label abbreviations

Column B of a department's day is a short, legible name for the job. The base
label is the packet label from the **Field Documents Policy** — the job number
plus the customer's first keyword, with the same stopword and collision rules, so
a schedule row and a crew's packet call the job the same thing. Each department
appends its own suffix:

| Department | Suffix | Extra |
|---|---|---|
| truss-cut | TC | precut rows: `TC-PC` |
| truss-fab | TF | nailing rows: `TF-NAIL`; girts rows: `TF-GIRTS` |
| steel-fab | SF | CondenStop jobs: `SF-CS` |
| trim-fab | TRIM | |
| hardware-trim-pack | HW | |
| verticals | VERT | |
| components | COMP | |
| laminates | LAM | precut items: `LAM-PC` |
| stack-out | SO | |
| loading | LOAD | consolidated rows use the RT number in place of the job number |

- job label max length: 28 characters

The source design points at a process document for these conventions and that
document is not in our hands, so the suffixes above are **frozen**: they are
chosen only so that every row is legible and no two departments' rows can be
confused when a supervisor is looking at a printed day. A plant that abbreviates
differently edits this table.

## Approval routing

Which desk a plant review lands on — both the extraction review, when a reading
is in doubt, and the daily approval of a department's schedule.

| Total hours | Reviewed at |
|---|---|
| Over 0 | plant-supervisor |

- Fallback desk: plant-supervisor

One band and a standing desk. The daily approval is a **permanent control
point** rather than a temporary safeguard: *"a manual approval is required
before any bot-generated daily schedule is final."* Nothing about a department's
day leaves this platform until somebody at that desk approves it, and the export
that a plant actually works from refuses to answer until they have.

## Open

- **Trim Fab's incentive crossing sits above its own few-specials ceiling.**
  29.03 hours against 28 + 1. Needs either the real starting figure and rate, or
  confirmation that a light trim day does not pay. See `## Incentive`.

**Minimum and maximum hours for four departments are still owed by Chris
Robson** — Verticals, Laminates, Stack Out and Components. The source design
names him and records the gap as an open decision, together with the rule to use
until he answers, and that rule is carried verbatim:

- goal max: —
- note: *schedule whatever Loading requires*

That is the Verticals row in the capacity table exactly as it stands, and it is
how the fill reads it: a department whose goal max is `—` has no target, so it
fills to its absolute max, or to the demand where it has no absolute max either,
and the run journals **which of those two clauses it used** so the answer is
never mistaken for a limit somebody actually set.

Laminates (25 spliced, 30 solid), Stack Out (15) and Components (15) do carry
goal max figures, because the source design's own table quotes them. What is
missing for all four departments is a confirmed **minimum** — every one of them
reads "out of the hole", which is the incentive figure and therefore only as
good as the invented incentive parameters above — and Chris Robson's word that
the quoted goal maxes are right.

When he answers, this is one edit to the capacity table and one ledger row. No
deploy, no code review, and every run from that moment cites the new revision.

**Also open, and both are noted rather than decided here:** whether the incentive
model above is linear (see the two assumptions in that section), and whether the
holiday calendar is the plant's real one.
