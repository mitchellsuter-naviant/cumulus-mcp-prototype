# Decisions

The doctrine states the current position only. This is where the position
came from: one dated entry per decision, what changed and why, newest last.
A reader who wants to argue with the doctrine reads here first.

## 2026-08-30

- **Exemplars retired.** The accounts-payable flow dropped the extra call
  that named the vendor and the per-vendor template it extracted against:
  two mechanisms doing what one governed prompt does, and keeping them in
  step with each other was pure cost. The flywheel keeps two knowledge
  assets, the model config and the correction set, and never a third. The
  doctrine followed the decision the code had already made.

## 2026-09-08

- **Datasets are plural by governance.** One Dataset per set whose
  retention, legal hold or read access differs, however many that makes.
  A reader applying the earlier clause put invoices and legal contracts in
  one set because no listed dial differed between them.

## 2026-09-14

- **Whether a Dataset's Role dial does any work — open.** The distinction it
  carries, curated knowledge against data, already lives in the graph: what
  makes a policy document knowledge is that an entity vouches for it and a
  ModelCall reads it, both observable facts, and no engine reads a declared
  Role. No Dataset declares one until this is settled; an estate view that
  wants "agents read this set" derives it from the model-call rows.

## 2026-09-15

- **Standing, not status.** Every record carries a machine-owned standing
  (proposed, attested, retracted, superseded) and no business word. Approved
  and rejected are what accounts payable decides about an invoice, so they
  are properties the Invoice type declares and its Actions set.
- **The record Kind.** A Dataset may hold semi-structured items as
  delivered, with provenance; no separate config Kind.
- **The Ledger and the event history are record Datasets, not machinery.**
  Append-only Tier 1 assets that export as records, which is how Lens 1 can
  call them the customer's while the Dataset clause still excludes run
  journals, the trigger index and queues.
- **The spine reads standing too.** Its gate claim said an in-review record
  waits "until approved". The gate reads the record's standing, proposed or
  attested, and never a business word; one vocabulary in both places.

## 2026-09-16

- **Entity is the only name.** The primitive was called Object and Entity in
  different places. One name, Entity, everywhere: doctrine, plan, code,
  console, routes and collections. The code rename is owed by the next
  build step.
- **A Link is declared as a type of its own** with a source, a target, a
  cardinality and an inverse name; most carry no properties, some may.
  Under review on 2026-09-18: whether ordinary links keep the one-line
  spelling on the source entity, with the class form reserved for a link
  that carries facts.
- **Mapping is governed knowledge, not a primitive.** It does no work of its
  own; a Flow reads it as it reads a model configuration. Ratification
  pending; the Lens 2 diagram still lists it until then.
- **Metric definitions are governed knowledge**, computed by a windowed
  Flow. Not a Rule: a Rule returns a verdict, a metric a number.
- **A ModelCall runs under a content-versioned configuration**, and every
  version that ever ran stays readable.
- **A claim may point at its evidence.** A claim carries its source, its
  confidence and the write that made it, and may name the file, the page and
  the region it was read from, so a value resolves to something a person can
  look at and not only to a step in a journal. Confidence lives on claims and
  on identity links, never on an entity as a whole.
- **A proposed record is owned by the run that wrote it**, or by the Flow
  when the Flow is windowed. Another run's write to it is refused with the
  owner named, never queued, and a settled record cannot be re-proposed.
  Ownership, not a lock: nothing waits.
- **Content enters a governed Dataset only by a Flow's write**, with the run
  and step recorded. What arrives from outside lands in a staging Dataset
  that says nothing about a file but that it arrived, held by the runs it was
  handed to; the person or system that uploaded it never chooses where it
  belongs.
- **Faithfulness is checked, not hoped.** No attested value stands without a
  claim naming its source, and a sweep reports any that does.
- **Reads are audited to a sink, not into the Ledger.** Decisions and
  governed writes are the Ledger and live in the map's store; a read is
  high-volume operational audit, recorded to an append-only audit sink, a
  sourced port.
- **Three things are versioned, three ways.** A run pins the Flow version it
  started on and is resumed on it or restarted on the live one; the ontology
  carries a version every write stamps, additive changes need nothing and a
  breaking change ships with a migration Flow; governed knowledge is
  versioned by the hash of its content, so a changed prompt is a new version
  and never a migration.
- **The index names the stamped facts and the content items** — standing,
  source, provenance, the ontology version, id, key aliases, external
  identity; File, Record, Row — taken from WORKPLAN-DATA §2, layers 4 to 6.

## 2026-09-18

- **Three kinds of record-keeping, three names.** The *run journal* is a
  Flow run's own memory of its steps, kept so it resumes without redoing
  work; machinery. The *Ledger* is the permanent record of every governed
  operation, one table with one row shape, each row naming what it is
  about and who did it; the customer's, and the audit. *Logs* are
  operational telemetry and never evidence. "Curation ledger" is retired
  as a name: there is one Ledger and it holds everything governed,
  including model calls and human decisions. Rejected: splitting the
  Ledger by subject (an entity ledger, a system ledger), because one
  approval touches an entity, a file and a system and should be one row.
- **System is a primitive; Connector is not a word.** A System is a system
  of record or a source the map declares: what lives there and which
  adapter reaches it, with credentials in the deployment. The adapter is
  machinery and needs no name; what the doctrine once called a Connector
  is a System's adapter.
