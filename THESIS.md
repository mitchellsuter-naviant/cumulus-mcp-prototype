Cumulus Thesis

Why an ontology is the path from passive AI to autonomous AI.

How to read this document

This is the argument beneath DOCTRINE.md. The doctrine says what Cumulus is and
how it is built; this document says why that shape — and not a chat box, not a
tool-calling agent over raw APIs — is the one that reaches autonomy. It uses the
doctrine's status markers ([LOCKED] / [PROPOSED] / [OPEN]) with the same
meaning. Most of this document is [PROPOSED]: it is the position we argue from,
written down so it can be attacked in one place instead of relitigated in
fragments.

The claim

[PROPOSED] Building an ontology and knowledge graph is the only way to move
from passive AI to autonomous AI, because autonomy requires a model of how the
business functions — and every architecture that reaches for autonomy ends up
with one, whether or not it admits it.

The claim, sharpened

Passive → autonomous is not a capability upgrade. It is a change in **failure
semantics**. A passive AI's failure is a bad answer, and a human filters it
before anything happens. An autonomous AI's failure is a bad action,
*committed* — money moved, a record posted, a vendor merged. What absorbs that
shift is not a better model. It is the environment around the model: resolved
identity, typed actions, gates, provenance, evaluation. Every one of those is a
function **of** a model of the business.

So the ontology is not what makes autonomy *possible* — an ungoverned agent can
act on an ERP today. The ontology is what makes autonomy **survivable**: the
difference between autonomy an organization can grant and autonomy it must
revoke after the first incident.

The strongest form of the argument

[PROPOSED] **You do not choose whether to have an ontology. You choose whether
it is explicit and governed or implicit and scattered.**

A tool-calling agent over raw APIs has an ontology too — it lives in prompt
strings and tool descriptions, unversioned, unadjudicated, and slightly
different per agent. Two agents with identical ten-million-token contexts can
still disagree about which vendor is which; a graph with adjudicated identity
cannot. The competing architectures do not eliminate the model of the business.
They smuggle it in without governance. Seen this way, "only way" stops being
bravado and approaches tautology: the choice on offer is not
ontology-vs-no-ontology, it is governed-vs-smuggled.

Why scale does not beat this

The standard objection is the bitter lesson: hand-encoded structure
historically loses to compute and data. But the bitter lesson applies to
*reasoning and perception* — learnable regularities of the world. An ontology,
done right, does not encode reasoning; it encodes **contingent facts and
commitments**:

- that `V-10041` in the ERP *is* Acme Industrial (identity — a fact about this
  deployment, present in no training distribution);
- that a human claim outranks a model claim (precedence — a policy choice);
- that receipts are kept two years (retention — a commitment);
- that invoices over the posting limit need a person (a gate — a decision).

No amount of pretraining infers these, because they are not regularities. They
are *decisions this organization made*, and scale cannot learn what has not
been decided. The ontology is where decisions live so that models can act
inside them. Structure that competes with the model loses; structure that
carries what the model cannot know wins.

Why now — the scissors

The semantic web failed in the 2000s on economics: ontology authoring cost more
than dumb consumers could ever repay. Large language models moved **both blades
of the scissors at once**:

- the *consumer* is finally smart enough to exploit a knowledge graph fully — a
  model that can traverse, join, and explain; and
- the *authoring cost* collapses, because the model can propose structure and
  humans ratify it — which is exactly the flywheel: corrections land as
  structure, promotion is a governed decision, the graph maintains itself
  through use.

The idea did not get better. The economics flipped on both ends
simultaneously. That is why this is a thesis for now and not a re-run of 2006.

The evaluation argument

[PROPOSED] The least-appreciated half of the thesis: **you cannot even specify
correct autonomous behavior without a state model.** The first prototype
tried. Prose behavior specs were unfalsifiable; the evaluation design only
stabilized when a spec became *assertions over graph state at checkpoints* —
after the resolve step the Vendor must be linked and nothing extracted yet; at
commit the Invoice, its lines, and the match must exist; a held invoice asserts
its entities are absent. The ontology is not just the substrate for acting. It is
the substrate for *knowing whether the action was right*. A vendor of autonomy
without a graph cannot test its own product.

