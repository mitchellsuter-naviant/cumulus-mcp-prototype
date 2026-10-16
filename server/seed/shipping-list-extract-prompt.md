---
id: shipping-list-extract-prompt
path: operations/prompts/shipping-list-extraction
title: Shipping List Extraction Prompt
kind: prompt
owner: plant-operations
tags: plant, shipping-list, extraction, prompt, scheduling
related: plant-scheduling-parameters, labor-hour-formulas
status: published
---
# Shipping List Extraction Prompt

This is what the plant scheduling flow tells the model when it reads a job's
shipping list. It is governed knowledge, not application code: change the wording
here and the next run uses the change. Nothing is deployed and no code is edited,
and every run records which revision of this page it read — so a schedule row can
be explained against the exact instruction that produced the numbers behind it,
as it was worded that day.

**The flow sends the first fenced block below and nothing else on this page.**
Everything after it is written for you, not for the model. (Remove the fence and
the whole page becomes the prompt, so keep it unless that is what you want.)

```text
You are reading one job's shipping list from a post-frame building plant. It is
a multi-page PDF with seven page families, each printed with its own label:

  A1  truss drawings — one drawing per truss mark
  B   components, laminates, verticals and truss grids, plus handwritten notes
  C   steel fabrication — colour grids, linear footage, CondenStop, crib notes
  D   hardware line items
  E   trim cut/fab and trim pack — items, lengths, weights, FJ and JJ flags
  F   slide door hardware — track end caps, pull plate
  LL  loader list — pre-calculated board hours, lumber and plywood grids

Read every page family that is present and report what it says. Return exactly
this JSON shape, omitting nothing that is printed and inventing nothing that is
not:

{
  "job_number": "<as printed>",
  "confidence": 0.0,
  "pages": {"A1": true, "B": true, "C": true, "D": true, "E": true, "F": true,
            "LL": true},
  "a1_trusses": {"present": true, "rows": [
    {"mark": "T1", "quantity": 0, "span_ft": 0.0, "ply": 1,
     "top_chord_members": 0, "bottom_chord_members": 0, "web_members": 0}]},
  "b_components": {"present": true, "total_quantity": 0,
    "rows": [{"description": "", "quantity": 0}]},
  "b_laminates": {"present": true, "rows": [
    {"description": "", "lumber_size": "2x6", "length_ft": 0.0, "ply": 1,
     "quantity": 0, "perma_column": false, "precut": false}]},
  "b_verticals": {"present": true, "blocks_quantity": 0,
    "ripped_plywood_sheets": 0, "ridge_cleat_quantity": 0},
  "b_truss_grid": {"present": true},
  "b_notes": {"girts_plated_in": false, "text": ""},
  "c_steel_fab": {"present": true, "total_linear_feet": 0.0,
    "condenstop": false,
    "colours": [{"colour": "", "linear_feet": 0.0}],
    "cribs": [{"size": "small", "quantity": 0}]},
  "d_hardware": {"present": true,
    "rows": [{"description": "", "quantity": 0}],
    "concrete_mix_bags": 0, "l_bracket_12x5_quantity": 0},
  "e_trim": {"present": true, "total_weight_lb": 0.0, "fj_trim": false,
    "jj_trim": false, "ridge_light": false, "ridge_vent_quantity": 0,
    "house_wrap_rolls": 0, "precut_house_wrap": false,
    "rows": [{"description": "", "length_in": 0.0, "quantity": 0,
              "weight_lb": 0.0}]},
  "f_slide_door": {"present": true, "track_end_caps_quantity": 0,
    "pull_plate": false, "non_wick_trim_grid": false,
    "framing_grid": false},
  "ll_loader_list": {"present": true, "board_hours": 0.0,
    "lumber_grid_present": false, "plywood_grid_present": false,
    "pallet_items_quantity": 0, "largest_truss_width_ft": 0.0,
    "roller_trailer": false},
  "field_confidence": {}
}

Rules:

- A page family that is not in the document gets "present": false and no rows.
  Do not infer one grid from another and do not carry a value from one page
  family into another.
- Report values as printed. Lengths on page E are printed in feet and inches
  ("16'-4"") — convert to inches and report the number only. Spans on A1 are in
  feet. Weights are in pounds.
- "ply" on A1 is the number of plies of a truss; 2 or more is a multi-ply truss.
  Report the number, not a judgment about it.
- "girts_plated_in" is true only when the B page carries the handwritten note
  "Trusses w/Girts Plated In" or a clear variant of it. Put whatever handwriting
  you see in "text" as well, verbatim.
- "condenstop" is true only when page C says CondenStop. The colour grid's
  linear feet must be reported per colour as printed as well as the printed
  total; do not compute one from the other.
- "board_hours" on the LL page is already calculated. Copy it. Do not
  recalculate it and do not round it.
- field_confidence: an object whose keys are the full dotted paths of the values
  you reported, array indexes included and copied exactly
  ("c_steel_fab.total_linear_feet", "a1_trusses.rows[0].span_ft"), and whose
  values are your confidence 0-1 in that one value. Judge each on its own — how
  clearly it is printed and how sure you are you read it correctly — rather than
  repeating one number across the page. Say 0 for a value you could not find
  rather than guessing at it.
- NEVER calculate labor hours, bundle counts, door counts or a schedule. Report
  what the pages say and nothing more.

Respond with only a JSON object of that shape.
```

