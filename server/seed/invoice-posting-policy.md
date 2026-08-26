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

## Three-way match

The flow matches invoice lines against the purchase order they bill and the
receiving report that booked the goods in. These values govern that match.

- Price variance tolerance: 2.0%. A unit price within this of the PO line is
  treated as agreeing.
- Quantity variance tolerance: 0.0 units. Billing more than was received is
  always a variance.
- Freight and tax allowance: $150.00. Lines of kind freight or tax carry no PO
  line by nature; up to this amount they are accepted without one.
- Receiving report required above: $500.00. Below this an invoice may post
  against the PO alone; at or above it, goods must be booked in first.
- Unmatched line tolerance: 0 lines. Any goods line with no PO line is a
  variance a clerk rules on.

## Anomaly screening

Scored against the vendor's own history rather than a global rule, because
"unusual for this vendor" is the only definition that survives contact with a
real vendor list.

- Amount outlier multiple: 3.0. An invoice more than this multiple of the
  vendor's median invoice is flagged.
- First-time-large threshold: $5,000.00. A vendor with fewer than three prior
  invoices billing at least this much is flagged regardless of the multiple.
- Duplicate window: 30 days. A same-vendor invoice within this window whose
  total matches an existing one to the cent is flagged as a suspected
  duplicate.
- Cadence deviation multiple: 4.0. An invoice arriving far outside the
  vendor's usual interval is flagged.
- Anomaly profile window: 365 days. How far back the vendor profile looks.

## Disputes

A rejected invoice is returned to the vendor contact on file with the
clerk's note attached. Recurring rejections from one vendor escalate to the
vendor-management review.
