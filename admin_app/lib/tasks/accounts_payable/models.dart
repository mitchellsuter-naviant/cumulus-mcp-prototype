/// The AP invoices table — the accounts-payable flow's output.
/// Mirrors `server/app/payables.py`.
library;

import '../expense_reimbursement/models.dart' show JournalEvent;

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
  final String vendorTaxId;
  final String invoiceNumber;
  final String invoiceDate;
  final String dueDate;
  final String poNumber;
  final double totalUsd;
  final String lineSummary;
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
    required this.vendorTaxId,
    required this.invoiceNumber,
    required this.invoiceDate,
    required this.dueDate,
    required this.poNumber,
    required this.totalUsd,
    required this.lineSummary,
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
    vendorTaxId: json['vendor_tax_id'] as String? ?? '',
    invoiceNumber: json['invoice_number'] as String? ?? '',
    invoiceDate: json['invoice_date'] as String? ?? '',
    dueDate: json['due_date'] as String? ?? '',
    poNumber: json['po_number'] as String? ?? '',
    totalUsd: (json['total_usd'] as num? ?? 0).toDouble(),
    lineSummary: json['line_summary'] as String? ?? '',
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
    matched: json['matched'] as int? ?? 0,
    matchVariance: json['match_variance'] as int? ?? 0,
    matchBlocked: json['match_blocked'] as int? ?? 0,
    anomalies: json['anomalies'] as int? ?? 0,
  );
}

// ---------------------------------------------------------------------------
// The submission panel, and one invoice as a screen
// ---------------------------------------------------------------------------

/// One governed value the flow reads, at the revision it reads it.
///
/// [deciding] is set when this run's verdict reason names it — attribution of
/// a sentence the Rule wrote, never a second copy of the Rule.
class PolicyClause {
  final String id;
  final String label;
  final String value;
  final String ref;
  final bool deciding;

  const PolicyClause({
    required this.id,
    required this.label,
    required this.value,
    required this.ref,
    required this.deciding,
  });

  factory PolicyClause.fromJson(Map<String, dynamic> json) => PolicyClause(
    id: json['id'] as String? ?? '',
    label: json['label'] as String? ?? '',
    value: json['value'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
    deciding: json['deciding'] as bool? ?? false,
  );
}

/// Where a run stopped, and whether this persona may release it.
class OutcomeGate {
  final String stepId;
  final String stepName;
  final String taskId;
  final String taskTitle;
  final String queue;
  final bool mayDecide;
  final String whyNot;

  const OutcomeGate({
    required this.stepId,
    required this.stepName,
    required this.taskId,
    required this.taskTitle,
    required this.queue,
    required this.mayDecide,
    required this.whyNot,
  });

  factory OutcomeGate.fromJson(Map<String, dynamic> json) => OutcomeGate(
    stepId: json['step_id'] as String? ?? '',
    stepName: json['step_name'] as String? ?? '',
    taskId: json['task_id'] as String? ?? '',
    taskTitle: json['task_title'] as String? ?? '',
    queue: json['queue'] as String? ?? '',
    mayDecide: json['may_decide'] as bool? ?? false,
    whyNot: json['why_not'] as String? ?? '',
  );
}

/// One invoice as a screen: the row, where it stopped, and what it read.
/// The same shape for a row submitted a second ago and one from last week, so
/// the console renders both with one widget.
class PayableOutcome {
  final PayableInvoice invoice;
  final OutcomeGate? gate;
  final List<PolicyClause> policy;

  const PayableOutcome({
    required this.invoice,
    required this.gate,
    required this.policy,
  });

  factory PayableOutcome.fromJson(Map<String, dynamic> json) => PayableOutcome(
    invoice: PayableInvoice.fromJson(
      json['invoice'] as Map<String, dynamic>,
    ),
    gate: json['gate'] == null
        ? null
        : OutcomeGate.fromJson(json['gate'] as Map<String, dynamic>),
    policy: (json['policy'] as List<dynamic>? ?? const [])
        .map((e) => PolicyClause.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

/// What the submission door says for itself: the governed clauses that will
/// judge an upload, and the extractor that will read it.
///
/// It used to be a composer — a vendor picker, an order picker, line steppers
/// and four "deviation" checkboxes that serialized to a hint string. Every
/// control was a real graph row and the whole thing was still a way to make an
/// extraction out of a form. The one door left carries bytes.
class SubmissionPanel {
  final List<PolicyClause> policy;

  /// The extractor wired behind the ModelCall — `stub` locally, which reads
  /// the *filename* rather than the page. The upload card says so, because a
  /// person is owed that before they read anything into the answer.
  final String extractor;

  const SubmissionPanel({required this.policy, required this.extractor});

  PolicyClause? clause(String id) {
    for (final c in policy) {
      if (c.id == id) return c;
    }
    return null;
  }

  factory SubmissionPanel.fromJson(Map<String, dynamic> json) =>
      SubmissionPanel(
        policy: (json['policy'] as List<dynamic>? ?? const [])
            .map((e) => PolicyClause.fromJson(e as Map<String, dynamic>))
            .toList(),
        extractor: json['extractor'] as String? ?? '',
      );
}

class PayablesScreen {
  final List<PayableOutcome> invoices;
  final PayableStats stats;
  final SubmissionPanel submission;

  const PayablesScreen({
    required this.invoices,
    required this.stats,
    required this.submission,
  });

  factory PayablesScreen.fromJson(Map<String, dynamic> json) => PayablesScreen(
    invoices: (json['invoices'] as List<dynamic>? ?? const [])
        .map((e) => PayableOutcome.fromJson(e as Map<String, dynamic>))
        .toList(),
    stats: PayableStats.fromJson(json['stats'] as Map<String, dynamic>),
    submission: SubmissionPanel.fromJson(
      (json['submission'] as Map<String, dynamic>?) ?? const {},
    ),
  );
}
