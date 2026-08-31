/// The system-state screen. Mirrors `Dashboard` in `server/app/ui_api.py`.
///
/// Nothing here is a new record. Every field is composed server-side from a
/// surface that already owns it — the graph's counts, the Sources rows, the
/// task queue, the ledger, the flow projections — so each number on this screen
/// is answerable by a list one rail stop away.
library;

import 'data_layer.dart' show EstateCard;
import 'identity_screen.dart' show IdentityBook, SweepRun;

export 'data_layer.dart' show EstateCard;
export 'identity_screen.dart' show IdentityBook, SweepRun;

/// One object type, as the map holds it now.
class TypeState {
  final String name;
  final String label;
  final int instances;

  /// Instances where two sources assert the same field differently. Precedence
  /// picks a winner to read; this is how many rows are standing on one.
  final int conflicted;

  const TypeState({
    required this.name,
    required this.label,
    required this.instances,
    required this.conflicted,
  });

  factory TypeState.fromJson(Map<String, dynamic> json) => TypeState(
    name: json['name'] as String? ?? '',
    label: json['label'] as String? ?? '',
    instances: json['instances'] as int? ?? 0,
    conflicted: json['conflicted'] as int? ?? 0,
  );
}

/// A Collection and what is inside it — the unit governance hangs off.
class CollectionState {
  final String id;
  final String label;
  final String role;
  final String origin;
  final String custody;
  final String retention;
  final bool authoritative;
  final List<TypeState> types;
  final int objects;
  final int conflicted;

  const CollectionState({
    required this.id,
    required this.label,
    required this.role,
    required this.origin,
    required this.custody,
    required this.retention,
    required this.authoritative,
    required this.types,
    required this.objects,
    required this.conflicted,
  });

  factory CollectionState.fromJson(Map<String, dynamic> json) =>
      CollectionState(
        id: json['id'] as String? ?? '',
        label: json['label'] as String? ?? '',
        role: json['role'] as String? ?? '',
        origin: json['origin'] as String? ?? '',
        custody: json['custody'] as String? ?? '',
        retention: json['retention'] as String? ?? '',
        authoritative: json['authoritative'] as bool? ?? false,
        types: (json['types'] as List<dynamic>? ?? const [])
            .map((e) => TypeState.fromJson(e as Map<String, dynamic>))
            .toList(),
        objects: json['objects'] as int? ?? 0,
        conflicted: json['conflicted'] as int? ?? 0,
      );
}

class MapState {
  final int objects;
  final int links;
  final int conflicted;
  final List<CollectionState> collections;

  /// TBox violations over the whole graph. Null — deliberately not zero — for
  /// a tenant-scoped persona: a violation is a Naviant bug, and an alarm the
  /// reader can take no action on is worse than no alarm.
  final int? invariants;
  final Map<String, int>? invariantKinds;

  const MapState({
    required this.objects,
    required this.links,
    required this.conflicted,
    required this.collections,
    required this.invariants,
    required this.invariantKinds,
  });

  factory MapState.fromJson(Map<String, dynamic> json) => MapState(
    objects: json['objects'] as int? ?? 0,
    links: json['links'] as int? ?? 0,
    conflicted: json['conflicted'] as int? ?? 0,
    collections: (json['collections'] as List<dynamic>? ?? const [])
        .map((e) => CollectionState.fromJson(e as Map<String, dynamic>))
        .toList(),
    invariants: json['invariants'] as int?,
    invariantKinds: (json['invariant_kinds'] as Map<String, dynamic>?)?.map(
      (k, v) => MapEntry(k, v as int),
    ),
  );
}

/// A type one feed resolves rows against, and what the book holds of it now.
class EntityCount {
  final String type;
  final int instances;

  /// How many Mappings resolve rows against this type. More than one and the
  /// book is shared, so [instances] is not this feed's output.
  final int feeds;

  const EntityCount({
    required this.type,
    required this.instances,
    required this.feeds,
  });

  factory EntityCount.fromJson(Map<String, dynamic> json) => EntityCount(
    type: json['type'] as String? ?? '',
    instances: json['instances'] as int? ?? 0,
    feeds: json['feeds'] as int? ?? 0,
  );
}

