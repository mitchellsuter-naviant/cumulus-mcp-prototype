---
id: certification-alert-policy
path: safety/policies/certification-alerts
title: Certification Alert Policy
authority_level: policy
owner: hr-safety
tags: certifications, training, dot, inspections, compliance, retention
status: published
---
# Certification Alert Policy

When a credential or an inspection is close enough to expiring that somebody
should be told, who is asked when the record itself is wrong, and how long a
record is kept.

The credential-tracking flow reads this page as governed values. Edits here
change what the flow does — no code changes, and every edit is logged. **This
page is also what decides whether any work happens at all**: the window opens
on a timer, but the cadence, the stages and the horizon below are what turn an
open window into a notice.

## Run cadence

- run cadence: daily

The original design says two different things about this, and the difference
matters: §5.1 schedules the bot *"monthly on the 1st at 5 am"*, while the
architecture section describes a *"daily scheduled trigger"* with
*"increased frequency October–November"*. Rather than pick one in code, the
cadence is frozen here, in the open, at **daily**.

Daily is the safe reading. A stage is sent once per cycle — the record carries
the last stage it was notified at, so a window that finds nothing due writes
nothing, sends nothing and raises nothing. A monthly run, by contrast, can miss
a 30-day stage entirely: a card expiring on the 20th passes its 30-day mark on
the 21st of the month before, eleven days after the last run. Daily costs an
empty query a day and cannot skip a stage.

Making this monthly is an edit to the line above plus one change to the
schedule. Both have to happen, and because the flow reads the line rather than
inferring it, the document and the timer can never silently disagree about what
the service level is.

## Notice stages

- notice stages: 60, 30, 15
- notice SLA floor: 30 days

| Stage | Days before expiry | What it is for |
|---|---|---|
| first | 60 | book the class, the appointment or the inspection |
| second | 30 | the service-level floor: nobody is told later than this |
| third | 15 | the last call before an employee or a machine is out of service |

The floor is a promise, not a stage: *"notify supervisors at least 30 days
before expiry"*. It is stated separately because it is what a stage list can be
checked against — a stage list whose widest entry is under 30 would break the
promise, and the run says so rather than quietly honouring the edit.

A stage already sent this cycle is not sent again. A recertification resets the
cycle, so the same record can pass 60 · 30 · 15 again next year.

## What is tracked

- categories tracked: certification, training, licence, medical, clearance
- untracked when: the source holds no next-due date

| Category | Source | What runs out |
|---|---|---|
| certification | training records | an equipment certification |
| training | training records | a course with a recertification date |
| licence | driver qualifications | a commercial driving licence |
| medical | driver qualifications | a DOT medical card |
| clearance | driver qualifications | the PSP and clearinghouse checks |

A category not on the list above is carried in the graph and never alerted on.
Adding one is a line here.

## Inspections

Annual vehicle, trailer and lift inspections run on the same stages as the
credentials above, against the equipment register's next-due date rather than a
person's expiry date. External vendors do the inspecting; the register records
when it is due.

**An asset the source holds no next-due date for is not tracked and never
alerts.** This is the original design's own exclusion — *"skid steers excluded —
no tracked dates"* — expressed as a property of the data instead of a list of
machine types. A skid steer with no due date is untracked because nobody sets
one, and a skid steer somebody starts inspecting annually becomes tracked the
day a due date lands, with no edit here at all. Those exclusions are **counted**
in every run: an equipment register that quietly stopped carrying due dates
would show up as a rising untracked count rather than as a silent absence of
alerts.

## Retention

- retention horizon: 7 years
- past the horizon: reported for manual review

Records for people who have left are kept for seven years and then **flagged**.
The customer's own statement of the rule, kept verbatim because it is the part
that is easy to get wrong: *"flag records older than 7 years for deletion — no
automatic deletion, reported for manual review."*

Nothing here deletes anything. A retained record past the horizon becomes one
of the four exception classes below and lands on a desk, because a deletion is
a decision and a decision needs somebody's name on it.

## Exceptions

Four classes, and they are aggregated: one task per class per window carrying
its rows, never one task per row. A window that finds eleven unreadable dates
asks one question eleven times over, which is one question.

| Class | Raised when |
|---|---|
| missing-or-unreadable-date | the record carries no expiry or next-due date, or one that cannot be read as a date |
| unresolved-holder | the record names a holder no employee or asset can be resolved to |
| terminated-holder-still-active | the holder has left but the record is still on the active roster |
| retained-past-horizon | a retained record is older than the retention horizon above |

The colour of a cell is never evidence. The trackers this replaces were
colour-coded, and the original design was explicit that a bot must
*"never rely on colour coding"* — the date is read directly, and a date that
cannot be read is the first exception class rather than a guess.

## Approval routing

Which desk the four aggregate exception tasks land on.

| Days remaining | Reviewed at |
|---|---|
| Over 0 | hr-safety |

- Fallback desk: hr-safety

One band, because these tasks are aggregates about records rather than requests
from people: there is no submitter to route back to and no amount to band on.
Splitting DOT exceptions to a different desk from training exceptions is a
second row here.

## Out of scope, because the customer scoped it out

Three things the original design named as staying manual. They stay manual here
too, and they are listed rather than left unsaid so that nobody reads their
absence as an oversight:

- **Downloading a DOT document.** *"Document download and spreadsheet date
  update will remain manual."*
- **Updating a date in a spreadsheet.** Same clause. There is no spreadsheet to
  update: the dates live in the systems of record and are read from there.
- **Retrieving inspection photos.** *"Photo retrieval and date update will
  remain manual."*

This flow tracks the row and asks the question. Widening it past what the
customer signed off would be a decision taken by whoever wrote the code, which
is precisely the arrangement this page exists to replace.

Sending the notices themselves is also not done here. The desks and the
workspace **are** the notification in this release; outbound mail is a recorded,
separate decision, and when it is taken the recipients per notice class belong
on this page — the holder's supervisor, the driver and the safety lead, the crew
foreman — resolved against the org chart rather than typed as addresses.
