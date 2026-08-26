/// The AP invoices table — the accounts-payable flow's output.
/// Mirrors `server/app/payables.py`.
library;

import 'reimbursement.dart' show JournalEvent;

class InvoiceCandidate {
  final String model;
  final Map<String, String> fields;

  const InvoiceCandidate({required this.model, required this.fields});

  factory InvoiceCandidate.fromJson(Map<String, dynamic> json) {
    final fields = <String, String>{};
    json.forEach((k, v) {
      if (k != 'model' && v != null) fields[k] = '$v';
    });
    return InvoiceCandidate(
      model: json['model'] as String? ?? '',
      fields: fields,
    );
  }
}

class PayableInvoice {
  final String id;
  final String submittedBy;
  final String submittedAtIso;
  final String vendorSlug;
  final String vendorName;
  final double vendorConfidence;
  final String exemplarRef;
  final String extractionPath; // one-shot | ensemble
  final String invoiceNumber;
  final String invoiceDate;
  final String dueDate;
  final String poNumber;
  final double totalUsd;
  final String lineSummary;
  final List<InvoiceCandidate> candidates;
  final List<String> conflicts;
  final String threeWayMatch;
  final String fraudScreen;
  final String state; // posted | pending | denied
  final String verdictReason;
  final String policyRef;
  final double postLimitUsd;
  final String taskId;
  final String decidedBy;
  final String decisionNote;
  final List<JournalEvent> journal;

  const PayableInvoice({
    required this.id,
    required this.submittedBy,
    required this.submittedAtIso,
    required this.vendorSlug,
    required this.vendorName,
    required this.vendorConfidence,
    required this.exemplarRef,
    required this.extractionPath,
    required this.invoiceNumber,
    required this.invoiceDate,
    required this.dueDate,
    required this.poNumber,
    required this.totalUsd,
    required this.lineSummary,
    required this.candidates,
    required this.conflicts,
    required this.threeWayMatch,
    required this.fraudScreen,
    required this.state,
    required this.verdictReason,
    required this.policyRef,
    required this.postLimitUsd,
    required this.taskId,
    required this.decidedBy,
    required this.decisionNote,
    required this.journal,
  });

  factory PayableInvoice.fromJson(Map<String, dynamic> json) => PayableInvoice(
    id: json['id'] as String,
    submittedBy: json['submitted_by'] as String? ?? '',
    submittedAtIso: json['submitted_at_iso'] as String? ?? '',
    vendorSlug: json['vendor_slug'] as String? ?? '',
    vendorName: json['vendor_name'] as String? ?? '',
    vendorConfidence: (json['vendor_confidence'] as num? ?? 0).toDouble(),
    exemplarRef: json['exemplar_ref'] as String? ?? '',
    extractionPath: json['extraction_path'] as String? ?? '',
    invoiceNumber: json['invoice_number'] as String? ?? '',
    invoiceDate: json['invoice_date'] as String? ?? '',
    dueDate: json['due_date'] as String? ?? '',
    poNumber: json['po_number'] as String? ?? '',
    totalUsd: (json['total_usd'] as num? ?? 0).toDouble(),
    lineSummary: json['line_summary'] as String? ?? '',
    candidates: (json['candidates'] as List<dynamic>? ?? const [])
        .map((e) => InvoiceCandidate.fromJson(e as Map<String, dynamic>))
        .toList(),
    conflicts: (json['conflicts'] as List<dynamic>? ?? const [])
        .map((e) => e as String)
        .toList(),
    threeWayMatch: json['three_way_match'] as String? ?? 'placeholder',
    fraudScreen: json['fraud_screen'] as String? ?? 'placeholder',
    state: json['state'] as String? ?? 'pending',
    verdictReason: json['verdict_reason'] as String? ?? '',
    policyRef: json['policy_ref'] as String? ?? '',
    postLimitUsd: (json['post_limit_usd'] as num? ?? 0).toDouble(),
    taskId: json['task_id'] as String? ?? '',
    decidedBy: json['decided_by'] as String? ?? '',
    decisionNote: json['decision_note'] as String? ?? '',
    journal: (json['journal'] as List<dynamic>? ?? const [])
        .map((e) => JournalEvent.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

class PayableStats {
  final int total;
  final int posted;
  final int pending;
  final int denied;
  final double postedUsd;
  final int oneShot;
  final int ensemble;

  const PayableStats({
    required this.total,
    required this.posted,
    required this.pending,
    required this.denied,
    required this.postedUsd,
    required this.oneShot,
    required this.ensemble,
  });

  factory PayableStats.fromJson(Map<String, dynamic> json) => PayableStats(
    total: json['total'] as int? ?? 0,
    posted: json['posted'] as int? ?? 0,
    pending: json['pending'] as int? ?? 0,
    denied: json['denied'] as int? ?? 0,
    postedUsd: (json['posted_usd'] as num? ?? 0).toDouble(),
    oneShot: json['one_shot'] as int? ?? 0,
    ensemble: json['ensemble'] as int? ?? 0,
  );
}