- **Effect is a governed operation**, the outbound twin of ModelCall: an
  Action's committed consequence in a System, through the same Action
  Gateway, with an idempotency key, Ledger row as intent then committed,
  Task on refusal. This ends "Action Gateway" naming two things: the gate
  is one, and an Effect is what passes through it outward. Open: where the
  translation from an Entity to what the System expects is written (on the
  System, on the Action, or both).
- **Removed from the doctrine:** the note that Entity was formerly Object.
  Retired names do not appear in the doctrine; they appear here.

## 2026-09-18, later

- **The System holds the translation, the Action names the Effect.** How an
  Entity is phrased for a system, and how that system's rows are read back
  as Entities, are pure functions on the System declaration, so a reader of
  one System sees everything Cumulus says to it and reads from it. An
  Action declares its Effect, `@action(attests=True, effect=Erp.vendor_bill)`,
  so no flow can forget it. The adapter is core, one per product or
  protocol, and is the only thing that speaks HTTP; consultants write no
  transport code. A System declaration is one customer's instance of a
  product (Acme's NetSuite) over the shared adapter for that product.
- **An Effect is its own Ledger row**, caused by the Action's row, in the
  same table: intent before the call, committed with the system's id after,
  or refused with the reason. A committed Effect that created a record
  writes the identity Link to it, and the read side uses that Link to
  recognise its own echo. Transient failure retries under the same key;
  persistent failure fails the step, and the run is resumed by hand until a
  Task on persistent failure is built.
- **Open: whether a System declares its resources** (`vendors =
  resource("vendors")`) or names them only inside its functions. The case
  for declaring: the adapter, the identity Link and the map export all need
  the name.

## 2026-09-18, evening

- **The Flow commits an Effect explicitly; an Action changes only the
  graph.** Reverses the morning's `effect=` on the Action. The consultant
  writes `ctx.graph.act(invoice.approve, ...)` and then, as its own line,
  `ctx.system(Erp).create(vendor_bill(invoice))`. Explicit beats implicit: a
  graph verb never makes a network call, the consultant decides when and
  whether a consequence reaches a System, and the translation function is
  visibly theirs. Given up: the guarantee that approving always posts. The
  Ledger shows an approval with no Effect as what it is.
- **Resources are not declared.** The record names its table as a string
  the system itself defines: `Erp.record("vendorBills", key=invoice.id, ...)`.
  A typo fails at the system, with its message. Closes the morning's open
  question.
- **The idempotency key is named on the record**, by the consultant, in one
  word, rather than derived by core from the action.
- **The translation functions are methods on the System class.** The
  phrasebook and the system it is for sit in one place, and `ctx.system(Erp)`
  hands the flow one object carrying the consultant's methods and core's
  verbs (`record`, `create`, `get`, `changed`), which are reserved names and
  cannot be overridden. No resource declarations, no marks, no scanning:
  a plain subclass with plain methods.
- **Where the request code lives.** In the adapter, behind three verbs
  (`create`, `get`, `changed`; a fourth, `perform`, when a flow first needs
  an operation that is none of those). A product's adapter is core
  (`core/adapters/netsuite.py`), written once during the first engagement on
  that product. A bespoke API's adapter is machinery too but reusable by
  nobody, so it lives in `server/customer/<name>/adapters/`, ships in that
  customer's image, and is the one place in a customer tree allowed to
  import an HTTP client. The customer keeps the map; the adapter is
  plumbing they do not.
- **No adapter yet.** Reverses the two entries above it. A System is a class
  whose methods do the whole job, request included, with names that read as
  business steps (`erp.post_vendor_bill(invoice)`,
  `erp.purchase_orders_changed(since)`). Core supplies the base class, the
  configured HTTP client from settings, `ctx.system(Cls)`, and the `@effect`
  mark that writes the Ledger row around an outbound method. The adapter
  layer, the generic verbs and the `Record` value are deferred to the day a
  second customer runs the same product, which is when the shared request
  code is extracted with two real examples in hand. Accepted for now: a
  System class holds transport code inside the customer tree; the
  map-versus-machinery line for Systems is redrawn at that extraction.

## 2026-09-18, go

- **Explicit today, machinery at the second caller.** Vendor matching is
  `find` plus a similarity function in the map; a Trigger is not built for
  anything a clock can do; ERP rows become entities directly with no landing
  Dataset; claims wait for the first disagreement between sources;
  ownership of proposed records waits for the first collision; versions are
  the two stamps (ontology version on writes, flow version checked on
  resume) and no migration framework; kinds are Money, Date, Literal and
  list[FileRef] only; Dataset dials with one legal value are dropped; echo
  suppression waits until a flow reads what another writes; `follow` waits
  for the three-way match.
- **Kept in core against that test:** the Identity link type, because the
  owner wants the ERP's id for a vendor to live on the link and not on the
  Vendor; the run lease and named ask sites, because they fix wrong
  behaviour rather than anticipate a need.
- **One review screen.** The AP Task asks for the vendor and the corrected
  figures together; a second Task for the vendor is not raised.
- **Event and Trigger, minimal and portable.** An Event is a fact the
  machinery writes (a file arrived, a run completed, an entity was
  attested). A Trigger is a map declaration binding a fact or a clock to a
  Flow. The Dispatcher reads triggers and starts runs through a `Dispatch`
  port (inline adapter for local and tests, Cloud Tasks for GCP) and one
  authenticated tick endpoint any cloud's cron can call. No cloud name in
  core outside its adapter.
- **Build order for this pass:** A1 Entity everywhere, standing, merge
  writes, retract through the gate, no hand-writes; A2 one Ledger, named
  ask sites, run lease, version check; B Link as a type, Identity, `find`;
  C1 System, the demo ERP, the writeback; C2 Event, Trigger, Dispatch, the
  sync flows; D the console.

## 2026-09-19, pass 1 built

- **What landed** (cumulus-vision, uncommitted, not deployed): Entity
  everywhere; standing with proposed as the default and a three-row gate
  table; merging writes; retract through the gate and no hand-writes from
  the API; one Ledger with the run journal and logs named apart; named ask
  sites; a run lease; the flow version checked on resume; Link as a type
  with the one-line spelling minting it; Identity in core; `find`;
  `System` with `@effect` and `ctx.system`; Acme's ERP against the demo
  ecosystem; the AP flow matching vendors in one review Task and posting an
  approved invoice as a vendor bill; Events (one kind, `run.completed`),
  Schedule and On triggers, a Dispatch port with inline and Cloud Tasks
  adapters, one authenticated tick route; vendor and purchase-order sync
  flows chained by a trigger; the console renamed, browsing and retracting
  only, with the single review screen, a Ledger page, effects, identity
  links and triggers shown.
- **Judgments worth keeping.** An Effect's Ledger rows are not keyed by
  operation the way graph rows are: a retried outside call is a second
  event, so a 500 leaves a refused row and, later, a committed one. The
  internal routes authenticate with a shared header from a Secret Manager
  token rather than OIDC, because any cloud's cron can send a header; OIDC
  is a later adapter. `SELF_URL` is the Hosting domain, stable across
  deploys. A Schedule's interval is held open by a run of any status.
  `purchase_orders_changed` returns the order id, the vendor id and the
  entity, because `placed_with` needs the vendor id.
- **Still to do from the review**, next pass: Money and Date as value
  kinds; split `core/graph.py`; the CLAUDE.md rewrite with LESSONS.md and
  ROADMAP.md; the WORKPLAN-DATA re-cut; roles; console URLs; the demo ERP
  function deploy, which needs the firebase CLI.

## 2026-09-19, doctrine restated current-first

- **The doctrine states the current position only.** Twenty dated inline
  notes are gone from it, and no clause leads with the wording its own
  correction retires — which a document that tells agents to read fragments
  could not afford. The history is here, which is where a reader who wants to
  argue with a clause starts.
- **Part is the third kind of thing in the ontology.** No identity of its
  own, held inside its whole, written and loaded with it. It joins the Lens 2
  diagram, the glossary, the engine table and the index, and the Dataset
  clause's "there are only Entities, Links, and content Datasets" is deleted:
  it contradicted the part clause four paragraphs above it.
- **Mapping is not a primitive anywhere.** Ratifies 2026-09-16 and finishes
  it: out of the glossary's primitive list, the Lens 2 diagram, the engine
  table, the index and the Lens 8 seam, and into one glossary line under
  governed knowledge. The doctrine's own engine table had already given it no
  engine, which was the tell.
- **The engine table carries a state column**, `built` / `planned` /
  `deferred` per primitive, read off what cumulus-vision runs today: Role is
  the only planned one and Workspace the only deferred one, and a Rule is
  built as a pure method with the generic interpreter deferred. A reader of
  the doctrine can now tell which sentences are standing code.
- **A Dataset has six dials**: Kind, Custody, Retention, Legal hold, Read
  access, and Source vs. Derived. The Role dial is deleted, closing
  2026-09-14; update mode and keep-history go with it, because neither has a
  caller anywhere and a dial nothing reads is a dial an author guesses at.
- **An Action's effect on standing is three, not four**: it attests, it
  retracts, or it leaves standing alone. `reopens` was never decided and
  nothing implements it.
- **A resolution is not a response.** The write regime stands and is stated
  properly: a resolution picks a winner among competing claims by the
  property's declared authority order — machinery applying a declaration —
  with a Rule admitted where the order cannot decide and a Task where neither
  can. But nothing starts a resolution and nothing waits on it, so it leaves
  the response-kind set, which is now Flow and Action.
- **The disciplines stack has nine layers.** The Framing said "nine" twice
  over a stack of eight; enterprise integration — validated, permissioned
  changes to systems of record — was named in the Framing's own second view
  and missing from the stack. It is now its own layer between
  governance/security and durable execution.
- **A rendition may be served by the API.** The Lens 5 invariant said bytes
  never pass through the API tier while every page image and every download
  did. Original bytes move by reference; a rendition made for a screen or a
  model — a page image, a PDF of a scan — may be served by the API, because
  it is made from the original and is not it.
- **Customer-editable splits in two.** A value — a threshold, a precedence
  order, a retention period — is editable in the shell, versioned and audited
  there, and that is what makes a customer changing it safe. A prompt or an
  answer schema is authored, reviewed and content-hashed, because a shell
  edit to a prompt is a version in no history and an answer no schema fits is
  a broken Flow.
- **"Record facts" becomes "Stamped facts"**, and the glossary defines
  standing, claim, key alias, File, Record and Row, so the index names
  nothing the document has not defined.
- **The locked-decisions and still-open paragraphs are regenerated** from the
  body. The old summary claimed two Dataset dials where the body listed nine,
  a materialized temporal graph no clause states, and knew nothing of
  standing or of any September amendment.
- **The thesis' small proofs are the first prototype's**, each carrying a
  clause on what the current build can do: no claims, one-hop traversal from
  a resolved Employee, no pinned reads, no point-in-time rebuild. The
  reify-or-bind rule is inlined rather than pointing at the prototype's
  README, "object" is gone, and a Flow shipping with assertions over graph
  state at named checkpoints — versioned with it, the correction set its
  regression data — is [PROPOSED] in both documents, in the thesis'
  evaluation argument and in Lens 7.

## 2026-09-19, governed knowledge and policy

- **Policy is governed knowledge stored as an Entity.** A `Knowledge` base on
  `Entity` marks a type whose instances are declared in the map rather than
  observed, and which is versioned by the hash of its content: a change at
  the next deploy writes a new record superseding the old, every version
  stays readable, and a run pins the versions it read. Everything else is
  ordinary Entity machinery: standing, source, Ledger, links, `find`, the
  future MCP read. `Policy(Knowledge)` supplies purpose, effective-from and
  review-by; each concrete policy is its own type with its own fields. A
  Policy type, its purpose and every field must carry a description, refused
  at import otherwise, because an agent answering a person's question has
  only those sentences to go on.
- **ModelConfig stays as built until something reads it from the graph.** It
  is the same kind of thing and will move onto `Knowledge` with its content
  hash unchanged; today nothing consumes it there and the Models page shows
  all of it, so the refactor waits for its first consumer.
- **A model call names its policy by two facts, not a declared link.** The
  call row records the fills it rendered; the run records the policy
  versions `ctx.policy` read; every Ledger row the run writes copies them.
  Field-level provenance is not recorded. The dependency between a prompt
  and a policy is derived from those rows, never declared beside the fills,
  which are the dependency the code enforces.
- **Rule outcomes go to the Ledger at the moment they mattered, never onto
  the entity.** The gate evaluates every rule a type declares when an action
  runs and writes the findings on the act row beside the policy versions in
  force; the findings a flow showed a person are in the Task's context, and
  the decision row records the answer. The present is computed on demand.
  A stored verdict is a claim about now that goes false; a Ledger row is a
  fact about a moment.
- **Console.** The Knowledge stop is renamed Ontology, since that is what it
  shows. Policies get a stop of their own; the Models stop stays the LLM
  insight surface (configs, versions, schemas, prompts, call sites, usage,
  the policy versions each call ran under). Workspaces arrive under Work,
  minimal, projecting only data the console already reads. The rail is
  grouped: Work, Know, Audit.
- **Expenses.** The flow takes a list of receipts and one run judges the
  batch; the console's multi-file Start is punted as core API work, so the
  console starts it with one receipt for now. Receipts stay flat and are
  keyed by merchant, date and total so a duplicate is findable. No writeback.

## 2026-09-19, pass 2 built

- **What landed** (cumulus-vision, uncommitted): `Knowledge(Entity)` with
  declared instances and content versions; `Policy` with required
  descriptions; `ctx.policy` pinning versions on the run and on every Ledger
  row the run writes; the rule contract (`Finding`; a boolean or a number
  refused); the gate writing every rule's verdict on the act row; verdicts on
  demand; `PostingLimits` and the over-limit doubt in the AP review; the
  expense reimbursement map (Dataset, `Receipt`, `ExpenseReport`,
  `ExpensePolicy`, the flow over a batch with one review, the `expenses`
  queue) and its review screen; Workspaces as declared projections with four
  panel kinds and two declared (Accounts payable, Expenses); the console's
  Knowledge stop renamed Ontology, a Policies stop, verdicts on a record,
  policy versions on model calls, the rail grouped Work, Know, Audit.
- **Judgments worth keeping.** A rule names the Policy it needs by its
  parameter's annotation and the gate hands it the whole record; chosen over
  an `inputs=` at the act site because the verdicts route has no flow to
  supply values. A policy's declared instance is a nested `class declared:`
  of plain values, no import and no ceremony. Duplicate and stale receipts
  are flow-level checks, because a rule can read neither the graph nor the
  clock. Entity filters on the API are spelled `where.<property>=` so a
  property can never collide with the route's own parameters.


## 2026-09-19, the entity explorer

- **Identity gets its own section on a record**, first under the title,
  with one row for the key as this graph holds it and one row per System the
  type declares an Identity link into. A declared System with no edge is
  drawn as "not matched" rather than left out, because a Vendor with no ERP
  edge today shows nothing, which reads as "in no system of record" when it
  may mean the sync never ran. Identity rows push nothing: the far end is a
  row in the ERP. Standing, decidedBy and reversibleWith on identity edges
  wait for the first flow that writes a proposed match.
- **The Ontology page's right pane is one explorer: a stack of frames drawn
  as a trail.** A table frame winnows (filter, find, sort, page); a record
  frame follows (one record, every edge). A row is the door and pushes a
  record; a record's many-side count pushes a table narrowed by that
  relationship, with the type and the relationship pinned; a one-link row
  pushes a record; back pops one and a crumb pops to it; following always
  pushes, even to a record already in the trail. Before this the page held
  one open id, so following a link replaced the record and back landed on
  the type's list.
- **The table is generated from the declaration in bands**: record (type,
  standing, label) and provenance (source, written by, updated) for every
  type; properties, links and identity between them once a type is chosen.
  Common bands only across all types, which makes the table a search over
  the whole graph. Eight declared properties before the picker starts
  hiding. No per-type table code, ever.
- **The Definition tab is demoted to a Declaration sheet** over whichever
  frame is on top; property descriptions ride as header tooltips; a type
  with no records shows its declaration in place of the empty table.
  Systems and Policies keep their declaration tabs until one earns an
  explorer.
- **Not built, on purpose.** Selection and bulk operations (the user: no
  idea what group selection is for); a rail stop for the explorer; hover-only
  controls (hover may relabel the chevron, never hide the door); the
  prototype's identity workbench, which is a Workspace fed by a Flow.
- **API.** `GET /entities` takes `type` optionally (common fields across
  every type without it), gains `via.<link id>=<entity id>` and
  `identity.<system>=any|none`, and each row carries link and identity
  summaries; `GET /entities/{id}` returns one-links, identity slots and
  `link_groups` (count plus the three newest) instead of resolving every
  edge, which on a Vendor with 5,000 orders was 5,000 reads. `by.run=` for a
  Runs-page door is later. Design canvas: the "Cumulus Entity Explorer"
  artifact, revision 3.

## 2026-09-19, routing a Task to people

- **A person is a Queue with one member.** Every Principal is bound to
  `person:<email>`, a rule in the customer's queues module and nothing in
  core. A Task for one person, the submitter who must correct receipts or the
  manager a policy names, is routed to that queue and reaches them through
  the Tasks page like any other. The doctrine's "never to a person" holds in
  form; the coverage argument does not apply to a person's own corrections.
- **Several Queues, first answer wins.** A Task names one or more Queues, is
  listed for anyone bound to any of them, and the already-decided guard
  settles a race. No new concept: a Task was always any bound Principal's to
  take.
- **Two contributions are two Tasks.** A Task is one judgment from one
  person. Raised one after the other today, because a body runs as one line
  and pauses at one point; an `ask_all` that suspends on several Tasks is the
  extension if a case ever needs them raised at once. Not built.
- **Escalation is a branch that reads a policy.** `ExpensePolicy` names the
  approver queue, the controller queue and the amount above which the
  controller takes it; the flow reads the policy it already pinned and picks.
  A manager route is the same branch reading an Employee-to-Employee link the
  HRIS feeds, the first caller for `follow`.


## 2026-09-20, the sentence bar

- **The Ontology page loses its left-hand type column.** The user found
  it heavy, and a hand-made filter offered only a standing dropdown until a
  type was chosen, which read as a missing feature. Four layouts were drawn
  (the "Four Ways to Narrow the Graph" canvas): a 240 px facet rail, a
  Filters popover, a sentence bar, a landing of type cards. **The sentence
  bar was chosen.** The query is rendered across the top as words and
  tokens in one fixed order — head type, `at` standing, `where` property =
  value, a link's own name with the far record's label, `known to` or
  `not known to` a system, `matching` find — every token a dropdown with
  counts, the type token's menu carrying the old column (types by domain
  with counts and a first sentence of description), and "+ and…" offering
  what the type declares and the sentence does not yet say. Only five words
  are prose and they are a lookup by clause kind; nouns come from the
  declaration verbatim; nothing is parsed from typed text.
