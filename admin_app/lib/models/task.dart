/// The Task Queue: units of human judgment, routed through Queues.
///
/// Mirrors `server/app/tasks.py`. A task carries its whole world — evidence,
/// the knowledge refs it rests on, the proposed action — so the person can
/// decide in seconds without leaving the panel.
library;

import 'data_layer.dart' show DocumentRegion;

class TaskEvidence {
  final String label;
  final String value;

  const TaskEvidence({required this.label, required this.value});

  factory TaskEvidence.fromJson(Map<String, dynamic> json) => TaskEvidence(
    label: json['label'] as String? ?? '',
    value: json['value'] as String? ?? '',
  );
}

/// One extracted field the flow's checks put in doubt, and where on the page
/// it was read from.
///
/// The seam between the flow that decides *which* fields need a human and the
/// screen that asks about them, written down in WORKPLAN-REAL §4 so neither
/// side owns it. The point of the shape is subtraction: an extraction has
/// thirty fields and a clerk should be asked about the three that are actually
/// in doubt, so a run reports a verdict per field and the screen leads with
/// the ones whose [status] is not `ok`.
///
/// [path] is the canonical fact path — the same vocabulary [DocumentRegion.field],
/// the flow contract and the golden sets use — which is what lets a box on the
/// page, an editable input and a correction all name one thing.
class FieldVerdict {
  final String path;

  /// Screen copy for the field: "Invoice total", "Line 3 — amount". Written by
  /// the step that raised the verdict because it knows what it looked at; the
  /// screen shows it rather than deriving a label from the path.
  final String label;

  /// The value as read, already rendered for display.
  final String value;

  /// 0–1 from the extractor. Zero means it did not say, which is not the same
  /// claim as "certainly wrong" and is rendered differently.
  final double confidence;

  /// `ok` · `low_confidence` · `inconsistent` · `missing`.
  final String status;

  /// One sentence, shown to the clerk verbatim. Never reworded on the way to
  /// the screen: it is the check's own account of what it found.
  final String reason;

  /// The box on the page, filled in by the targeted locate call. Present only
  /// for fields that need review — a clean field is never located, which is
  /// the whole reason the second model call is cheap — and absent even then
  /// when the locator could not find it or the document has no pages to draw.
  final DocumentRegion? region;

  const FieldVerdict({
    required this.path,
    required this.label,
    required this.value,
    required this.confidence,
    required this.status,
    required this.reason,
    this.region,
  });

  /// Whether this field is settled. Note the asymmetry: only the literal
  /// string `ok` is settled, so a verdict that arrives with no status at all
  /// reads as needing a look. Showing a clerk one field too many costs them a
  /// glance; hiding one the checks flagged costs the invoice.
  bool get ok => status == 'ok';

  bool get disputed => !ok;

  /// What to put in front of a person. Nothing derived from the path: a
  /// verdict with no label is a wire defect, and the path is the one string
  /// certain to be there.
  String get displayLabel => label.isNotEmpty ? label : path;

  factory FieldVerdict.fromJson(Map<String, dynamic> json) => FieldVerdict(
    path: json['path'] as String? ?? '',
    label: json['label'] as String? ?? '',
    value: json['value'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 0).toDouble(),
    status: json['status'] as String? ?? '',
    reason: json['reason'] as String? ?? '',
    region: json['region'] == null
        ? null
        : DocumentRegion.fromJson(json['region'] as Map<String, dynamic>),
  );
}

/// One vendor the resolver thought this invoice might be from, and how well.
class VendorCandidate {
  final String id;
  final String name;

  /// 0–1 from the resolver's own blend. Shown rather than thresholded: the
  /// reason a person is being asked is that no score cleared the bar, and
  /// hiding the numbers would hide how close the call is.
  final double score;

  const VendorCandidate({
    required this.id,
    required this.name,
    required this.score,
  });

  factory VendorCandidate.fromJson(Map<String, dynamic> json) =>
      VendorCandidate(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        score: (json['score'] as num? ?? 0).toDouble(),
      );
}

