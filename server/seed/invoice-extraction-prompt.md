---
id: invoice-extraction-prompt
path: finance/prompts/invoice-extraction
title: Invoice Extraction Prompt
kind: prompt
owner: finance-ops
tags: ap, invoices, extraction, prompt
status: published
---
# Invoice Extraction Prompt

This is what the accounts-payable flow tells the model when it reads an inbound
invoice. It is governed knowledge, not application code: change the wording here
and the next run uses the change. Nothing is deployed and no code is edited, and
every run records which revision of this page it read — so a posting decision
can always be explained against the exact instruction that produced it, as it
was worded that day.

**The flow sends the first fenced block below and nothing else on this page.**
Everything after it is written for you, not for the model. (Remove the fence and
the whole page becomes the prompt, so keep it unless that is what you want.)

```text
Extract from this invoice: vendor_name — the party that issued it, as printed,
not normalized and not guessed at from an identifier — vendor_tax_id if one is
printed, invoice_number, invoice_date (ISO 8601), due_date (ISO 8601),
po_number, subtotal_usd, freight_usd, tax_usd, total_usd, currency, a short
line_summary, your confidence 0-1, and lines: an array of the billed lines, each
with sku, description, quantity, unit_price_usd, amount_usd and kind (goods,
freight, tax or discount).

Also return field_confidence: an object whose keys are the field names above
and whose values are your confidence 0-1 in that one field, and on each line a
field_confidence object over that line's own keys. Judge each field on its own
— how clearly that value is printed and how sure you are you read it correctly
— rather than repeating one number for the whole page. Omit a field you did not
find rather than reporting low confidence in a guess.

Respond with only a JSON object with those keys.
```

## What the flow does with the answer

One call reads the whole document — there is no second pass that names the
vendor first. What comes back is then handed to machinery that checks it, and
each field has somewhere it lands.

- `vendor_name` and `vendor_tax_id` go to the **resolver**, which decides which
  vendor in the master this is, using the thresholds in the **Entity Resolution
  Policy**. A name it cannot place confidently stops the run and asks a clerk;
  one it has never seen is minted as `onboarding`.
- `po_number` is what the three-way match keys on. Without it the invoice has
  nothing to match against and is routed for review rather than posted.
- `lines` are paired against the purchase-order lines and the receiving lines
  already in the graph, within the tolerances in the **Invoice Posting Policy**.
  `sku` and `quantity` are what the pairing uses; a missing `sku` shows up as an
  unmatched line, not as a silent pass.
- `total_usd` is checked against that policy's auto-post limit, and `confidence`
  against its minimum extraction confidence. Either one out of bounds sends the
  invoice to the ap-clerk queue.
- `field_confidence` decides **which fields a clerk is asked about**. Anything
  below the minimum field confidence in the posting policy is put in front of
  the reviewer with a box drawn around it on the page; everything else is
  collapsed as context. This is the field that turns "check this invoice" into
  "check these three numbers", so a score that is the same for every field —
  or one number copied across all of them — costs a reviewer real time. Say 0
  for a field you did not find rather than guessing at a value and marking it
  low; a missing required field is caught separately and named as missing.

## Editing this safely

- **Do not ask for a normalized or looked-up vendor name.** The instruction says
  "as printed, not normalized and not guessed at from an identifier" on purpose:
  deciding which entity a printed name refers to is the resolver's job, against
  the vendor master. A model asked to normalize will quietly invent an identity
  from memory, and the resolver will then match against something that was never
  on the page.
- **Keep the key names.** The flow looks for exactly these; renaming one here
  does not rename it downstream, it makes that value arrive empty.
- **Keep "respond with only a JSON object".** The answer is parsed, not read.
- Say what you want read, not what you want decided. Whether an invoice posts is
  the posting policy's job and the clerk's — a prompt that tells the model to
  approve payment moves a governed decision somewhere nothing audits it.
- Local development runs a deterministic stub extractor with no model behind it,
  so it ignores this page. The revision is still recorded, because the flow
  really did read it; the wording only changes what comes back once a real model
  is configured.
