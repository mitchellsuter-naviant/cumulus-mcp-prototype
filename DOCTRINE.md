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

Entity — a canonical business thing (a Customer, an Invoice); a node in the knowledge graph with identity and relationships.
Link — a relationship between Entities, declared as a type of its own with a source, a target, a cardinality and a name from the other side: semantic ("BilledBy"), structural ("filedUnder"), or cross-system identity (a typed identity Link: "this ERP id is that canonical Customer," carrying the external id, confidence, method and standing). Most Link types carry no properties; some do, because a property of a Link is a fact about the edge.
Part — a thing with no identity of its own (an invoice line, an order line), held inside the Entity it belongs to and written and loaded with it. Composition is declared on the whole as its parts; a part anything else would point at, or that has a life of its own, is an Entity.
Action — a single, deliberate, guarded change committed through the Action Gateway (e.g. "approve this invoice"). Each Action is a defined, typed mutation.
Event — an immutable record that something happened (the fact, emitted when state changes). A kind of Event exists only once something consumes it: a run completing, today; a record's own events — written, linked, unlinked, acted, attested, retracted — for exactly the types and kinds a Trigger names.
Trigger — a declarative binding: "when Event X occurs, start response Y." Engine-managed causation. (Distinct from a Rule — see below.) The response is a Flow, or a single Action where the answer is one guarded change and nothing waits.
Rule — a declarative check: a pure predicate that returns a verdict (e.g. "is the amount over the threshold?"). Transparent and governed; used for logic and governance decisions. A Rule returns findings; an empty list means it holds.
Flow — the one kind of authored logic: durable, calls models via ModelCall, branches, retries, and may pause for a human. A Flow may run per case (one invoice, one deal) or over a window (keep the vendor list in agreement with the ERP); which it is, is read off its Trigger, never declared as a type.
Dataset — a container of content the customer owns (a table, a document's bytes, embeddings, a prompt). Distinct from structure, which lives in the graph. One Dataset per set whose governance differs.
System — an outside system of record or source the map declares as a class: which deployment settings hold its address and credentials, which never enter the map, and methods, written by the FDE and named for the business step they perform, that do what that system needs: phrase the request, make it, read the reply back as Entities. A method that changes the outside system is marked as an Effect. Until a second customer runs the same product there is no separate adapter; when one does, the shared request code moves out of both Systems into one, and the methods shrink back to phrasing.
Human — one canonical record per person, curated in Cumulus, with every system that knows them (the identity provider, the HRIS) mapped onto it by identity Links; being an employee is a relationship on it and having a sign-in is a Link on it. Agent — its sibling, later: acts, holds roles, never signs in as a person. Principal — not a record: whoever is calling, resolved per request to one Human or one Agent.
Role — the business thing: *AP clerk*, *Expenses controller*. **Data**, and it lives in one Workspace: a record that Workspace's administrator creates, naming the queues or subqueues its holders read and the flows it lets them start, over what the Workspace links and nothing wider; the map may seed a Workspace's starting set, written once at startup. There are no platform roles [2026-10-02]. A role is held as a binding at its Workspace, the way a cloud binds a principal to a role on one thing, and confers work, never permission. The three fixed words — admin, member, auditor — are not roles: held at the platform they are **Platform access**, what somebody may do to Cumulus, the one table every verb is checked against; held at a Workspace they are **Workspace access**, whether somebody is one of its people and whether they run it. Platform access and membership of any Workspace, Platform included, are independent facts.
Queue — where work waits, declared in code and platform-wide: few, stable, named for the work (accounts payable, access), what a Flow asks. A Queue may declare named **subqueues**, one level deep, spelled as a path (`expenses/controller`), with no order between them — beside or above is a fact about the names and about who is bound, never about the mechanism. A person has a queue of their own; the administrators of each Workspace have one, core's own exception, because core must be able to ask them — and Platform's is the platform's, so the platform has none of its own [2026-10-02].
Task — a unit of human judgment: an agent proposes, a person decides, the system resumes. Raised by a Flow's ask or by an Action (assigning a checklist item); carries a due date; its answer resumes a run or runs an action on a record.
Checklist — a core base Entity the map may subclass: a named set of things that must become true about one record (a link, about), its items held inside it as Parts — key, title, done, who and when, a section or parent key for grouping and sub-items, a document reference as evidence — with actions assign, complete and reopen by item key, and finished when every item is done. Not a Task: assigning an item raises a Task, so who owes it and by when live on the Task alone.
WorkCalendar — a core Entity type, with Holiday beside it: the days a business runs and the days it closes, kept by a Workspace's administrators through its own actions. Core's and never subclassed, because what a calendar *is* is the same question in every business; which calendar a person is on is the map's to say, read off facts the map's own systems land. [2026-10-04]
Workspace — a case-centric interface ("Seeing Space") for how a workstream gets done (e.g. a Deal Room). Perpendicular to Tasks: you can watch how work runs without being assigned to it. [PROPOSED 2026-09-29] In the console a Workspace is the one door onto anything that is not a person's own work: the frame is a bar of four routes over every screen — Home, Workspaces, My Tasks, the person — and the platform's own administration is drawn as a Workspace too ("Cumulus Admin", first in the list; its stops are what is built, what is known, what is connected to and how it is governed), a projection of the admin pages with no data of its own, so the console has one shape for watching work and the same shape for running the thing that does it. [2026-10-02] The platform's own workspace is called **Platform** (id `platform`; the console had drawn it as Cumulus Admin, then Platform Admin) and is a Workspace core declares on every deployment — a record, a visibility (`hidden`), members with Workspace access and Roles of its own, like any other — whose stops are the admin pages, plus the one stop no other Workspace has: **Platform access**, the roster of every Human and their level, there because that fact is about everybody and not about its members. Being a platform administrator does not put anybody in it; bootstrap binds the first administrators there, and after that it is its Members stop like everywhere else. A Workspace the map declares is read in **views** — `View(name, panels)`, one kind of the work it gathers, as a person turns to it — listed on its rail in declaration order, and operated through the health of the flows and Systems those panels name. Naviant's first: one Financial operations workspace, read as Accounts payable and Expense reimbursement. [2026-09-30] A Workspace has a record with one fact that is data, its visibility, and its people are bindings at its scope; see the clause under Work.
Case — a pattern, not a primitive and not a declaration: a record that is the centre of long-running work (an Opportunity, a Handover, a claim), whose lifecycle is the business's, that many Flows act on over time, and none of which is in charge. Named so that it is recognised; it adds no machinery. Such a record is an ordinary Entity, and the Flows that act on it are started by Triggers on its own Events.

Governed operations, categories, and machinery (not primitives)

ModelCall — a governed operation within a Flow: an LLM invocation, audited and cost-tracked (which model ran, inputs, captured output). Not an "Agent" primitive — the Flow is the agent.
Effect — a governed operation, the outbound twin of ModelCall: a System method that changes the outside system, called by a Flow as its own step, never from an Action. Around it the machinery writes one Ledger row as intent before the call and marks it committed with what came back, or refused with the system's reason; the method itself makes a retry harmless. An Action changes the graph and only the graph; what follows in a System is the Flow's explicit next line, and the Ledger shows an approval that was never posted as exactly that.
Governed Knowledge — a category, not a primitive: the declarative middle between raw data and machinery code — prompts, the schemas answers must fit, model configs, thresholds and precedence orders as values, Rules, Metrics, Mappings. Lives in the map (portable, versioned). Values a customer edits in the shell are governed knowledge, not application code — which is what makes editing them safe: every shell edit is a new version of the record, content-hashed, superseding the one before, with its Ledger row, so a history is always there to read. What the map declares in code is the **seed** — written once, when no record of it exists — and a record the shell has written since supersedes it; a coding agent reading a `declared` block must read it as the first value, never the current one. Prompts and the schemas answers must fit are authored, reviewed and shipped with the map, versioned by the hash of their content.
Policy — governed knowledge stored as an Entity: a type whose instance is declared in the map rather than observed, with a purpose in prose, an effective date, a review date and its own fields, each carrying a description an agent can answer a person from. Versioned by the hash of its values, every version kept, pinned by every run that reads it. Its values and descriptions may be edited in the shell: the map's declared block is the seed, and the stored record wins. What a rule is handed, and what an agent reads before it acts.
Mapping — governed knowledge, not a primitive: how a source's fields and identifiers bind to the ontology, plus identity keys and freshness policy. A Flow reads it exactly as it reads a model configuration; it does no work of its own, and the transform logic (parse, embed, derive) is the Flow's.
Skill — governed knowledge stored as an Entity, beside Policy: a page of markdown a person writes in the shell for Cumulus to read — *About us*, *How we book a short-paid invoice* — versioned by the hash of its body like any other governed write, every version kept. What a ModelCall or a tool a model is handed may read; nothing reads one yet, and the reader's governance (which version a run pinned) is decided before the reader is built.
Metric — governed knowledge, not a primitive and not an Entity: a figure the map declares over Runs, Tasks or Entities (a count, a sum, a duration, a ratio, an aging), versioned by the hash of its declaration so an answer says which definition produced it, and **computed when read**. Nothing is written to the graph and no run pins one: a Metric is for people and for screens. A Flow that needs a figure as an input does not read a Metric; it computes and writes the derived data — a claim, a Dataset row — and the Metric may then read that. The word is Metric, the one a business user already uses for a figure on a page [2026-10-07; it was Metric for three days]. Telemetry — the machinery's own figures, runs in flight, model spend, latency — is the other lane and never a Metric. **Telemetry is the other lane and is never a Metric** [2026-10-07]: how the machinery itself is doing — runs in flight, model spend, calls that failed — is core's to count, declared in no map and pinned by no run, kept as closed-day rollups in the Record port (a projection, not an Entity, rebuilt from the Ledger by the next close) and counted live for today. A Metric is the customer's figure about the business; telemetry is Naviant's figure about the machinery; neither borrows the other's store or its vocabulary.
Surface — a delivery channel (the Cumulus shell, Microsoft Teams, Outlook, an MCP client), not a work artifact. Belongs to Interface & Consumption (Lens 6).
Transport — sourced infrastructure that carries data (a stream service, a file drop, a change feed). Cumulus is aware of it as a registered endpoint but does not build or maintain it.
Sync — an execution pattern (an inbound Flow + an outbound Flow + echo suppression + a reconciliation policy), not a primitive.
Action Gateway — the Cumulus-built control point every decision write passes through: validates, permission-checks, records to the Ledger. A change that must reach a System goes through the same gate as an Effect. Fully treated in the Governance spine.
The three write regimes — every write to the core is an observation (recording what an external system reported — audited via lineage), a decision (a deliberate change — through the Action Gateway), or a resolution (an automatic choice among competing claims by the property's declared authority order — automatic like an observation, audited like a decision).
Ledger — the permanent, append-only record of every governed operation: every Action, every write to the graph or into a Dataset, every human decision, every ModelCall, every Effect in a System. One row shape; each row names what it is about (an Entity, a File, a run, a System) and who or what did it. There is one Ledger, it is the customer's, and it is the audit. [AMENDED 2026-10-07] It also holds what a run said about itself: a note the flow wrote at a step (`ctx.note` — the flow's own sentence, the only place a branch chosen in plain code leaves a reason), and the run's own starting, finishing, failing and asking. A run's complete trail is its Ledger rows in time order grouped by step; the run page reads the Ledger for the story and the journal only for the skeleton, and never the logs. `WORKPLAN-TRAIL.md` in cumulus-vision.
Run journal — a Flow run's own memory: each step's result, kept on the run so it resumes where it paused without redoing work or paying twice. Machinery; the customer never reads it.
Logs — operational telemetry for the people running the machinery: errors, latency, traces. Never evidence, never cited as audit.
Ontology — the customer's canonical model: the Entity types, Link types, and vocabulary agents reason over.

Stamped facts and content items (what the machinery records on a thing, and what a Dataset holds)

standing — how far a record has got, and nothing about the business: proposed, attested, retracted, superseded. Machine-owned and never a business word; an Action attests, retracts, or leaves it alone, and the Action Gateway reads it as a gate.
claim — one asserted value for one property, carrying its source, its confidence and the write that made it, and optionally the evidence it was read from: a File, a page, a region on the page.
key alias — a business handle for an Entity (a vendor's name, an invoice number), several per Entity and correctable; the id beneath them is minted, permanent, and never a business value.
File — bytes plus their record: name, type, size, hash, page count.
Record — one semi-structured item as it was delivered, with provenance (an API payload, a CRM contact, a webhook).
Row — one item under a declared schema.
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
   enterprise integration <- so a permissioned change can reach a system of record
   governance / security  <- so it is safe to let anything act at all
   ontology / knowledge   <- so there is a correct model to act on
   integrity engineering  <- so the model provably matches reality
   distributed systems    <- so state stays correct across many systems
   data engineering       <- so the underlying data is trustworthy at all

Diagram: the capabilities Cumulus is built from, stacked so each depends on the ones beneath it; AI sits on top and is the smallest layer.

Most companies selling "AI" start at the top and skip everything beneath it — a chat box bolted onto a dashboard. Cumulus is mostly the bottom seven layers, with AI riding on top. That inversion — investing in the foundation rather than the chat box — is the product. Three disciplines are the durable advantage precisely because a chat-box competitor never attempts them: distributed systems (keeping data correct across many systems), integrity engineering (proving your copy matches reality), and ontology engineering (modeling what the business's data means). Anyone can call a language model; almost no one does reliable two-way synchronization with verified provenance into a customer-owned model of the business.

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
   entities · ontology · links · data · event history · Ledger
   + the DEFINITIONS of flows, actions, rules, mappings, configs
                              (runs on)
   THE MACHINERY -- Naviant-owned · rented · goes dark on cancellation
   engines (the car) · experience shell (the dashboard) · FDE co-management (the driver)

Diagram: the customer owns a portable "map" of how their organization works; Naviant rents the "machinery" that runs it. Cancel, and they keep the map and lose the machinery.

Naviant is the dashboard, the engines, and the driver. The customer owns the roads and the destination. They rent the car; they never rent the map.

Doctrine
The customer accumulates a portable map of how their organization works. It only grows, and it is always theirs. This — not "you own your data" — is the proposition. Owning your data is table stakes; owning how the work runs is the moat.
Naviant's product is the machinery, and it is excellent precisely because there is no lock-in. The company wins on the quality of the engine and the driver, not on hostage-taking.
Portability is a gradient — three tiers: Tier 1, pure assets (data, graph, event history, Ledger) — fully portable, just data; Tier 2, the blueprint (declarative definitions of flows, actions, rules, mappings, configs) — portable as specification, needs an engine, not necessarily Cumulus's; Tier 3, machinery (runtimes, shell, FDE labor) — rented.
The crown jewel is the Ledger — accumulated human judgment; the customer's compounding institutional asset and their dataset for evaluating and tuning models.
Source data exports as bytes; derived data exports as recipes. An ingested Dataset exports as its content; a computed Dataset (embeddings, an aggregate) exports as the recipe to regenerate it — source plus transformation. A vector index is the classic machinery-output-masquerading-as-asset: the portable truth is the source plus the embedding recipe, not the index.
The transparency bar [LOCKED]

The bar the map must clear is transparency, not re-executability:

The map must give the customer enough to understand how their organization works — the structure and intent of its flows, the meaning of its rules — in human-readable form. It need not be a turnkey, runnable specification, and it need not include implementation code. What is prohibited is the map being opaque — a pointer revealing nothing, like "this rule = Naviant internal function #47." A plain-language description of what a rule does is transparent even though it is not runnable.

Two consequences: structured where cheap and valuable; text where structure would be forced — a precedence list, a threshold, or a flow's shape are captured as data (transparent and engine-usable), while genuinely complex logic is captured as a faithful description (implementation stays Naviant's). And this resolves the ownership-vs-IP tension — "the customer owns how their organization works" (transparency) is compatible with "Naviant owns the implementation," precisely because transparency needs no runnable code; the stronger bar would have forced giving away implementation.

The Forgetting qualification — replay vs. the right to be forgotten [PROPOSED]

A permanent, immutable history (which lets the customer rebuild state by replay) appears to conflict with a person's right to erasure. Reconciliation — crypto-shredding: personal data is stored encrypted with a per-person key; forgetting destroys the key; the history's structure survives, the personal content becomes an unreadable tombstone; replay still runs. The guarantee is then honestly stated as "the state can be rebuilt from history except for cryptographically-forgotten fields." [OPEN] sub-decisions: key granularity (per-person keys require tagging whose data each event holds, at write time); the personal-vs-structural cut line, per field in the Mapping; the derived-data tax (forgetting a source key does not auto-purge derivations, so derivations must be person-tagged and regenerable); and the Ledger itself contains personal data and is not exempt. Caveat: crypto-shredding is the accepted answer, not magic — pre-shred backups and anywhere data escaped the envelope need their own handling.

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

   ONTOLOGY (what things are, what is held inside them, and what happens to them)
     Entity · Link · Part · Action · Event

   CONNECTIVE (a fact, and a check)          <- two DIFFERENT kinds of thing:
     Trigger  = a binding (Event -> response; engine-managed causation)
     Rule     = a check (a pure predicate returning a verdict)

   PROCESS & DATA (authored logic + its content + the systems it reaches)
     Flow      = authored logic (a case or a window; may wait on a human)
     Dataset   = a governed set of content (files, records, rows, prompts)
     System    = a system of record or source the map knows how to reach

   IDENTITY, ACCESS & WORK (who acts, what they may do, where work goes)
     Principal · Role · Queue

   EXPERIENCE (how humans engage)
     Task · Workspace

Diagram: the fixed set of primitives, grouped loosely by role. Trigger and Rule are shown together but are deliberately unlike each other — one is causation-wiring, the other a check.

Each primitive, with the engine that enacts it (engines are machinery, never primitives):

   PRIMITIVE (portable definition) ENACTED BY (machinery)                 STATE
   Entity / Link / Part            the graph store                        built
   Action                          the Action Gateway                     built
   Event                           the event log / bus                    built (one kind)
   Trigger                         the Dispatcher (evaluates it,          built (a schedule and
                                   pre-response)                          an event)
   Rule                            a generic rule interpreter             built as a pure method;
                                   (evaluated in-flight)                  the interpreter deferred
   Flow                            the durable-execution engine (or the   built
                                   stream/throughput engine, where
                                   volume demands it — Lens 8)
   Dataset                         the storage adapter for its            built (the document Kind)
                                   access-shape
   System                          its own methods, and the shared        built
                                   adapter a second customer earns
   Principal                       the identity provider                  built (authentication only)
   Role                            the permission check, and the          planned
                                   queue-bindings it confers
   Queue                           the task service                       built
   Task                            the task service and its screens       built
   Workspace                       the shell                              built (a projection; no data of its own)

This annotation prevents two classic confusions and says what is running: it shows that Sync is not a missing primitive — it is simply the engine (and pattern) that enacts data-movement, exactly as "the flow engine" enacts a Flow and neither engine is a primitive; it carries the state of each primitive in this build, so a reader knows which part of the doctrine is standing code; and it shows why Trigger and Rule are not the same kind of thing — a Trigger is enacted by heavy infrastructure (the Dispatcher, the event bus), a Rule by a light interpreter.

Doctrine
The vocabulary is fixed and small (above). A Link is declared as a type of its own, with a source, a target, a cardinality and a name from the other side, and carries one of three kinds of relationship: semantic ("BilledBy"), structural ("filedUnder"), or cross-system identity. Most Link types carry no properties, and some do — the external id, confidence and method on an identity Link, a proficiency on a skill — because a property of a Link is a fact about the edge. The one-line spelling on the Entity and the class spelling are one implementation: the short form mints exactly that type, so there is one declaration, one export and one write path. A relationship that someone adjudicates, or that other things point at, is an Entity with two Links, not a Link with many properties. A Part — an invoice line, an order line — is the third kind of thing in the ontology, and it is neither an Entity nor a Link: it has no identity of its own, is held inside its whole, and is written and loaded with it. A structural Link joins two Entities that each have identity (a document filed under a folder); composition is declared on the whole as its parts. A part that anything else would point at, or that has a life of its own, is an Entity.
Trigger and Rule are firmly distinct — not variations on a theme. [LOCKED] A Trigger is a binding — "when Event X happens, start response Y" — engine-managed causation that kicks off real work. A Rule is a check — a pure predicate that returns a verdict ("amount over threshold?"), the unit of governance, transparency, and business logic. One is causation; the other is logic. They sit near each other only because both are declarative connective tissue between a fact and a response; they are otherwise unalike.
A Rule is a pure check, transparent and governed — but its implementation is reserved. [LOCKED] The execution boundary holds: a Rule has no side effects, no fetching, no orchestration, no waiting — it evaluates inputs handed to it and returns a verdict. If it needs to fetch, act, or orchestrate, it graduates to a Flow. But the earlier "never free-form code" constraint is dropped: a Rule must be transparent and governed (its meaning inspectable and reportable), yet how it is implemented is an implementation choice we reserve — the same transparency-not-mechanism bar as the rest of the map. Rules never fetch: the caller gathers inputs and hands them in, and a limit comes from a Policy the caller read. A Rule returns findings: an empty list means it holds; a finding names the path it is about, the reason in a sentence and, where the Rule measured something, the value and the limit it was measured against, so a scalar over a threshold is a verdict a person can read and a screen can draw. A Rule returning a bare boolean or a number is refused when the map loads, because a verdict that cannot say why is one nobody can act on. Rule outcomes are written to the Ledger at the moment they mattered and never onto the Entity: the gate evaluates every Rule the type declares when an Action runs and records the findings on the act row beside the Policy versions in force, and the findings a Flow showed a person sit in the Task they were shown in. The present is computed on demand. A stored verdict is a claim about now that goes false; a Ledger row is a fact about a moment.
One kind of authored logic: the Flow. [LOCKED] A Flow is durable and has one set of powers — call models via ModelCall, branch, retry, and raise a human Task with suspend/resume. Flows differ in shape, not in type. Some run once per case ("process this one invoice"); some run over a window ("keep the vendor list in agreement with the ERP"). That difference is real and load-bearing — it decides how a Flow writes and how it escalates (next point) — but it is read off the Flow's own Trigger and its writes, never declared as a separate primitive. This retires the earlier Workflow/DataFlow split, which was right about the physics and wrong about the level: putting a behavioural difference in the type system bought arguments about which noun a definition was, rather than better defaults. The tell that used to pick a noun now picks a shape, and the shape is derivable, so nothing has to be classified by hand.
Windowed Flows land bulk observations and escalate on aggregates; per-case Flows commit per case. [LOCKED] A Flow woken by a windowing Event — a timer (from the Scheduling capability) or a count/threshold crossing, through the same Event → Trigger mechanism as everything else — is processing a batch: it lands bulk observations (per-revision lineage, never one-Action-per-row) and emits summarizing Events and raises Tasks on exceptions or aggregates, never per-record, so the reactive plane stays off the hot path. A Flow woken by a business Event is processing one case and commits per-case decisions through the Action Gateway. A Flow may also be invoked synchronously (request → response, e.g. hand back a signed upload URL). These are defaults attached to a Flow's trigger shape, not to its type.
A Dataset is one primitive with independent dials, holding content only. [LOCKED] Six dials, and each one decides where one set ends and the next begins: Kind (document, tabular, record, media, graph, vector, prompt — a record is a semi-structured item as it was delivered, with provenance, the shape most APIs return and the one an email, a CRM contact or a webhook payload has; a configuration table or a threshold list is a record by shape, so there is no separate config Kind); Custody (core-owned vs. referenced-in-place — Lens 5); Source vs. Derived (point 7); and, because a Dataset is the unit governance hangs off, the governance dials — Retention (a duration applied at write, or indefinite; purge is machinery), Legal hold, and Read access (which Roles may read the set: the set-level attachment point for the Governance spine's read permissions, refined per item through the graph). Datasets are plural by governance: one per set whose governance differs, however many that makes — a table is a Dataset, a governed set of documents is a Dataset, and "all documents" is not. Content enters a governed Dataset only by a Flow's write, with the run and step recorded. What arrives from outside lands first in a staging Dataset that says nothing about it but that it arrived, held by the runs it was handed to and released when they complete; the person or system that uploaded it never chooses where it belongs. Crucially, a Dataset is owned map content, not a universal storage bucket — machinery/operational state (run journals, the trigger index, queues) is not a Dataset. The Ledger and the event history are not machinery: they are record Datasets, append-only, Tier 1 assets that export as records, which is how Lens 1 can call them the customer's and this clause can still exclude journals. Structure lives in the graph, not in Datasets: a folder is a folder Entity with filedUnder Links, not a Dataset.
Source and derived data are linked by a derivation record. [LOCKED] A derived Dataset records { from, fromVersion, recipe, producedBy, staleWhen }. This buys portability (export as source + recipe), freshness (source advances past the pin → staleWhen fires → regenerate), and provenance. Rule of thumb: sources are synced; derivatives are regenerated — never "sync a vector store."
A canonical field keeps its competing claims, not just the winner (for agent-acted fields). [LOCKED] { resolved, claims:[ERP $75k, CRM $50k, human $60k] }. A claim carries its source, its confidence and the write that made it, and may point at the evidence it was read from — a file, a page, a region on the page — so a value resolves to something a person can look at, not only to a step in a journal. An incoming synced value is a new claim evaluated against the property's declared authority order, not a blind overwrite — so a bad order is correctable without re-syncing, and the choice stays auditable and reversible. Confidence lives on claims and on identity Links, never on an Entity as a whole.
Every write to the core is one of three regimes — observation, decision, or resolution. [LOCKED] An observation records what an external system reported (a Flow lands it; audited by per-revision lineage; bulk, not per-row). A decision deliberately commits a consequence (an Action through the Action Gateway; per-write permission checks). A resolution picks a winner among competing claims by the property's declared authority order — machinery applying a declaration, automatic like an observation and audited like a decision: it records which claim won and why, admits a Rule where the order cannot decide, and raises a Task where neither can. The classifying test is intent: "a system told us" is an observation; "we determined this should happen" is a decision.
Event, Trigger, and Rule are the connective tissue between "a fact happened" and "something responds." [LOCKED] An Event is the immutable fact. A Trigger binds it to a response and names a response-kind. A Rule is a check consulted by a response. (Trigger and Rule remain firmly distinct — point 2.)

A record emits Events, and a Trigger may answer one with an Action. [LOCKED] A record's own events — written, linked, unlinked, acted, attested, retracted — are emitted for exactly the types and kinds some Trigger names, so the log carries nothing nobody reads and no vocabulary nobody agreed. A Trigger on them names the type, the kind and a predicate over the record (a stage reached, a link of one type arriving), and its response is a Flow or one Action. An Action as the response is for one guarded change with nothing to wait for — a checklist completing advances a stage, a case closing deactivates access — and it runs through the same Gateway as every Action, with the Trigger named as who did it. Anything that waits on a person, calls a model, retries over time or reaches an outside System is a Flow. Who wrote the property is not part of the event: a termination written by the HRIS sync and one written by a person's Action wake the same Trigger. A Trigger on a record fires once, when its predicate becomes true, and the run it starts is keyed to the record, so a fact written twice starts one response; and a Flow may wait on a record's Event the way it waits on a Task, which is how one flow yields to another without a lock.

The response-kind vocabulary is a closed set of two, cut by execution weight. [LOCKED] Flow (durable, multi-step, can pause — heavyweight) and Action (a single guarded write — medium). A resolution is not a response: nothing starts it and nothing waits on it; it happens inside a write, when claims compete. Not response-kinds either: a Task (always raised by a response, which must also handle its answer) and "notify/record" (that is just the Event in the log). Closed because it is cut by execution machinery, of which there are only these tiers.
Evaluation happens at two levels. [LOCKED] A Dispatcher evaluates Triggers — whether a response should start — before any response exists (a thing cannot decide whether it itself should begin). The running response evaluates its Rules in-flight, as reached.
A ModelCall is a governed operation, not an "Agent" primitive. [LOCKED] An LLM invocation inside a Flow is a first-class governed operation — audited and cost-tracked: which model ran, the inputs, the captured output/reasoning. The configuration a ModelCall runs under — prompt template, the schema its answer must fit, provider and its answer-changing settings — is a declared item of governed knowledge, versioned by the hash of its content and never migrated; every ModelCall record names the configuration and version that answered it, and every version that ever ran stays readable. There is deliberately no "Agent" primitive, because the Flow is the agent (adding "Agent" would collide with them). What must be governed is the model invocation — which models run in the environment, their cost, and their outputs — and that is the ModelCall.
Identity, access, and work distribution: Principal, Role, Queue. [LOCKED]
Human — one canonical record per person, curated in Cumulus and never derived from another system's: the identity provider and the HRIS are mapped onto it by identity Links, each authoritative for the properties the map declares theirs, and Cumulus authoritative for the access facts (may they sign in, their Platform access, their Workspace access at each Workspace, and the Roles they hold). The person is the Entity; being an employee is a relationship on it and having a sign-in is a Link on it, so a contractor, a Naviant engineer and an employee who never signs in are all ordinary Humans. Agent is the sibling record, later. Principal is not a record: whoever is calling, resolved per request to one Human or one Agent, which is where the word was always clearest. Which system leads is a declaration per property and may move: a customer who wants Cumulus to lead on employment facts moves those names out of the HRIS's tuple, declares the Actions that write them on the type — hire, transfer, leave, return, terminate — and the former owner becomes an Effect target or is retired, with no change to machinery. Payroll, benefits, tax and statutory reporting are never Cumulus's: regulated ledgers stay in a System, and Cumulus is the system of process and, where the customer wants it, of org truth.
Role — the business thing, and **data**: a record a Workspace's administrator creates at that Workspace, over the queues and subqueues the Workspace links and nothing wider; the map may declare a Workspace's starting set, written once at startup the way the first administrators are. **A Role lives in one Workspace and there are no platform roles** [2026-10-02]: the platform's own administration is the Platform Workspace, core-declared, and its roles live there like any other's. A role names the queues or subqueues its holders read and the flows it lets them start, and confers work, never permission. **A role is held as a binding at its Workspace** — recorded on the Human with a Link's three facts and a fourth (which, since when, by whom, and where). **Access is the other fact and has its own verb.** The three fixed words are *Platform access* on the Human (admin, member, auditor; one; the whole of what the permission table reads) and *Workspace access* held at a Workspace (the same three words: the bound may open a private or hidden Workspace, and its admins run it; member against auditor there is enforced when reads are scoped); `grant` moves access and `hold` moves roles, and neither accepts the other's word. Platform access and membership of Platform are independent: holding admin at the platform binds nobody there, and bootstrap sets both for the first administrators. **The platform comes first**: a workspace's administrator binds only Humans with active access, and nobody is let into this Cumulus from inside a workspace. The last administrator of a workspace cannot be unbound, the same rule Platform access has. People decisions — who reads which part of a queue, whether managers also read what clerks read — live in roles and bindings, never in flow code.
Queue — where work waits, declared in code, platform-wide, with optional subqueues. [LOCKED] A Flow asks a queue or a subqueue by name, the queue being a place the business runs and not a kind of person; every holder of a role bound to that queue — or to that subqueue — sees the Task in their personal "My Tasks", and a Task may be put to one person by the queue that is theirs alone. A role bound to the queue reads every subqueue; a role bound to a subqueue reads that part; a Task at the bare queue waits for everyone bound anywhere in it. Subqueues are a named partition and carry no order: where a chain of authority exists — review, then approve — the knowledge of what comes next belongs to the policy that decides when a question needs more authority, and the flow asks at the subqueue the policy named. The kind of work is never a queue: thirty flows ask questions of two or three queues, and the queue is read by kind, by due date and by what it is about. Queues grow with the places a business runs, which is few and slow; roles grow with the kinds of people, and are data; flows grow with the kinds of work, and name neither.
A Workspace lists the queues it links (`queues=`, subqueues implied), checked at startup, and that is what its administrator may create roles over and bind people to, and nothing wider: linking a queue to a Workspace delegates who reads it. A flow that does not know which Workspace it serves asks the queue; the administrators of one Workspace are reached by core's own queue for them.

A Workspace is a declaration with a record, and its people are bindings. [LOCKED] What an engineer changes — the views, the panels, the queues it links — is the declaration, code and a deploy. What an administrator changes is one record per declared Workspace, written at startup the way the first administrators are and retired when the declaration goes, carrying the one fact that is data: **visibility**, `public | private | hidden`, default private. Public is listed and open to anyone who may read the map; private is listed and open to the bound; hidden is not listed and open to the bound. Membership is not on the record: it is the bindings on Humans that name the Workspace, read back by a query, so the record holds no roster and no hierarchy. [2026-10-02: the platform's own workspace, Platform, is no longer exempt — it has a record like any other, born `hidden`; what may be done inside it is still the verbs.] Visibility gates the Workspace's page and its listing and nothing else: the records its panels read stay readable through the routes they always had until scoped reads arrive, and a clause that said otherwise would be a lock that is not there. Asking to join a private Workspace is a Task to its administrators.
Every Workspace opens on Focus. [PROPOSED 2026-10-02] The first stop on every rail is called **Focus** and is where a person lands: what matters now in this work, as counts that are doors to the views and to My Tasks, under the sentence the declaration gives for what this work is. Focus is derived, never declared — one card per view the map lists, each carrying what that view's panels have waiting — so an author who adds a view has added a card. Cumulus Admin's Focus is the fleet's measures and the administrators' queue. The views keep their names and their order after it. **The Employee Hub is the one personal page in the workspace shape.** A Workspace is impersonal and the hub is about one person, so it is not a Workspace: it is listed in no directory, has no record, no visibility and no bindings, and its door stays the face at the end of the bar. But it is drawn by the same rail with the same first stop — Focus (what you can do and what is waiting on you), My Record (your Human as the record frame draws it, and where you stand), Documents (what you handed in and what you were given) — because the console has one shape for a place a person works, and a second shape for the place about themself would be the console saying there are two kinds of place. The hub carries no assistant of its own: an HR agent on the screen was drawn, held as a placeholder and then abandoned (2026-10-02), because a chat box beside a dashboard is what the Framing refuses, and whatever answers questions about policy and a person's own record is the shell's one assistant, scoped, when that is designed.
Cross-system identity is a typed identity Link. [LOCKED] When a Flow decides "this external id is that canonical Entity," the result is a Link with required properties — not a new primitive, and not a thin plain Link: confidence (0–1), method (tax_id_exact / fuzzy_name / human_confirmed / …), decidedBy (a rule id or a Principal), reversibleWith (what to restore on un-merge), and standing (proposed / attested — the same standing every record carries; an exact-id match is attested by the rule that made it, a fuzzy match is proposed until a person stands behind it). It reuses existing machinery: a low-confidence match raises a Task (a human confirms); the decision is logged to the Ledger; and a proposed Link cannot be acted on until someone stands behind it. This satisfies the governance requirement that high-blast-radius merges be reversible and provenance-carrying.
An in-progress Entity is real but standing-gated. [LOCKED] A draft (an invoice extracted but not yet confirmed) becomes a real graph Entity at creation, standing = proposed — visible, queryable, renderable — but the Action Gateway refuses a consequential Action against it until someone stands behind it. Visible without being actionable. What the gate reads is the record's standing, who stands behind it, and it carries no business meaning: approved and rejected are business outcomes, what accounts payable decides about an invoice, and a rejected invoice is a real record the business will not pay; both are properties the type declares and its actions set. Four values for every type: proposed (a Flow proposed it from what it read; nobody has stood behind it; owned by the run that wrote it), attested (a person, through an Action, or an authoritative source stood behind it), retracted (a person said this record should not exist), superseded (another record stands in its place). In code the field is named standing, never status, so it cannot be read as a business property; an Action declares its effect on standing, and there are three — it attests, it retracts, or it leaves standing alone. No type adds to these values or writes them. A proposed record is owned by the run that wrote it, or by the Flow when the Flow is windowed; another run's write to it is refused with the owner named, never queued, and a settled record cannot be re-proposed by a write at all. Ownership, not a lock: nothing waits.
Governed Knowledge is a category, realized by existing primitives — not a new primitive. [LOCKED] The declarative middle between raw data and machinery code — prompts, the schemas answers must fit, model configs, thresholds and precedence orders as values, Rules, metric definitions, and Mappings — lives in the map: portable, versioned, audited. The doctrine point: a customer changing a threshold is changing governed knowledge in the map, not application code — which is what makes it safe (versioned, audited) rather than dangerous. Two kinds of governed knowledge edit differently, and the difference is the safety story. A value — a threshold, a precedence order, a retention period, a Policy's fields and their descriptions, a Skill's page — is editable in the shell, where the edit is a new content-hashed version superseding the last, with its Ledger row, so there is always a history; what the map declares in code is the seed, written when no record exists and superseded by whatever the shell has written since. An answer schema is authored, reviewed and shipped with the rest of the map and versioned by the hash of its content, because an answer no schema fits is a broken Flow; a prompt a Flow's correctness depends on is shipped the same way. [amended 2026-10-04: shell-edited prose was refused as "a version in no history"; with content versions and the Ledger it has one] A Mapping is governed knowledge too, not a primitive: it declares how a source's fields and identifiers bind to the ontology, plus identity keys and freshness policy, and a Flow reads it exactly as it reads a model configuration — it does no work of its own, and the transform logic (parse, embed, derive) belongs to the Flow. A Metric — which Runs, Tasks or Entities, narrowed how, under which arithmetic and window — is declared and versioned by content like a model configuration, and computed when it is read; it is never an Entity, nothing writes it to the graph and no run pins it, because it is a figure for a person or a screen and not an input to a decision. Where a Flow needs the number, the Flow computes and writes the derived data (a claim, a Dataset row) and a Metric may read that; a Metric is not a Rule, because a Rule returns a verdict and a Metric a number. [amended 2026-10-04: was "metric definition… computed by a windowed Flow into derived claims or a derived Dataset"] It is all realized by prompt-Kind Datasets, small record Datasets, and Rules; no new primitive. "Knowledge-ness" (does it make agents smarter?) and "governed-ness" (is it transparent/versioned/audited?) are independent properties. Governed knowledge other than a Metric is stored as Entities of a Knowledge type: its instances are declared in the map rather than observed, written at startup as attested records with the map as their source, versioned by the hash of their content with every version kept and superseded in turn, and pinned by every run that reads them, so a decision is explainable against the version that applied. A Policy is such a type. A model configuration is the same kind of thing and joins the graph when something reads it there; until then it is versioned the same way in its own store.
The ontology is graph-shaped; it does not all live in a graph database. [LOCKED] The domain model is Entities + Links + traversal (the logical graph). But physical storage is mixed, per access-shape: run state lives in the durable-execution engine's store; the active-trigger index lives in a fast key-value store (for O(1) dispatch); Dataset content lives in tabular / blob / vector stores by shape; the event log lives in a stream. The graph is the referencing spine that ties these together — not the universal container. Forcing everything (run journals, the dispatch index) into a graph database would be the wrong tool and would break the adapter boundaries.
Everything is authored in Python and exported to the map by decorators. [LOCKED] FDEs author the primitives as Python under full lifecycle discipline (version control, review, CI/CD); decorators export each one's declarative meaning into customer-owned map storage, and the engines interpret it. The governing invariant: the export conveys complete declarative meaning — transparent, structured where valuable and described in text where structure would be forced — and never a pointer back into Naviant code (which would hollow the map). It need not be re-executable; it must never be opaque.
Invariant

Every mutation is a decision (an Action through the Gateway), an observation (a Flow/Sync write audited by lineage), or a resolution (an audited automatic choice); every human decision is a Task routed through a Queue. No primitive does another's job — no un-audited writes, no bulk data forced through per-row Actions, no hierarchy hidden in a container instead of the graph. Rules are pure checks and never fetch. ModelCalls are governed. The ontology is Cumulus's model; storage shape never leaks up. (One face of the Governance spine.)

Common failure mode

Reaching for a missing primitive that is really an engine or a pattern — "Sync should be a primitive" (it is the pattern and the engine that move data, as the flow engine enacts a Flow). Conflating Trigger and Rule (causation vs. check). Making a Dataset the universal storage bucket (it is owned map content; machinery state is not a Dataset). An "Agent" primitive (the Flow is the agent; govern the ModelCall instead). A rule that is really code (it fetches or orchestrates → a Flow). A storage adapter's data model becoming the ontology.

Open
[CLOSED 2026-09-27] access-role and assignment-role are two kinds of one primitive: the platform role is the access ladder and the business role is assignment and, later, scope.
[OPEN] the threshold for keeping competing claims vs. storing only the winner, per field.
[PARKED] full bitemporal history; where "agent drops by your queue" ambient context attaches; the implementation of the authoring/execution model (replay-based execution with journaled steps; dual rendering of flow structure — per-run trace plus design-time diagram; step configuration read from map-resident knowledge; SDK ergonomics) — deferred to a dedicated implementation-design session.
Lens 3 · Architectural Layers

"What are the major parts, and how do they stack?"

Core model — three layers, one membrane
   EXPERIENCE LAYER  (Naviant-built shell · Workspaces · Task Queue · surfaces)
        |
   ===== THE GOVERNED CONTRACT  (command + observe) =================
        |         the membrane — nothing crosses it by a privileged path
   EXECUTION LAYER   (Flow engine · Action Gateway · Dispatcher)
        |  writes only through Actions / Flow · reads only through query / retrieve
   KNOWLEDGE & DATA LAYER  (ontology · graph · Datasets · event history · the Ledger)

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
[RESOLVED 2026-10-07] a materialized read-model for heavy interface reads — as a contract capability only, never a shell-to-storage line. Resolved in cumulus-vision/STORAGE.md, and the rule is this: a figure about *now* is counted on read (the Record port's indexed count, a bounded range, never a listing); a figure about a day that has **closed** is rolled up once, by a flow, into a projection in the Record port — one document per subject per day, reproducible from the Ledger and the runs, so it is never a second source of truth — and a series is those documents plus today counted live and marked partial. An analytical store holds the exported history and is read only through the API, only for a screen that asks what nothing declared, never on an operational page's path. The client never aggregates.
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
One kind of authored logic does the doing: the Flow (Lens 2). A business transaction is a per-case Flow; populating or maintaining the model is a windowed Flow — the same primitive, different trigger shape. A windowed Flow never waits on a person: an ask would suspend the run whose window is still open while the next tick started another, so what it cannot land alone it hands to a per-case Flow from a step, keyed so the same case is raised once, and that Flow asks.
Human-in-the-loop is first-class and is the point. An agent proposes, a human decides in seconds with full context, the system resumes; the judgment lands in the Ledger, never discarded.
A response pauses in place for a human and resumes in the same run. [LOCKED] When a Flow raises a Task it suspends — its state becomes a stored "waiting on a human" record, which costs nothing while it waits (no warm process) — and the same run resumes from exactly where it paused when the Task resolves. A new run is right only when the human's action starts an independent process. For an "always require approval before X" rule, prefer putting the gate on the Action itself (so it cannot be forgotten) over writing it into one flow.
Tasks route to Queues; Roles bind people to them. [LOCKED] A Task is put to a queue or a subqueue (or to one person), and every holder of a role bound there sees it in their personal "My Tasks." Assigning to a Queue rather than a person is what gives shared team queues, coverage during absence, and load distribution; assigning straight to a person is the brittle special case, and its honest form is a Queue with one member: every Principal is bound to a queue of their own, so a Task that concerns one person, the employee whose receipts want correcting or the manager a policy names, is routed to that person's queue and reaches them by the same list as every other Task. A Task may name several Queues at once; the first answer is the answer and a second is refused as already decided. A Task is one unit of judgment from one person: where two or more people must each contribute, that is two or more Tasks, and combining what they said is the Flow's work.

A Case is a pattern, not a primitive. [LOCKED] Some records are the centre of long-running work — an Opportunity, a Handover, a claim: their lifecycle is the business's, many Flows act on them over time, and none of those Flows is in charge. The doctrine names the pattern so that it is recognised, and adds no machinery for it. Such a record is an ordinary Entity: its stage is a property with a fixed set of values, its checklist is a Checklist about it, its people are Links, its page is the record frame until a type needs a screen, and the Flows that act on it are started by Triggers on its own Events. Anything that would make a Case a thing of its own — state that is not graph state, logic that is not an Action or a Flow, a queue or a page mechanism of its own — is the pattern being mistaken for a primitive. An Invoice is the same pattern with one Flow in charge; the day a second Flow acts on invoices, nothing changes.

There are no locks; a gate is a state on the record, and it comes before any Effect. [LOCKED] A run may wait on a person for days and nothing warm exists to hold a lock, so exclusivity between flows is business logic on the record in the business's own words — an invoice under review — set by an Action when a flow takes the record up and cleared by an Action when it is done, so the gate is visible on the record and in the Ledger, and checked as a Rule at the moment any other action runs. Every write carries the version it read and a write to a record that moved is refused naming what moved: a person reads again and presses again, a step re-runs. A flow that meets a gate does not roll back, because rollback is never assumed: it waits on the record's Event, or it fails to a person with what it did. So a flow puts its gate before anything that reaches outside — what comes before it is reading, model calls and proposed records, which are harmless to abandon — and a gate met after an Effect is the partial failure a person is handed. A failed run that leaves a gate standing is not stuck: it is at a person already.

A Task asks; a Checklist holds what is owed. [LOCKED] A Checklist is the core base Entity for the things that must become true about one record: it is about that record, its items are Parts held inside it — a thing with no identity of its own, exactly as an invoice line is — and it is finished when every item is done. It is core's because the console has to know the shape to draw checkboxes, sections and sub-items, and the map decides which checklist a record gets and when, in code, as a subclass carrying the Rules for items that are done by evidence. Who owes an item and by when is never on the item: assigning one is an Action that raises a Task to a person's own queue or to a queue, naming the checklist and the item key, with a due date; the Task's answer runs complete on that item, or leaves it open with a reason; reassigning supersedes the Task and raises another; an item done by evidence raises no Task. So a person has one list, My Tasks, and the record shows each item with who holds it and by when, read off its open Task. Three things change about Task to carry this: it has a due date; it may be raised by an Action as well as by a Flow's ask; and its answer has a consumer, a run to resume or an action on a record to run. Every Task still goes to a queue, is answered once, is audited and has a view. An item cannot be the source of a Link: evidence is a document reference on the part, or a Rule over the record's own links. A generic board of work about nothing is not this and is not built; when a customer wants to run projects, a Project is a record the map declares and a Checklist is about it.

Interest is a Link, the Feed is a read, delivery is a Surface. [LOCKED] Telling somebody something is not a Task and not a primitive. Who cares about a record is already a Link on it — owner, successor, submitter — and one declared core Link, watches, from a Human to any record, is following on purpose. What happened is already the Ledger, with its subject and who did it. The Feed is one read that joins the two: what changed on records linked to me and what waits at my queues, since a time — computed on read, stored nowhere, never a queue, shown on Home. Delivery to email or Teams is a Surface reading the same Feed. Work that expects a person is a Task at their own queue; a notice expects nothing.

Flows write to the knowledge layer — through governed writes. [LOCKED] Reaching the data/knowledge layer is a question of route, not of permission: a Flow writes there whenever the work requires it, but through an Action or an emitted Event, never a raw storage write. The direct-vs-decoupled rule: write directly (via an Action) when it is a deterministic in-scope part of the transaction (mark this invoice approved, set this resolved value); write via an Event (the decoupled flywheel) when it is a knowledge improvement that may need policy or curation (an unknown vendor that might graduate to known), because promotion to shared canonical knowledge is a governed decision, not an inline side effect.
The flywheel is a concrete mechanism. [LOCKED] A human correction flows back decoupled, via an Event (e.g. ExtractionCorrected), not inline — so the business path stays lean. Two distinct knowledge assets, not one blob: the model config (prompt/params) and the correction set (accumulated raw→corrected pairs — the crown-jewel eval/tuning dataset). Write-back rule: always append the correction to the correction set automatically (and log it); promotion into canonical knowledge (the model config) is never automatic by default — a declarative Rule decides per situation whether to auto-promote (e.g. low-stakes vendor, after N consistent corrections) or raise a curation Task; and every knowledge change, automatic or human, lands in the Ledger. This closes the unknown-vendor loop: the first invoice is best-effort plus human confirmation, which seeds the correction set and puts the vendor in the graph, so the second invoice matches a vendor that is already there and standing attested — the vendor graduates from unknown to known through use.
Reconciliation is a Task-rooted audit tree. [LOCKED] A discrepancy raises a Task; the human's choice is a decision in the Ledger; each correction pushed to a wrong system is an Action tagged caused by that Task. A recurring discrepancy re-elevates with its history attached ("third time; you set $75k, it keeps reverting"), turning a nagging repeat into a systemic signal.
Failure escalates to a human; it does not silently roll back. [LOCKED] Transient failures retry safely (a completed step, on retry, does nothing rather than duplicating); a persistent failure raises a Task with full context ("posted to two of three systems; the third rejected it"). Rollback is not assumed — systems of record often cannot be un-written; true undo is offered only where an Action has a genuine safe inverse. (Governance spine.)
Invariant

A human is invoked only where judgment is the point — never for deterministic work. Every human decision commits through a guarded Action and lands in the Ledger. When in doubt about a consequential change, propose it as a Task rather than committing automatically. (One face of the Governance spine.)

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
MDM, CDC, and sync are not separate mechanisms — they are Flows (plus sourced transport). [LOCKED] CDC (change-data-capture) is a sourced change-capture adapter (Transport) feeding an inbound landing Flow. Sync is a pattern — an inbound Flow plus an outbound Flow plus automatic echo suppression plus a reconciliation policy — not an atomic thing. MDM (master-data management) is emergent from primitives already defined: canonical Entities, cross-system identity Links (typed, with confidence and reversal — Lens 2), and claim reconciliation (retained claims resolved by the property's declared authority order). None requires a new primitive; each is Flow logic doing a different job.
The deployment target is the customer's choice — Cumulus's instance, or the customer's own cloud (bring-your-own-cloud, customer-managed encryption keys, and where required a zero-data-retention model configuration).
Integrity is a custody concern. [LOCKED] Every stored revision carries a sourceVersion (the source's own version — its claim) and, where warranted, a contentHash (Cumulus's independent verification). Disagreement is a signal that raises an Event and possibly a Task. Hashing is optional and cost-tiered (large media trusts the source version; small high-trust text hashes eagerly); the verification policy rides in the Mapping.
The reference (storageRef) lives at the revision grain and encodes a portable resolution path — system, protocol, and the customer's own credentials — even when Cumulus resolves via a faster internal path.
Invariant

Every reference resolves with credentials and protocols the customer owns — never a vault only Naviant's engine can open. Original bytes move by reference, directly between the store and whoever needs them, rather than through the API tier; a rendition made for a screen or a model — a page image, a PDF of a scan — may be served by the API, because it is made from the original and is not it. Transport is sourced; the map is owned. (One face of the Governance spine.)

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
A Surface is a delivery channel, not a work artifact. [LOCKED] Task and Workspace are the work (the "what"); a Surface — the Cumulus shell, Teams, Outlook, Claude Desktop — is the where, the channel through which a Task or Workspace reaches a person. A single Task may appear on multiple surfaces. Surfaces belong here, alongside REST/SDK/MCP, because they are a reach concern. This is where the surface-reduction goal (Framing) lands operationally: reduce the employee's environment toward a small interoperating set of surfaces, let work be executed in any of them, but keep it driven by Cumulus. [OPEN 2026-09-29] The shell carries an assistant pane beside any page, opened from one sparkle on the bar. Today it is a placeholder that repeats what is typed and reads nothing. What answers it is undecided: the Framing refuses a chat box bolted onto a dashboard, so whatever does must be a governed ModelCall over the knowledge layer, scoped to the page and to what the caller may read, proposing and never committing — and until that is designed the pane says what it is.
MCP is an ambient surface, not a generic developer API. Scoped to the connecting user (an agent reaches only what that user may reach) and HITL-mediated: reads and reasoning are ideal MCP tools, but an MCP write proposes (a low-risk guarded Action, or a Task) rather than committing directly. [PROPOSED 2026-10-08] The write posture, decided for the first cut: a model proposes and a person confirms. An MCP tool may propose a filled input for a Flow the connecting Principal may start, or an answer to a Task at that Principal's queue, each validated against the type the map declared and summarised in words; a separate `confirm` carries the exact proposal out through the same two calls the console makes, and a proposal expires in minutes. No Action on a record is proposable from MCP until a proposal has a queue to be decided at (cumulus-vision ASSESSMENT-ACTIONS §4.3). The connecting Principal is a Human by personal key, or an Agent — the sibling record — by its own key, holding roles at scopes and capped at member. See cumulus-vision WORKPLAN-SURFACES.md.
The SDK is two artifacts. A consumer SDK (customer-facing, over the public contract) and an authoring/kernel SDK (Naviant-owned, for FDEs to define what the engines run). Different owners, different stability guarantees — never conflated.
The shell is just another consumer — over the consumer SDK, the same projection everything else uses.
Invariant

Every face and every surface goes through the one contract, the one Action Gateway, the one audit path. No face has a capability the core does not govern; MCP never gets its own write path. (One face of the Governance spine.)

Common failure mode

Auto-generating MCP tools one-for-one from the REST spec (wrecks agent legibility, exposes destructive operations, provides no composites). Treating the two SDKs as one package. Treating a Surface as a kind of Task/Workspace rather than a channel they are delivered through.

Open
[OPEN] whether an MCP write may commit a low-risk Action or must always raise a Task. [PROPOSED 2026-10-08: neither, for the first cut — it proposes a Flow start or a Task answer and a person confirms; see the clause above.]
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
FDEs author the Tier-2 definitions; the customer owns them. "Naviant builds it" (labor) and "the customer owns the configuration" (asset) are both true. Definitions — Flows, Mappings, Rules, configs — are version-controlled, CI/CD-shipped, and must be transparent and portable (Lens 1). Three things change under a run that is waiting on a person, and each is versioned its own way. A run pins the Flow version it started on, and the integer moves only for a change that would break replaying that run's journal through the new body, never for a new step, a fix to an unreached step, a prompt edit or an optional field. When it moves, a run waiting on a person is restarted on the live version by the platform itself at its next tick — the old run superseded, its open Tasks closed unanswered, what people had already answered asked again, the actor a name for the deployment — and a failed run waits for a person to restart it, because it was already theirs to look at. Nothing in flight is migrated. Keeping an old version deployable beside the new is held until a customer has runs that wait longer than a release cycle. The ontology carries a version, every write stamps the version it wrote under, an additive change needs nothing, and a breaking change ships with a migration Flow that rewrites shape and never standing or meaning, refused at startup without one. Governed knowledge is versioned by the hash of its content: a changed prompt is a new version, never a migration.
A Flow ships with its own evaluation. [PROPOSED] A Flow declares assertions over graph state at named checkpoints — after the resolve step the Vendor is linked and nothing is extracted yet; at commit the Invoice, its lines and the match exist; a held invoice asserts its entities are absent — versioned with the Flow, so a change to the Flow changes the assertions with it, and the correction set its Tasks produce is its regression data. A behaviour spec written as prose is unfalsifiable; the same spec written over graph state is a test.
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

   The SEAM (ontology · Action-Gateway governance · human-in-the-loop ·
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
[OPEN, PART RESOLVED 2026-10-07] the per-port contracts: the four **storage** ports — Record, Graph, Analytical, Blob — are drafted in cumulus-vision/STORAGE.md, each with its required floor, its optional enrichments and the Google Cloud adapter behind it (Firestore, the adjacency list over it with Spanner Graph as the adapter a multi-hop traversal earns, BigQuery fed by sourced export, Cloud Storage); vector and text search are enrichments a store carries and not ports of their own, because a separate vector database is a product decision and a warm endpoint with a floor. The remaining ~12 ports are still undrafted.
[OPEN] the bundled-egress question: a bundled API-Gateway-port adapter for greenfield customers (build, wrap, or neither)? Never a mandated integration platform; always below the Action Gateway.
[OPEN] the concurrently-suspended scheduling pattern at very large scale; build-vs-adopt per plane; multi-port adapter scale thresholds.
The Governance Spine · Why a CISO Can Say Yes

Not a lens — the rules that run through all eight. Every lens's invariant is, underneath, one of these claims. Placed last because it draws on every primitive and layer, but foundational, not an afterthought. Most likely to be read in isolation, so it restates the terms it depends on.

Core model — the Action Gateway and the Ledger
   any actor (an agent · a Flow · the shell · an MCP tool)
        |
   [ ACTION GATEWAY ]  <- Cumulus-built control point. NOT an off-the-shelf product.
     checks: schema? permitted (role- and attribute-based access)? within limits?
             does the target's standing allow the change?
     then:   commit the change and emit an Event
        |
   [ IMMUTABLE LEDGER ]
     every action and every human decision, time-stamped -> history is replayable

Diagram: every deliberate change funnels through one checkpoint that validates and permission-checks it, then lands in the Ledger, the permanent record.

Two definitions this section leans on: a decision write is a change Cumulus commits because it determined the change should happen (through the Action Gateway); an observation write is Cumulus recording what an external system reported (landed by a Flow, audited by per-revision lineage, in bulk — not one approval per row).

The spine's claims
No un-audited writes, ever. Decisions go through the Action Gateway; observations through audited lineage; resolutions record which value won and why. A direct, un-audited database write is the single anti-pattern the architecture exists to prevent.
What Cumulus asserts is tiered. [LOCKED] An attested record with a source is what Cumulus asserts as true, and the only tier an agent may act or spend against. A proposed record is real, visible and not yet true. A document in a governed Dataset is evidence a fact may rest on, never a fact. Derived retrieval — chunks, embeddings, a graph a tool extracted from documents — is candidates a model reads, never asserted; a contradiction between it and an attested record is a finding that raises a Task, not a coin toss. Every answer the platform gives names its tier.
Two things are called "gateway"; never blur them. [LOCKED] The Action Gateway is the Cumulus-built governance brain (ontology- and permission-aware, Ledger-writing) — the seam, never a product. A commodity API gateway (Kong/Apigee/cloud-native) is delivery plumbing — rate-limiting, retries, auth — that sits below the Action Gateway and never governs. Related: Cumulus does not require an integration platform ("iPaaS"); it harmonizes semantically (ontology + Mapping + identity resolution), which is higher-value than transport harmonization. A customer with no integration layer is ideal, not blocked.
Fine-grained permissions everywhere, including reads. Role- and attribute-based; even viewing a document is recorded, not just changing it. Decisions and governed writes are the Ledger and live in the map's store; a read is high-volume operational audit and is recorded to an append-only audit sink, a sourced port, never the object store.
Full history and provenance; the state can be rebuilt from the log. Each derived datum records which source version produced it; you can reconstruct any past state. Compactly: state = f(events, ledger) — with one exception (claim 9). Faithfulness is checked, not hoped: no attested value stands without a claim naming its source, and a sweep reports any that does.
Echo suppression is automatic machinery. [LOCKED] Every outbound change is stamped "originated by Cumulus," so when it echoes back through a feed it is recognized as its own and recorded as a no-op — not mistaken for a fresh external change. Automatic, because per-flow opt-in would eventually be forgotten and cause oscillation.
A record's standing is a governance gate, not a label. [LOCKED] A proposed record is visible and queryable, but the Action Gateway refuses a consequential action against it until someone stands behind it. Visible without being actionable. In code the gate reads the record's standing, proposed or attested, never a business word such as approved; see Lens 2's standing-gating clause for the four values and their meaning.
Knowledge changes are logged too. [LOCKED] Every improvement to knowledge — human-approved or automatically promoted — is recorded and reversible. Automatic ≠ invisible. The Ledger records both human decisions and automatic promotions.
High-impact changes are reversible and carry provenance — especially entity merges (the typed identity Link carries confidence and reversal info). Competing field values are retained alongside the winner, so a bad choice is correctable without re-pulling.
Deletion is reconciled via crypto-shredding. [PROPOSED] Personal data is encrypted per person; forgetting destroys the key; structure survives, content becomes a tombstone; replay still runs. So claim 4 is "rebuildable except for cryptographically-forgotten fields." Open sub-questions in Lens 1.
Governed operations are audited — including model calls. [LOCKED] A ModelCall (an LLM invocation inside a Flow) records which model ran, its inputs, and its captured output — for audit and cost control. Which models run in the environment is itself a governed, reportable fact.
Failure escalates to a human; it does not silently roll back. [LOCKED] Transient failures retry safely; a persistent partial failure raises a Task with full context. Undo is offered only where an Action has a genuine safe inverse; rollback is never assumed.
The spine's invariant

Nothing writes to the core by any path that skips governance: decisions through the Action Gateway, observations through audited lineage, knowledge changes and model calls into the Ledger. Nothing acts without leaving a trace. Four temptations are forbidden because each is a governance hole disguised as a convenience: an opaque step; a reference only Naviant can resolve; a bulk write skipping lineage "for speed"; the shell reading storage directly "for latency." The spine holds across every surface (Lens 6) and every layer (Lens 3).

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
   ONTOLOGY               Entity · Link (incl. typed identity Link) · Part · Action · Event
   CONNECTIVE             Trigger (a binding) · Rule (a check)
   PROCESS & DATA         Flow · Dataset · System
   IDENTITY/ACCESS/WORK   Principal · Role · Queue
   EXPERIENCE             Task · Workspace

   Governed operations:   ModelCall · Effect
   Category:              Governed Knowledge (Policy, prompts, answer schemas, model configs,
                          Metrics, Mappings, thresholds and precedence
                          orders, Rules)
   Stamped facts:         standing · source · provenance (the write site) · the ontology
                          version · id, key aliases, external identity — stamped by
                          machinery, never authored
   Content items:         File · Record · Row — what a Dataset holds
   Not primitives:        Surface (a delivery channel, Lens 6) · Sync (a pattern) ·
                          Transport (sourced) · Read (a capability) ·
                          Mapping (governed knowledge, read by a Flow)
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

Map vs. machinery; three portability tiers; transparency (not re-executability) as the bar. Three write regimes (observation / decision / resolution), a resolution decided by the property's declared authority order. Governance as a spine. Event / Trigger / Rule as connective tissue, with Trigger (a binding) and Rule (a check) firmly distinct; a Rule a transparent, governed pure check whose implementation is reserved. Two-level evaluation (Dispatcher for Triggers, the running response for Rules). Response-kinds closed at Flow and Action. One authored-logic primitive, the Flow, with per-case vs. windowed read off its Trigger rather than declared. Entity, Link and Part as the three kinds of thing in the ontology; a Link a declared type, most of them without properties; standing (proposed / attested / retracted / superseded) as the machine-owned gate, carrying no business word, and an Action attesting, retracting, or leaving it alone. Datasets plural by governance, six dials, content only, structure in the graph, entry only by a Flow's write with staging in front of it, source-and-recipe for derived sets, competing claims retained with their evidence. The ontology graph-shaped but not physically one database. Human (and later Agent) as the records and Principal as the caller resolved per request; Role (platform/business/workspace kinds) and Queue for access and work routing; a held role recorded on the Human, queues bound through roles; Queue↔Workspace an optional many-to-many Link. Typed identity Link with confidence, method and reversibility. ModelCall as a governed operation under a content-hashed configuration (no "Agent" primitive), and Effect as its outbound twin, committed by a Flow as its own step and never by an Action. System as a primitive: what an outside system holds, and the business steps taken in it. Governed Knowledge as a category, not a primitive — shell-editable values, authored and content-hashed prompts and schemas, Mapping and metric definitions among them. Sync a pattern, Transport sourced, MDM/CDC/sync as Flows rather than new mechanisms. Automatic echo suppression; same-run suspend/resume; the flywheel write-back; retry-then-escalate failure handling. Action-Gateway vs. API-Gateway distinction; no mandated integration platform. Python-plus-decorator authoring exporting to the map. Surfaces as delivery channels; surface-reduction as a goal. Case as a pattern and not a primitive, carried by a record's own Events (emitted only for what a Trigger names, fired once per record, keyed) with an Action as a Trigger's response beside a Flow; no locks, a gate a state on the record before any Effect; Checklist as the core base for what is owed about a record, its items Parts, assignment a Task with a due date whose answer runs an action; the Feed a read over Links and the Ledger; which system leads on a property a declaration that may move to Cumulus, regulated ledgers excepted.

Still open, and parked

Open: the threshold for keeping competing claims rather than only the winner; the four crypto-shredding sub-decisions; the replay-as-hard-contract question and the reversal semantics for merges; whether reading and reasoning share the Execution contract or get a sibling query API, and the materialized read-model behind it; the confidence/blast-radius thresholds for auto-commit vs. propose-a-Task; enrichment timing and the re-elevation policy; ingest-and-own vs. reference-in-place as a per-asset-class policy, and the derived-artifact policy beside a referenced original; the MCP write posture and the synchronous-vs-asynchronous shape of a flow trigger; "no opaque steps" as a hard authoring gate, and the surface of the platform kit; the per-port capability contracts; the bundled-egress adapter; the concurrently-suspended scheduling pattern at very large scale.

Parked (deferred by decision): the four implementation threads (replay-based execution, dual rendering of flow structure, step configuration as map-resident knowledge, SDK ergonomics); full bitemporal history; the "agent drops by your queue" ambient interaction.