/// Which entity a case is about, when the resolver could not settle it.
///
/// Accounts payable v9 folds this into the invoice review rather than raising a
/// gate of its own: two gates over one document is two interruptions for one
/// reading of it, and the person checking the numbers can see the printed name
/// while they do it. Null is the normal case — the resolver settled it, or the
/// flow has no entity question — and a decision answers it by naming the chosen
/// id in its payload.
class VendorQuestion {
  /// The id the resolver settled on, or null when it settled nothing — which
  /// is the only reason this block is on a Task at all.
  final String? resolved;

  /// The name as printed on the document. What a person compares against.
  final String printed;

  final List<VendorCandidate> candidates;

  const VendorQuestion({
    this.resolved,
    required this.printed,
    this.candidates = const [],
  });

  factory VendorQuestion.fromJson(Map<String, dynamic> json) => VendorQuestion(
    resolved: json['resolved'] as String?,
    printed: json['printed'] as String? ?? '',
    candidates: [
      for (final candidate in (json['candidates'] as List<dynamic>? ?? const []))
        VendorCandidate.fromJson(candidate as Map<String, dynamic>),
    ],
  );
}

class TaskProposal {
  final String action;
  final String target;
  final String summary;

  /// The per-field verdicts, when the version that raised this task computed
  /// them. **Null and empty are different facts and the screens rely on it:**
  /// null is a flow that never ran a field-verdict step, and the honest screen
  /// for that is the whole extraction; empty is a flow that ran one and found
  /// nothing in doubt, which is a clean invoice and reads as a fast approve.
  /// Collapsing the two would turn "we did not look" into "we looked and it is
  /// fine".
  final List<FieldVerdict>? fields;

  /// The same `fields` before any flow's reading of them.
  ///
  /// `fields` is untyped by contract — the flow that raised the Task is the
  /// only thing that interprets it — and the two flows that use it put
  /// different shapes there. Accounts payable puts [FieldVerdict]s, which is
  /// what [fields] projects. Expense-reimbursement v10 puts one row per
  /// document: the stored artifact, the submitter's own memo about it, and the
  /// extraction summary for it. Reading those rows as verdicts would yield a
  /// verdict with no path, no status and no reason — not an error, which is
  /// worse than one — so a screen for the second reads these.
  final List<Map<String, dynamic>>? rawFields;

  /// The entity question this review also answers, or null when there is none.
  final VendorQuestion? vendor;

  const TaskProposal({
    required this.action,
    required this.target,
    required this.summary,
    this.fields,
    this.rawFields,
    this.vendor,
  });

  factory TaskProposal.fromJson(Map<String, dynamic> json) {
    final raw = json['fields'] == null
        ? null
        : [
            for (final field in json['fields'] as List<dynamic>)
              (field as Map).cast<String, dynamic>(),
          ];
    return TaskProposal(
      action: json['action'] as String? ?? '',
      target: json['target'] as String? ?? '',
      summary: json['summary'] as String? ?? '',
      fields: raw?.map(FieldVerdict.fromJson).toList(),
      rawFields: raw,
      vendor: json['vendor'] == null
          ? null
          : VendorQuestion.fromJson(
              (json['vendor'] as Map).cast<String, dynamic>(),
            ),
    );
  }
}

class TaskDecision {
  final String verdict; // approved | rejected | corrected
  final String note;
  final String correction;
  final String decidedBy;
  final String decidedAtIso;

  const TaskDecision({
    required this.verdict,
    required this.note,
    required this.correction,
    required this.decidedBy,
    required this.decidedAtIso,
  });

  factory TaskDecision.fromJson(Map<String, dynamic> json) => TaskDecision(
    verdict: json['verdict'] as String? ?? '',
    note: json['note'] as String? ?? '',
    correction: json['correction'] as String? ?? '',
    decidedBy: json['decided_by'] as String? ?? '',
    decidedAtIso: json['decided_at_iso'] as String? ?? '',
  );
}

