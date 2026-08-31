/// The order desk's payload. Mirrors `/api/ui/v1/disagreements`.
///
/// The identity workbench's sibling, and the difference is the whole of the
/// write plane. There, two rows *might* be one party and a steward says whether
/// they are; what changes is what Cumulus believes. Here the two rows are
/// certainly about one commitment, the customer's own two systems say different
/// things about it, and approving reaches out of Cumulus into an ERP that is
/// not ours.
///
/// So this payload carries one thing the identity one does not: [DisagreementChange],
/// which is what the write would do — read server-side off the same write table
/// the release Action commits through. Nothing on this side composes a
/// before/after of its own; a screen that did could promise a transition the
/// Action would refuse.
library;

import 'identity_screen.dart' show IdentitySignal;

export 'identity_screen.dart' show IdentitySignal;

/// One labelled fact off one book's row.
///
/// A list of label/value pairs rather than a map of props, because the two
/// columns this screen draws do **not** share a vocabulary — a deal has a
/// stage, an amount and a close date; an order has a status, a total and an
/// order date. There is no shared key to compare on, and pretending there is
/// one would draw a grid of empty cells.
class DisagreementFact {
  final String label;
  final String value;

  const DisagreementFact({required this.label, required this.value});

  factory DisagreementFact.fromJson(Map<String, dynamic> json) =>
      DisagreementFact(
        label: json['label'] as String? ?? '',
        value: json['value'] as String? ?? '',
      );
}

/// One system's account of the commitment, as a person reads it.
class DisagreementSide {
  final String id;
  final String ref;
  final String type;
  final String label;
  final String naturalKey;

  /// Which book this is, in the words the column is headed with.
  final String book;

  /// The state the finding turns on, in the ontology's slugged vocabulary.
  final String state;

  /// What this side held when the finding was written. Equal to [state] on a
  /// finding that is still true, and the whole point when it is not.
  final String stateWhenFound;

  final List<DisagreementFact> facts;

  /// Which feeds assert this row exists.
  final List<String> sources;

  /// True when the finding points at a row the book no longer holds.
  final bool missing;

  const DisagreementSide({
    required this.id,
    required this.ref,
    required this.type,
    required this.label,
    required this.naturalKey,
    required this.book,
    required this.state,
    required this.stateWhenFound,
    required this.facts,
    required this.sources,
    required this.missing,
  });

  /// True when this row has moved out from under the finding.
  bool get moved => stateWhenFound.isNotEmpty && state != stateWhenFound;

