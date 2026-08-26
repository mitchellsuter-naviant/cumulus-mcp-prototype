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

class QueueTask {
  final String id;
  final String kind; // decision | match_review | curation | eval_review | exception
  final String title;
  final String summary;
  final String queue;
  final String priority; // normal | high
  final String status; // open | done
  final String flowId;
  final String caseRef;
  final List<String> refs;
  final List<TaskEvidence> evidence;
  final TaskProposal? proposal;
  final String resumes;
  final String createdAtIso;
  final TaskDecision? decision;

  const QueueTask({
    required this.id,
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
  });

  bool get isOpen => status == 'open';
  bool get isHigh => priority == 'high';

  factory QueueTask.fromJson(Map<String, dynamic> json) => QueueTask(
    id: json['id'] as String,
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
