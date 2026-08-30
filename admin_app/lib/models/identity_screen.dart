/// The identity workbench's payload. Mirrors `/api/ui/v1/identity`.
///
/// One proposed merge is three things that live apart in the graph and are
/// illegible apart: the typed identity Link the sweep wrote (what is proposed,
/// and what a steward has since said about it), the two objects it joins (the
/// records a person compares), and the `merge_review` Task that carries the
/// decision (whose work it is). The server composes them; nothing here
/// re-derives any of it.
///
/// Deliberately not named `Identity` — that word is already taken in this
/// console by the platform-user roster, and these are counterparties.
library;

/// One piece of evidence, with what it was worth and why.
///
/// Weight and detail travel together because the number is only useful beside
/// the sentence that produced it — which is the whole reason the sweep is
/// rules and arithmetic rather than a model.
class IdentitySignal {
  final String name;
  final double weight;
  final String detail;

  const IdentitySignal({
    required this.name,
    required this.weight,
    required this.detail,
  });

  /// `+0.55` / `-0.35` — a signal that argued against counts too.
  String get signed => '${weight >= 0 ? '+' : ''}${weight.toStringAsFixed(2)}';

  bool get argues => weight < 0;

  factory IdentitySignal.fromJson(Map<String, dynamic> json) => IdentitySignal(
    name: json['name'] as String? ?? '',
    weight: (json['weight'] as num? ?? 0).toDouble(),
    detail: json['detail'] as String? ?? '',
  );
}

/// One of the two records, as a person compares them.
class IdentitySide {
  final String id;
  final String ref;
  final String type;
  final String label;
  final String naturalKey;
  final String status;

  /// The *resolved* props — what the record reads as under precedence, which
  /// is what a steward is deciding about. The claims behind a field are one
  /// drill-down further, in the ABox explorer.
  final Map<String, String> fields;

  /// Which systems assert this record exists. A record one feed has ever seen
  /// is a different kind of duplicate from one three feeds agree on.
  final List<String> sources;

  /// Fields where this record's *own* sources already disagree.
  final List<String> conflicted;

  const IdentitySide({
    required this.id,
    required this.ref,
    required this.type,
    required this.label,
    required this.naturalKey,
    required this.status,
    required this.fields,
    required this.sources,
    required this.conflicted,
  });

  /// True when the proposal points at a record the book no longer holds.
  bool get missing => status == 'missing';