- **Facet counts are computed in the client, so narrowing by standing,
  property and identity moves there too.** The table asks the server for
  `type` and `via` only (the link join is an index read) and applies the
  equalities over the listing it already has, which is the same equality
  the route does. A facet's counts are taken over the rows matching every
  other clause, so a set facet still shows what its other values would
  give. Workspace panels keep sending `where` to the server.
- **One value per property.** The route has equality and no OR, so a
  token holds one value; a menu is a single choice. An OR needs a route
  change first.
- **Rejected for now.** The rail (still a column); the popover (hides the
  query behind a button); the landing (a screen before any table and a read
  per type on arrival), kept as the pairing to add in front of the sentence
  if the world-at-a-glance view is asked for.

## 2026-09-20, later: a field token holds a condition

- **The picker loses its connectives and its label.** "+ and…" becomes a
  bare plus; its menu lists what the type declares as names with a kind
  beside each — fields (key first), links, systems, record — never "where
  x". The sentence supplies "where" once the token exists. A field already
  in the sentence is greyed, not hidden. A search box over the rows appears
  only when there are more than ten of them.
- **A field's editor is chosen by kind and by cardinality.** A fixed set
  (choice, boolean, standing) is checkboxes with counts, and two ticks read
  "or"; money, number and date are two fields, from and to, either empty;
  a string with twelve or fewer distinct values in the current match is a
  set, above that a search with `contains` as the default and `is exactly`
  a switch away; a document is present or none. Six conditions and no
  seventh: equals, any of, contains, between (one or both ends), present.
  Each has one token text and one encoding (`a|b`, `~text`, `min..max`,
  `@none`) so a URL can carry it later.
