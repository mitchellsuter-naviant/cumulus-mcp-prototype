Cumulus Doctrine

The authoritative doctrine for Cumulus.

How to read this document

Each section is written to stand on its own when read in isolation — key terms are defined where they are used, and the load-bearing definitions are restated rather than cross-referenced, so any single section can be understood without the others. This is deliberate: the doctrine is consumed both cover-to-cover by people and in retrieved fragments by AI agents, and a fragment must carry its own meaning. Some repetition between sections is therefore intentional.

Status markers tell a reader (human or agent) how firm a statement is:

[LOCKED] — a settled decision; treat as a binding constraint.
[PROPOSED] — a recommended position, not yet ratified.
[OPEN] — an unresolved question; do not treat as decided.
[PARKED] — deliberately deferred to a later design session.

Structure. A glossary defines the core vocabulary. A Framing section establishes why Cumulus exists. Eight lenses each answer one orthogonal question, ordered so each can rely on the ones before it: the thesis (1), the vocabulary (2), how it is arranged (3), how work happens over it (4), where it runs (5), how it is accessed (6), who builds it (7), how it is physically realized (8). A Governance spine then states the cross-cutting safety rules — placed last because it draws on every lens, though it is foundational rather than an afterthought. An index ties it together.

Glossary of core terms

Each term is also defined inline where it first appears; this is the quick reference.

Foundational frame

Map — the customer-owned, portable representation of how their organization works: its data, its ontology, and the declarative definitions of its flows, rules, and integrations. What the customer keeps if they cancel.
Machinery — the Naviant-owned, rented parts that run the map: the execution engines, the user-facing shell, and the FDE labor. What goes dark on cancellation.
The seam — the differentiated part Cumulus builds rather than adopts: the shared model of what the business's data means (identity resolution, the ontology), the human-judgment layer, the governance brain, and the portability guarantee.
FDE — Forward Deployed Engineer; Naviant staff who author and co-manage a customer's Cumulus deployment.

The primitives (the nouns Cumulus thinks in; fully treated in Lens 2). Every primitive is a portable definition in the map; where it needs an engine to enact it, that engine is separate machinery.

