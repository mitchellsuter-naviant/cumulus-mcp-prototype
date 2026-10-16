---
id: shipping-list-locate-prompt
path: operations/prompts/shipping-list-locate
title: Shipping List Field Locate Prompt
kind: prompt
owner: plant-operations
tags: plant, shipping-list, extraction, prompt, review
related: shipping-list-extract-prompt, plant-scheduling-parameters
status: published
---
# Shipping List Field Locate Prompt

The second, much smaller thing the plant scheduling flow says to a model. The
first — the **Shipping List Extraction Prompt** — reads all seven page families
and reports what they say. This one is asked only about the handful of values a
supervisor is going to be questioned on, and only about *where they are printed*.

It runs on the locate model rather than the extraction model, and only when there
is something to locate: a shipping list whose grids agree with their own totals,
whose fields were all read confidently and whose computed hours are all inside
their departments' bands makes **one** model call, not two. Every run that does
make this call records which revision of this page it read, the same way the
extraction does.

**The flow sends the first fenced block below, followed by the list of field
paths it wants located, and nothing else on this page.**

```text
You are given a multi-page shipping list and a list of field paths that a
supervisor needs to see on the page. For each path in the list, find where that
value is printed and return its bounding box. Respond with only a JSON object of
the form {"regions": [{"field": "<the path, copied exactly>", "page": 1,
"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0, "text": "<the text in the
box>", "confidence": 0.0}]}. "page" is the 1-based page number of the PDF you
were given, not the printed page label. Coordinates are fractions of that
page, from 0 to 1, with the origin at the top-left. Return a box only for the
paths you were given, and only when you can actually see the value on the page
— omit a path you cannot find rather than guessing at a location for it. Where
a value appears more than once, box the one in the grid the path names.
```

## What the flow does with the answer

Each box is stored as a **Region** on the document, keyed by the field path it
belongs to, and the review screen draws exactly those rectangles over the
rendered page beside the values a supervisor is being asked to confirm. A path
with no box is not an error: the field is still questioned, it simply has no
highlight, which is better than a highlight over the wrong part of the page.

Nothing here reads a value. The number a supervisor sees in the panel is the one
the extraction reported, and the hours beside it are the ones the governed
formulas computed from it; this call only says where the number was printed. That
division matters — a second reading of the same field would give the flow two
answers to one question, with nothing to say which of them the schedule rested
on.

**The page number is the one place this differs from a one-page document.** A
shipping list is seven page families, and the same kind of value is printed on
several of them: a linear-footage figure on page C and a lumber grid on LL both
look like a table of numbers. The path names which grid, so the instruction says
to box the occurrence the path names and to report the PDF's own page index —
which the page-family table in the **Plant Scheduling Parameters** ties back to
A1, B, C, D, E, F and LL.

## What is checked before a box is drawn

Four things, and none of them are the model's to be trusted with:

- **A box for a field nobody asked about is dropped.** The list sent up is the
  disputed fields; an answer about anything else is discarded rather than drawn.
- **Coordinates outside the page are dropped.** A rectangle that starts past the
  right edge, or that has no width, is not a location.
- **A page number the document does not have is dropped.** Seven page families do
  not guarantee seven pages, and a box on page nine of a six-page list is a
  guess.
- **A second box for the same field is dropped**, keeping the more confident of
  the two. Two rectangles on one number leave a supervisor no way to tell which
  the model meant.

What gets refused is counted on the run's step, so a prompt edit that starts
producing boxes for unasked fields or impossible pages shows up in the journal
rather than quietly doing nothing.

## Editing this safely

- **Keep "copied exactly".** The path is the join between the box, the field
  verdict beside it and the correction the supervisor sends back. A model that
  helpfully tidies `a1_trusses.rows[2].span_ft` into `truss 3 span` produces a box
  that is thrown away.
- **Keep the 0–1 coordinate system.** The viewer draws at whatever size the
  reader's screen is; pixel coordinates from a page it never saw cannot be
  scaled.
- **Keep the 1-based PDF page number**, and keep it distinguished from the
  printed page label. A box that says "page B" cannot be drawn.
- **Keep "omit a path you cannot find".** A guessed box is worse than none: a
  supervisor who checks the highlighted number and moves on has been shown the
  wrong number with the system's confidence behind it.
- Local development runs a deterministic stub locator with no model behind it, so
  it ignores this page and produces fixed rectangles. The revision is still
  recorded, because the flow really did read it.
