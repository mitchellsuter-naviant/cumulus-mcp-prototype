---
id: expense-approval-policy
path: finance/policies/expense-approval
title: Expense Approval Policy
authority_level: policy
owner: finance-ops
tags: expenses, approvals, finance
status: published
---
# Expense Approval Policy

Every reimbursement is confirmed by the person who submitted it, and checked
against the values below.

## Who confirms an expense

The person who submitted it. A reimbursement rests on one claim — *this is what
the receipt says* — and the only person who can vouch for that is the one who
was holding the receipt. So every submission comes back to its submitter to be
confirmed before anything is recorded, whatever the amount and whatever the
checks below found.

The checks are not a routing decision. They say what is worth a second look —
over the auto-approval limit, a restricted category, an uncertain reading, no
stated purpose — and that sentence travels with the request to the desk it was
always going to. Finance sees every recorded expense with its findings attached
and the submitter's confirmation on the record.

## Automated reimbursement

The expense-reimbursement flow reads this section as governed values. Edits
here change what the flow does — no code changes, and every edit is logged.

- Auto-approval limit: $200.00 per receipt. Above this the request is flagged
  as over the limit when it goes back to its submitter, and the flag is
  recorded against it.
- Minimum extraction confidence: 0.80. Below this the reading is flagged as
  uncertain, so the submitter knows which number to check first.
- Restricted categories: alcohol, gifts, entertainment. These are always
  flagged, whatever the amount.
- Business purpose required above: $75.00. A line at or above this amount must
  carry a stated purpose — from the submitter's own commentary, or supplied
  when they confirm — before it can be recorded as approved.
- Requested amount tolerance: $0.50. A line may request less than the document
  total (the submitter's own split); requesting more than the document supports
  by beyond this tolerance is always flagged.

## Approval routing

Which desk a review lands on. The flow reads this table the same way it reads
the limits above: edit it here and the next submission routes differently, with
no code change and every edit logged.

A desk is either a **queue** somebody administers, or a resolver answered
against the case: `submitter`, which sends the review back to the person who
raised it, on their own desk; or `manager-of(submitter)`, which is answered
against the org chart.

| Amount | Desk |
| --- | --- |
| Over $0 | submitter |

- Fallback desk: expense-review

The fallback is what a request gets when the resolver cannot answer — a case
with no human behind it, or a desk registry that could not be reached. It is a
real desk with real people on it, not a dead letter.

## Submission rules

- A submission must carry the receipt itself. A description of an expense is
  not evidence of one, and nothing is recorded without the artifact.
- Expenses must be submitted within 30 days of being incurred.
- Receipts are required for any expense over $25.
- Travel expenses follow the corporate travel guideline in addition to this policy.