[PROPOSED] So a Flow should ship with its own evaluation: assertions over
graph state at named checkpoints, declared beside the Flow and versioned with
it, so that changing the Flow changes what it claims to be correct — and the
correction set its Tasks produce, every raw reading beside what a person made
of it, is its regression data. That is this argument one turn further on: if
the graph is what makes correctness expressible at all, the expression belongs
inside the artifact whose correctness is at stake, not in a document beside
it. The doctrine carries the matching clause in Lens 7.

Small proofs from the first prototype

The thesis will be won on demonstrations, not argument. These four came out of
the first prototype, `cumulus-mcp-prototype`, which is the guide and not the
product; the clause on each says what the current build can do today.

- "Do two sources disagree about Acme Industrial's name?" is a one-line query
  there and **unaskable** in a system without claims-that-carry-sources. The
  current build has no claims yet: a write keeps what it was not given rather
  than erasing it, but the value that lost is not kept beside the winner.
- "How much has this employee submitted in six months?" is a graph traversal
  from a resolved Employee — not a search, and not re-extraction. The current
  build does this, one hop: the invoices come back off the Employee's links,
  and traversal past one hop waits for the flow that needs it.
- A decision made months ago is explainable against the *revision of the
  policy it actually read*, because reads pin revisions. Not yet in the
  current build: a model call pins the configuration that answered it, by the
  hash of its prompt, schema and provider block, and every retired version
  stays readable — but a read of a policy document pins nothing.
- Point-in-time reconstruction of "the world as of step z1" falls out of claims
  recording the step that wrote them — provenance pays for evaluation. Not yet
  in the current build: every write names the run and step that made it, in
  the Ledger, and nothing rebuilds the world as it stood at one of them.

The Palantir read

Palantir is the right inspiration, read carefully. The ontology *idea* was not
their innovation — it is decades old. What they proved is that an ontology only
works **operationalized**: entities and links bound to *actions with
write-back*, built incrementally per use-case, paid for by workflow value, and
co-managed by forward-deployed engineers. When LLMs arrived, their customers
already had the substrate that gives a model safe, typed handles — which is why
their AI platform landed while chat-box competitors stalled. Every element of
that operational form is already in the doctrine: the Action Gateway, the
per-wedge build, the FDE model.

Where Cumulus differs — and the sharpest available differentiator:
**ownership**. Their ontology is inseparable from their platform; leaving is
famously brutal, and customers know it. The map-vs-machinery split — the
customer keeps the ontology, the data, the definitions, and the Ledger; only the engines go dark — converts the thesis's biggest buyer
objection (lock-in fear) into the pitch: *the ontology you can leave with.*

The economic argument

Graph curation **compounds to the customer**. Fine-tuning compounds to the
model vendor; prompt libraries compound to nobody; a curated ontology plus its
correction sets is an appreciating asset the customer owns — which pairs with
the portability guarantee. Every correction a person makes is retained
judgment; the flywheel turns retained judgment into structure; structure makes
the next run cheaper and safer. That loop is the product.

Where the thesis will be tested

Theses rarely die on theory. The known failure modes, and the standing
defenses:

1. **Boil-the-ocean.** Most ontology projects die as multi-year enterprise
   data models that ship nothing. Defense: build the graph one workflow at a
   time, and only the parts a running Flow reads and writes. An ontology no
   flow touches is a wiki with edges.
2. **Curation after novelty.** Year one, people curate; year three the graph
   drifts unless corrections land as structure automatically. The flywheel is
   not a feature — it is the maintenance strategy. If it does not carry the
   curation load, the graph rots.
3. **Over-modeling.** Not everything is an entity. A telemetry feed turned
   into a million ABox individuals is a failure exactly as bad as no graph.
   The reify-or-bind rule is the guardrail. Reify a source row — give it an
   id, a standing and a history of its own — only when the row itself needs
   identity, provenance, or human adjudication; otherwise bind it as a
   property of an entity that already exists, and leave the rest in the
   source, resolved lazily when something asks.
4. **"Only" is doing sales work.** The epistemically careful claim is "the
   only *safe, auditable, compounding* path currently visible." Keep the
   strong wording in the pitch and the careful wording in the engineering.

One sentence

Autonomy is not a property of the model; it is a property of the environment
the model is placed in — and an ontology is what an environment safe enough
for autonomy looks like when you write it down.
