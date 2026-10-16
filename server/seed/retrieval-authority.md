---
id: retrieval-authority
path: platform/standards/retrieval-authority
title: Retrieval Authority
authority_level: standard
owner: platform-eng
tags: retrieval, search, authority, precedence, governed-knowledge
status: published
---
# Retrieval Authority

How much a document's authority is worth when something has to choose between
two documents that both answer the question. This is a precedence list plus a
weight, and it lives in a document rather than in code because the order is a
statement about how this organization is governed, not an implementation
detail — the same reason the time-off window and the model price sheet do.

## Two decisions, and conflating them is the bug

**Relevance decides whether a document is an answer. Authority decides which
of the answers governs.** Retrieval ranks on relevance; authority is only ever
allowed to reorder documents that already answered the question.

Weighting authority into relevance directly is the failure mode this table
exists to avoid. Sorting results by authority puts the least relevant policy
above the how-to that actually answers the question, and every reader learns to
distrust the ranking. So authority buys *places*, not rank — a bounded number
of positions a document may climb, and never enough to lift something that was
not already near the answer.

## Precedence and lift

The rows are in precedence order, highest authority first. `Lift` is how many
places a document of that level may climb in a ranking; it is capped at three
regardless of what is written here, because a document that was not in the top
few was not an answer.

| Authority level | Lift |
|---|---|
| policy | 3 |
| standard | 2 |
| guideline | 1 |
| how-to | 0 |
| note | 0 |

A document with **no authority level is not a document** — it is a prompt, a
config, or a generated schema export, and it carries no lift, cites as
machinery, and is never the answer to a question about how the organization
works.

## The relevance floor

Coverage is the fraction of the meaningful words in a question that appear
anywhere in a document. It is the cheap, explainable test for whether a
retrieved document is about the question at all, and it is what stops a
ranking's best-of-a-bad-set from being reported as an answer.

| Setting | Value |
|---|---|
| Minimum coverage | 0.34 |
| Governing coverage | 0.60 |
| Tie band | 0.15 |

Two thresholds, because there are three honest things to say and only two of
them are "here is the answer".

Below **minimum coverage** a document is not citable at all. A grounded
answerer must say it did not find the answer and name who owns the question. It
must not name a governing document, because at that coverage it does not have
one — it has the top of a ranking, which is not the same thing and is worse
than nothing when a reader believes it.

Between the two, a document is worth handing over and not worth calling the
rule: *the closest thing in the corpus, which may not answer this*. That
sentence is the one a corpus gap should produce, and the gap is the finding —
somebody should write the missing document.

At or above **governing coverage**, the highest-authority survivor governs and
the answer says so.

## Authority only breaks a tie

**Tie band** is how far below the best-covered document another may sit and
still count as answering the same question about as well. Inside it, authority
decides; outside it, authority buys nothing.

Without that gate the lift does the wrong thing in the case that matters most.
Asked about dental cover, the benefits guide covers the whole question and the
PTO policy two-thirds of it — and a policy outranks a guideline, so a lift
blind to coverage hands the employee the PTO policy as the document governing
their dental plan. It does not. It is more authoritative in general and it is
not about the question, and that distinction is the entire reason relevance and
authority are two decisions rather than one.

## Conflict

When two documents above the floor disagree, the higher row in the table
governs and the answer says so, naming both. Silence about a conflict is the
one outcome that is never acceptable: a reader who is told only the winning
rule has no way to discover that their team follows the other one.
