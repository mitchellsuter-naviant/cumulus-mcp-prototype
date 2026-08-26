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

/// One reimbursable fact. Deliberately not the same thing as a document: a
/// compound receipt yields several of these, and a reviewer may add one the
/// extraction missed.
class ExpenseLine {
  final int ordinal;
  final String documentId;
  final String vendor;
  final String date;
  final String category;
  final String description;

  /// What the paper says, and what the employee is actually claiming. The
  /// difference is the submitter's own split and is legitimate.
  final double documentTotalUsd;
  final double requestedUsd;
  final String purpose;
  final double confidence;
  final String origin; // model | human
  final String correctedBy;
  final String graphRef;

  const ExpenseLine({
    required this.ordinal,
    required this.documentId,
    required this.vendor,
    required this.date,
    required this.category,
    required this.description,
    required this.documentTotalUsd,
    required this.requestedUsd,
    required this.purpose,
    required this.confidence,
    required this.origin,
    required this.correctedBy,
    required this.graphRef,
  });

  bool get isHuman => origin == 'human';

  /// True when the submitter (or a reviewer) claimed less than the receipt.
  bool get isSplit => requestedUsd < documentTotalUsd - 0.005;

  factory ExpenseLine.fromJson(Map<String, dynamic> json) => ExpenseLine(
    ordinal: json['ordinal'] as int? ?? 0,
    documentId: json['document_id'] as String? ?? '',
    vendor: json['vendor'] as String? ?? '',
    date: json['date'] as String? ?? '',
    category: json['category'] as String? ?? '',
    description: json['description'] as String? ?? '',
    documentTotalUsd: (json['document_total_usd'] as num? ?? 0).toDouble(),
    requestedUsd: (json['requested_usd'] as num? ?? 0).toDouble(),
    purpose: json['purpose'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 0).toDouble(),
    origin: json['origin'] as String? ?? 'model',
    correctedBy: json['corrected_by'] as String? ?? '',
    graphRef: json['graph_ref'] as String? ?? '',
  );
}

/// A stored artifact as the run refers to it afterwards.
class DocumentSlot {
  final String documentId;
  final String filename;
  final String note;
  final int byteSize;
  final String mediaType;

  const DocumentSlot({
    required this.documentId,
    required this.filename,
    required this.note,
    required this.byteSize,
    required this.mediaType,
  });

  factory DocumentSlot.fromJson(Map<String, dynamic> json) => DocumentSlot(
    documentId: json['document_id'] as String? ?? '',
    filename: json['filename'] as String? ?? '',
    note: json['note'] as String? ?? '',
    byteSize: json['byte_size'] as int? ?? 0,
    mediaType: json['media_type'] as String? ?? '',
  );
}

/// Why a request was elevated. Typed, because the reasons need different
/// review screens: fixing an extraction is not the same job as supplying a
/// missing purpose, which is not the same job as ruling on something out of
/// policy.
class Escalation {
  final String kind;
  final int lineOrdinal;
  final String detail;

  const Escalation({
    required this.kind,
    required this.lineOrdinal,
    required this.detail,
  });

  factory Escalation.fromJson(Map<String, dynamic> json) => Escalation(
    kind: json['kind'] as String? ?? '',
    lineOrdinal: json['line_ordinal'] as int? ?? 0,
    detail: json['detail'] as String? ?? '',
  );
}

class Reimbursement {
  final String id;
  final String runId;
  final String commentary;
  final List<DocumentSlot> documents;
  final List<ExpenseLine> lines;
  final double requestedUsd;
  final double approvedUsd;
  final List<Escalation> escalations;
  final int reviewRounds;
  final bool humanOverride;
  final String graphRef;
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
    required this.runId,
    required this.commentary,
    required this.documents,
    required this.lines,
    required this.requestedUsd,
    required this.approvedUsd,
    required this.escalations,
    required this.reviewRounds,
    required this.humanOverride,
    required this.graphRef,
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
    runId: json['run_id'] as String? ?? '',
    commentary: json['commentary'] as String? ?? '',
    documents: (json['documents'] as List<dynamic>? ?? const [])
        .map((e) => DocumentSlot.fromJson(e as Map<String, dynamic>))
        .toList(),
    lines: (json['lines'] as List<dynamic>? ?? const [])
        .map((e) => ExpenseLine.fromJson(e as Map<String, dynamic>))
        .toList(),
    requestedUsd: (json['requested_usd'] as num? ?? 0).toDouble(),
    approvedUsd: (json['approved_usd'] as num? ?? 0).toDouble(),
    escalations: (json['escalations'] as List<dynamic>? ?? const [])
        .map((e) => Escalation.fromJson(e as Map<String, dynamic>))
        .toList(),
    reviewRounds: json['review_rounds'] as int? ?? 0,
    humanOverride: json['human_override'] as bool? ?? false,
    graphRef: json['graph_ref'] as String? ?? '',
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
  final int lines;
  final int humanCorrected;

  const ReimbursementStats({
    required this.total,
    required this.approved,
    required this.pending,
    required this.denied,
    required this.approvedUsd,
    required this.autoApproved,
    required this.lines,
    required this.humanCorrected,
  });

  factory ReimbursementStats.fromJson(Map<String, dynamic> json) =>
      ReimbursementStats(
        total: json['total'] as int? ?? 0,
        approved: json['approved'] as int? ?? 0,
        pending: json['pending'] as int? ?? 0,
        denied: json['denied'] as int? ?? 0,
        approvedUsd: (json['approved_usd'] as num? ?? 0).toDouble(),
        autoApproved: json['auto_approved'] as int? ?? 0,
        lines: json['lines'] as int? ?? 0,
        humanCorrected: json['human_corrected'] as int? ?? 0,
      );
}