/// One inbound feed's health.
///
/// Two different facts share the word *unchanged* and this shape keeps them
/// apart. [lastPullOutcome] `== 'unchanged'` is the **connector** saying the
/// estate reported no delta since the watermark — nothing fetched, no run
/// started. [unchangedRows] counts **rows** that arrived byte-identical on a
/// pull that did deliver. A feed can have either without the other.
class FeedState {
  final String source;
  final String kind;
  final String flowId;

  /// Empty when no Mapping claims this feed — the finding, not a missing field.
  final String contractId;
  final String transport;

  /// The type a reified row becomes, and empty for a **bind** mapping — whose
  /// rows become nothing of their own and go straight to [entities]. That
  /// distinction is what makes [recordsLanded] comparable to what the book
  /// holds.
  final String landsAs;

  /// What the book holds of [landsAs], on the same terms as [entities]. Null
  /// for a bind mapping, which lands nothing of its own. Carried so the
  /// destination line can name where the rows *went* — the resolution targets
  /// were being printed under a heading about landing.
  final EntityCount? landedType;

  /// Null for a feed that pushes; it has no app for discovery to confirm.
  final bool? active;

  final String lastRunAtIso;
  final int? lastRunAgeSeconds;
  final String lastStatus;
  final String lastError;
  final int deliveries;

  /// Rows the delivery carried, against rows the landing step kept. Two facts,
  /// not one: on a reify feed they diverge by two thirds.
  final int records;

  final int recordsLanded;

  /// Null — never zero — when this feed's contract does not report the
  /// disposition. See [SourceFeed] in `data_layer.dart`: a bind mapping has
  /// no resolve step and no payload hash, so it never answers these.
  final int? created;
  final int? linked;
  final int? unchangedRows;

  final int awaitingSteward;
  final List<EntityCount> entities;

  /// Whether Cumulus pulls this feed. A pushed feed keeps its own cadence and
  /// has no cursor, so every pull field below is empty as a fact about the
  /// arrangement rather than as missing data.
  final bool pulls;
  final String lastPullAt;
  final int? lastPullAgeSeconds;
  final String lastPullOutcome;
  final String lastPullError;
  final String watermark;
  final int? watermarkAgeSeconds;

  const FeedState({
    required this.source,
    this.kind = '',
    this.flowId = '',
    this.contractId = '',
    this.transport = '',
    this.landsAs = '',
    this.landedType,
    this.active,
    this.lastRunAtIso = '',
    this.lastRunAgeSeconds,
    this.lastStatus = '',
    this.lastError = '',
    this.deliveries = 0,
    this.records = 0,
    this.recordsLanded = 0,
    this.created,
    this.linked,
    this.unchangedRows,
    this.awaitingSteward = 0,
    this.entities = const [],
    this.pulls = false,
    this.lastPullAt = '',
    this.lastPullAgeSeconds,
    this.lastPullOutcome = '',
    this.lastPullError = '',
    this.watermark = '',
    this.watermarkAgeSeconds,
  });

  factory FeedState.fromJson(Map<String, dynamic> json) => FeedState(
    source: json['source'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    contractId: json['contract_id'] as String? ?? '',
    transport: json['transport'] as String? ?? '',
    landsAs: json['lands_as'] as String? ?? '',
    landedType: json['landed_type'] == null
        ? null
        : EntityCount.fromJson(json['landed_type'] as Map<String, dynamic>),
    active: json['active'] as bool?,
    lastRunAtIso: json['last_run_at_iso'] as String? ?? '',
    lastRunAgeSeconds: json['last_run_age_seconds'] as int?,
    lastStatus: json['last_status'] as String? ?? '',
    lastError: json['last_error'] as String? ?? '',
    deliveries: json['deliveries'] as int? ?? 0,
    records: json['records'] as int? ?? 0,
    recordsLanded: json['records_landed'] as int? ?? 0,
    // Absent stays absent — see the field comments above.
    created: json['created'] as int?,
    linked: json['linked'] as int?,
    unchangedRows: json['unchanged_rows'] as int?,
    awaitingSteward: json['awaiting_steward'] as int? ?? 0,
    entities: (json['entities'] as List<dynamic>? ?? const [])
        .map((e) => EntityCount.fromJson(e as Map<String, dynamic>))
        .toList(),
    pulls: json['pulls'] as bool? ?? false,
    lastPullAt: json['last_pull_at'] as String? ?? '',
    lastPullAgeSeconds: json['last_pull_age_seconds'] as int?,
    lastPullOutcome: json['last_pull_outcome'] as String? ?? '',
    lastPullError: json['last_pull_error'] as String? ?? '',
    watermark: json['watermark'] as String? ?? '',
    watermarkAgeSeconds: json['watermark_age_seconds'] as int?,
  );

  bool get failed => lastStatus == 'failed' || lastPullOutcome == 'failed';

  /// Nothing has ever been delivered under this feed. A registered feed that
  /// has never pulled is here too, which is why the pull half is asked
  /// separately.
  bool get neverDelivered => deliveries == 0;
}

class ArrivalsState {
  final EstateCard estate;
  final List<FeedState> feeds;
  final List<String> withoutContract;
  final List<String> failing;