  factory DisagreementSide.fromJson(Map<String, dynamic> json) =>
      DisagreementSide(
        id: json['id'] as String? ?? '',
        ref: json['ref'] as String? ?? '',
        type: json['type'] as String? ?? '',
        label: json['label'] as String? ?? '',
        naturalKey: json['natural_key'] as String? ?? '',
        book: json['book'] as String? ?? '',
        state: json['state'] as String? ?? '',
        stateWhenFound: json['state_when_found'] as String? ?? '',
        facts: (json['facts'] as List<dynamic>? ?? const [])
            .map((e) => DisagreementFact.fromJson(e as Map<String, dynamic>))
            .toList(),
        sources: (json['sources'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
        missing: json['missing'] as bool? ?? false,
      );
}

/// What approving writes, and what it leaves exactly as it is.
///
/// Every field is read off the write table server-side. [unchanged] is
/// *derived* there — the ERP facts minus the one field the PATCH sets — so it
/// cannot drift from the column beside it, and a second writable field would
/// appear in both places at once or in neither.
class DisagreementChange {
  /// The one field the PATCH sets.
  final String field;

  /// The ERP's own words on both sides of the arrow. Not the graph's slug: the
  /// slug is Cumulus's vocabulary, and this is a promise about somebody else's
  /// system.
  final String fromValue;
  final String toValue;

  /// Everything the screen shows about the order that this write does not
  /// touch, in the labels the column used.
  final List<String> unchanged;

  final String target;
  final String app;
  final String resource;

  /// How the ERP addresses this row — off `external_id`, never the transaction
  /// id.
  final String recordId;

  /// Whether the write plane permits this write today.
  final bool permitted;

  /// Why not, when not. Always a sentence.
  final String whyNot;

  const DisagreementChange({
    required this.field,
    required this.fromValue,
    required this.toValue,
    required this.unchanged,
    required this.target,
    required this.app,
    required this.resource,
    required this.recordId,
    required this.permitted,
    required this.whyNot,
  });

  factory DisagreementChange.fromJson(Map<String, dynamic> json) =>
      DisagreementChange(
        field: json['field'] as String? ?? '',
        fromValue: json['from_value'] as String? ?? '',
        toValue: json['to_value'] as String? ?? '',
        unchanged: (json['unchanged'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
        target: json['target'] as String? ?? '',
        app: json['app'] as String? ?? '',
        resource: json['resource'] as String? ?? '',
        recordId: json['record_id'] as String? ?? '',
        permitted: json['permitted'] as bool? ?? false,
        whyNot: json['why_not'] as String? ?? '',
      );
}

/// Whether this finding can be decided, by whom, and where.
///
/// `ProposalGate`'s three states with different sentences behind the third. A
/// merge proposal with no Task is an anomaly the next sweep repairs; a finding
/// with no Task may equally be one the desk already settled — the sweep never
/// re-raises one — so the server's `why_not` names both readings and this side
/// draws it.
class DisagreementGate {
  final String taskId;

  /// `open`, or the verdict the desk already recorded.
  final String taskStatus;
  final String queue;
  final bool mayDecide;
  final String whyNot;

  const DisagreementGate({
    required this.taskId,
    required this.taskStatus,
    required this.queue,
    required this.mayDecide,
    required this.whyNot,
  });

  bool get hasTask => taskId.isNotEmpty;

  bool get isOpen => taskStatus == 'open';

  factory DisagreementGate.fromJson(Map<String, dynamic> json) =>
      DisagreementGate(
        taskId: json['task_id'] as String? ?? '',
        taskStatus: json['task_status'] as String? ?? '',
        queue: json['queue'] as String? ?? '',
        mayDecide: json['may_decide'] as bool? ?? false,
        whyNot: json['why_not'] as String? ?? '',
      );
}

/// One finding: the two rows, the evidence, the write, and where it stands.
class DisagreementCase {
  final String linkId;
  final String linkType;

  /// `link:<type>/<id>` — the ref the Task carries as its `case_ref`, so the
  /// review screen finds its case without a second contract.
  final String caseRef;

  final String findingClass;

  /// `standing` · `resolved` · `dismissed` · `superseded`.
  final String status;

  /// The machine's correlation score and the evidence behind it, untouched by
  /// any decision — closing a finding does not rewrite how good it was.
  final double confidence;
  final String method;
  final List<IdentitySignal> signals;
  final String policyRef;
  final String detectedAt;
  final String runId;
  final String counterparty;

  /// The two books, named for what they are rather than left/right: this
  /// screen is read across a labelled boundary, not down a symmetric pair.
  final DisagreementSide crm;
  final DisagreementSide erp;

  final DisagreementChange change;

  /// True when either row has moved since the finding was written. Not a
  /// refusal — the release re-reads the ERP itself and escalates — but the one
  /// fact a controller must have before pressing anything.
  final bool movedSinceFound;

  /// `by_cumulus` or `externally` on a resolved one; empty on every other
  /// status, `dismissed` included.
  final String resolvedHow;

  final String decidedAt;
  final String decidedBy;

  /// The desk's own words, off the edge.
  final String resolution;

  final DisagreementGate gate;

  const DisagreementCase({
    required this.linkId,
    required this.linkType,
    required this.caseRef,
    required this.findingClass,
    required this.status,
    required this.confidence,
    required this.method,
    required this.signals,
    required this.policyRef,
    required this.detectedAt,
    required this.runId,
    required this.counterparty,
    required this.crm,
    required this.erp,
    required this.change,
    required this.movedSinceFound,
    required this.resolvedHow,
    required this.decidedAt,
    required this.decidedBy,
    required this.resolution,
    required this.gate,
  });

  bool get isStanding => status == 'standing';

  bool get isDismissed => status == 'dismissed';

  bool get isResolved => status == 'resolved';

  /// True when Cumulus made the write, false when it found the work already
  /// done. Only meaningful on a resolved one.
  bool get releasedByCumulus => resolvedHow == 'by_cumulus';

  /// The score as the screen prints it. Two places, like the merge review's —
  /// a third would imply a precision the weights do not have.
  String get score => confidence.toStringAsFixed(2);

  factory DisagreementCase.fromJson(Map<String, dynamic> json) =>
      DisagreementCase(
        linkId: json['link_id'] as String? ?? '',
        linkType: json['link_type'] as String? ?? '',
        caseRef: json['case_ref'] as String? ?? '',
        findingClass: json['finding_class'] as String? ?? '',
        status: json['status'] as String? ?? '',
        confidence: (json['confidence'] as num? ?? 0).toDouble(),
        method: json['method'] as String? ?? '',
        signals: (json['signals'] as List<dynamic>? ?? const [])
            .map((e) => IdentitySignal.fromJson(e as Map<String, dynamic>))
            .toList(),
        policyRef: json['policy_ref'] as String? ?? '',
        detectedAt: json['detected_at'] as String? ?? '',
        runId: json['run_id'] as String? ?? '',
        counterparty: json['counterparty'] as String? ?? '',
        crm: DisagreementSide.fromJson(
          json['crm'] as Map<String, dynamic>? ?? const {},
        ),
        erp: DisagreementSide.fromJson(
          json['erp'] as Map<String, dynamic>? ?? const {},
        ),
        change: DisagreementChange.fromJson(
          json['change'] as Map<String, dynamic>? ?? const {},
        ),
        movedSinceFound: json['moved_since_found'] as bool? ?? false,
        resolvedHow: json['resolved_how'] as String? ?? '',
        decidedAt: json['decided_at'] as String? ?? '',
        decidedBy: json['decided_by'] as String? ?? '',
        resolution: json['resolution'] as String? ?? '',
        gate: DisagreementGate.fromJson(
          json['gate'] as Map<String, dynamic>? ?? const {},
        ),
      );
}

/// The last pass over the two books. Null when there has never been one.
///
/// Every field is a number the sweep published in its run outputs. Nothing here
/// is re-derived, and `blockedOnIdentity` could not be: it counts evidence the
/// pass **declined to read**, which leaves no trace on the graph to walk
/// afterwards.
class StateSweepRun {
  final String runId;
  final String atIso;
  final String status;
  final int ordersExamined;
  final int candidates;
  final int found;

  /// What unfinished identity work is costing, in evidence the sweep was not
  /// allowed to admit.
  final int blockedOnIdentity;

  final int orderWithoutDeal;
  final int ambiguous;
  final int deskTasks;

  const StateSweepRun({
    required this.runId,
    required this.atIso,
    required this.status,
    required this.ordersExamined,
    required this.candidates,
    required this.found,
    required this.blockedOnIdentity,
    required this.orderWithoutDeal,
    required this.ambiguous,
    required this.deskTasks,
  });

  factory StateSweepRun.fromJson(Map<String, dynamic> json) => StateSweepRun(
    runId: json['run_id'] as String? ?? '',
    atIso: json['at_iso'] as String? ?? '',
    status: json['status'] as String? ?? '',
    ordersExamined: json['orders_examined'] as int? ?? 0,
    candidates: json['candidates'] as int? ?? 0,
    found: json['found'] as int? ?? 0,
    blockedOnIdentity: json['blocked_on_identity'] as int? ?? 0,
    orderWithoutDeal: json['order_without_deal'] as int? ?? 0,
    ambiguous: json['ambiguous'] as int? ?? 0,
    deskTasks: json['desk_tasks'] as int? ?? 0,
  );
}

class DisagreementScreen {
  final List<DisagreementCase> standing;
  final List<DisagreementCase> decided;

  /// What `decided` is a window onto. Equal to `decided.length` until the
  /// history outgrows the server's window.
  final int decidedTotal;

  final StateSweepRun? lastSweep;

  /// True when this persona holds the desk rather than reading over its
  /// shoulder.
  final bool yours;

  final String desk;

  const DisagreementScreen({
    required this.standing,
    required this.decided,
    required this.decidedTotal,
    required this.lastSweep,
    required this.yours,
    required this.desk,
  });

  /// Every finding, standing and settled — how the review screen finds one by
  /// its case ref.
  Iterable<DisagreementCase> get all => [...standing, ...decided];

  DisagreementCase? byCaseRef(String caseRef) {
    for (final c in all) {
      if (c.caseRef == caseRef) return c;
    }
    return null;
  }

  factory DisagreementScreen.fromJson(Map<String, dynamic> json) =>
      DisagreementScreen(
        standing: (json['standing'] as List<dynamic>? ?? const [])
            .map((e) => DisagreementCase.fromJson(e as Map<String, dynamic>))
            .toList(),
        decided: (json['decided'] as List<dynamic>? ?? const [])
            .map((e) => DisagreementCase.fromJson(e as Map<String, dynamic>))
            .toList(),
        decidedTotal: json['decided_total'] as int? ?? 0,
        lastSweep: json['last_sweep'] == null
            ? null
            : StateSweepRun.fromJson(
                json['last_sweep'] as Map<String, dynamic>,
              ),
        yours: json['yours'] as bool? ?? false,
        desk: json['desk'] as String? ?? '',
      );
}
