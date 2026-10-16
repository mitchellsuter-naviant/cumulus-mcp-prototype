import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../services/api_client.dart';
import 'invoice_review_core.dart';

/// Verifying what the extraction read off an invoice — accounts-payable v6.
///
/// The moment a `curation` task raises: the model has read a document and is
/// not sure enough to post it. The numbers on the document lead, because the
/// question is whether they are the right numbers; the match and the anomaly
/// screen follow, as the consequence of getting them wrong.
///
/// Nothing is hidden either way. A correction here carries the same payload
/// [VerifyThreeWayMatch] sends, and the flow re-runs the same checks over it.
class ExtractionReview extends StatelessWidget {
  final ApiClient api;
  final QueueTask task;

  const ExtractionReview({super.key, required this.api, required this.task});

  @override
  Widget build(BuildContext context) {
    return InvoiceReviewCore(
      api: api,
      task: task,
      eyebrow: 'Extraction review · ${task.queue}',
      subtitle: 'Check what the extraction read against the document, and '
          'correct it where it read wrong — the three-way match and the '
          'anomaly screen then run again over the corrected values.',
      primary: InvoicePanel.extraction,
    );
  }
}
