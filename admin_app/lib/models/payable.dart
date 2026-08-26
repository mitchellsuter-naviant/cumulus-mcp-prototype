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

/// One billed line on an invoice.
class InvoiceLine {
  final String sku;
  final String description;
  final double quantity;
  final double unitPriceUsd;
  final double amountUsd;
  final String kind; // goods | freight | tax | discount

  const InvoiceLine({
    required this.sku,
    required this.description,
    required this.quantity,
    required this.unitPriceUsd,
    required this.amountUsd,
    required this.kind,
  });

  factory InvoiceLine.fromJson(Map<String, dynamic> json) => InvoiceLine(
    sku: json['sku'] as String? ?? '',
    description: json['description'] as String? ?? '',
    quantity: (json['quantity'] as num? ?? 0).toDouble(),
    unitPriceUsd: (json['unit_price_usd'] as num? ?? 0).toDouble(),
    amountUsd: (json['amount_usd'] as num? ?? 0).toDouble(),
    kind: json['kind'] as String? ?? 'goods',
  );
}

/// One pairing in the retained three-way match: an invoice line against the PO
/// line that ordered it and the receiving line that booked it in, with the
/// variance and the reason. Kept rather than recomputed, so a clerk sees which
/// line failed and an auditor can ask again in two years.
class MatchPairing {
  final int invoiceOrdinal;
  final String description;
  final String sku;
  final String kind;
  final double invoiceQuantity;
  final double invoiceUnitPriceUsd;
  final double invoiceAmountUsd;
  final String poNumber;
  final int poOrdinal;
  final double poQuantity;
  final double poUnitPriceUsd;
  final double poMatchScore;
  final String receiptNumber;
  final double receivedQuantity;
  final double priceVarianceUsd;
  final double priceVariancePct;
  final double quantityVariance;
  final String verdict;
  final String reason;

  const MatchPairing({
    required this.invoiceOrdinal,
    required this.description,
    required this.sku,
    required this.kind,
    required this.invoiceQuantity,
    required this.invoiceUnitPriceUsd,
    required this.invoiceAmountUsd,
    required this.poNumber,
    required this.poOrdinal,
    required this.poQuantity,
    required this.poUnitPriceUsd,
    required this.poMatchScore,
    required this.receiptNumber,
    required this.receivedQuantity,
    required this.priceVarianceUsd,
    required this.priceVariancePct,
    required this.quantityVariance,
    required this.verdict,
    required this.reason,
  });

  bool get clean => verdict == 'matched' || verdict == 'allowed-no-po';

  factory MatchPairing.fromJson(Map<String, dynamic> json) => MatchPairing(
    invoiceOrdinal: json['invoice_ordinal'] as int? ?? 0,
    description: json['description'] as String? ?? '',
    sku: json['sku'] as String? ?? '',
    kind: json['kind'] as String? ?? 'goods',
    invoiceQuantity: (json['invoice_quantity'] as num? ?? 0).toDouble(),
    invoiceUnitPriceUsd: (json['invoice_unit_price_usd'] as num? ?? 0).toDouble(),
    invoiceAmountUsd: (json['invoice_amount_usd'] as num? ?? 0).toDouble(),
    poNumber: json['po_number'] as String? ?? '',
    poOrdinal: json['po_ordinal'] as int? ?? 0,
    poQuantity: (json['po_quantity'] as num? ?? 0).toDouble(),
    poUnitPriceUsd: (json['po_unit_price_usd'] as num? ?? 0).toDouble(),
    poMatchScore: (json['po_match_score'] as num? ?? 0).toDouble(),
    receiptNumber: json['receipt_number'] as String? ?? '',
    receivedQuantity: (json['received_quantity'] as num? ?? 0).toDouble(),
    priceVarianceUsd: (json['price_variance_usd'] as num? ?? 0).toDouble(),
    priceVariancePct: (json['price_variance_pct'] as num? ?? 0).toDouble(),
    quantityVariance: (json['quantity_variance'] as num? ?? 0).toDouble(),
    verdict: json['verdict'] as String? ?? '',
    reason: json['reason'] as String? ?? '',
  );
}

/// One anomaly the screen found, scored against this vendor's own history.
class AnomalyFinding {
  final String kind;
  final String detail;

