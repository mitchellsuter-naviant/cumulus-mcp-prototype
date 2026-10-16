/// The AP workspace's payload, after the composer left.
///
/// The deleted `invoice_scenario_test.dart` pinned the console's copy of the
/// hint grammar to the hints the repository ships. There is no copy any more:
/// the grammar is server-side authoring apparatus, the console posts bytes,
/// and what is worth pinning is the *absence* — a payload that grew a
/// `scenario` or a `twists` list again would be one commit from composing.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/tasks/accounts_payable/models.dart';

Map<String, dynamic> _screen({Map<String, dynamic>? submission}) => {
  'invoices': [
    {
      'invoice': {
        'id': 'inv_1',
        'run_id': 'run_ap_1',
        'submitted_by': 'flow:ap-intake',
        'submitted_at_iso': '2026-09-02T10:00:00+00:00',
        'invoice_number': 'ACME-INV-9001',
        'total_usd': 1840.0,
        'state': 'posted',
        'document_id': 'doc_abc123',
      },
      'gate': null,
      'policy': [
        {
          'id': 'auto-post-limit',
          'label': 'Auto-post limit',
          'value': r'$2,500.00',
          'ref': 'knowledge:invoice-posting-policy@rev1',
          'deciding': true,
        },
      ],
    },
  ],
  'stats': {'total': 1, 'posted': 1},
  'submission':
      submission ??
      {
        'policy': [
          {
            'id': 'auto-post-limit',
            'label': 'Auto-post limit',
            'value': r'$2,500.00',
            'ref': 'knowledge:invoice-posting-policy@rev1',
            'deciding': false,
          },
        ],
        'extractor': 'stub',
      },
};

void main() {
  group('the submission panel', () {
    test('carries the governed clauses and the reader that will read the page', () {
      final screen = PayablesScreen.fromJson(_screen());
      expect(screen.submission.extractor, 'stub');
      final limit = screen.submission.clause('auto-post-limit');
      expect(limit, isNotNull);
      expect(limit!.value, r'$2,500.00');
      expect(limit.ref, 'knowledge:invoice-posting-policy@rev1');
      // A clause nobody serves is null rather than a blank row.
      expect(screen.submission.clause('not-a-clause'), isNull);
    });

    test('a payload with no panel degrades instead of throwing', () {
      // The screen used to hard-cast `json['composer']`, so a server that
      // stopped serving the key took the workspace down with it. A console and
      // a server are deployed separately and one of them is always first.
      final withoutPanel = Map<String, dynamic>.from(_screen())
        ..remove('submission');
      final screen = PayablesScreen.fromJson(withoutPanel);
      expect(screen.submission.policy, isEmpty);
      expect(screen.submission.extractor, '');
      expect(screen.invoices, hasLength(1));
    });
  });

  group('an outcome', () {
    test('is the row, where it stopped, and the clauses it read', () {
      final screen = PayablesScreen.fromJson(_screen());
      final outcome = screen.invoices.single;
      expect(outcome.invoice.invoiceNumber, 'ACME-INV-9001');
      expect(outcome.invoice.documentId, 'doc_abc123');
      expect(outcome.gate, isNull);
      expect(outcome.policy.single.deciding, isTrue);
    });

    test('a run intake started is submitted by the flow, not by a person', () {
      // The thing the whole intake lane is for, seen from the console: the
      // rows a clerk works were filed by the customer's own document system,
      // not typed into a form here.
      final screen = PayablesScreen.fromJson(_screen());
      expect(screen.invoices.single.invoice.submittedBy, 'flow:ap-intake');
    });
  });
}
