---
id: ap-intake-policy
path: finance/policies/ap-intake
title: Accounts Payable Intake Policy
authority_level: policy
owner: finance-ops
tags: ap, invoices, intake, documents, dms
status: published
---
# Accounts Payable Intake Policy

Which documents arriving in the company's document system are supplier
invoices, and therefore start an accounts-payable run.

## Where supplier invoices are filed

The ap-intake flow reads this section as governed values. Edits here change
which documents become invoices — no code changes, and every edit is logged.

A folder is named by its full path from the root of the document tree. A
document filed in that folder, or anywhere beneath it, is treated as a supplier
invoice. Nothing else about the file is consulted: not its name, not its size,
and not what a model would say about the page. Where a person filed something
is the most reliable statement of intent the document system holds, and it is
the one a clerk would use.

- Invoice folder: Accounts Payable

## How much one window takes on

- Invoices per intake window: 25

Each invoice this flow finds is answered by a whole accounts-payable run —
extraction included — before the delivery that woke it is acknowledged. The
bound is what keeps a first sync of a long-established filing tree from
answering hundreds of invoices in one request. Nothing is dropped: what a
window does not take on is still unread, and the next window finds it.

Raise this where the document system delivers rarely and in bulk. Lower it
where deliveries are frequent and the extraction model is slow.

## What is not read

- A document whose file type carries no page a model can read — a spreadsheet,
  an archive, a plain-text note — is counted and left alone. It stays unread,
  and stays visible as unread.
- A document the map already holds the bytes of, at the revision the pointer
  names, is not fetched again. A vendor who reissues a corrected invoice under
  the same document id creates a new revision, and that *is* read again: it is
  a different invoice.

## Disputes about scope

If invoices are being missed, the folder list above is the first thing to
check — a department that started filing under a new heading is a policy edit,
not a bug. If documents that are not invoices are starting runs, the fix is
also here: narrow the folder, or ask the department to file elsewhere.