  const ArrivalsState({
    required this.estate,
    required this.feeds,
    required this.withoutContract,
    required this.failing,
  });

  factory ArrivalsState.fromJson(Map<String, dynamic> json) => ArrivalsState(
    estate: EstateCard.fromJson(
      json['estate'] as Map<String, dynamic>? ?? const {},
    ),
    feeds: (json['feeds'] as List<dynamic>? ?? const [])
        .map((e) => FeedState.fromJson(e as Map<String, dynamic>))
        .toList(),
    withoutContract: (json['without_contract'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    failing: (json['failing'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );

  /// Rows handed to the door across every feed. The total the header states,
  /// because it is the one that means "traffic": summing [recordsLanded]
  /// instead reported a third of the estate's arrivals as if it were all of
  /// them, since a reify feed lands a fraction of what it carries.
  int get recordsReceived => feeds.fold(0, (sum, f) => sum + f.records);

  int get recordsLanded => feeds.fold(0, (sum, f) => sum + f.recordsLanded);
}

/// One desk: what is on it, how long the oldest has waited, and who holds it.
class QueueState {
  final String queue;
  final int open;
  final int highPriority;
  final Map<String, int> byKind;
  final String oldestOpenAtIso;
  final int? oldestOpenAgeSeconds;

  /// Personas bound to this desk, by label. A queue with work and nobody bound
  /// accumulates where no personal inbox would ever surface it.
  final List<String> bound;

  const QueueState({
    required this.queue,
    this.open = 0,
    this.highPriority = 0,
    this.byKind = const {},
    this.oldestOpenAtIso = '',
    this.oldestOpenAgeSeconds,
    this.bound = const [],
  });

  factory QueueState.fromJson(Map<String, dynamic> json) => QueueState(
    queue: json['queue'] as String? ?? '',
    open: json['open'] as int? ?? 0,
    highPriority: json['high_priority'] as int? ?? 0,
    byKind: (json['by_kind'] as Map<String, dynamic>? ?? const {}).map(
      (k, v) => MapEntry(k, v as int),
    ),
    oldestOpenAtIso: json['oldest_open_at_iso'] as String? ?? '',
    oldestOpenAgeSeconds: json['oldest_open_age_seconds'] as int?,
    bound: (json['bound'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );

  bool get unowned => bound.isEmpty;
}

/// Governed writes under one regime, split by how they ended. A refusal is
/// counted beside a commit because a write the Gateway stopped is the spine
/// doing its job.
///
/// [failed] is the third ending and not a shade of the second: [refused] is
/// the Gateway saying no, and nothing happened; [failed] is a write it allowed
/// that then broke — a graph write that raised, a system that did not answer.
/// One reads as governance working, the other as something to go and look at.
class RegimeCount {
  final String regime;
  final int committed;
  final int refused;
  final int failed;

  const RegimeCount({
    required this.regime,
    required this.committed,
    required this.refused,
    this.failed = 0,
  });

  factory RegimeCount.fromJson(Map<String, dynamic> json) => RegimeCount(
    regime: json['regime'] as String? ?? '',
    committed: json['committed'] as int? ?? 0,
    refused: json['refused'] as int? ?? 0,
    failed: json['failed'] as int? ?? 0,
  );

  /// Every entry under this regime. The bar draws this, so the line under it
  /// has to name all three parts or the picture and the sentence disagree.
  int get total => committed + refused + failed;
}

class LedgerWindow {
  final int days;
  final int scanned;
  final bool truncated;
  final int inWindow;
  final List<RegimeCount> byRegime;
  final int refused;
  final int failed;
  final String newestAtIso;

  const LedgerWindow({
    this.days = 30,
    this.scanned = 0,
    this.truncated = false,
    this.inWindow = 0,
    this.byRegime = const [],
    this.refused = 0,
    this.failed = 0,
    this.newestAtIso = '',
  });

  factory LedgerWindow.fromJson(Map<String, dynamic> json) => LedgerWindow(
    days: json['days'] as int? ?? 30,
    scanned: json['scanned'] as int? ?? 0,
    truncated: json['truncated'] as bool? ?? false,
    inWindow: json['in_window'] as int? ?? 0,
    byRegime: (json['by_regime'] as List<dynamic>? ?? const [])
        .map((e) => RegimeCount.fromJson(e as Map<String, dynamic>))
        .toList(),
    refused: json['refused'] as int? ?? 0,
    failed: json['failed'] as int? ?? 0,
    newestAtIso: json['newest_at_iso'] as String? ?? '',
  );
}

/// What the counterparty books are waiting on, taken from the workbench.
class IdentityState {
  final int standing;
  final int awaitingDecision;

  /// Standing proposals with no Task at all. Every proposal the sweep writes
  /// raises one, so this is an anomaly rather than a policy.
  final int withoutTask;
  final int confirmed;
  final int withdrawn;
  final List<IdentityBook> books;

  /// Null when the sweep has never run — never a zeroed row standing in for
  /// one.
  final SweepRun? lastSweep;

  const IdentityState({
    this.standing = 0,
    this.awaitingDecision = 0,
    this.withoutTask = 0,
    this.confirmed = 0,
    this.withdrawn = 0,
    this.books = const [],
    this.lastSweep,
  });

  factory IdentityState.fromJson(Map<String, dynamic> json) => IdentityState(
    standing: json['standing'] as int? ?? 0,
    awaitingDecision: json['awaiting_decision'] as int? ?? 0,
    withoutTask: json['without_task'] as int? ?? 0,
    confirmed: json['confirmed'] as int? ?? 0,
    withdrawn: json['withdrawn'] as int? ?? 0,
    books: (json['books'] as List<dynamic>? ?? const [])
        .map((e) => IdentityBook.fromJson(e as Map<String, dynamic>))
        .toList(),
    lastSweep: json['last_sweep'] == null
        ? null
        : SweepRun.fromJson(json['last_sweep'] as Map<String, dynamic>),
  );
}

class JudgmentState {
  final int openTasks;
  final int highPriority;
  final List<QueueState> queues;
  final List<String> unownedWithWork;
  final LedgerWindow ledger;
  final IdentityState identity;

  const JudgmentState({
    required this.openTasks,
    required this.highPriority,
    required this.queues,
    required this.unownedWithWork,
    required this.ledger,
    required this.identity,
  });

  factory JudgmentState.fromJson(Map<String, dynamic> json) => JudgmentState(
    openTasks: json['open_tasks'] as int? ?? 0,
    highPriority: json['high_priority'] as int? ?? 0,
    queues: (json['queues'] as List<dynamic>? ?? const [])
        .map((e) => QueueState.fromJson(e as Map<String, dynamic>))
        .toList(),
    unownedWithWork: (json['unowned_with_work'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    ledger: LedgerWindow.fromJson(
      json['ledger'] as Map<String, dynamic>? ?? const {},
    ),
    identity: IdentityState.fromJson(
      json['identity'] as Map<String, dynamic>? ?? const {},
    ),
  );
}

/// One flow whose declared footprint and its observed one disagree.
///
/// Both directions are gaps and they are opposite problems. `undeclared` is a
/// flow touching what no Collection says it may — governance not covering what
/// is happening. `unobserved` is a declaration no run has exercised — a stale
/// promise, or a path nothing has tested.
class FlowGap {
  final String flowId;
  final String label;

  /// `never_ran` · `ran_but_wrote_nothing` · `observed`. Carried because a
  /// flow with no history produces `unobserved` on every declaration it has,
  /// which is not the same finding as one that ran and skipped a path.
  final String state;
  final int totalRuns;
  final List<String> undeclaredWrites;
  final List<String> undeclaredReads;
  final List<String> unobservedWrites;
  final List<String> unobservedReads;

  const FlowGap({
    required this.flowId,
    this.label = '',
    this.state = 'never_ran',
    this.totalRuns = 0,
    this.undeclaredWrites = const [],
    this.undeclaredReads = const [],
    this.unobservedWrites = const [],
    this.unobservedReads = const [],
  });

  factory FlowGap.fromJson(Map<String, dynamic> json) => FlowGap(
    flowId: json['flow_id'] as String? ?? '',
    label: json['label'] as String? ?? '',
    state: json['state'] as String? ?? 'never_ran',
    totalRuns: json['total_runs'] as int? ?? 0,
    undeclaredWrites: (json['undeclared_writes'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    undeclaredReads: (json['undeclared_reads'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    unobservedWrites: (json['unobserved_writes'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    unobservedReads: (json['unobserved_reads'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );

  bool get undeclared =>
      undeclaredWrites.isNotEmpty || undeclaredReads.isNotEmpty;
}

/// What the ModelCalls in the window cost, and whether that is a measurement.
class SpendState {
  final int days;
  final int runs;
  final int modelCalls;
  final int pricedModelCalls;
  final double costUsd;
  final int tokens;

  /// `measured` · `partial` · `unrecorded` · `no-model-calls`. The whole tile:
  /// a $0.00 has two readings pointing opposite ways, and only one of them is
  /// a fact about spending.
  final String basis;

  const SpendState({
    this.days = 30,
    this.runs = 0,
    this.modelCalls = 0,
    this.pricedModelCalls = 0,
    this.costUsd = 0,
    this.tokens = 0,
    this.basis = 'no-model-calls',
  });

  factory SpendState.fromJson(Map<String, dynamic> json) => SpendState(
    days: json['days'] as int? ?? 30,
    runs: json['runs'] as int? ?? 0,
    modelCalls: json['model_calls'] as int? ?? 0,
    pricedModelCalls: json['priced_model_calls'] as int? ?? 0,
    costUsd: (json['cost_usd'] as num? ?? 0).toDouble(),
    tokens: json['tokens'] as int? ?? 0,
    basis: json['basis'] as String? ?? 'no-model-calls',
  );
}

class GovernanceState {
  final int flowsTotal;
  final List<FlowGap> gaps;
  final List<String> feedsWithoutContract;

  /// Null for a tenant-scoped persona, on the same reading as
  /// [MapState.invariants].
  final List<String>? registryProblems;
  final SpendState spend;

  const GovernanceState({
    required this.flowsTotal,
    required this.gaps,
    required this.feedsWithoutContract,
    required this.registryProblems,
    required this.spend,
  });

  factory GovernanceState.fromJson(Map<String, dynamic> json) =>
      GovernanceState(
        flowsTotal: json['flows_total'] as int? ?? 0,
        gaps: (json['gaps'] as List<dynamic>? ?? const [])
            .map((e) => FlowGap.fromJson(e as Map<String, dynamic>))
            .toList(),
        feedsWithoutContract:
            (json['feeds_without_contract'] as List<dynamic>? ?? const [])
                .map((e) => '$e')
                .toList(),
        registryProblems: (json['registry_problems'] as List<dynamic>?)
            ?.map((e) => '$e')
            .toList(),
        spend: SpendState.fromJson(
          json['spend'] as Map<String, dynamic>? ?? const {},
        ),
      );
}

/// The whole system, as of one instant.
class Dashboard {
  /// When the reading was taken. On the payload because none of the counts
  /// below is a stored total, and a figure rendered without one invites being
  /// read as a standing fact.
  final String atIso;
  final int windowDays;
  final MapState map;
  final ArrivalsState arrivals;
  final JudgmentState judgment;
  final GovernanceState governance;

  const Dashboard({
    required this.atIso,
    required this.windowDays,
    required this.map,
    required this.arrivals,
    required this.judgment,
    required this.governance,
  });

  factory Dashboard.fromJson(Map<String, dynamic> json) => Dashboard(
    atIso: json['at_iso'] as String? ?? '',
    windowDays: json['window_days'] as int? ?? 30,
    map: MapState.fromJson(json['map'] as Map<String, dynamic>? ?? const {}),
    arrivals: ArrivalsState.fromJson(
      json['arrivals'] as Map<String, dynamic>? ?? const {},
    ),
    judgment: JudgmentState.fromJson(
      json['judgment'] as Map<String, dynamic>? ?? const {},
    ),
    governance: GovernanceState.fromJson(
      json['governance'] as Map<String, dynamic>? ?? const {},
    ),
  );
}
