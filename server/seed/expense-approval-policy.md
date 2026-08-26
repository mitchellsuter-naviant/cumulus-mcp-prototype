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

## Submission rules

- Expenses must be submitted within 30 days of being incurred.
- Receipts are required for any expense over $25.
- Travel expenses follow the corporate travel guideline in addition to this policy.
