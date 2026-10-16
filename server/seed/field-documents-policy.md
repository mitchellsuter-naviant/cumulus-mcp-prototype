---
id: field-documents-policy
path: operations/policies/field-documents
title: Field Documents Policy
authority_level: policy
owner: field-operations
tags: field, crews, documents, jobs, packets, dms
status: published
---
# Field Documents Policy

Which documents a field crew is entitled to before it drives to a job, where
those documents are filed, and what the job packet is called.

The field-documents flow reads this page as governed values. Edits here change
what the flow does — no code changes, and every edit is logged with a revision
number the run then cites. A superintendent who wants crews to see one more
document type, or a horizon of three weeks instead of two, edits this page.

## Document types

The four documents that make up a job packet, in the order a crew reads them.
The order is the order they are listed on the packet and in the review screen;
it is not a priority for fetching, because all four are looked for every time.

| Order | Document type | Filename pattern |
|---|---|---|
| 1 | Shipping List | `<job number> Shipping List*.pdf` |
| 2 | Engineering Plans | `<job number> Engineering Plans*.pdf` |
| 3 | Job Site Map | `<job number> Job Site Map*.pdf` |
| 4 | Crew Hours | `<job number> Crew Hours*.pdf` |

A document whose name matches none of the four patterns is left where it is and
counted as unclassified. It is not guessed at: a file in a job folder that
nobody named to the convention is a filing question for the office, and naming
it here is how the answer gets applied.

## Where job documents are filed

- document folder root: Job Documents
- job folder name: `<job number>`
- latest revision decided by: latestVersion
- revision tiebreak: createdAt

A folder is named by its full path from the root of the document tree, the same
way the AP intake policy names its invoice folder. Everything a crew needs sits
under `Job Documents/<job number>`; nothing else about the file is consulted.

**"The topmost one is the most recent" is not a rule this system can keep.**
The portal panel the crews used showed duplicates newest-first, so the top of
the list was the answer. A document system sorted by name says nothing about
age, so the rule here is a comparison instead of a position: of the documents
matching one type's pattern, the one with the **highest `latestVersion`** is
the current one, and where two share a version the more recently created wins.
The previous revision is marked superseded and its link withdrawn — it is not
deleted, because a crew that already printed it should be able to find out what
they are holding.

- Three SharePoint folders the retired bot maintained — `Construction Details`,
  `JobSite Pictures` and `Archive` — are **not created, moved, copied or
  deleted** by anything here. A packet is the answer to a question about the
  graph, and a crew's assignment is an edge with a validity, so "no longer this
  crew's job" is a withdrawn edge rather than a folder somebody has to move.
  That is a deliberate removal, recorded here so a reader comparing this page
  to the original design can see the steps were dropped on purpose.

## Which jobs are in scope

- job status in scope: Scheduled
- scheduling horizon: 14 days
- jobs per document window: 40

Two weeks, the original design's own horizon, counted forward from the day the
window runs against the job's load date. A job whose status has moved off
`Scheduled` — to `In Progress`, `Complete` or `Cancelled` — is not selected at
all, which is how the rule *"once the job moves to IN PROGRESS, stop checking
for new versions"* is kept: there is nothing to switch off.

The window bound is what stops the first sync of a long-established filing tree
from answering hundreds of jobs in one request. Nothing is dropped — a job the
window did not take on is still in scope, is reported as deferred, and the next
window finds it. Raise it where deliveries are rare and bulky; lower it where
they are frequent.

## Packet label

- label pattern: `<job number> <first keyword of the customer's company name>`
- label keyword source: the customer's company name
- label keyword fallback: the customer's own name
- label stopwords: the, a, an, of
- on label collision: append the next keyword
- label case: as printed

The label is what a crew, a foreman and a scheduler all call the same job, so
it has to be derived the same way every time and be legible when it is wrong.

**Stopwords.** A leading article carries no information and makes two customers
look alike, so the words above are skipped when the first keyword is chosen.
`The Willows Equine LLC` on job 250661 is labelled **`250661 Willows`**, not
`250661 The`.

**Collisions.** Two customers can begin with the same keyword. When the label a
job would get is already held by a different customer inside the same window,
the next keyword of that customer's name is appended, and the run records that
it did. `Evermore Ridge Estates` on 250553 becomes **`250553 Evermore Ridge`**
and `Evermore Farms LLC` on 250774 becomes **`250774 Evermore Farms`**.

Both rules are governed values, not code, which is what makes a third
convention — a customer whose useful keyword is the second one, a site name
instead of a company name — one line on this page rather than a deploy.

## Missing documents

- missing document windows before a task: 3
- partial packet: informational

The original design's answer to a job with nothing in its document panel was to
*"log as information and continue; notify if recurring"*, and that is exactly
what the threshold above is. A job with no documents is counted the first time
and the second time; on the third consecutive window with none of the four, it
joins the aggregate task for the field-operations desk.

A job that is missing **some** of the four is informational at every window and
never raises a task on its own. A packet with plans and a site map but no crew
hours sheet is a normal Tuesday; a job the office has never filed anything for
is a filing failure, and only the second one is worth somebody's morning.

Lower the threshold to be told sooner. Raising it past a job's own horizon means
a crew can reach the site before anybody is asked, which is the failure this
number exists to prevent.

## Unresolved crews

A job carries a crew id and a foreman's name as the job system spells them. When
that crew id matches no crew in the workforce system, the job is **not** dropped
and no document question is asked about it: it goes into a single aggregate task
asking who this crew is. One identity question, once per window, rather than one
document question per job nobody can deliver to.

Jobs erected by a subcontractor are the ordinary case of this, and the answer is
a person's, not a pattern's: the retired design matched the word
`SUBCONTRACTOR` in a report header, which worked until a second subcontractor
was hired.

## Approval routing

Which desk the aggregate document and identity tasks land on. Governed here for
the same reason the horizon is: who chases a missing shipping list is an
operations decision, and moving it should be an edit to this page.

| Missing types | Reviewed at |
|---|---|
| Over 0 | field-operations |

- Fallback desk: field-operations

Both tasks this flow raises are aggregates — one about documents, one about
crews — so there is one band and it names the standing desk. A resolver such as
`submitter` would answer nothing useful here: no person handed these jobs over,
they arrived from a job system on a schedule.
