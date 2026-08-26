---
id: invoice-posting-policy
path: finance/policies/invoice-posting
title: Invoice Posting Policy
authority_level: policy
scope: org
owner: finance-ops
tags: ap, invoices, posting, finance
status: published
---
# Invoice Posting Policy

How inbound vendor invoices are posted to the AP ledger, and when a human
reviews first.

## Automated posting

The accounts-payable flow reads this section as governed values. Edits here
change what the flow does — no code changes, and every edit is logged.

- Auto-post limit: $2,500.00 per invoice. At or under this limit, a
  confidently classified invoice from a known vendor posts without review.
- Minimum classification confidence: 0.75. Below this the invoice is
  elevated to the ap-clerk queue regardless of amount.
- First-time vendors are always reviewed: the three-model ensemble extracts
  the document, an adjudicator merges the candidates, and a clerk verifies
  the proposed exemplar before anything posts.

## Controls not yet automated

- Three-way match (invoice ↔ purchase order ↔ receiving record) is declared
  in the flow as a placeholder until the PO and receiving feeds land.
- Fraud screening (bank-detail changes, duplicate invoices, round-number
  anomalies) is likewise declared and pending.

## Disputes

A rejected invoice is returned to the vendor contact on file with the
clerk's note attached. Recurring rejections from one vendor escalate to the
vendor-management review.