/// The lease on a Task somebody is working right now.
///
/// A lease and not a lock: it expires, so an abandoned claim returns to the
/// pool rather than putting the absence problem the Queue exists to solve back
/// one level down. Mirrors `TaskClaim` in `server/app/tasks.py`.
class TaskClaim {
  final String holderUid;
  final String holderEmail;
  final String holderLabel;
  final String claimedAtIso;
  final String expiresAtIso;

  /// Times this claim was taken from a live holder. On the task rather than
  /// only in the log, because a case three people have wrestled over is a fact
  /// about the case.
  final int stealCount;

  const TaskClaim({
    required this.holderUid,
    required this.holderEmail,
    required this.holderLabel,
    required this.claimedAtIso,
    required this.expiresAtIso,
    required this.stealCount,
  });

  String get who => holderLabel.isNotEmpty
      ? holderLabel
      : (holderEmail.isNotEmpty ? holderEmail : holderUid);

  factory TaskClaim.fromJson(Map<String, dynamic> json) => TaskClaim(
    holderUid: json['holder_uid'] as String? ?? '',
    holderEmail: json['holder_email'] as String? ?? '',
    holderLabel: json['holder_label'] as String? ?? '',
    claimedAtIso: json['claimed_at_iso'] as String? ?? '',
    expiresAtIso: json['expires_at_iso'] as String? ?? '',
    stealCount: (json['steal_count'] as num? ?? 0).toInt(),
  );
}

class QueueTask {
  final String id;
  /// The run this task suspended. Non-empty for anything a live flow raised.
  final String runId;
  // decision | match_review | merge_review | curation | eval_review | exception
  final String kind;
  final String title;
  final String summary;
  final String queue;
  final String priority; // low | normal | high
  final String status; // open | done
  final String flowId;

  /// The flow-definition version this task was raised under.
  ///
  /// A task is answered by the screens of the definition that raised it, not
  /// by whatever is live when somebody gets to it: a queue three days deep can
  /// outlive a publish, and asking a v6 question through a v7 screen is how a
  /// reviewer ends up correcting a field the run never read. Zero when the
  /// record predates the field or the task was built locally — the registry
  /// falls back to the newest package it has.
  final int flowVersion;

  final String caseRef;

  /// Who the case is *about* — the submitter, on both flows that raise a gate
  /// over somebody's own paperwork. Empty when the case has no human subject
  /// (a sim-filed intake, an older record): a task about nobody is a real
  /// state, and a console that guessed one would be attributing work.
  ///
  /// First-class rather than display text in an evidence row, so a surface can
  /// show it without re-parsing prose. It is often, but not always, the person
  /// whose desk the task landed on — a routing fallback separates the two.
  final String subjectEmail;

  final List<String> refs;
  final List<TaskEvidence> evidence;
  final TaskProposal? proposal;
  final String resumes;
  final String createdAtIso;
  final TaskDecision? decision;

  /// Whether *this* principal may decide *this* task, computed server-side
  /// from exactly the checks the decide endpoint runs. The console used to
  /// render a live verdict row on every task in the deployment and discover
  /// the refusal only after somebody had committed to one.
  final bool decidable;

  /// Why not, written to be shown verbatim.
  final String blockedReason;

  /// free · yours · held
  final String claimState;

  /// Whether you hold this desk at all — a different question from whether you
  /// may decide right now. "Not your queue" is a dead end; "somebody is
  /// working it" is a Take-it-over button.
  final bool holdsQueue;

  /// Who does hold this desk. Telling somebody a queue is not theirs without
  /// naming whose leaves them nothing to do next.
  final List<String> queueHolders;

  final TaskClaim? claim;

  const QueueTask({
    required this.id,
    required this.runId,
    required this.kind,
    required this.title,
    required this.summary,
    required this.queue,
    required this.priority,
    required this.status,
    required this.flowId,
    this.flowVersion = 0,
    required this.caseRef,
    this.subjectEmail = '',
    required this.refs,
    required this.evidence,
    required this.proposal,
    required this.resumes,
    required this.createdAtIso,
    required this.decision,
    // Server-computed annotations rather than facts the task carries, so they
    // default: a locally constructed QueueTask — a test double, an optimistic
    // update — has no honest value for "may this principal decide it", and
    // the safe default for a permission is no.
    this.decidable = false,
    this.blockedReason = '',
    this.claimState = 'free',
    this.holdsQueue = false,
    this.queueHolders = const [],
    this.claim,
  });