  const AnomalyFinding({required this.kind, required this.detail});

  factory AnomalyFinding.fromJson(Map<String, dynamic> json) => AnomalyFinding(
    kind: json['kind'] as String? ?? '',
    detail: json['detail'] as String? ?? '',
  );
}

class PayableInvoice {
  final String id;
  final String runId;
  final String documentId;
  final String vendorId;
  final String vendorResolution; // linked | new | review
  final String vendorMethod;
  final List<String> vendorCandidates;
  final double subtotalUsd;
  final double freightUsd;
  final double taxUsd;
  final String currency;
  final List<InvoiceLine> lines;
  final String matchVerdict;
  final String matchSummary;
  final String matchRef;
  final List<MatchPairing> matchPairings;
  final List<AnomalyFinding> anomalyFindings;
  final String anomalyBasis;
  final String graphRef;
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
    required this.runId,
    required this.documentId,
    required this.vendorId,
    required this.vendorResolution,
    required this.vendorMethod,
    required this.vendorCandidates,
    required this.subtotalUsd,
    required this.freightUsd,
    required this.taxUsd,
    required this.currency,
    required this.lines,
    required this.matchVerdict,
    required this.matchSummary,
    required this.matchRef,
    required this.matchPairings,
    required this.anomalyFindings,
    required this.anomalyBasis,
    required this.graphRef,
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
    runId: json['run_id'] as String? ?? '',
    documentId: json['document_id'] as String? ?? '',
    vendorId: json['vendor_id'] as String? ?? '',
    vendorResolution: json['vendor_resolution'] as String? ?? '',
    vendorMethod: json['vendor_method'] as String? ?? '',
    vendorCandidates: (json['vendor_candidates'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    subtotalUsd: (json['subtotal_usd'] as num? ?? 0).toDouble(),
    freightUsd: (json['freight_usd'] as num? ?? 0).toDouble(),
    taxUsd: (json['tax_usd'] as num? ?? 0).toDouble(),
    currency: json['currency'] as String? ?? 'USD',
    lines: (json['lines'] as List<dynamic>? ?? const [])
        .map((e) => InvoiceLine.fromJson(e as Map<String, dynamic>))
        .toList(),
    matchVerdict: json['match_verdict'] as String? ?? '',
    matchSummary: json['match_summary'] as String? ?? '',
    matchRef: json['match_ref'] as String? ?? '',
    matchPairings: (json['match_pairings'] as List<dynamic>? ?? const [])
        .map((e) => MatchPairing.fromJson(e as Map<String, dynamic>))
        .toList(),
    anomalyFindings: (json['anomaly_findings'] as List<dynamic>? ?? const [])
        .map((e) => AnomalyFinding.fromJson(e as Map<String, dynamic>))
        .toList(),
    anomalyBasis: json['anomaly_basis'] as String? ?? '',
    graphRef: json['graph_ref'] as String? ?? '',
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
    threeWayMatch: json['three_way_match'] as String? ?? '',
    fraudScreen: json['fraud_screen'] as String? ?? '',
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
  final int matched;
  final int matchVariance;
  final int matchBlocked;
  final int anomalies;

  const PayableStats({
    required this.total,
    required this.posted,
    required this.pending,
    required this.denied,
    required this.postedUsd,
    required this.oneShot,
    required this.ensemble,
    required this.matched,
    required this.matchVariance,
    required this.matchBlocked,
    required this.anomalies,
  });

  factory PayableStats.fromJson(Map<String, dynamic> json) => PayableStats(
    total: json['total'] as int? ?? 0,
    posted: json['posted'] as int? ?? 0,
    pending: json['pending'] as int? ?? 0,
    denied: json['denied'] as int? ?? 0,
    postedUsd: (json['posted_usd'] as num? ?? 0).toDouble(),
    oneShot: json['one_shot'] as int? ?? 0,
    ensemble: json['ensemble'] as int? ?? 0,
    matched: json['matched'] as int? ?? 0,
    matchVariance: json['match_variance'] as int? ?? 0,
    matchBlocked: json['match_blocked'] as int? ?? 0,
    anomalies: json['anomalies'] as int? ?? 0,
  );
}
