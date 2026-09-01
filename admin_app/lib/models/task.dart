/// The Task Queue: units of human judgment, routed through Queues.
///
/// Mirrors `server/app/tasks.py`. A task carries its whole world — evidence,
/// the knowledge refs it rests on, the proposed action — so the person can
/// decide in seconds without leaving the panel.
library;

class TaskEvidence {
  final String label;
  final String value;

  const TaskEvidence({required this.label, required this.value});

  factory TaskEvidence.fromJson(Map<String, dynamic> json) => TaskEvidence(
    label: json['label'] as String? ?? '',
    value: json['value'] as String? ?? '',
  );
}

class TaskProposal {
  final String action;
  final String target;
  final String summary;

  const TaskProposal({
    required this.action,
    required this.target,
    required this.summary,
  });

  factory TaskProposal.fromJson(Map<String, dynamic> json) => TaskProposal(
    action: json['action'] as String? ?? '',
    target: json['target'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
  );
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
  final String caseRef;
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
    required this.caseRef,
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
    caseRef: json['case_ref'] as String? ?? '',
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