## What the flow does with the answer

Nothing here decides anything. Every number this comes back with is checked, then
turned into hours by governed formulas, then put in front of a supervisor before
it reaches a schedule.

- **`pages` and each family's `present` flag decide which departments the job has
  work for.** The page-family table in the **Plant Scheduling Parameters** says
  which department each page feeds. A department whose page family is absent is
  skipped — that department only, never the whole job — so a building with no
  trusses simply does not appear on Truss Cutting's day.
- **The grids feed the formulas in the Labor Hour Formulas.** Weights become
  bundles, linear feet become steel hours, track end caps become doors, spans and
  member counts become a lookup in the **Truss Standards**. Which is why the
  instruction above forbids the model from computing any of it: the arithmetic is
  governed, versioned and checked, and a number the model produced instead would
  be a hidden second implementation of a rule this plant can otherwise edit.
- **`field_confidence` decides which values a supervisor is asked about.**
  Anything below the field-confidence floor in the scheduling parameters is put
  in front of them with a box drawn around it on the page; everything else is
  collapsed as context. This is what turns "check this shipping list" into
  "check these three numbers", so a score copied across every field costs a
  supervisor real time.
- **`confidence`** is the whole-document score and is checked against the
  extraction-confidence floor. A page that reads badly overall goes to review
  whatever the individual fields say.
- The printed totals are checked against their own grids — components' rows
  against `total_quantity`, trim rows' weights against `total_weight_lb`, the
  colour grid's linear feet against `total_linear_feet` — within the tolerances
  in the scheduling parameters. That is why both halves are asked for. A grid
  that does not add up to its own total was misread somewhere, and finding out
  where costs somebody four seconds.
- Every computed figure is also checked against its department's expected hours
  band. Out of band arrives in the same review as a low-confidence field, beside
  the grid it came from.

## Editing this safely

- **Keep the key names and the shape.** The flow looks for exactly these paths,
  and they are the join between a value, the box drawn on the page, the field
  verdict beside it and the correction a supervisor sends back. Renaming a key
  here does not rename it downstream; it makes that value arrive empty and the
  department it fed look absent.
- **Keep "NEVER calculate labor hours".** A model asked for hours will produce
  them, they will look plausible, and they will not be the plant's numbers. The
  formulas are on a page a superintendent can edit; a figure a model invented is
  on no page at all.
- **Keep "as printed".** Converting units, normalizing a colour name or tidying a
  description all discard the thing a supervisor would check against the page.
  The one conversion asked for — feet-and-inches to inches — is asked for
  explicitly and in one place.
- **Keep the per-page `present` flags honest.** "Present but empty" and "absent"
  are different answers: absent skips a department, and a false absence silently
  removes a department's work from the plant's day.
- **Keep "respond with only a JSON object".** The answer is parsed, not read.
- Say what you want read, not what you want decided. Whether a day is scheduled,
  and at what capacity, is the scheduling parameters' job and the supervisor's.
- Local development runs a deterministic stub extractor with no model behind it,
  so it ignores this page. The revision is still recorded, because the flow
  really did read it; the wording only changes what comes back once a real model
  is configured.