- **Standing is a set, and the "show retracted" switch goes.** Retracted
  and superseded rows show when the set includes them; an empty set means
  the two in use. One mechanism fewer.
- **The MVP directive.** The user, approving the canvas: "do not add any
  bells and whistles for the sake of UI/UX at this point, we want to keep
  the app and the api very clean during this MVP stage." So the drawings'
  histogram, distribution bars, presets (including the one read from a
  Policy), named periods and the blank typed token are NOT built; the
  function is. The API is untouched: every condition richer than equality
  is a client rule over the listing the table already fetched.

## 2026-09-20, later still: changing a flow under its open runs

- **The question.** Approving a task on a run started yesterday returned a
  409: the flow's integer had moved and a run is only ever driven by the
  code that started it. What should happen to work in flight when the code
  changes?
- **The rule for the integer.** It moves only when replaying an old journal
  through the new body would run a step twice or skip one (a rename), hand a
  journaled output to code reading a new shape, refuse or reinterpret an
  answer a person is about to give (an ask's type changed, or a new ask put
  before the open one), or read the payload in a new shape. A new step, a fix
  to an unreached step, a prompt edit or an optional answer field never move
  it, because the journal is keyed by name and a run refused for the integer
  alone is stranded for nothing. Applied at once: expense-reimbursement's
  bump to 3 was compatible and went back to 2.