  factory IdentitySide.fromJson(Map<String, dynamic> json) => IdentitySide(
    id: json['id'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
    type: json['type'] as String? ?? '',
    label: json['label'] as String? ?? '',
    naturalKey: json['natural_key'] as String? ?? '',
    status: json['status'] as String? ?? '',
    fields: {
      for (final e in (json['fields'] as Map<String, dynamic>? ?? const {})
          .entries)
        e.key: '${e.value}',
    },
    sources: (json['sources'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    conflicted: (json['conflicted'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );
}

/// Whether this proposal can be decided, by whom, and where.
///
/// Three states, and the third is the one a workbench normally gets wrong:
/// there is a Task and it is yours, there is a Task and it is somebody else's,
/// and there is **no Task at all** because the proposal cleared the governed
/// confidence bar and the sweep paged nobody. Only the first draws controls.
class ProposalGate {
  final String taskId;

  /// `open`, or the verdict a steward already recorded.
  final String taskStatus;
  final String queue;
  final bool mayDecide;

  /// Why not, when not. Always a sentence — never a dead button.
  final String whyNot;

  const ProposalGate({
    required this.taskId,
    required this.taskStatus,
    required this.queue,
    required this.mayDecide,
    required this.whyNot,
  });

  bool get hasTask => taskId.isNotEmpty;

  bool get isOpen => taskStatus == 'open';

  factory ProposalGate.fromJson(Map<String, dynamic> json) => ProposalGate(
    taskId: json['task_id'] as String? ?? '',
    taskStatus: json['task_status'] as String? ?? '',
    queue: json['queue'] as String? ?? '',
    mayDecide: json['may_decide'] as bool? ?? false,
    whyNot: json['why_not'] as String? ?? '',
  );
}

/// One proposed merge: the pair, the evidence, and where it stands.
class IdentityProposal {
  final String linkId;
  final String linkType;

  /// `link:<type>/<id>` — the same ref the Task carries as its `case_ref`, so
  /// the review screen finds its proposal without a second contract.
  final String caseRef;
  final String pairKind;

  /// `in_review` · `confirmed` · `withdrawn`.
  final String status;

  /// The machine's score, untouched by any decision: confirming does not
  /// promote it to 1.0, because that would erase how good the proposal was
  /// before a human looked at it.
  final double confidence;
  final String method;
  final List<IdentitySignal> signals;
  final String reversibleWith;
  final String policyRef;
  final String proposedAt;
  final String runId;

  /// Empty on a standing proposal. The sweep records itself as the decider of
  /// record; rendering that as a decision would be inventing history.
  final String decidedBy;
  final String decidedAt;
  final String withdrawnBecause;

  final IdentitySide left;
  final IdentitySide right;
  final List<String> agree;
  final List<String> conflict;
  final ProposalGate gate;

  const IdentityProposal({
    required this.linkId,
    required this.linkType,
    required this.caseRef,
    required this.pairKind,
    required this.status,
    required this.confidence,
    required this.method,
    required this.signals,
    required this.reversibleWith,
    required this.policyRef,
    required this.proposedAt,
    required this.runId,
    required this.decidedBy,
    required this.decidedAt,
    required this.withdrawnBecause,
    required this.left,
    required this.right,
    required this.agree,
    required this.conflict,
    required this.gate,
  });

  bool get isStanding => status == 'in_review';

  bool get isConfirmed => status == 'confirmed';

  bool get isWithdrawn => status == 'withdrawn';

  /// `0.79`, the way every screen in this console prints a confidence.
  String get score => confidence.toStringAsFixed(2);

  /// Both labels, in the order the edge holds them.
  String get pair => '${left.label} / ${right.label}';

  factory IdentityProposal.fromJson(Map<String, dynamic> json) =>
      IdentityProposal(
        linkId: json['link_id'] as String? ?? '',
        linkType: json['link_type'] as String? ?? '',
        caseRef: json['case_ref'] as String? ?? '',
        pairKind: json['pair_kind'] as String? ?? '',
        status: json['status'] as String? ?? '',
        confidence: (json['confidence'] as num? ?? 0).toDouble(),
        method: json['method'] as String? ?? '',
        signals: (json['signals'] as List<dynamic>? ?? const [])
            .map((e) => IdentitySignal.fromJson(e as Map<String, dynamic>))
            .toList(),
        reversibleWith: json['reversible_with'] as String? ?? '',
        policyRef: json['policy_ref'] as String? ?? '',
        proposedAt: json['proposed_at'] as String? ?? '',
        runId: json['run_id'] as String? ?? '',
        decidedBy: json['decided_by'] as String? ?? '',
        decidedAt: json['decided_at'] as String? ?? '',
        withdrawnBecause: json['withdrawn_because'] as String? ?? '',
        left: IdentitySide.fromJson(
          json['left'] as Map<String, dynamic>? ?? const {},
        ),
        right: IdentitySide.fromJson(
          json['right'] as Map<String, dynamic>? ?? const {},
        ),
        agree: (json['agree'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
        conflict: (json['conflict'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
        gate: ProposalGate.fromJson(
          json['gate'] as Map<String, dynamic>? ?? const {},
        ),
      );
}

/// One counterparty book, and what is outstanding against it.
class IdentityBook {
  final String type;
  final String label;
  final String stewardQueue;
  final int objects;
  final int standing;
  final int confirmed;
  final int withdrawn;

  /// Whether this persona owns the desk that judges this book.
  final bool yours;

  const IdentityBook({
    required this.type,
    required this.label,
    required this.stewardQueue,
    required this.objects,
    required this.standing,
    required this.confirmed,
    required this.withdrawn,
    required this.yours,
  });

  factory IdentityBook.fromJson(Map<String, dynamic> json) => IdentityBook(
    type: json['type'] as String? ?? '',
    label: json['label'] as String? ?? '',
    stewardQueue: json['steward_queue'] as String? ?? '',
    objects: json['objects'] as int? ?? 0,
    standing: json['standing'] as int? ?? 0,
    confirmed: json['confirmed'] as int? ?? 0,
    withdrawn: json['withdrawn'] as int? ?? 0,
    yours: json['yours'] as bool? ?? false,
  );
}

/// The governed thresholds, as the screen quotes them.
class SweepPolicy {
  final double proposeAt;
  final double stewardTaskBelow;
  final double nameFloor;
  final double ceiling;
  final String sourceRef;

  /// False when the policy asset is missing and code defaults are in force —
  /// a fact about the deployment, not a number to quote as governed.
  final bool foundAsset;

  const SweepPolicy({
    required this.proposeAt,
    required this.stewardTaskBelow,
    required this.nameFloor,
    required this.ceiling,
    required this.sourceRef,
    required this.foundAsset,
  });

  factory SweepPolicy.fromJson(Map<String, dynamic> json) => SweepPolicy(
    proposeAt: (json['propose_at'] as num? ?? 0).toDouble(),
    stewardTaskBelow: (json['steward_task_below'] as num? ?? 0).toDouble(),
    nameFloor: (json['name_floor'] as num? ?? 0).toDouble(),
    ceiling: (json['ceiling'] as num? ?? 0).toDouble(),
    sourceRef: json['source_ref'] as String? ?? '',
    foundAsset: json['found_asset'] as bool? ?? false,
  );
}

/// The last pass over the book. Null when there has never been one.
class SweepRun {
  final String runId;
  final String atIso;
  final String status;
  final int compared;
  final int considered;
  final int proposed;
  final int standing;
  final int stewardTasks;

  const SweepRun({
    required this.runId,
    required this.atIso,
    required this.status,
    required this.compared,
    required this.considered,
    required this.proposed,
    required this.standing,
    required this.stewardTasks,
  });

  factory SweepRun.fromJson(Map<String, dynamic> json) => SweepRun(
    runId: json['run_id'] as String? ?? '',
    atIso: json['at_iso'] as String? ?? '',
    status: json['status'] as String? ?? '',
    compared: json['compared'] as int? ?? 0,
    considered: json['considered'] as int? ?? 0,
    proposed: json['proposed'] as int? ?? 0,
    standing: json['standing'] as int? ?? 0,
    stewardTasks: json['steward_tasks'] as int? ?? 0,
  );
}

class IdentityScreen {
  final List<IdentityProposal> standing;
  final List<IdentityProposal> decided;

  /// What `decided` is a window onto. Equal to `decided.length` until the
  /// history outgrows the server's window.
  final int decidedTotal;
  final List<IdentityBook> books;
  final SweepPolicy policy;
  final SweepRun? lastSweep;

  /// The identity desks this persona holds. Empty for an observer.
  final List<String> desks;

  /// True when this persona is here as a curator rather than an observer.
  final bool curates;

  const IdentityScreen({
    required this.standing,
    required this.decided,
    required this.decidedTotal,
    required this.books,
    required this.policy,
    required this.lastSweep,
    required this.desks,
    required this.curates,
  });

  /// Every proposal, standing and ruled — how the entity view and the review
  /// screen find one by its case ref.
  Iterable<IdentityProposal> get all => [...standing, ...decided];

  IdentityProposal? byCaseRef(String caseRef) {
    for (final p in all) {
      if (p.caseRef == caseRef) return p;
    }
    return null;
  }

  factory IdentityScreen.fromJson(Map<String, dynamic> json) => IdentityScreen(
    standing: (json['standing'] as List<dynamic>? ?? const [])
        .map((e) => IdentityProposal.fromJson(e as Map<String, dynamic>))
        .toList(),
    decided: (json['decided'] as List<dynamic>? ?? const [])
        .map((e) => IdentityProposal.fromJson(e as Map<String, dynamic>))
        .toList(),
    decidedTotal: json['decided_total'] as int? ?? 0,
    books: (json['books'] as List<dynamic>? ?? const [])
        .map((e) => IdentityBook.fromJson(e as Map<String, dynamic>))
        .toList(),
    policy: SweepPolicy.fromJson(
      json['policy'] as Map<String, dynamic>? ?? const {},
    ),
    lastSweep: json['last_sweep'] == null
        ? null
        : SweepRun.fromJson(json['last_sweep'] as Map<String, dynamic>),
    desks: (json['desks'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    curates: json['curates'] as bool? ?? false,
  );
}
