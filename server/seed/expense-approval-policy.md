---
id: expense-approval-policy
path: finance/policies/expense-approval
title: Expense Approval Policy
authority_level: policy
scope: org
owner: finance-ops
tags: expenses, approvals, finance
status: published
---
# Expense Approval Policy

All employee expenses must be approved according to the thresholds below.

## Approval thresholds

| Amount | Approver |
| --- | --- |
| Under $500 | Direct manager |
| $500 – $5,000 | Department head |
| Over $5,000 | VP of Finance |

## Automated reimbursement

The expense-reimbursement flow reads this section as governed values. Edits
here change what the flow does — no code changes, and every edit is logged.

- Auto-approval limit: $200.00 per receipt. At or under this limit a
  confidently extracted, in-policy receipt is approved without review.
- Minimum extraction confidence: 0.80. Below this the request is elevated
  to the expense-review queue regardless of amount.
- Restricted categories: alcohol, gifts, entertainment. These are always
  elevated for human review.
- Business purpose required above: $75.00. A line at or above this amount must
  carry a stated purpose — from the submitter's own commentary or supplied by a
  reviewer — before it can be approved.
- Requested amount tolerance: $0.50. A line may request less than the document
  total (the submitter's own split); requesting more than the document supports
  by beyond this tolerance is always elevated.

## Approval routing

Which desk an elevated request lands on. The flow reads this table the same way
it reads the limits above: edit it here and the next submission routes
differently, with no code change and every edit logged.

A desk is either a **queue** somebody administers, or `manager-of(submitter)`,
which is answered against the org chart when the request is elevated — the
submitter's manager gets it on their own desk.

| Amount | Desk |
| --- | --- |
| Over $0 | manager-of(submitter) |
| Over $5,000 | expense-review-exec |

- Restricted category desk: expense-review-exec
- Fallback desk: expense-review

The fallback is what a request gets when the org chart cannot answer — a
submitter with no manager recorded, or a contractor outside the reporting
line. It is a real desk with real people on it, not a dead letter.

## Submission rules

- Expenses must be submitted within 30 days of being incurred.
- Receipts are required for any expense over $25.
- Travel expenses follow the corporate travel guideline in addition to this policy.
