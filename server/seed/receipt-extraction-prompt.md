---
id: receipt-extraction-prompt
path: finance/prompts/receipt-extraction
title: Receipt Extraction Prompt
kind: prompt
scope: org
owner: finance-ops
tags: expenses, extraction, reimbursement, prompt
status: published
---
# Receipt Extraction Prompt

This is what the expense-reimbursement flow tells the model when it reads a
submitted receipt. It is governed knowledge, not application code: change the
wording here and the next run uses the change. Nothing is deployed and no code
is edited, and every run records which revision of this page it read — so a
reimbursement decision can always be explained against the exact instruction
that produced it, as it was worded that day.

**The flow sends the first fenced block below and nothing else on this page.**
Everything after it is written for you, not for the model. (Remove the fence and
the whole page becomes the prompt, so keep it unless that is what you want.)

```text
Extract from this receipt: vendor (lowercase slug), date (ISO), total_usd
(number), category (one of meals, travel, lodging, mileage, supplies, software,
alcohol, gifts, entertainment, other), your confidence 0-1, and lines: an array
of the distinct charges on the receipt, each with vendor, date, category,
amount_usd and description. If the receipt covers only one charge, return a
single line. Respond with only a JSON object with keys vendor, date, total_usd,
category, confidence, lines.
```

## What the flow does with the answer

The receipt image goes to the model along with this instruction, and if the
submitter wrote a note it is appended as `Submitter note: …` — their commentary
is context for the reading, never an instruction that replaces this one.

- `vendor`, `date` and `total_usd` become the request's own fields.
- `lines` becomes one reimbursable line each. This is why the instruction asks
  for an array even when there is one charge: a dinner and a taxi on a single
  folio are two facts, and a reviewer can afterwards correct an amount, split a
  line further, or add one the model missed. Each line records who last touched
  it, so a corrected line is never confused with an extracted one.
- `category` is checked against the restricted categories in the **Expense
  Approval Policy** — alcohol, gifts and entertainment are always sent for human
  review. If you add a category here, add it there too, or it will be extracted
  and then never screened.
- `confidence` is checked against the minimum extraction confidence in the same
  policy. Below the floor, the request goes to the expense-review queue instead
  of being auto-approved, however small it is.

## Editing this safely

- **Keep the key names.** The flow looks for exactly `vendor`, `date`,
  `total_usd`, `category`, `confidence` and `lines`. Renaming one here does not
  rename it downstream; it makes that value arrive empty.
- **Keep "respond with only a JSON object".** The answer is parsed, not read.
- Say what you want read, not what you want decided. Whether an expense is
  approved is the policy's job and the review queue's — a prompt that tells the
  model to approve things moves a governed decision somewhere nothing audits it.
- Local development runs a deterministic stub extractor with no model behind it,
  so it ignores this page. The revision is still recorded, because the flow
  really did read it; the wording only changes what comes back once a real model
  is configured.
