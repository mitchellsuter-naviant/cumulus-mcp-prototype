---
id: model-pricing
path: platform/standards/model-pricing
title: Model Pricing
authority_level: standard
owner: platform-eng
tags: models, cost, pricing, governed-knowledge, finops
status: published
---
# Model Pricing

What one model call costs, per million tokens, in US dollars. Every ModelCall
step in every flow is priced against this table at the moment the call returns,
and the figure it produces is what the console's spend tiles and the cost
recommender add up. Editing a row here changes what the next run records; it
does not change what a run already recorded, because a journal is a record of
what happened and re-pricing history would make it a projection.

These are display-plausible rates for a prototype, not a contract with a
vendor. Replace them with the ones on your own invoice — that is the whole
reason they live in a document instead of in code.

## Rates

| Model | Input $/M tokens | Output $/M tokens |
|---|---|---|
| gemini-3.7-flash | 0.30 | 2.50 |
| gemini-3.5-flash-lite | 0.10 | 0.40 |
| claude-fable-5 | 5.00 | 25.00 |
| claude-opus-5 | 5.00 | 25.00 |
| claude-sonnet-5 | 3.00 | 15.00 |
| claude-haiku-4-5 | 1.00 | 5.00 |

## A model with no row here is unpriced, never cheap

A call whose model id is missing from the table above records a cost of
**zero**, and zero here means *nobody knew the rate* — it does not mean the
call was free. That is why the console reports a `cost_basis` beside every
spend figure rather than the figure alone: `unrecorded` says calls happened
and none of them carried a price, which is a different fact from
`no-model-calls`, and the opposite of the reassuring `$0.00` a naive rollup
would print over the same hole.

The local stub extractors are the honest example. They call nothing, they
record `stub-extractor` as the model and zero tokens, and they are deliberately
absent from the table: a development deployment should show `unrecorded`, not a
convincing bill.

## The blended rate, and what it is used for

The cost recommender compares two rungs of the same ladder by blending each
model's two rates at **60% input, 40% output** — the rough shape of an
extraction workload, where the document is long and the JSON is short. It is a
comparison, not a bill: the blend is only ever used as a *ratio* between two
models, so its absolute value never reaches a page.

That comparison reads this table at the moment the card is drawn, which is the
opposite of how a ModelCall is priced and right for the same reason: a
recommendation is a claim about what a future run would cost, so it uses
today's price sheet, while a journal entry is a claim about a run that already
happened, so it keeps the price that was in force then.

## The downgrade ladder is not here

Which model may be proposed as a substitute for which — `gemini-3.7-flash` down
to `gemini-3.5-flash-lite`, `claude-opus-5` down to `claude-sonnet-5` — stays
in code, and deliberately. A rate is a fact about a vendor's price list and
belongs in a document a steward can correct from an invoice. A rung is a claim
that two models are close enough in capability to be swapped after an eval, and
one rung at a time: two rungs is a different flow, not a saving.

**Cross-vendor rungs are deliberately absent from that ladder.** Swapping
vendors changes what a prompt means. That is a migration to evaluate, not a
cost saving to apply from a card — and the fact that this table prices both
vendors side by side must not be read as an invitation to trade one for the
other on price.
