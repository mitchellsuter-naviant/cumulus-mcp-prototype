/// The reimbursements table — the expense-reimbursement flow's output.
/// Mirrors `server/app/reimbursements.py`.
library;

class JournalEvent {
  final String stepId;
  final String name;
  final String kind;
  final String summary;
  final String atIso;

  const JournalEvent({
    required this.stepId,
    required this.name,
    required this.kind,
    required this.summary,
    required this.atIso,
  });

  factory JournalEvent.fromJson(Map<String, dynamic> json) => JournalEvent(
    stepId: json['step_id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    atIso: json['at_iso'] as String? ?? '',
  );
}

class Reimbursement {
  final String id;
  final String employee;
  final String submittedAtIso;
  final String filename;
  final String vendor;
  final String date;
  final double totalUsd;
  final String category;
  final double confidence;
  final String state; // approved | pending | denied
  final String verdictReason;
  final String policyRef;
  final double policyLimitUsd;
  final String model;
  final String taskId;
  final String decidedBy;
  final String decisionNote;
  final List<JournalEvent> journal;

  const Reimbursement({
    required this.id,
    required this.employee,
    required this.submittedAtIso,
    required this.filename,
    required this.vendor,
    required this.date,
    required this.totalUsd,
    required this.category,
    required this.confidence,
    required this.state,
    required this.verdictReason,
    required this.policyRef,
    required this.policyLimitUsd,
    required this.model,
    required this.taskId,
    required this.decidedBy,
    required this.decisionNote,
    required this.journal,
  });

  factory Reimbursement.fromJson(Map<String, dynamic> json) => Reimbursement(
    id: json['id'] as String,
    employee: json['employee'] as String? ?? '',
    submittedAtIso: json['submitted_at_iso'] as String? ?? '',
    filename: json['filename'] as String? ?? '',
    vendor: json['vendor'] as String? ?? '',
    date: json['date'] as String? ?? '',
    totalUsd: (json['total_usd'] as num? ?? 0).toDouble(),
    category: json['category'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 0).toDouble(),
    state: json['state'] as String? ?? 'pending',
    verdictReason: json['verdict_reason'] as String? ?? '',
    policyRef: json['policy_ref'] as String? ?? '',
    policyLimitUsd: (json['policy_limit_usd'] as num? ?? 0).toDouble(),
    model: json['model'] as String? ?? '',
    taskId: json['task_id'] as String? ?? '',
    decidedBy: json['decided_by'] as String? ?? '',
    decisionNote: json['decision_note'] as String? ?? '',
    journal: (json['journal'] as List<dynamic>? ?? const [])
        .map((e) => JournalEvent.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

class ReimbursementStats {
  final int total;
  final int approved;
  final int pending;
  final int denied;
  final double approvedUsd;
  final int autoApproved;

  const ReimbursementStats({
    required this.total,
    required this.approved,
    required this.pending,
    required this.denied,
    required this.approvedUsd,
    required this.autoApproved,
  });

  factory ReimbursementStats.fromJson(Map<String, dynamic> json) =>
      ReimbursementStats(
        total: json['total'] as int? ?? 0,
        approved: json['approved'] as int? ?? 0,
        pending: json['pending'] as int? ?? 0,
        denied: json['denied'] as int? ?? 0,
        approvedUsd: (json['approved_usd'] as num? ?? 0).toDouble(),
        autoApproved: json['auto_approved'] as int? ?? 0,
      );
}
