/// The field-verdict wire contract, pinned against the server's own example.
///
/// The backend step that computes verdicts and the screen that renders them
/// were built in parallel, against a contract written down rather than
/// discovered — which is the right way to split the work and also the way a
/// seam silently rots. Nothing else in either suite runs the two against each
/// other: the server tests assert on Python objects and the screen tests build
/// Dart fixtures, so both can be green while the JSON between them disagrees.
///
/// The payload below is copied verbatim from the accounts-payable v8 report of
/// what `GET /api/v1/tasks/{id}` serves. If a change to either side breaks
/// this test, that is the seam breaking, and the fix is a conversation between
/// both halves rather than an edit to whichever one is closer to hand.
library;

import 'dart:convert';

import 'package:cumulus_admin/models/task.dart';
import 'package:flutter_test/flutter_test.dart';

/// Verbatim from the server: a review task's proposal carrying two verdicts —
/// one low-confidence with a box, one arithmetically inconsistent with a box.
const String _serverProposal = '''
{"action": "post-invoice", "target": "ap-records",
 "summary": "Post ACME-INV-7000 for \$1,428.25 to the AP ledger",
 "fields": [
   {"path": "invoice.total_usd", "label": "Invoice total", "value": "1428.25",
    "confidence": 0.4, "status": "low_confidence",
    "reason": "the extractor read this at 0.40 confidence, below the governed floor of 0.70",
    "region": {"field": "invoice.total_usd", "page": 1, "x": 0.09, "y": 0.61,
               "width": 0.24, "height": 0.035, "text": "", "confidence": 0.5}},
   {"path": "line_items[0].amount_usd", "label": "Line 1 — amount",
    "value": "990.00", "confidence": 0.93, "status": "inconsistent",
    "reason": "2 × \$486.00 is \$972.00, but this line reads \$990.00",
    "region": {"field": "line_items[0].amount_usd", "page": 1, "x": 0.51,
               "y": 0.82, "width": 0.24, "height": 0.035, "text": "",
               "confidence": 0.5}}]}
''';

TaskProposal _parse(String json) =>
    TaskProposal.fromJson(jsonDecode(json) as Map<String, dynamic>);

void main() {
  group('the server payload parses into what the screen reads', () {
    test('every verdict field survives the wire', () {
      final proposal = _parse(_serverProposal);

      expect(proposal.action, 'post-invoice');
      expect(proposal.fields, hasLength(2));

      final total = proposal.fields![0];
      expect(total.path, 'invoice.total_usd');
      expect(total.label, 'Invoice total');
      expect(total.value, '1428.25');
      expect(total.confidence, closeTo(0.4, 1e-9));
      expect(total.status, 'low_confidence');
      expect(total.reason, contains('below the governed floor'));

      // The reason is the check's own sentence and reaches the clerk whole.
      // Anything that truncated or reworded it here would be the screen
      // paraphrasing the evidence an invoice is approved on.
      expect(
        total.reason,
        'the extractor read this at 0.40 confidence, '
        'below the governed floor of 0.70',
      );
    });

    test('a box arrives with usable geometry', () {
      final line = _parse(_serverProposal).fields![1];

      expect(line.region, isNotNull);
      final box = line.region!;
      // Normalized, so the overlay can draw at any render size — the whole
      // reason the server sends fractions rather than pixels.
      expect(box.x, inInclusiveRange(0.0, 1.0));
      expect(box.y, inInclusiveRange(0.0, 1.0));
      expect(box.width, inInclusiveRange(0.0, 1.0));
      expect(box.height, inInclusiveRange(0.0, 1.0));
      expect(box.page, 1);
      // The box names the same fact path as the verdict that carries it. That
      // identity is what lets selecting a field light its box and back again;
      // if the two vocabularies ever diverge, the pairing silently stops.
      expect(box.field, line.path);
    });

    test('line paths are zero-based and the label is one-based', () {
      final line = _parse(_serverProposal).fields![1];

      // `line_items[0]` is the first line and reads as "Line 1". The screen
      // trusts the server's label rather than deriving it, precisely so this
      // off-by-one lives in exactly one place — but if the server ever ships
      // the other convention, a clerk is sent to the wrong row.
      expect(line.path, 'line_items[0].amount_usd');
      expect(line.label, startsWith('Line 1'));
    });
  });

  group('absent and empty stay different facts', () {
    test('no fields key at all is null — the flow never looked', () {
      // v6 and v7 raise tasks with no verdict step behind them. The screen
      // answers this with the whole extraction form, because it has no basis
      // for telling a clerk which parts are fine.
      final proposal = _parse(
        '{"action": "post-invoice", "target": "ap-records", "summary": ""}',
      );
      expect(proposal.fields, isNull);
    });

    test('an empty list is a clean invoice — the flow looked and found none', () {
      // v8 always emits the key. Empty means every field cleared its checks,
      // which is a fast approve, not an unexamined one. Collapsing these two
      // into each other is the bug this pair of tests exists to prevent.
      final proposal = _parse(
        '{"action": "post-invoice", "target": "ap-records", '
        '"summary": "", "fields": []}',
      );
      expect(proposal.fields, isNotNull);
      expect(proposal.fields, isEmpty);
    });
  });

  group('the screen survives a payload the server has not promised', () {
    test('a verdict with no box is still a correctable field', () {
      // The locate call can find nothing, or be skipped. Neither is a reason
      // to drop a field a check flagged.
      final proposal = _parse(
        '{"action": "a", "target": "t", "summary": "", "fields": ['
        '{"path": "invoice.due_date", "label": "Due date", "value": "",'
        ' "confidence": 0.0, "status": "missing", "reason": "not on the page"}]}',
      );
      final verdict = proposal.fields!.single;
      expect(verdict.region, isNull);
      expect(verdict.status, 'missing');
      // Zero confidence is the extractor saying nothing, not saying "wrong".
      expect(verdict.confidence, 0.0);
    });

    test('a sparse verdict defaults rather than throwing', () {
      // Defensive on purpose: a task already raised, whose payload predates a
      // field, must still open a screen. A parse that threw here would take
      // out the queue rather than one row.
      final proposal = _parse(
        '{"action": "a", "target": "t", "summary": "", '
        '"fields": [{"path": "invoice.total_usd"}]}',
      );
      final verdict = proposal.fields!.single;
      expect(verdict.label, '');
      expect(verdict.value, '');
      expect(verdict.status, '');
      expect(verdict.region, isNull);
      // And an unstated status reads as unsettled, so the field is shown.
      expect(verdict.disputed, isTrue);
    });
  });
}