  bool get isHeldByYou => claimState == 'yours';
  bool get isHeldBySomeoneElse => claimState == 'held';

  /// Take the lease. Offered on any open task on a desk you hold and are not
  /// already holding — including one somebody else has, where it becomes a
  /// takeover rather than a claim.
  bool get canClaim => isOpen && holdsQueue && !isHeldByYou;

  /// A takeover, which is recorded. Distinguished from a plain claim so the
  /// button can say which one it is before it is pressed.
  bool get claimWouldSteal => isHeldBySomeoneElse;

  bool get isOpen => status == 'open';
  bool get isHigh => priority == 'high';

  /// A task that must have a decision path without competing for attention
  /// with work that is waiting on one — an identity ratification, where the
  /// evidence is strong and a human still has to say yes.
  bool get isLow => priority == 'low';

  factory QueueTask.fromJson(Map<String, dynamic> json) => QueueTask(
    id: json['id'] as String,
    runId: json['run_id'] as String? ?? '',
    kind: json['kind'] as String? ?? 'decision',
    title: json['title'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    queue: json['queue'] as String? ?? '',
    priority: json['priority'] as String? ?? 'normal',
    status: json['status'] as String? ?? 'open',
    flowId: json['flow_id'] as String? ?? '',
    flowVersion: (json['flow_version'] as num? ?? 0).toInt(),
    caseRef: json['case_ref'] as String? ?? '',
    // Null on the wire is "this case is about nobody", which reads as empty
    // rather than as a missing value to go looking for.
    subjectEmail: json['subject_email'] as String? ?? '',
    refs: (json['refs'] as List<dynamic>? ?? const [])
        .map((e) => e as String)
        .toList(),
    evidence: (json['evidence'] as List<dynamic>? ?? const [])
        .map((e) => TaskEvidence.fromJson(e as Map<String, dynamic>))
        .toList(),
    proposal: json['proposal'] == null
        ? null
        : TaskProposal.fromJson(json['proposal'] as Map<String, dynamic>),
    resumes: json['resumes'] as String? ?? '',
    createdAtIso: json['created_at_iso'] as String? ?? '',
    decision: json['decision'] == null
        ? null
        : TaskDecision.fromJson(json['decision'] as Map<String, dynamic>),
    decidable: json['decidable'] as bool? ?? false,
    blockedReason: json['blocked_reason'] as String? ?? '',
    claimState: json['claim_state'] as String? ?? 'free',
    holdsQueue: json['holds_queue'] as bool? ?? false,
    queueHolders: (json['queue_holders'] as List<dynamic>? ?? const [])
        .map((e) => e as String)
        .toList(),
    claim: json['claim'] == null
        ? null
        : TaskClaim.fromJson(json['claim'] as Map<String, dynamic>),
  );
}

class TaskStats {
  final int open;
  final int highPriority;
  final Map<String, int> byQueue;
  final Map<String, int> byKind;

  const TaskStats({
    required this.open,
    required this.highPriority,
    required this.byQueue,
    required this.byKind,
  });

  static const empty = TaskStats(
    open: 0,
    highPriority: 0,
    byQueue: {},
    byKind: {},
  );

  factory TaskStats.fromJson(Map<String, dynamic> json) => TaskStats(
    open: json['open'] as int? ?? 0,
    highPriority: json['high_priority'] as int? ?? 0,
    byQueue: (json['by_queue'] as Map<String, dynamic>? ?? const {}).map(
      (k, v) => MapEntry(k, v as int),
    ),
    byKind: (json['by_kind'] as Map<String, dynamic>? ?? const {}).map(
      (k, v) => MapEntry(k, v as int),
    ),
  );
}
