import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../services/api_client.dart';
import 'invoice_review_core.dart';

/// Answering a held invoice — accounts-payable v6.
///
/// The moment the match or the anomaly screen raises: the extraction is not
/// in doubt, the *comparison* is. So what each line matched against leads, and
/// the editable reading follows underneath — because the usual fix for a
/// variance is still a number that was read wrong, or a PO the invoice never
/// carried.
///
/// A correction re-runs the checks rather than overriding them; approving
/// accepts the flags as they stand, and is recorded as exactly that.
class VerifyThreeWayMatch extends StatelessWidget {
  final ApiClient api;
  final QueueTask task;

  const VerifyThreeWayMatch({
    super.key,
    required this.api,
    required this.task,
  });

  @override
  Widget build(BuildContext context) {
    return InvoiceReviewCore(
      api: api,
      task: task,
      eyebrow: 'Invoice review · ${task.queue}',
      subtitle: 'Correct what the extraction read, and the three-way match '
          'and the anomaly screen run again over the corrected values — a '
          'correction re-runs the checks rather than overriding them.',
      primary: InvoicePanel.match,
    );
  }
}