Entity — a canonical business thing (a Customer, an Invoice); a node in the knowledge graph with identity and relationships. [AMENDED 2026-09-16] Formerly Entity. One name, the one the code and the console already use; every clause below reads Entity where it once read Entity.
Link — a relationship between Entities: semantic ("BilledBy"), structural ("filedUnder"), or cross-system identity (a typed identity Link: "this ERP id is that canonical Customer," carrying confidence and reversal info).
Action — a single, deliberate, guarded change committed through the Action Gateway (e.g. "approve this invoice"). Each Action is a defined, typed mutation.
Event — an immutable record that something happened (the fact, emitted when state changes).
Trigger — a declarative binding: "when Event X occurs, start response Y." Engine-managed causation. (Distinct from a Rule — see below.)
Rule — a declarative check: a pure predicate that returns a verdict (e.g. "is the amount over the threshold?"). Transparent and governed; used for logic and governance decisions.
Flow — the one kind of authored logic: durable, calls models via ModelCall, branches, retries, and may pause for a human. A Flow may run per case (one invoice, one deal) or over a window (keep the vendor list in agreement with the ERP); which it is, is read off its Trigger, never declared as a type.
Mapping — the declarative binding configuration a Flow reads: how an external source's fields and identifiers bind to the ontology, plus identity keys and freshness policy. Config, not logic.
Dataset — a container of content the customer owns (a table, a document's bytes, embeddings, a prompt). Distinct from structure, which lives in the graph. Carries a Role: data or knowledge.
Principal — an actor with identity: a human or an agent. (A person may also be a business Entity; that is separate from their Principal/account identity.)
Role — a named role, of a kind: platform (customer-admin, customer-user, naviant-engineer…), business (AP-clerk, controller…), or workspace (scoped to one Workspace instance). A Role confers both permissions (access) and queue-bindings (work assignment).
Queue — a named work destination Principals bind to. Tasks route to a Queue; every bound Principal sees them in their personal "My Tasks."
Task — a unit of human judgment: an agent proposes, a person decides, the system resumes.
Workspace — a case-centric interface ("Seeing Space") for how a workstream gets done (e.g. a Deal Room). Perpendicular to Tasks: you can watch how work runs without being assigned to it.

Governed operations, categories, and machinery (not primitives)

ModelCall — a governed operation within a Flow: an LLM invocation, audited and cost-tracked (which model ran, inputs, captured output). Not an "Agent" primitive — the Flow is the agent.
Governed Knowledge — a category, not a primitive: the declarative middle between raw data and machinery code — prompts, model configs, thresholds-as-values, Rules. Lives in the map (portable, versioned, editable in the shell). Customer-editable configs are governed knowledge, not application code — which is what makes them safe.
Surface — a delivery channel (the Cumulus shell, Microsoft Teams, Outlook, an MCP client), not a work artifact. Belongs to Interface & Consumption (Lens 6).
Transport — sourced infrastructure that carries data (a stream service, a file drop, a change feed). Cumulus is aware of it as a registered endpoint but does not build or maintain it.
Sync / Connector — machinery and machinery-config: Sync is an execution pattern (an inbound Flow + an outbound Flow + echo suppression + a reconciliation policy); a Connector is a standing integration handle. Neither is a primitive.
Action Gateway — the Cumulus-built control point every decision write passes through: validates, permission-checks, records to the ledger. Fully treated in the Governance spine.
The three write regimes — every write to the core is an observation (recording what an external system reported — audited via lineage), a decision (a deliberate change — through the Action Gateway), or a resolution (an automatic choice among competing claims by a Rule — automatic like an observation, audited like a decision).
Curation ledger — the permanent, append-only record of every human decision and every automatic knowledge change; the customer's compounding record of judgment.
Ontology — the customer's canonical model: the Entity types, Link types, and vocabulary agents reason over.
Framing · AI-Native Is a Discipline, Not a Feature

This section establishes why Cumulus exists and what kind of thing it is. It is the foundation the eight lenses each view one face of.

The claim

An AI-native organization is not one that uses AI. It is one whose knowledge, data, and processes have been brought onto a foundation solid enough that AI can safely act on them. Becoming AI-native is therefore not primarily an AI project — it is the work of applying a stack of well-established engineering disciplines to your own organization, with AI as the final layer that pays off once the foundation exists.

The evidence is the design process itself. Nearly every hard problem in building Cumulus turned out to be a classical engineering problem, not an AI problem: change-data-capture (detecting and streaming changes out of a source system) done idempotently (so re-processing the same change is safe); content hashing (fingerprinting data to prove a copy still matches its source); reconstructing state by replaying an event log; crypto-shredding (making data unrecoverable by destroying its key, to honor deletion requests). None is solved by a better language model. The AI is the last mile; the disciplines are the road that must exist first.

The discipline stack — each layer depends on the one below it
   AI / agents            <- the last mile. Powerful, and the EASIEST part.
   ------------------------------------------------------------------
   HCI / interaction      <- so humans can supply judgment in seconds
   durable execution      <- so work can pause for days and wait on humans
   governance / security  <- so it is safe to let anything act at all
   ontology / knowledge   <- so there is a correct model to act on
   integrity engineering  <- so the model provably matches reality
   distributed systems    <- so state stays correct across many systems
   data engineering       <- so the underlying data is trustworthy at all

Diagram: the capabilities Cumulus is built from, stacked so each depends on the ones beneath it; AI sits on top and is the smallest layer.

Most companies selling "AI" start at the top and skip everything beneath it — a chat box bolted onto a dashboard. Cumulus is mostly the bottom six layers, with AI riding on top. That inversion — investing in the foundation rather than the chat box — is the product. Three disciplines are the durable advantage precisely because a chat-box competitor never attempts them: distributed systems (keeping data correct across many systems), integrity engineering (proving your copy matches reality), and ontology engineering (modeling what the business's data means). Anyone can call a language model; almost no one does reliable two-way synchronization with verified provenance into a customer-owned model of the business.

A second view — two mature planes and a seam
   READ PLANE  — "get the world's state into a trustworthy core"
     data engineering: change-capture, event logs, loading/transforming data
     -> OBSERVATIONS (records of what external systems say) flow IN
                        ( THE SEAM — the differentiated part )
   WRITE PLANE — "let agents and people act on systems safely and auditably"
     enterprise integration: validated, permissioned changes to systems of record
     -> DECISIONS (deliberate changes Cumulus commits) flow OUT

Diagram: data flows in as "observations" through a data-engineering layer and out as "decisions" through an integration layer; the valuable, hard-to-copy part is the "seam" between them. ("Observation" and "decision" are two of the three write regimes, defined precisely in Lens 2.)

Roughly 60% of Cumulus is these two mature disciplines — reliable ingestion (the read plane) and safe, audited action (the write plane) — both well-solved, so Cumulus adopts rather than reinvents them. The other ~40% is the part no competitor builds and where value concentrates: a shared model of what the business's data means (so an update from the ERP and an action to Salesforce refer to the same real-world customer — this is master-data management, "MDM"), the human-judgment layer, and the guarantee the customer can walk away with their map. The stance: adopt the commodity 60%; build the 40% that is the moat.

Why the environment matters — surface reduction

Today's digital workplace is fractured: work happens across dozens of tools, and some of it happens only in the gaps between them — in a conversation between two people that never lands in any system. An AI-native (or "agentic") enterprise is one where agents are woven into every corner, doing the background lifting so people spend their time on higher-value judgment. The practical goal is surface reduction — collapsing a sprawl of tools toward a small set (for example {Outlook, Teams, Claude Desktop, Cumulus}) that interoperate, where work can be executed in any of those channels but is driven by Cumulus underneath. This matters because tool and surface proliferation carries a real, compounding tax — context-switching, fractured state, work that falls through the cracks — and reducing it is a large part of the value.

Why this belongs in the customer conversation

Cumulus is not selling AI; it is selling the discipline, delivered as a co-managed partnership, with AI as the payoff once the foundation exists. Co-management by FDEs (Forward Deployed Engineers — Naviant staff who author and run a deployment) is not a services footnote: the nine disciplines are genuinely hard, and the FDE model is how a customer acquires competence in distributed systems, integrity, and ontology engineering without building all nine in-house. The discipline is the deliverable; AI-native is the result.

A note on safety, stated up front

Because acting on an enterprise's systems is only acceptable if it is safe and auditable, a set of governance rules runs through every part of Cumulus — no un-audited changes, a complete replayable history, and human oversight wherever judgment is required. These are foundational, not an add-on; they are stated in full as the Governance spine at the end, placed there so the pieces they govern are already introduced. A reader whose first question is "why is this safe enough to let it act?" can jump straight there.

Lens 1 · Ownership & Value

"Who keeps what on cancellation — and why isn't this lock-in?"

Core model — Map vs. Machinery
   THE MAP  -- customer-owned · portable · only accumulates ----------
   entities · ontology · links · data · event history · curation ledger
   + the DEFINITIONS of flows, actions, rules, mappings, configs
                              (runs on)
   THE MACHINERY -- Naviant-owned · rented · goes dark on cancellation
   engines (the car) · experience shell (the dashboard) · FDE co-management (the driver)

Diagram: the customer owns a portable "map" of how their organization works; Naviant rents the "machinery" that runs it. Cancel, and they keep the map and lose the machinery.

Naviant is the dashboard, the engines, and the driver. The customer owns the roads and the destination. They rent the car; they never rent the map.

Doctrine
The customer accumulates a portable map of how their organization works. It only grows, and it is always theirs. This — not "you own your data" — is the proposition. Owning your data is table stakes; owning how the work runs is the moat.
Naviant's product is the machinery, and it is excellent precisely because there is no lock-in. The company wins on the quality of the engine and the driver, not on hostage-taking.
Portability is a gradient — three tiers: Tier 1, pure assets (data, graph, event history, curation ledger) — fully portable, just data; Tier 2, the blueprint (declarative definitions of flows, actions, rules, mappings, configs) — portable as specification, needs an engine, not necessarily Cumulus's; Tier 3, machinery (runtimes, shell, FDE labor) — rented.
The crown jewel is the curation ledger — accumulated human judgment; the customer's compounding institutional asset and their dataset for evaluating and tuning models.
Source data exports as bytes; derived data exports as recipes. An ingested Dataset exports as its content; a computed Dataset (embeddings, an aggregate) exports as the recipe to regenerate it — source plus transformation. A vector index is the classic machinery-output-masquerading-as-asset: the portable truth is the source plus the embedding recipe, not the index.
The transparency bar [LOCKED]

The bar the map must clear is transparency, not re-executability:

The map must give the customer enough to understand how their organization works — the structure and intent of its flows, the meaning of its rules — in human-readable form. It need not be a turnkey, runnable specification, and it need not include implementation code. What is prohibited is the map being opaque — a pointer revealing nothing, like "this rule = Naviant internal function #47." A plain-language description of what a rule does is transparent even though it is not runnable.

Two consequences: structured where cheap and valuable; text where structure would be forced — a precedence list, a threshold, or a flow's shape are captured as data (transparent and engine-usable), while genuinely complex logic is captured as a faithful description (implementation stays Naviant's). And this resolves the ownership-vs-IP tension — "the customer owns how their organization works" (transparency) is compatible with "Naviant owns the implementation," precisely because transparency needs no runnable code; the stronger bar would have forced giving away implementation.

The Forgetting qualification — replay vs. the right to be forgotten [PROPOSED]

A permanent, immutable history (which lets the customer rebuild state by replay) appears to conflict with a person's right to erasure. Reconciliation — crypto-shredding: personal data is stored encrypted with a per-person key; forgetting destroys the key; the history's structure survives, the personal content becomes an unreadable tombstone; replay still runs. The guarantee is then honestly stated as "the state can be rebuilt from history except for cryptographically-forgotten fields." [OPEN] sub-decisions: key granularity (per-person keys require tagging whose data each event holds, at write time); the personal-vs-structural cut line, per field in the Mapping; the derived-data tax (forgetting a source key does not auto-purge derivations, so derivations must be person-tagged and regenerable); and the curation ledger itself contains personal data and is not exempt. Caveat: crypto-shredding is the accepted answer, not magic — pre-shred backups and anywhere data escaped the envelope need their own handling.

Invariant

Every Tier-2 definition is authored and stored in an engine-agnostic, transparent form, separable from the machinery that runs it. No opaque steps whose logic cannot be inspected; no reference to data only Naviant's engine can resolve. (One face of the Governance spine.)

Common failure mode

Fusing the table-stakes claim ("you own your data") with the moat ("you own how your organization works"). Second: machinery masquerading as map — a model-specific index or an opaque step called a portable asset. The test is the Cancellation Test: could a competent team understand and reconstruct how the organization works from this artifact alone? (Understand and reconstruct — not necessarily lift-and-shift and run.)

Open
[OPEN] the four Forgetting sub-decisions.
[OPEN] whether to commit to the "rebuild from history" guarantee as a hard contractual promise (it constrains Event design — see the Governance spine).
Lens 2 · The Primitives

"What are the nouns Cumulus thinks in?" — the vocabulary every later lens relies on.

Core model — a small vocabulary, each with an enacting engine

The primitives group loosely by role (below), but the grouping is a reading aid, not a strict taxonomy — the load-bearing fact about each primitive is not its group but that it is a portable definition in the map, enacted (where it needs enacting) by a separate engine that is machinery. That "definition → engine" split is the deep structure.

   ONTOLOGY (what things are, and what happens to them)
     Entity · Link · Action · Event

   CONNECTIVE (a fact, and a check)          <- two DIFFERENT kinds of thing:
     Trigger  = a binding (Event -> response; engine-managed causation)
     Rule     = a check (a pure predicate returning a verdict)

   PROCESS & DATA (authored logic + its config + its content)
     Flow      = authored logic (a case or a window; may wait on a human)
     Mapping   = the declarative binding-config a Flow reads
     Dataset   = the content (data or knowledge)

   IDENTITY, ACCESS & WORK (who acts, what they may do, where work goes)
     Principal · Role · Queue

   EXPERIENCE (how humans engage)
     Task · Workspace

Diagram: the fixed set of primitives, grouped loosely by role. Trigger and Rule are shown together but are deliberately unlike each other — one is causation-wiring, the other a check.

Each primitive, with the engine that enacts it (engines are machinery, never primitives):

   PRIMITIVE (portable definition)     ENACTED BY (machinery)
   Entity / Link                       the graph store
   Action                              the Action Gateway
   Event                               the event log / bus
   Trigger                             the Dispatcher (evaluates it, pre-response)
   Rule                                a generic rule interpreter (evaluated in-flight)
   Flow                                the durable-execution engine (or the
                                       stream/throughput engine, where volume
                                       demands it — Lens 8)
   Mapping                             (read by a Flow — no engine of its own)
   Dataset                             the storage adapter for its access-shape

This annotation prevents two classic confusions: it shows that Sync is not a missing primitive — it is simply the engine (and pattern) that enacts data-movement, exactly as "the flow engine" enacts a Flow and neither engine is a primitive; and it shows why Trigger and Rule are not the same kind of thing — a Trigger is enacted by heavy infrastructure (the Dispatcher, the event bus), a Rule by a light interpreter.

Doctrine
The vocabulary is fixed and small (above). Links carry three kinds of relationship: semantic ("BilledBy"), structural ("filedUnder"), and cross-system identity. [AMENDED 2026-09-16] A Link is declared as a type of its own, with a source, a target, a cardinality and a name from the other side; most carry no properties, and some do — confidence and method on an identity Link, a proficiency on a skill — because a property of a Link is a fact about the edge. A relationship that someone adjudicates, or that other things point at, is an Entity with two Links, not a Link with many properties. A part — an invoice line, an order line — is neither an Entity nor a Link: it has no identity of its own, is held inside its whole, and is written and loaded with it. A structural Link joins two Entities that each have identity (a document filed under a folder); composition is declared on the whole as its parts. A part that anything else would point at, or that has a life of its own, is an Entity.
Trigger and Rule are firmly distinct — not variations on a theme. [LOCKED] A Trigger is a binding — "when Event X happens, start response Y" — engine-managed causation that kicks off real work. A Rule is a check — a pure predicate that returns a verdict ("amount over threshold?"), the unit of governance, transparency, and business logic. One is causation; the other is logic. They sit near each other only because both are declarative connective tissue between a fact and a response; they are otherwise unalike.
A Rule is a pure check, transparent and governed — but its implementation is reserved. [LOCKED] The execution boundary holds: a Rule has no side effects, no fetching, no orchestration, no waiting — it evaluates inputs handed to it and returns a verdict. If it needs to fetch, act, or orchestrate, it graduates to a Flow. But the earlier "never free-form code" constraint is dropped: a Rule must be transparent and governed (its meaning inspectable and reportable), yet how it is implemented is an implementation choice we reserve — the same transparency-not-mechanism bar as the rest of the map. Rules never fetch: the caller gathers inputs and hands them in. Starter rule-types: precedence-list, threshold, match-condition, range/set, cross-reference (data handed in), accumulation — an extensible core, admissible only if the generic interpreter can evaluate them.
One kind of authored logic: the Flow. [LOCKED] A Flow is durable and has one set of powers — call models via ModelCall, branch, retry, and raise a human Task with suspend/resume. Flows differ in shape, not in type. Some run once per case ("process this one invoice"); some run over a window ("keep the vendor list in agreement with the ERP"). That difference is real and load-bearing — it decides how a Flow writes and how it escalates (next point) — but it is read off the Flow's own Trigger and its writes, never declared as a separate primitive. This retires the earlier Workflow/DataFlow split, which was right about the physics and wrong about the level: putting a behavioural difference in the type system bought arguments about which noun a definition was, rather than better defaults. The tell that used to pick a noun now picks a shape, and the shape is derivable, so nothing has to be classified by hand.
Windowed Flows land bulk observations and escalate on aggregates; per-case Flows commit per case. [LOCKED] A Flow woken by a windowing Event — a timer (from the Scheduling capability) or a count/threshold crossing, through the same Event → Trigger mechanism as everything else — is processing a batch: it lands bulk observations (per-revision lineage, never one-Action-per-row) and emits summarizing Events and raises Tasks on exceptions or aggregates, never per-record, so the reactive plane stays off the hot path. A Flow woken by a business Event is processing one case and commits per-case decisions through the Action Gateway. A Flow may also be invoked synchronously (request → response, e.g. hand back a signed upload URL). These are defaults attached to a Flow's trigger shape, not to its type.
"Mapping" is only the binding-config, not the logic. [LOCKED] [PROPOSED 2026-09-16: Mapping is governed knowledge, not a primitive. It does no work of its own; a Flow reads it, exactly as a Flow reads a model configuration or a metric definition. Declaring it beside the landing step that reads it, versioned by content, keeps its transparency and portability and removes a primitive that only ever waited to be read. The locked text below stands as the definition of what a Mapping says.] A Mapping declares how a source's fields and identifiers bind to the ontology, plus identity keys and freshness policy — portable, customer-owned configuration a Flow reads. The transform logic (parse, embed, derive) is the Flow, not the Mapping. (This resolves the earlier "Mapping is too small a word" confusion: it was too small because it was being asked to carry the logic; now the Flow carries logic and the Mapping carries binding.)
A Dataset is one primitive with independent properties, holding content only. [LOCKED] Properties: Kind (document, tabular, record, media, graph, vector, prompt — [AMENDED 2026-09-15] record added: a semi-structured item as it was delivered, with provenance, the shape most APIs return and the one an email, a CRM contact or a webhook payload has; a configuration table or a threshold list is a record by shape, so no separate config Kind is added); Role (data — delete it and history is lost; vs. knowledge — delete it and the agents get dumber); two independent dials — update mode (snapshot / append / upsert) and keep-history (yes/no); Custody (core-owned vs. referenced-in-place — Lens 5); Source vs. Derived (point 8); and, because a Dataset is the unit governance hangs off, the governance dials — Retention (a duration applied at write, or indefinite; purge is machinery), Legal hold, and Read access (which Roles may read the set: the set-level attachment point for the Governance spine's read permissions, refined per item through the graph). [AMENDED 2026-09-08] The list once stopped at Source vs. Derived, and a reader applying it placed invoices and legal contracts in one document Dataset because no listed dial differed between them. These three are the dials that do differ, and they decide where one set ends and the next begins. Datasets are therefore plural by design: one per set whose governance differs, however many that makes — a table is a Dataset, a governed set of documents is a Dataset, and "all documents" is not. [OPEN 2026-09-14] Whether Role is a dial at all. The distinction it carries, curated knowledge against data, already lives in the graph: what makes a policy document knowledge is that an object vouches for it and a ModelCall reads it, both observable facts, and no engine reads a declared Role. Until decided, no Dataset declares one; if the estate view needs "agents read this set", it derives that from the model-call ledger. [PROPOSED 2026-09-16] Content enters a governed Dataset only by a Flow's write, with the run and step recorded. What arrives from outside lands first in a staging Dataset that says nothing about it but that it arrived, held by the runs it was handed to and released when they complete; the person or system that uploaded it never chooses where it belongs. Crucially, a Dataset is owned map content, not a universal storage bucket — machinery/operational state (flow run journals, the trigger index, queues) is not a Dataset. [AMENDED 2026-09-15] The curation ledger and the event history are not machinery: they are record Datasets, append-only, Tier 1 assets that export as records, which is how Lens 1 can call them the customer's and this clause can still exclude journals. Structure lives in the graph, not in Datasets: a folder is a folder Entity with filedUnder Links, not a Dataset. The "Member" concept is retired — there are only Entities, Links, and content Datasets.
Source and derived data are linked by a derivation record. [LOCKED] A derived Dataset records { from, fromVersion, recipe, producedBy, staleWhen }. This buys portability (export as source + recipe), freshness (source advances past the pin → staleWhen fires → regenerate), and provenance. Rule of thumb: sources are synced; derivatives are regenerated — never "sync a vector store."
A canonical field keeps its competing claims, not just the winner (for agent-acted fields). [LOCKED] { resolved, claims:[ERP $75k, CRM $50k, human $60k] }. An incoming synced value is a new claim evaluated against a per-field precedence policy, not a blind overwrite — so a bad precedence rule is correctable without re-syncing, and the choice stays auditable and reversible. [AMENDED 2026-09-16] A claim carries its source, its confidence, and the write that made it, and may point at the evidence it was read from — a file, a page, a region on the page — so a value resolves to something a person can look at, not only to a step in a journal. Confidence lives on claims and on identity Links, never on an Entity as a whole.
Every write to the core is one of three regimes — observation, decision, or resolution. [LOCKED] An observation records what an external system reported (Sync/Flow lands it; audited by per-revision lineage; bulk, not per-row). A decision deliberately commits a consequence (an Action through the Action Gateway; per-write permission checks). A resolution automatically picks a winner among competing claims by a declarative Rule (automatic like an observation, audited and escalatable like a decision — records which claim won and why, raises a Task if the Rule cannot decide). The classifying test is intent: "a system told us" is an observation; "we determined this should happen" is a decision.
Event, Trigger, and Rule are the connective tissue between "a fact happened" and "something responds." [LOCKED] An Event is the immutable fact. A Trigger binds it to a response and names a response-kind. A Rule is a check consulted by a response. (Trigger and Rule remain firmly distinct — point 2.)
The response-kind vocabulary is a closed set of three, cut by execution weight. [LOCKED] Flow (durable, multi-step, can pause — heavyweight); Action (a single guarded write — medium); Resolution (a lightweight in-memory rule evaluation that records its result or escalates — lightweight). Not response-kinds: a Task (always raised by a response, which must also handle its answer) and "notify/record" (that is just the Event in the log). Closed because it is cut by execution machinery, of which there are only these tiers.
Evaluation happens at two levels. [LOCKED] A Dispatcher evaluates Triggers — whether a response should start — before any response exists (a thing cannot decide whether it itself should begin). The running response evaluates its Rules in-flight, as reached.
A ModelCall is a governed operation, not an "Agent" primitive. [LOCKED] An LLM invocation inside a Flow is a first-class governed operation — audited and cost-tracked: which model ran, the inputs, the captured output/reasoning. [AMENDED 2026-09-16] The configuration a ModelCall runs under — prompt template, the schema its answer must fit, provider and its answer-changing settings — is a declared item of governed knowledge, versioned by the hash of its content and never migrated; every ModelCall record names the configuration and version that answered it, and every version that ever ran stays readable. There is deliberately no "Agent" primitive, because the Flow is the agent (adding "Agent" would collide with them). What must be governed is the model invocation — which models run in the environment, their cost, and their outputs — and that is the ModelCall.
Identity, access, and work distribution: Principal, Role, Queue. [LOCKED]
Principal — an actor with identity, human or agent (not "Human," because agents act too, and permissions/assignment apply to both). A person may also be a business Entity ("this consultant is staffed on that deal"), which is distinct from their Principal/account identity.
Role — a named role of a kind: platform (customer-admin, customer-leader, customer-user, naviant-engineer, naviant-support), business (AP-clerk, controller, deal-lead), or workspace (scoped to a specific Workspace instance). A Role confers both permissions (access) and queue-bindings (assignment). A Principal holds many roles at once; a role-assignment is a Link (Principal → Role), optionally scoped to a Workspace — which is how one person holds different roles across different workspaces.
Queue — a named work destination Principals bind to. A Task routes to a Queue; every bound Principal sees it in their personal "My Tasks." Binding is direct or conferred by a Role (holding the AP-clerk role binds you to the AP queue). Assigning to a Queue — rather than to a person — is what makes work survive absence, share across a team, and load-balance.
The Queue ↔ Workspace relationship is an optional many-to-many Link, not containment: org-wide queues have no Workspace; a watched Workspace may have no Queue; one Deal Workspace may have a Queue per deal; a shared Queue may serve many Workspaces.
Cross-system identity is a typed identity Link. [LOCKED] When a Flow decides "this external id is that canonical Entity," the result is a Link with required properties — not a new primitive, and not a thin plain Link: confidence (0–1), method (tax_id_exact / fuzzy_name / human_confirmed / …), decidedBy (a rule id or a Principal), reversibleWith (what to restore on un-merge), and standing (proposed / attested — [AMENDED 2026-09-15] the same standing every record carries; an exact-id match is attested by the rule that made it, a fuzzy match is proposed until a person stands behind it). It reuses existing machinery: a low-confidence match raises a Task (a human confirms); the decision is logged to the curation ledger; a low-confidence match sits at status=in_review and is status-gated (cannot be acted on until confirmed). This satisfies the governance requirement that high-blast-radius merges be reversible and provenance-carrying.
An in-progress Entity is real but status-gated. [LOCKED] A draft (an extracted-but-unapproved invoice) becomes a real graph Entity at creation, status = in_review — visible, queryable, renderable — but the Action Gateway refuses a consequential Action against it until someone stands behind it. Visible without being actionable. [AMENDED 2026-09-15] The word "approved" is retired from this clause: it is a business outcome, what accounts payable decides about an invoice, and a rejected invoice is a real record the business will not pay; both are properties the type declares and its actions set. What the gate reads is the record's standing, who stands behind it, and it carries no business meaning. Four values for every type: proposed (a Flow proposed it from what it read; nobody has stood behind it; owned by the run that wrote it), attested (a person, through an Action, or an authoritative source stood behind it), retracted (a person said this record should not exist), superseded (another record stands in its place). In code the field is named standing, never status, so it cannot be read as a business property; an Action declares only its effect on standing (attests, retracts, reopens, or none). No type adds to these values or writes them. [PROPOSED 2026-09-16] A proposed record is owned by the run that wrote it, or by the Flow when the Flow is windowed; another run's write to it is refused with the owner named, never queued, and a settled record cannot be re-proposed by a write at all. Ownership, not a lock: nothing waits.
Governed Knowledge is a category, realized by existing primitives — not a new primitive. [LOCKED] The declarative middle between raw data and machinery code — prompts, model configs, thresholds-as-values, Rules, and metric definitions — lives in the map (portable, versioned, editable in the shell). The doctrine point: a customer editing a prompt or threshold in the shell is editing governed knowledge in the map, not application code — which is what makes it safe (versioned, audited) rather than dangerous. It is realized by knowledge-role Datasets (Kind=prompt), small config Datasets, and Rules; no new primitive. [AMENDED 2026-09-16] A metric definition — which Entity type, which property, aggregated, grouped and filtered how — is governed knowledge, declared and versioned by content like a model configuration, and computed by a windowed Flow into derived claims or a derived Dataset. It is not a Rule: a Rule returns a verdict, a metric a number, and a Rule may use the number. "Knowledge-ness" (does it make agents smarter?) and "governed-ness" (is it transparent/versioned/audited?) are independent properties.
The ontology is graph-shaped; it does not all live in a graph database. [LOCKED] The domain model is Entities + Links + traversal (the logical graph). But physical storage is mixed, per access-shape: run state lives in the durable-execution engine's store; the active-trigger index lives in a fast key-value store (for O(1) dispatch); Dataset content lives in tabular / blob / vector stores by shape; the event log lives in a stream. The graph is the referencing spine that ties these together — not the universal container. Forcing everything (run journals, the dispatch index) into a graph database would be the wrong tool and would break the adapter boundaries.
Everything is authored in Python and exported to the map by decorators. [LOCKED] FDEs author the primitives as Python under full lifecycle discipline (version control, review, CI/CD); decorators export each one's declarative meaning into customer-owned map storage, and the engines interpret it. The governing invariant: the export conveys complete declarative meaning — transparent, structured where valuable and described in text where structure would be forced — and never a pointer back into Naviant code (which would hollow the map). It need not be re-executable; it must never be opaque.
Invariant

Every mutation is a decision (an Action through the Gateway), an observation (a Flow/Sync write audited by lineage), or a resolution (an audited automatic choice); every human decision is a Task routed through a Queue. No primitive does another's job — no un-audited writes, no bulk data forced through per-row Actions, no hierarchy hidden in a container instead of the graph. Rules are pure checks and never fetch. ModelCalls are governed. The ontology is Cumulus's model; storage shape never leaks up. (One face of the Governance spine.)

Common failure mode

Reaching for a missing primitive that is really an engine or a pattern — "Sync should be a primitive" (it is the engine/pattern that enacts a Mapping, like the flow engine enacts a Flow). Conflating Trigger and Rule (causation vs. check). Making a Dataset the universal storage bucket (it is owned map content; machinery state is not a Dataset). An "Agent" primitive (the Flow is the agent; govern the ModelCall instead). A rule that is really code (it fetches or orchestrates → a Flow). A storage adapter's data model becoming the ontology.

Open
[OPEN] whether access-role and assignment-role ever need to diverge into separate concepts (for now, one Role confers both).
[OPEN] the threshold for keeping competing claims vs. storing only the winner, per field.
[PARKED] full bitemporal history; where "agent drops by your desk" ambient context attaches; the implementation of the authoring/execution model (replay-based execution with journaled steps; dual rendering of flow structure — per-run trace plus design-time diagram; step configuration read from map-resident knowledge; SDK ergonomics) — deferred to a dedicated implementation-design session.
Lens 3 · Architectural Layers

"What are the major parts, and how do they stack?"

Core model — three layers, one membrane
   EXPERIENCE LAYER  (Naviant-built shell · Workspaces · Task Queue · surfaces)
        |
   ===== THE GOVERNED CONTRACT  (command + observe) =================
        |         the membrane — nothing crosses it by a privileged path
   EXECUTION LAYER   (Flow engine · Action Gateway · Dispatcher)
        |  writes only through Actions / Flow · reads only through query / retrieve
   KNOWLEDGE & DATA LAYER  (ontology · graph · datasets · event & curation history)

Diagram: three layers — the user-facing Experience layer, the compute Execution layer (the Flow engine), and the customer-owned Knowledge/Data layer — separated by one governed contract everything must pass through.

Doctrine
Three layers: Experience, Execution, Knowledge/Data. The Execution layer holds the Flow engine — durable execution, with a stream/throughput engine beside it where volume demands one (Lens 8) — plus the Action Gateway and the Dispatcher.
The contract is the membrane, no exceptions. Even Naviant's shell reaches Execution only through the same contract an outside system uses; it holds no shortcut and never touches storage directly.
The contract runs two directions — command and observe. Command is request/response; observe is subscribe/stream. A live Seeing Space cannot be built on polling, so observe is a first-class half.
Execution operates on Knowledge but does not own it. Compute is Naviant IP; state is the customer's. Adjacency is not ownership (the seam with Lens 1).
Invariant

No layer reaches a lower layer except through the governed contract. The shell never reads storage directly; no change reaches a system of record except through the Action Gateway. Any capability the shell has, an ambient surface can also express. (One face of the Governance spine.)

Common failure mode

Confusing the Knowledge layer (architecture) with customer ownership (Lens 1). Second: a shell direct-read "just for latency" — the read-side twin of the un-audited write.

Open
[OPEN] whether reading/reasoning shares the Execution contract or gets a sibling query API.
[OPEN] a materialized read-model for heavy interface reads — only as a contract capability, never a shell-to-storage line.
[LOCKED] interface-local state (layout, preferences, drafts) never leaks into the ontology.
Lens 4 · Work & Interaction Model

"How does work actually get done, and by whom?"

Core model — two layers of logic, and human judgment as first-class
   AUTHORED LOGIC         Flow      — a case or a window; may wait on a human
        it can: call models (ModelCall) · branch · retry · RAISE A TASK (suspend/resume)
        ------------------------------------------------------------------------
   Task       a human supplies judgment (the point of the interaction)
   Queue      where Tasks land; Principals bind to it; appear in their "My Tasks"
   Workspace  a Seeing Space for how a workstream gets done (perpendicular to Tasks)
   Event      the continuous stream others react to

Diagram: one authored-logic container (the Flow, per case or per window); work reaches humans as Tasks routed through Queues, and Workspaces give visibility into how a workstream runs.

Agents do the heavy lifting; humans step in only where judgment is the point — a short, high-context decision. Human-in-the-loop ("HITL") is a designed primitive, not an error path.

Doctrine
Work reduces to two perpendicular things for a person: Tasks and Workspaces. A Task is work you are asked to execute, served with full context (data plus agent synthesis). A Workspace is a Seeing Space for how a kind of work gets done — correlating, synthesizing, and reporting on a workstream — and it is impersonal: you can watch how a workstream runs without being assigned anything in it.
One kind of authored logic does the doing: the Flow (Lens 2). A business transaction is a per-case Flow; populating or maintaining the model is a windowed Flow — the same primitive, different trigger shape.
Human-in-the-loop is first-class and is the point. An agent proposes, a human decides in seconds with full context, the system resumes; the judgment lands in the curation ledger, never discarded.
A response pauses in place for a human and resumes in the same run. [LOCKED] When a Flow raises a Task it suspends — its state becomes a stored "waiting on a human" record, which costs nothing while it waits (no warm process) — and the same run resumes from exactly where it paused when the Task resolves. A new run is right only when the human's action starts an independent process. For an "always require approval before X" rule, prefer putting the gate on the Action itself (so it cannot be forgotten) over writing it into one flow.
Tasks route to Queues; Principals bind to Queues. [LOCKED] A Task is assigned to a Queue (a named work destination), and every Principal bound to that Queue — directly, or by holding a Role that confers the binding — sees it in their personal "My Tasks." Assigning to a Queue rather than a person is what gives shared team inboxes, coverage during absence, and load distribution; assigning straight to a person is the brittle special case.
Flows write to the knowledge layer — through governed writes. [LOCKED] Reaching the data/knowledge layer is a question of route, not of permission: a Flow writes there whenever the work requires it, but through an Action or an emitted Event, never a raw storage write. The direct-vs-decoupled rule: write directly (via an Action) when it is a deterministic in-scope part of the transaction (mark this invoice approved, set this resolved value); write via an Event (the decoupled flywheel) when it is a knowledge improvement that may need policy or curation (an unknown vendor that might graduate to known), because promotion to shared canonical knowledge is a governed decision, not an inline side effect.
The flywheel is a concrete mechanism. [LOCKED] A human correction flows back decoupled, via an Event (e.g. ExtractionCorrected), not inline — so the business path stays lean. Two distinct knowledge assets, not one blob: the model config (prompt/params) and the correction set (accumulated raw→corrected pairs — the crown-jewel eval/tuning dataset). [AMENDED 2026-08-30] This clause named a third, the exemplar (a one-shot per-vendor template), and the accounts-payable flow retired it: an extra call to name the vendor and a per-vendor template to extract against were two mechanisms doing what one governed prompt does, and keeping them in step with each other was pure cost. The doctrine follows the decision the code already made. Write-back rule: always append the correction to the correction set automatically (and log it); promotion into canonical knowledge (the model config) is never automatic by default — a declarative Rule decides per situation whether to auto-promote (e.g. low-stakes vendor, after N consistent corrections) or raise a curation Task; and every knowledge change, automatic or human, is logged. This closes the unknown-vendor loop: the first invoice is best-effort plus human confirmation, which seeds the correction set and a curated profile, so the second invoice is the known, one-shot path — the vendor graduates from unknown to known through use.
Reconciliation is a Task-rooted audit tree. [LOCKED] A discrepancy raises a Task; the human's choice is a ledger decision; each correction pushed to a wrong system is an Action tagged caused by that Task. A recurring discrepancy re-elevates with its history attached ("third time; you set $75k, it keeps reverting"), turning a nagging repeat into a systemic signal.
Failure escalates to a human; it does not silently roll back. [LOCKED] Transient failures retry safely (a completed step, on retry, does nothing rather than duplicating); a persistent failure raises a Task with full context ("posted to two of three systems; the third rejected it"). Rollback is not assumed — systems of record often cannot be un-written; true undo is offered only where an Action has a genuine safe inverse. (Governance spine.)
Invariant

A human is invoked only where judgment is the point — never for deterministic work. Every human decision commits through a guarded Action and lands in the curation ledger. When in doubt about a consequential change, propose it as a Task rather than committing automatically. (One face of the Governance spine.)

Common failure mode

Treating HITL as an exception path — drowning people in low-value confirmations (no confidence gating) or letting agents auto-commit decisions that needed judgment. The discriminator is confidence and blast radius, not convenience.

Open
[OPEN] confidence / blast-radius thresholds for auto-commit vs. propose-a-Task.
[OPEN] event-driven vs. batch vs. hybrid timing for flywheel enrichment.
[OPEN] the re-elevation loop-vs-escalate policy.
Lens 5 · Deployment & Custody

"Where does it run, and where do the assets physically live — and what carries the data?"

Core model — one unified map, distributed custody, sourced transport
   THE MAP -- always core-resident · unified · customer-owned
   + for every asset: WHERE it lives and HOW to reach it (a governed reference)
        |  references point outward — custody varies per asset
   Cumulus's store  |  the customer's cloud (VPC, CMEK)  |  an external system of record
        ^
        |  data is CARRIED in/out by TRANSPORT (sourced, not built):
        |  stream services · file drops · change feeds · managed sinks

Diagram: the map (what exists, what it means, where each asset is) stays unified in the core; the actual bytes of any asset may live anywhere, reached through a governed reference; and the infrastructure that carries data in and out is sourced from managed services, not built.

Doctrine
Custody is per-asset; the map is monolithic and core-resident. The bytes of one asset can live externally while another lives in Cumulus's store, but the knowledge of what they are and how they relate stays unified.
We copy some data in and reference other data in place — a per-source (even per-dataset) decision. [LOCKED] Ingest-and-own (copy in) when the source is slow, fragile, rate-limited, or must be reasoned over in bulk — e.g. sync a slow old ERP's customer master every few hours. Reference-in-place (read-through) when the source is performant and authoritative — e.g. query Snowflake live. The decision criteria: source performance, freshness need, query cost/limits, and whether bulk reasoning is required. Critically, the map looks the same either way — the ontology says "this field exists and means this"; copy-vs-reference is a custody property underneath, so business logic never knows or cares, and the decision can change later without touching any Flow.
Transport is sourced machinery, not a primitive and not something we build. [LOCKED] The infrastructure that carries data — a stream service (Kinesis/Kafka/Pub-Sub), a file drop (S3), a change feed (Debezium/DMS), a managed sink (BigQuery) — is sourced from managed services. Cumulus is aware of it as a registered endpoint but does not build, maintain, or regulate it; high volume is Transport's problem. The rule: Transport carries; a Flow processes. A Flow enters only when there is authored logic to run; raw high-volume landing (IoT straight into BigQuery) is Transport plus a referenced Dataset, with no Flow in the hot path.
MDM, CDC, and sync are not separate mechanisms — they are Flows (plus sourced transport). [LOCKED] CDC (change-data-capture) is a sourced change-capture adapter (Transport) feeding an inbound landing Flow. Sync is a pattern — an inbound Flow plus an outbound Flow plus automatic echo suppression plus a reconciliation policy — not an atomic thing. MDM (master-data management) is emergent from primitives already defined: canonical Entities, cross-system identity Links (typed, with confidence and reversal — Lens 2), and claim reconciliation (retained claims plus Resolution plus a precedence Rule). None requires a new primitive; each is Flow logic doing a different job.
The deployment target is the customer's choice — Cumulus's instance, or the customer's own cloud (bring-your-own-cloud, customer-managed encryption keys, and where required a zero-data-retention model configuration).
Integrity is a custody concern. [LOCKED] Every stored revision carries a sourceVersion (the source's own version — its claim) and, where warranted, a contentHash (Cumulus's independent verification). Disagreement is a signal that raises an Event and possibly a Task. Hashing is optional and cost-tiered (large media trusts the source version; small high-trust text hashes eagerly); the verification policy rides in the Mapping.
The reference (storageRef) lives at the revision grain and encodes a portable resolution path — system, protocol, and the customer's own credentials — even when Cumulus resolves via a faster internal path.
Invariant

Every reference resolves with credentials and protocols the customer owns — never a vault only Naviant's engine can open. Bytes flow directly between the store and whoever needs them, never through the API tier. Transport is sourced; the map is owned. (One face of the Governance spine.)

Common failure mode

The hollow reference — a pointer only Cumulus's engine can resolve, so on cancellation the customer holds a map to a vault only Naviant can open. Second: a model-specific derivative treated as a portable asset when the portable truth is source plus recipe. Third: building transport — running your own always-on high-volume ingest instead of sourcing the stream endpoint (the "needs to stay warm" reflex, Lens 8).

Open
[OPEN] ingest-and-own vs. reference-in-place as a deliberate per-asset-class policy.
[OPEN] a derived-artifact policy (keeping text, thumbnail, and embeddings beside a referenced original so a Workspace can preview and reason when the source is briefly unreachable).
Lens 6 · Interface & Consumption

"How does the outside world touch the system — and through which channels does work reach people?"

Core model — one contract, three programmatic faces, delivered across surfaces
        SDK               MCP               raw REST
     (typed code)     (LLM agents)      (any runtime)
        \----------------+----------------/
                         |
        ONE curated contract (schemas · auth · permissions · Action Gateway · audit)
             command (request / response)  +  observe (subscribe / stream)
                         |
        delivered to people across SURFACES (channels, not work artifacts):
        Cumulus shell · Microsoft Teams · Outlook · Claude Desktop (MCP)

Diagram: three programmatic ways to reach the system (REST, SDK, MCP) are projections of one governed contract; and the work itself is delivered to people across a small set of surfaces — the reduced tool-set — all driven by Cumulus underneath.

Doctrine
One core, three programmatic faces. REST is the complete surface for software; the SDK is a typed convenience-and-correctness layer over REST, for code; MCP (the Model Context Protocol, by which an AI agent calls tools) is a curated, deliberately narrowed face for agents acting on a person's behalf.
A Surface is a delivery channel, not a work artifact. [LOCKED] Task and Workspace are the work (the "what"); a Surface — the Cumulus shell, Teams, Outlook, Claude Desktop — is the where, the channel through which a Task or Workspace reaches a person. A single Task may appear on multiple surfaces. Surfaces belong here, alongside REST/SDK/MCP, because they are a reach concern. This is where the surface-reduction goal (Framing) lands operationally: reduce the employee's environment toward a small interoperating set of surfaces, let work be executed in any of them, but keep it driven by Cumulus.
MCP is an ambient surface, not a generic developer API. Scoped to the connecting user (an agent reaches only what that user may reach) and HITL-mediated: reads and reasoning are ideal MCP tools, but an MCP write proposes (a low-risk guarded Action, or a Task) rather than committing directly.
The SDK is two artifacts. A consumer SDK (customer-facing, over the public contract) and an authoring/kernel SDK (Naviant-owned, for FDEs to define what the engines run). Different owners, different stability guarantees — never conflated.
The shell is just another consumer — over the consumer SDK, the same projection everything else uses.
Invariant

Every face and every surface goes through the one contract, the one Action Gateway, the one audit path. No face has a capability the core does not govern; MCP never gets its own write path. (One face of the Governance spine.)

Common failure mode

Auto-generating MCP tools one-for-one from the REST spec (wrecks agent legibility, exposes destructive operations, provides no composites). Treating the two SDKs as one package. Treating a Surface as a kind of Task/Workspace rather than a channel they are delivered through.

Open
[OPEN] whether an MCP write may commit a low-risk Action or must always raise a Task.
[LOCKED] build order — core plus REST first; MCP a thin projection once the contract is stable.
[OPEN] the synchronous-vs-asynchronous shape of a flow trigger.
Lens 7 · Delivery & Operating Model

"Who builds and runs it, and what is the partnership?"

Core model — engine plus driver, across a five-phase lifecycle
   Naviant FDEs (the driver) -- author · deploy · co-manage -->
   BUILD  ->  RUN  ->  SCALE  ->  GOVERN  ->  PROVE
   model     human-    curate     control    quantify
   the       in-the-   knowledge  risk       the ROI
   domain    loop ops
   the customer owns the map (Lens 1) · Naviant runs the machinery

Diagram: Cumulus is delivered as a co-managed partnership; FDEs author and operate it across five phases, while the customer owns the resulting map.

Doctrine
Co-managed, not self-serve. FDEs manage infrastructure, token/cost economics, and ongoing optimization under SLAs. The engine-plus-driver model is definitional.
The FDE model exists because the nine disciplines are hard. [LOCKED] It is how a customer acquires distributed-systems, integrity, and ontology competence without building all nine in-house.
FDEs author the Tier-2 definitions; the customer owns them. "Naviant builds it" (labor) and "the customer owns the configuration" (asset) are both true. Definitions — Flows, Mappings, Rules, configs — are version-controlled, CI/CD-shipped, and must be transparent and portable (Lens 1). [PROPOSED 2026-09-16] Three things change under a run that is waiting on a person, and each is versioned its own way. A run pins the Flow version it started on, which stays deployable; it is resumed on that version, or restarted on the live one with the old run superseded. The ontology carries a version, every write stamps the version it wrote under, an additive change needs nothing, and a breaking change ships with a migration Flow that rewrites shape and never standing or meaning, refused at startup without one. Governed knowledge is versioned by the hash of its content: a changed prompt is a new version, never a migration.
Value across five phases: Build (model the domain), Run (HITL operations), Scale (continuous knowledge curation), Govern (enterprise risk control), Prove (quantify return).
"Custom" means a custom definition and rendering on a shared platform kit — not greenfield code every time. [LOCKED] Bespoke Workspaces and Tasks, built on a shared kit (the contract SDK, a Task envelope, binding primitives, a design system) — what makes rapid deployment real.
Invariant

Every Tier-2 definition an FDE authors is engine-agnostic and transparent — no opaque steps, no Naviant-only references. FDE velocity is never bought by hollowing the map. (One face of the Governance spine.)

Common failure mode

"Custom" collapsing into per-customer hand-written code with no shared spine (rebuilds the fragmented-vendor-UI problem, shatters portability). An FDE reaching for an opaque internal function to hit a deadline.

Open
[OPEN] whether to enforce "no opaque steps" as a hard authoring gate.
[OPEN] the surface of the platform kit.
[OPEN] the build-vs-adopt stance per plane (Lens 8).
Lens 8 · Capabilities & Sourcing

"How is each discipline physically realized, and where does it run?"

Core model — capabilities are ports; platforms are adapters
   CUMULUS defines the CAPABILITY (a port + a contract, in our own vocabulary)
        +-- adapter: the customer's existing tool     (a) adopt what they have
        +-- adapter: a hyperscaler-native service     (c) GCP / AWS / Azure
        +-- adapter: a best-of-breed platform         (b) when it materially matters

   The SEAM (ontology · Action-Gateway governance · human-in-the-loop · Mapping ·
             identity resolution · portability) is NEVER a port. Cumulus builds it.

Diagram: each discipline is an abstract "capability" (a port) filled by an interchangeable "adapter"; the differentiated seam is deliberately not on the list. What is absent from the port catalog is the moat.

Doctrine
Contracts are capability-first, in Cumulus's vocabulary — never a vendor API, never lowest-common-denominator. The "event log" port is "a durable, ordered, replayable append with consumer offsets" — which Kafka, Kinesis, and Pub/Sub all satisfy — without exposing any one product or collapsing to a generic "queue."
The capability map, storage cut by access shape:
Capability	(a) Adopt customer's	(c) Hyperscaler-native	(b) Best-of-breed
Change Capture (Transport)	their Debezium / Kafka	AWS DMS, GCP Datastream, Azure Data Factory	Fivetran, Airbyte
Event Log / Stream (Transport)	their Kafka / Pulsar	Kinesis, Pub/Sub, Event Hubs	Confluent, Redpanda
Tabular / Analytical Store	their Snowflake / Databricks	BigQuery, Redshift, Synapse	Snowflake, Databricks
Semi-structured (record) store	their Mongo / Couchbase	DynamoDB, Cosmos, Firestore	MongoDB Atlas
Key-Value / Cache	their Redis	Memorystore, ElastiCache	Redis Enterprise
Object / Blob	their S3 / GCS	S3, GCS, Azure Blob	MinIO
Vector	their pgvector / Pinecone	Vertex Vector Search, Azure AI Search	Pinecone, Weaviate, Qdrant
Graph	their Neo4j	Neptune, Spanner Graph	Neo4j, TigerGraph
Durable Execution (Flow engine)	their Temporal	Step Functions, Azure Durable Functions	Temporal Cloud
Stream/Throughput (bulk Flow engine)	their Spark/Flink	Dataflow, Kinesis Analytics	Databricks, Flink
Stateless Compute	their k8s / functions	Lambda, Cloud Run, Cloud Functions	—
Scheduling	their scheduler	Cloud Scheduler, EventBridge Scheduler	—
Queue / Task Delivery	their SQS / queue	Cloud Tasks, SQS, Service Bus	—
API Gateway / policy	their MuleSoft / Apigee / Kong	AWS API Gateway, Apigee, Azure APIM	Kong, MuleSoft
LLM / model serving (ModelCall)	their endpoints	Bedrock, Vertex, Azure OpenAI	Anthropic, OpenAI
Identity / Secrets	their IdP / Vault	cloud IAM + KMS	Okta, HashiCorp Vault
(Storage is cut by access shape — tabular, document, key-value, blob, vector, graph — so a new database is just "which shapes does it serve," and a multi-model product fills several ports. Transport capabilities — change capture, event log — are the sourced infrastructure that carries data, per Lens 5.)
The sourcing cascade: adopt > cloud-native > best-of-breed. Prefer adopting what the customer runs (honors sovereignty, avoids migration, fastest); fall back to the deployment cloud's own service (least operational burden); reach for best-of-breed only when a capability materially differentiates. Decided per capability, co-managed by the FDE — the capability map is the FDE's deployment worksheet.
Each port declares a required floor plus optional enrichments; Cumulus backfills the gap (supplying idempotency, ordering, or hashing an adapter lacks) — what makes "adopt what they have" honest when what they have is weak (a source with no change-capture → Cumulus polls and hashes instead).
Adapters are multi-port; FDEs optimize for the fewest platforms covering the most ports (Mongo → Document + Vector; Postgres → Tabular + Document + Vector), with a scale caveat (a shared adapter yields to a dedicated one at extreme scale).
Durable execution and stream/throughput are two capabilities, not two primitives. The Durable Execution engine runs Flows that are long-running and suspend for humans; the Stream/Throughput engine runs Flows moving data at volume. They may share an inner logic-execution substrate but are sourced as distinct capabilities because their execution guarantees differ. Which engine enacts a given Flow is a deployment decision about that Flow's shape — machinery, per the definition/engine split — and never a property recorded in the map.
Durable Execution is serverless-capable, and suspension is why, not a problem. [LOCKED] A paused run is a stored record — free, not warm compute. Resumption is event-driven; active stepping is bursty; the wake-up is sourced from managed Scheduling + Queue/Task-Delivery services that push to a serverless handler. Cumulus itself runs no continuously-warm component. This is the systematic form of a principle worth stating plainly: whenever a component looks like it must run continuously in the background — a worker, a poller, a dispatcher, a scheduler, a high-volume ingest listener — that is almost always a mis-named capability. A managed service already does that job; name the port and source it, rather than running a warm box. This is what lets Cumulus run on pay-per-use infrastructure with nothing idling — even for a massive inbound stream, where the firehose is the sourced Transport's problem and the Flow only wakes to do logic.
Invariant

Every port's contract is in Cumulus's capability vocabulary, and every adapter choice stays below the portability line — a Dataset, Mapping, or the ontology is expressed against the capability ("lives in the Document Store"), never the adapter ("is a Firestore collection"). Swapping an adapter or exporting the map never rewrites the map. The seam is never a port.

Common failure mode

Naming a product in the architecture — the moment "we use Kafka" or "we run on Snowflake" is load-bearing, adopt-what-they-have and portability are lost. Specific leaks: a document store's model becoming the ontology; the "needs to stay warm" reflex (a mis-named port); conflating scale-to-zero compute with serverless-managed data; and building transport instead of sourcing it.

Open
[OPEN] the per-port contracts are undrafted — the concrete required/optional spec for each of the ~16 ports is the next artifact.
[OPEN] the bundled-egress question: a bundled API-Gateway-port adapter for greenfield customers (build, wrap, or neither)? Never a mandated integration platform; always below the Action Gateway.
[OPEN] the concurrently-suspended scheduling pattern at very large scale; build-vs-adopt per plane; multi-port adapter scale thresholds.
The Governance Spine · Why a CISO Can Say Yes

Not a lens — the rules that run through all eight. Every lens's invariant is, underneath, one of these claims. Placed last because it draws on every primitive and layer, but foundational, not an afterthought. Most likely to be read in isolation, so it restates the terms it depends on.

Core model — the Action Gateway and the ledger
   any actor (an agent · a Flow · the shell · an MCP tool)
        |
   [ ACTION GATEWAY ]  <- Cumulus-built control point. NOT an off-the-shelf product.
     checks: schema? permitted (role- and attribute-based access)? within limits?
             is the target's status allowed to change?
     then:   commit the change and emit an Event
        |
   [ IMMUTABLE LEDGER ]
     every action and every human decision, time-stamped -> history is replayable

Diagram: every deliberate change funnels through one checkpoint that validates and permission-checks it, then lands in a permanent audit log.

Two definitions this section leans on: a decision write is a change Cumulus commits because it determined the change should happen (through the Action Gateway); an observation write is Cumulus recording what an external system reported (landed by a Flow, audited by per-revision lineage, in bulk — not one approval per row).

The spine's claims
No un-audited writes, ever. Decisions go through the Action Gateway; observations through audited lineage; resolutions record which value won and why. A direct, un-audited database write is the single anti-pattern the architecture exists to prevent.
Two things are called "gateway"; never blur them. [LOCKED] The Action Gateway is the Cumulus-built governance brain (ontology- and permission-aware, ledger-writing) — the seam, never a product. A commodity API gateway (Kong/Apigee/cloud-native) is delivery plumbing — rate-limiting, retries, auth — that sits below the Action Gateway and never governs. Related: Cumulus does not require an integration platform ("iPaaS"); it harmonizes semantically (ontology + Mapping + identity resolution), which is higher-value than transport harmonization. A customer with no integration layer is ideal, not blocked.
Fine-grained permissions everywhere, including reads. Role- and attribute-based; even viewing a document is recorded, not just changing it. [PROPOSED 2026-09-16] Decisions and governed writes are ledger and live in the map's store; a read is high-volume operational audit and is recorded to an append-only audit sink, a sourced port, never the object store.
Full history and provenance; the state can be rebuilt from the log. Each derived datum records which source version produced it; you can reconstruct any past state. Compactly: state = f(events, ledger) — with one exception (claim 9). [AMENDED 2026-09-16] Faithfulness is checked, not hoped: no attested value stands without a claim naming its source, and a sweep reports any that does.
Echo suppression is automatic machinery. [LOCKED] Every outbound change is stamped "originated by Cumulus," so when it echoes back through a feed it is recognized as its own and recorded as a no-op — not mistaken for a fresh external change. Automatic, because per-flow opt-in would eventually be forgotten and cause oscillation.
A record's status is a governance gate, not a label. [LOCKED] An in-review record is visible and queryable, but the Action Gateway refuses a consequential action against it until approved. Visible without being actionable. [AMENDED 2026-09-15] In code the gate reads the record's standing, proposed or attested, never a business word such as approved; see Lens 2's status-gating clause for the four values and their meaning.
Knowledge changes are logged too. [LOCKED] Every improvement to knowledge — human-approved or automatically promoted — is recorded and reversible. Automatic ≠ invisible. The ledger records both human decisions and automatic promotions.
High-impact changes are reversible and carry provenance — especially entity merges (the typed identity Link carries confidence and reversal info). Competing field values are retained alongside the winner, so a bad choice is correctable without re-pulling.
Deletion is reconciled via crypto-shredding. [PROPOSED] Personal data is encrypted per person; forgetting destroys the key; structure survives, content becomes a tombstone; replay still runs. So claim 4 is "rebuildable except for cryptographically-forgotten fields." Open sub-questions in Lens 1.
Governed operations are audited — including model calls. [LOCKED] A ModelCall (an LLM invocation inside a Flow) records which model ran, its inputs, and its captured output — for audit and cost control. Which models run in the environment is itself a governed, reportable fact.
Failure escalates to a human; it does not silently roll back. [LOCKED] Transient failures retry safely; a persistent partial failure raises a Task with full context. Undo is offered only where an Action has a genuine safe inverse; rollback is never assumed.
The spine's invariant

Nothing writes to the core by any path that skips governance: decisions through the Action Gateway, observations through audited lineage, knowledge changes and model calls through the ledger. Nothing acts without leaving a trace. Four temptations are forbidden because each is a governance hole disguised as a convenience: an opaque step; a reference only Naviant can resolve; a bulk write skipping lineage "for speed"; the shell reading storage directly "for latency." The spine holds across every surface (Lens 6) and every layer (Lens 3).

The recurring failure mode

Governance holes arrive disguised as optimizations — a shell direct-read, an opaque step, a bulk write around the log, routing everything through an external integration platform. Each looks locally reasonable; each punches an un-audited hole in the layer the security story depends on.

Open
[OPEN] whether to commit to the replay guarantee as a hard contract (it constrains Event design — Events must carry enough to reconstruct, not merely notify).
[OPEN] the four crypto-shredding sub-decisions (Lens 1).
[OPEN] reversal semantics for high-impact actions like merges.
Index · What is Cumulus?

Cumulus is a co-managed Agentic Transformation Solution that helps an enterprise turn how its work actually gets done into a durable, owned digital form — and then runs that form as a living system.

The customer accumulates a map — how their organization works, owned and portable. Naviant supplies the machinery — the engines, the dashboard, the FDE drivers. The customer owns the roads and destination; they rent the car and driver. That is why it is not lock-in.

The spine sentence

Cumulus is a set of layers speaking through one contract, thinking in a fixed set of primitives, doing work (business transactions and data maintenance alike as Flows) that keeps humans in the loop, deployed across the customer's environment with data carried by sourced transport, accessed through interfaces and delivered across surfaces, co-delivered by FDEs, physically realized through sourced capabilities, and governed end-to-end — such that the customer accumulates and owns a portable map of how their organization works.

The primitive set (at a glance)
   ONTOLOGY               Entity · Link (incl. typed identity Link) · Action · Event
   CONNECTIVE             Trigger (a binding) · Rule (a check)
   PROCESS & DATA         Flow · Mapping · Dataset
   IDENTITY/ACCESS/WORK   Principal · Role · Queue
   EXPERIENCE             Task · Workspace

   Governed operation:    ModelCall
   Category:              Governed Knowledge (prompts, model configs, metric definitions, thresholds, Rules)
   Record facts:          standing · claim · provenance (the write site) · id, key aliases, external identity · ownership — stamped by machinery, never authored [AMENDED 2026-09-16]
   Content items:         File · Record · Row — what a Dataset holds [AMENDED 2026-09-16]
   Not primitives:        Surface (a delivery channel, Lens 6) · Sync (a pattern) ·
                          Connector (machinery-config) · Transport (sourced) · Read (a capability)
The lenses
#	Lens	Question	One-line doctrine
1	Ownership & Value	Who keeps what on cancellation?	The customer accumulates a portable, transparent map; Naviant rents the machinery.
2	The Primitives	What are the nouns?	A small fixed vocabulary; the Flow as the one authored logic; three write regimes; Principal/Role/Queue for identity and work; each primitive a definition with an enacting engine.
3	Architectural Layers	What are the parts?	Three layers (the Flow engine in the middle) separated by one governed contract.
4	Work & Interaction	How does work get done?	Two perpendicular things — Tasks (assigned work) and Workspaces (Seeing Spaces); agents lift, humans judge; the flywheel compounds knowledge.
5	Deployment & Custody	Where does it live?	Custody per-asset (copy or reference); map unified and core-resident; transport sourced; MDM/CDC/sync are Flows.
6	Interface & Consumption	How is it touched?	One contract, three faces (REST/SDK/MCP); work delivered across surfaces (the reduced tool-set), driven by Cumulus.
7	Delivery & Operating	Who builds and runs it?	Co-managed: FDEs author the customer-owned definitions and run the machinery.
8	Capabilities & Sourcing	How is it realized?	Each discipline is a port; adapters (adopt > cloud-native > best-of-breed) fill it; the seam is never a port.
—	Governance (spine)	Why can a CISO say yes?	Every decision is a guarded Action; every action leaves replayable lineage; knowledge changes and model calls are logged. Runs through all lenses; stated at the end.
Orthogonality notes
Lens 2 (Primitives) and Lens 4 (Work) blur most easily — the nouns vs. the verbs over them. Keep the cut sharp. Lens 2 is the heaviest lens and may eventually split (its Dataset model, or the Event/Trigger/Rule tissue, could stand alone).
The retired Workflow/DataFlow split is the cautionary tale for this document: a real behavioural difference (per-case vs. windowed) was encoded as two primitives, and the cost was classification arguments over definitions that sat in the middle. The difference survives as a derived shape; the noun did not. When a future distinction feels load-bearing, ask first whether it can be read off a definition rather than declared on one.
Governance is the spine, not a peer lens — every lens's invariant restates a governance claim, so it is stated once and threaded through.
Locked decisions (the settled base)

Map vs. machinery; three portability tiers; transparency (not re-executability) as the bar. Three write regimes (observation / decision / resolution). Governance as a spine. Event / Trigger / Rule as connective tissue, with Trigger (a binding) and Rule (a check) firmly distinct; Rule softened to a transparent-governed pure check with implementation reserved. Two-level evaluation (Dispatcher for Triggers, response for Rules). Response-kinds closed at Flow / Action / Resolution. One authored-logic primitive, the Flow — the earlier Workflow/DataFlow split retired, with per-case vs. windowed read off the Trigger. Mapping shrunk to binding-config; Sync/Connector demoted to machinery; Transport sourced, not built. MDM/CDC/sync as Flows, not new mechanisms. Datasets: two independent dials, content-only, structure-in-graph, Member retired, source-vs-derived recipes, retained competing claims. Temporal graph (materialized); the ontology graph-shaped but not physically one database. Principal / Role (platform/business/workspace kinds) / Queue for identity, access, and work routing; role-assignment and queue-binding as Links; Queue↔Workspace an optional many-to-many Link. Typed identity Link with confidence and reversibility. ModelCall as a governed operation (no "Agent" primitive). Governed Knowledge as a category (customer-editable configs are governed knowledge, not code). Draft-Entity (proposed-record) status-gating; automatic echo suppression; same-run suspend/resume; the flywheel write-back; retry-then-escalate failure handling. Action-Gateway vs. API-Gateway distinction; no mandated integration platform. Python-plus-decorator authoring exporting to the map. Surfaces as delivery channels; surface-reduction as a goal.

Still open, and parked

Open: whether access-role and assignment-role ever diverge; the observation/decision/resolution edge cases; the four crypto-shredding sub-decisions; the replay-as-hard-contract question; the retained-claims threshold; the re-elevation policy; the MCP write posture; the per-port capability contracts; the bundled-egress adapter; the confidence/blast-radius thresholds; enrichment timing.

Parked (deferred by decision): the four implementation threads (replay-based execution, dual rendering of flow structure, step configuration as map-resident knowledge, SDK ergonomics); full bitemporal history; the "agent drops by your desk" ambient interaction.