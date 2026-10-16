---
id: invoice-locate-prompt
path: finance/prompts/invoice-locate
title: Invoice Field Locate Prompt
kind: prompt
owner: finance-ops
tags: ap, invoices, extraction, prompt, review
status: published
---
# Invoice Field Locate Prompt

The second, much smaller thing the accounts-payable flow says to a model. The
first — the **Invoice Extraction Prompt** — reads the whole document and reports
what it says. This one is asked only about the handful of fields a reviewer is
going to be questioned on, and only about *where they are on the page*.

It runs on `gemini-3.7-flash` rather than the extraction model, and only when
there is something to locate: an invoice whose arithmetic agrees with itself and
whose fields were all read confidently makes **one** model call, not two. Every
run that does make this call records which revision of this page it read, the
same way the extraction does.

**The flow sends the first fenced block below, followed by the list of field
paths it wants located, and nothing else on this page.**

```text
You are given a document and a list of field paths that a reviewer needs to see
on the page. For each path in the list, find where that value is printed and
return its bounding box. Respond with only a JSON object of the form
{"regions": [{"field": "<the path, copied exactly>", "page": 1, "x": 0.0,
"y": 0.0, "width": 0.0, "height": 0.0, "text": "<the text in the box>",
"confidence": 0.0}]}. Coordinates are fractions of the page, from 0 to 1, with
the origin at the top-left. Return a box only for the paths you were given, and
only when you can actually see the value on the page — omit a path you cannot
find rather than guessing at a location for it.
```

## What the flow does with the answer

Each box is stored as a **Region** on the document, keyed by the field path it
belongs to, and the review screen draws exactly those rectangles over the
rendered page beside the inputs a clerk is being asked to confirm. A path with
no box is not an error: the field is still questioned, it simply has no
highlight, which is better than a highlight over the wrong part of the page.

Nothing here reads a value. The number a clerk sees in the panel is the one the
extraction reported; this call only says where it was printed. That division
matters — a second reading of the same field would give the flow two answers to
one question, with nothing to say which of them the posting decision rested on.

## What is checked before a box is drawn

Three things, and none of them are the model's to be trusted with:

- **A box for a field nobody asked about is dropped.** The list sent up is the
  disputed fields; an answer about anything else is discarded rather than drawn.
- **Coordinates outside the page are dropped.** A rectangle that starts past the
  right edge, or that has no width, is not a location.
- **A second box for the same field is dropped**, keeping the more confident of
  the two. Two rectangles on one number leave a reviewer no way to tell which
  the model meant.

What gets refused is counted on the run's step, so a prompt edit that starts
producing boxes for unasked fields shows up in the journal rather than quietly
doing nothing.

## Editing this safely

- **Keep "copied exactly".** The path is the join between the box, the field
  verdict beside it and the correction the clerk sends back. A model that
  helpfully tidies `line_items[2].amount_usd` into `line 3 amount` produces a
  box that is thrown away.
- **Keep the 0–1 coordinate system.** The viewer draws at whatever size the
  reader's screen is; pixel coordinates from a page it never saw cannot be
  scaled.
- **Keep "omit a path you cannot find".** A guessed box is worse than none: a
  reviewer who checks the highlighted number and moves on has been shown the
  wrong number with the system's confidence behind it.
- Local development runs a deterministic stub locator with no model behind it,
  so it ignores this page and produces fixed rectangles. The revision is still
  recorded, because the flow really did read it.