- **Restart and supersede, built.** The exit the doctrine already named:
  the old run closes as `superseded` naming its replacement, its open Tasks
  close unanswered (not a decision), one `restart` Ledger row says who and
  which versions, and a new run starts on the live version from the same
  payload and the same `started_by`, with an empty journal. Nothing is
  migrated.
- **Then the user's challenge:** "why give the user a choice, shouldn't the
  system determine one or the other? If we push changes shouldn't it just
  automatically queue a restart?" Yes. A stranded waiting run is a fact the
  server computes; a click adds toil and nothing else. So the tick sweeps
  waiting runs whose version is not the registered one and queues one
  restart each under `deploy:<revision>`, and deploy.sh ticks as soon as the
  new revision serves. Failed runs are not swept: a failed run already waits
  for a person, and restarting one somebody abandoned spends model calls on
  nothing. The console offers Resume when versions match and Restart when
  they differ, never both; the task-page notice and Restart button and the
  task-row version chip, built that morning, were removed the same day.
- **Held:** a computed hash of the flow's replay-relevant shape; old versions
  deployable side by side (the prototype's vN packages). **Rejected:**
  branching in the body on the pinned version. `VERSIONING.md` in
  cumulus-vision is the authoritative account; the doctrine clause on
  Tier-2 definitions was rewritten to match.

## 2026-09-27, Access: one Human per person, authority curated here

- **The hierarchy.** One canonical Human record per person in the graph,
  curated in Cumulus; the identity provider and the HRIS are mapped onto it
  by identity Links and never the source of its authority. Core declares the
  base with the access fields (a minted `hid` as the key, because an email
  changes and every link in the business points at the record; a unique
  email; `relationship`; `access`; `platform_role`; the roles held as parts)
  and the map subclasses it once, the way a policy subclasses Policy, to say
  what the business knows about its people. `Employee` is gone: being an
  employee is a relationship on the Human, having a sign-in is a Link on it.
- **Principal is the caller, not a record**: resolved per request to one
  Human (or, later, one Agent). The doctrine's Principal clause is rewritten.
- **Authority per property, declared.** The HRIS owns what the map's
  `authority` block says it owns, for a Human linked to an HRIS row; Cumulus
  owns the access facts and the existence of Humans no HRIS knows. Day one
  the HRIS leads and reads only; a conflict the sync cannot settle (identity
  ambiguity, a leaver with open work) is handed from a step to a per-case
  flow and becomes a Task at an `access` queue; reversal in the HRIS is a
  later effect. Termination is a fact the sync writes; deactivation is a
  declared rule (`ON_TERMINATION`).
- **Roles.** One platform role per Human (admin, member, auditor) is the
  access ladder, one table in core says what each may, and every route
  declares its verb in its signature. Business roles are declared in the
  map, bind queues and name startable flows, and are held as parts on the
  Human rather than as Links, because a Role is a declaration like a Queue
  and not a record. Direct queue binding is not built. Onboarding is a
  tranche on the Access stop, not a per-customer admit rule. The open
  question on access-role versus assignment-role is closed by this split.
- **The door.** A verified token whose email is an active Human, else 403
  not enrolled; the first administrators are a setting; sign-up is closed
  and the deploy refuses to proceed while it is open; a member reads
  everything until scoped reads flip the default to nothing.
- **The stop is Access**, under an Admin heading beside Deployment, later a
  tab of one Admin stop; the console hides and the server decides.
- **A step hands a case to a flow** (`ctx.start`, keyed), the one engine
  addition the sync needed; a Trigger still carries no payload.
- **The first customer is Naviant itself**, against the demo estate's
  `naviant` namespace, the owner seeded into its HRIS under the real
  address. Recorded in cumulus-vision's `WORKPLAN-ACCESS.md` §14, steps A0
  to A6 landed 2026-09-27; A7 (Entra), A8 (scoped reads), A9 (keys, Agents)
  and A10 (onboarding as a flow, reversal) are planned there.

## 2026-09-29, the frame: a bar, and workspaces as the doors

- **One bar over every screen, four routes on it.** Home, Workspaces, My
  Tasks and the person, with a search field in the middle that says it is
  not wired rather than pretending to look. The rail of eleven stops under
  four headings is gone from the frame: it asked a person to choose among
  the platform's parts before they had chosen their work.
- **Cumulus Admin is a Workspace.** The eleven stops live on inside it,
  under Build (Flows, Queues), Knowledge (Ontology, Data, Policies),
  Connect (Systems, Models) and Govern (Ledger, Access, Deployment), drawn
  by the same rail a map workspace is drawn by, and first in the list of
  workspaces. It is offered to every caller who may read the map — which
  today is every rung — and its stops hide by the same verbs they always
  did; who counts as a Cumulus administrator is the verbs, not a name, and
  when scoped reads narrow a member's map (A8) the workspace narrows with
  them. The alternative, gating the whole workspace on `access.manage`,
  would take Start run off a clerk's screen, since Flows is where a run is
  started.
- **A map workspace is a rail of Views and Operate.** One view, Overview,
  which is its declared panels; one operating stop, System health, which is
  the flows and Systems its panels already name, read from the routes the
  Flows and Systems stops read. Views of a workspace's own would be a
  declaration on `Workspace` the map does not carry; the console shows what
  is declared and nothing more.
- **The assistant pane is a placeholder and says so.** One pane down the
  right of any section but Home, one button on the bar, and it repeats what
  is typed. Marked OPEN in the doctrine rather than built as a chat box.
- **Styling is one configuration.** Every colour, tone, distance, radius
  and fixed width the console draws with is a field of one const
  (`console/lib/theme/tokens.dart`), carried as a `ThemeExtension` on the
  theme built from it; the five tones every chip is drawn in read from it,
  so no screen owns a colour.

## 2026-09-29, later: a Workspace is read in views

- **`View(name, panels)` joins the declaration.** The frame drew a map
  workspace as one view because `Workspace` declared a flat list of panels;
  asked to put accounts payable and expense reimbursement under one
  Financial operations workspace, the console could have grouped them itself,
  which would have been a grouping the map never declared. Instead the map
  declares it: a Workspace is `views`, each a named list of panels, and the
  bare `panels` is gone — one shape for one thing. A view carries no sentence
  of its own; its name is what it says and its panels are what it is.
- **Naviant declares one workspace, Financial operations,** with the two
  former workspaces as its views, panel for panel. The doctrine's Workspace
  clause now says so in place of the note that views were not yet carried.

## 2026-09-29, later still: the bar holds still

- **The assistant's button is the sparkle alone, on every route.** The word
  beside it made the bar's right-hand side a different width on different
  screens; its name lives on the tooltip and on the heading of the pane it
  opens. The pane opens beside any section, Home included: a screen the
  assistant will not open on is a screen a person learns not to ask about.
- **The search is anchored, not centred.** Centred in whatever the routes
  left over, it moved whenever anything on the right changed width. It now
  sits beside the wordmark at one width and shrinks only when the window is
  too narrow for it. A control a person aims at belongs in one place.

## 2026-09-29, last: the workspace rail shuts

- **One icon, in one place, both ways.** The rail is put away and brought
  back by the same control in the same spot, rather than by a handle that
  appears somewhere else once it is gone. Shut, it keeps the way back out of
  the workspace: the rail was carrying that, and shutting the stops is not
  asking to be stranded.
- **Whether it is shut belongs to the console, not the page.** Like the
  assistant's pane, it is one notifier: somebody who shut it meant it shut,
  and giving it back on the next workspace is arguing with them.
- **The way back is the supertitle, not an arrow beside it.** The word over
  the workspace's name says what the thing under it is, and the list of them
  is the same word, so it is the link: underlined and in the accent under the
  pointer. An arrow beside it was a second control saying what the word
  already said. Shut, where there is no supertitle, the arrow returns.


## 2026-09-29, Case: a pattern, and the machinery it turned out to need

- **A Case is a pattern, not a primitive and not a declaration.** The
  question was whether case management — an Opportunity, a Handover, a claim
  — needs a thing of its own beside Flow and Entity. A first draft gave it a
  marker, a lifecycle declaration, parties bound to a queue of its own, runs
  keyed to it and a declared page. Review took every one of those away: an
  invoice is also mutated by things other than its flow and re-run; a stage
  is a property with fixed values and a panel already narrows by one; people
  on a record are links a business declares when it needs them; a flow asks
  a person at the queue they already have; and nobody can say today what a
  case page looks like. What is left is a word the doctrine uses so that an
  engineer recognises the shape, and the sentence that guards it: state that
  is not graph state, or logic that is not an Action or a Flow, is the
  pattern being mistaken for a primitive.
- **"A Case must not hold logic" was overstated.** A Case has Actions and
  Rules because every Entity does; a button on a record calls an Action with
  no Flow in the way. What an Action cannot do is the Flow's four powers, and
  an Effect is a Flow's and never an Action's. The test is not "does it run
  code" but "can it wait or reach outside".
- **All that is needed is expanded Triggers, and one route.** Records emit
  Events for exactly the types and kinds some Trigger names, so a type nobody
  watches emits nothing. A Trigger on a record names a predicate, fires once
  when it becomes true, keys the run it starts to the record so a fact
  written twice starts one response, and may answer with a Flow or directly
  with an Action — decided in review, because a checklist completing should
  advance a stage without a flow in between. A Flow may wait on a record's
  Event the way it waits on a Task. And a person can run an Action on a
  record through the API, which is not case machinery at all: it is the gap
  the first comment exposed, that other things mutate an invoice too.
- **No locks.** Exclusivity between flows is a state on the record in the
  business's words, set and cleared by Actions and checked as a Rule when
  any other action runs; writes carry the version they read; a flow that
  meets a gate waits or fails to a person, never rolls back, so the gate
  goes before any Effect and what precedes it is proposals, harmless to
  abandon. "Somebody is already on this" is a claim on a Task if a queue ever
  needs one, never a lock on a record.
- **Checklist is the one base type the discussion justified, and its items
  are Parts.** A first cut made every checklist entry a record of its own
  with an owner and a due date, on the argument that a person has to find
  what they own without opening the record. That argument fell the moment
  ownership moved onto the Task: the Task is what a person finds, and an
  item only has to be findable from its checklist. So a Checklist is a core
  base Entity, about one record, finished when every item is done, and its
  items are Parts held inside it — a thing with no identity of its own,
  exactly as an invoice line is. Core's because the console has to know the
  shape; the map subclasses it to carry the Rules for items done by
  evidence, and decides which checklist a record gets and when, in code.
  One record per checklist, one Ledger row per action naming the item, and
  a template is code that writes one record. Two limits, both wanted: an
  item cannot be the source of a Link, and two people completing items on
  one checklist at once contend for one record — which never happens on a
  checklist of ten and would on a board of two hundred, which keeps a
  Checklist a checklist.
- **Assigning an item is a Task, and three things change about Task.**
  Who owes an item and by when live in exactly one place. Assign is an
  Action on the Checklist that raises a Task to a person's own queue or to a
  queue, naming the checklist and the item key, with a due date; the answer
  runs complete on that item; reassigning supersedes and raises again; an
  item done by evidence raises none. A person has one list, My Tasks. To
  carry this a Task gains a due date, may be raised by an Action as well as
  a Flow's ask, and its answer has a consumer — a run to resume, or an
  action on a record to run. Assigning to a team is free because Tasks
  already go to queues; and the Handover flow no longer holds four questions
  open, it writes the checklist, assigns, and ends.
- **No generic board.** A Monday-shaped List of work about nothing was
  considered and held: it is a product, not a type; it is where work goes to
  escape modeling, which is the opposite of the knowledge layer's bet; and
  the doctrine's answer to "a project" is a Project the map declares with a
  Checklist about it. The question underneath — whether Cumulus is where a
  team's unmodeled work lives or where modeled work runs — is the owner's,
  and if the first is chosen, columns, comments and views belong on a
  roadmap chosen on purpose.
- **Evidence for a checkbox is a document reference on the part, or a Rule
  over the record's own links.** Never a Link from the item, which has no
  identity to link from; the signed contract is a Document the record links
  to and the item's Rule reads that.
- **Notifications are not a primitive.** Interest is a Link (the record's own
  links, plus `watches` for following on purpose), what happened is the
  Ledger, the Feed is one read joining them, computed and never stored,
  shown on Home; a Surface delivers the same read later. Asking for work is
  a Task at a person's own queue, so opportunities add no queues.
- **The UI needs no concept of its own.** The map says what to show and the
  app says how; a record's page is the record frame until a type needs a
  screen, decided with the first one. A declared composition is a *view*
  and code is a *screen*.
- **The API stays one route per primitive.** The one new write is running
  an Action on a record; a Checklist is a record and needs no route of its
  own; the Feed is one read. Workspace-specific server code lives in the map
  as Actions, Rules, System methods and Flows, never as routes. Core owns
  routes; the map owns names.
- **The first case-shaped record is a Handover in the HR space.** Its event
  is live (the sync writes terminations hourly), it needs no new System, it
  exercises Trigger to Flow, Trigger to Action, a Checklist assigned
  through Tasks with evidence, and a workspace by stage, and it corrects a behaviour that is
  wrong today: the sync deactivates a leaver on sight and ignores the
  effective date. The Handover opens on the record, closes on the date, and
  deactivation happens at close.
- **The People workspace is built to absorb the HRIS.** Which system leads
  on an employment fact is a declaration per property; a customer who wants
  Cumulus to lead moves the names and declares the Actions, and the HRIS
  becomes an Effect target or is retired. Regulated ledgers — payroll,
  benefits, tax, statutory reporting — stay in a System for good. Every
  function the first cut does not build is a named seam in
  `WORKPLAN-CASE.md`. Access stays the platform's own lens on the same
  Human record.
- **Where this guidance lives.** The binding rules are the doctrine clauses
  above; the reasoning is here; the steps and acceptance are the workplan;
  and cumulus-vision's `CLAUDE.md` gets the authoring rules — gate before
  Effects, a Checklist never a Task, no locks — when the steps that make them true
  are built, because that file describes what is, not what is planned.
