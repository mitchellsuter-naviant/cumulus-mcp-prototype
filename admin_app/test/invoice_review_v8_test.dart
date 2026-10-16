import 'dart:convert';
import 'dart:typed_data';

import 'package:cumulus_admin/models/data_layer.dart';
import 'package:cumulus_admin/models/task.dart';
import 'package:cumulus_admin/tasks/accounts_payable/invoice_drafts.dart';
import 'package:cumulus_admin/tasks/accounts_payable/models.dart';
import 'package:cumulus_admin/tasks/accounts_payable/v8/invoice_review.dart';
import 'package:cumulus_admin/tasks/shared/review_frame.dart';
import 'package:cumulus_admin/theme/cumulus_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// The v8 invoice review, and the one sentence it exists to satisfy: **a clerk
/// is asked about three fields, not thirty.**
///
/// Everything asserted here is a way that could fail quietly. A screen that
/// drops a flagged field because it did not recognise the field's path still
/// looks like a working screen. So does one that shows "nothing needed you"
/// over a run that never looked. So does one that sends a correction under a
/// key the backend cannot match to the raw value — the invoice posts, and the
/// correction set silently fills with pairs nobody can tune on.
///
/// The last group is v9's addition to the same screen: the vendor question,
/// folded into this page instead of gated on its own. It fails quietly in a
/// worse way than a field does — an invoice posted against a company nobody
/// settled is a payment to the wrong party, and no later correction recovers
/// it — so the ways it could go wrong are pinned one at a time.
void main() {
  group('what a path points at', () {
    test('the invoice prefix is optional, because both are in use', () {
      // `Region.field` writes `invoice.line_items[0].amount_usd`; the contract
      // in WORKPLAN-REAL §4 writes `line_items[2].amount_usd`. The screen must
      // place both rather than making one of the two lanes wrong.
      expect(resolveInvoicePath('invoice.invoice_number')?.leaf,
          'invoice_number');
      expect(resolveInvoicePath('invoice_number')?.leaf, 'invoice_number');
      expect(resolveInvoicePath('line_items[2].amount_usd')?.lineIndex, 2);
      expect(resolveInvoicePath('invoice.line_items[2].amount_usd')?.leaf,
          'amount_usd');
    });

    test('an index is zero-based, which is what both sides agree on', () {
      // `line_items[2]` is labelled "Line 3" in the contract's own example.
      expect(resolveInvoicePath('line_items[0].quantity')?.lineIndex, 0);
      expect(resolveInvoicePath('lines[4].unit_price')?.lineIndex, 4);
      expect(resolveInvoicePath('lines[4].unit_price')?.leaf, 'unit_price_usd');
    });

    test('a doubt about a line as a whole still names the line', () {
      final ref = resolveInvoicePath('line_items[1]');
      expect(ref?.lineIndex, 1);
      // No leaf: the screen opens the line rather than one number on it, which
      // is the honest ask when qty × price ≠ amount and nobody knows which of
      // the three was misread.
      expect(ref?.leaf, '');
    });

    test('the header fields the arithmetic check implicates are placed', () {
      // "subtotal + freight + tax ≈ total" and "dates sane" are two of the
      // four arithmetic rules, so these paths are the expected traffic — not
      // an edge case to fall through to a free-text box.
      expect(resolveInvoicePath('invoice.total_usd')?.leaf, 'total_usd');
      expect(resolveInvoicePath('invoice.tax_usd')?.leaf, 'tax_usd');
      expect(resolveInvoicePath('invoice.due_date')?.leaf, 'due_date');
    });

    test('a path this does not know is unplaced, never mistaken', () {
      // Answering "close enough" here would put a clerk's correction on the
      // wrong field. Unplaced is a state the screen handles.
      expect(resolveInvoicePath('vendor.tax_id'), isNull);
      expect(resolveInvoicePath('invoice.remit_to_address'), isNull);
      expect(resolveInvoicePath(''), isNull);
      // A line leaf with no line is a malformed path, not a header field.
      expect(resolveInvoicePath('invoice.quantity'), isNull);
    });
  });

  group('the lede says which of three situations this is', () {
    testWidgets('fields in doubt lead, and everything else is folded', (
      tester,
    ) async {
      await _pump(tester, verdicts: [
        _verdict(
          path: 'invoice.line_items[1].amount_usd',
          label: 'Line 2 — amount',
          value: '1,844.00',
          status: 'inconsistent',
          reason: 'Line 2 reads 4 × \$486.00 but bills \$1,844.00.',
          region: _region('invoice.line_items[1].amount_usd'),
        ),
        _verdict(
          path: 'invoice.invoice_number',
          label: 'Invoice number',
          value: 'INV-2481',
          confidence: 0.41,
          status: 'low_confidence',
          reason: 'The invoice number was faint and may be INV-2431.',
          region: _region('invoice.invoice_number', page: 1),
        ),
        _verdict(
          path: 'invoice.po_number',
          label: 'PO number',
          value: 'PO-6013',
          confidence: 0.98,
          status: 'ok',
          reason: 'Read cleanly and matches an open order.',
        ),
      ]);

      expect(find.text('2 fields need you'), findsOneWidget);
      // The clean one is counted, not shown.
      expect(find.textContaining('The other 1 field(s) it read came back '
          'clean'), findsOneWidget);
      expect(find.text('PO number'), findsNothing);

      // The check's sentence, verbatim. This is the most useful string on the
      // screen and paraphrasing it would throw away the answer.
      expect(
        find.text('Line 2 reads 4 × \$486.00 but bills \$1,844.00.'),
        findsOneWidget,
      );
      expect(
        find.text('The invoice number was faint and may be INV-2431.'),
        findsOneWidget,
      );

      // Worst first: an arithmetic contradiction outranks a faint read.
      final cards = tester.getTopLeft(find.text('Line 2 — amount')).dy;
      final second = tester.getTopLeft(find.text('Invoice number')).dy;
      expect(cards, lessThan(second));

      // The thirty inputs are behind a chevron, not in front of the clerk.
      expect(find.text('Qty'), findsNWidgets(1)); // the flagged line's own
      expect(find.textContaining('3 line(s), none of them flagged'),
          findsOneWidget);
    });

    testWidgets('a clean invoice reads as a fast approve, not an empty form', (
      tester,
    ) async {
      // The common case once the auto-post limit is $0: nothing is wrong and
      // the invoice is still in front of a person.
      await _pump(tester, verdicts: const []);

      expect(find.text('Nothing needed a person'), findsOneWidget);
      expect(
        find.textContaining('the governed posting policy sends every invoice '
            'to a clerk'),
        findsOneWidget,
      );
      expect(find.textContaining('Approving is the whole job'), findsOneWidget);
      // The decision bar offers the approve, because nothing is dirty.
      expect(find.text('Approve as-is'), findsOneWidget);
      // And the page says why nothing is boxed — the second model call is the
      // thing that did not happen, and that is worth saying.
      expect(
        find.textContaining('the second model call is skipped entirely'),
        findsOneWidget,
      );
    });

    testWidgets('a run that raised no verdicts is not a run that found '
        'nothing', (tester) async {
      // Null and empty are different facts. Collapsing them would turn "we did
      // not look" into "we looked and it is fine" — over an invoice about to
      // be posted.
      await _pump(tester, verdicts: null);

      expect(find.text('This run reported no field verdicts'), findsOneWidget);
      expect(
        find.textContaining('that is different from every field being fine'),
        findsOneWidget,
      );
      expect(find.text('Nothing needed a person'), findsNothing);
      // The fallback is v6's behaviour: the whole extraction, open.
      expect(find.text('Invoice number'), findsOneWidget);
      expect(find.text('Qty'), findsNWidgets(3));
    });
  });

  group('a verdict with a box, and one without', () {
    testWidgets('a located field says where it is and opens selected', (
      tester,
    ) async {
      await _pump(tester, verdicts: [
        _verdict(
          path: 'invoice.total_usd',
          label: 'Invoice total',
          value: '4,120.00',
          status: 'inconsistent',
          reason: 'The lines sum to \$4,220.00.',
          region: _region('invoice.total_usd', page: 2),
        ),
      ]);

      expect(find.text('boxed on page 2'), findsOneWidget);
      // The screen opens on the first thing that needs a person, and on its
      // page — a clerk should not have to find either.
      expect(_selected(tester, 'Invoice total'), findsOneWidget);
      expect(find.textContaining('1 of 1 box(es) on this page'), findsOneWidget);
    });

    testWidgets('a field with no box is a focused input, not a dropped one', (
      tester,
    ) async {
      await _pump(tester, verdicts: [
        _verdict(
          path: 'invoice.total_usd',
          label: 'Invoice total',
          value: '',
          status: 'missing',
          reason: 'No total was printed anywhere on the page.',
        ),
      ]);

      expect(find.text('One field needs you'), findsOneWidget);
      expect(find.text('no box on the page'), findsOneWidget);
      expect(find.text('read “nothing”'), findsOneWidget);
      // And the page says the absence is the locator's, not the field's.
      expect(
        find.textContaining('a missing box is the locator saying it could not '
            'find the value, not the value being fine'),
        findsOneWidget,
      );
    });

    testWidgets('selecting a field moves the selection to it', (tester) async {
      await _pump(tester, verdicts: [
        _verdict(
          path: 'invoice.invoice_number',
          label: 'Invoice number',
          status: 'missing',
          value: 'INV-2481',
          reason: 'faint',
          region: _region('invoice.invoice_number'),
        ),
        _verdict(
          path: 'invoice.line_items[2].quantity',
          label: 'Line 3 — quantity',
          status: 'low_confidence',
          value: '2',
          reason: 'The quantity column was cut off.',
          region: _region('invoice.line_items[2].quantity'),
        ),
      ]);

      expect(_selected(tester, 'Invoice number'), findsOneWidget);
      expect(_selected(tester, 'Line 3 — quantity'), findsNothing);

      await tester.tap(find.text('Line 3 — quantity'));
      await tester.pumpAndSettle();

      expect(_selected(tester, 'Line 3 — quantity'), findsOneWidget);
      expect(_selected(tester, 'Invoice number'), findsNothing);
    });

    testWidgets('selecting a box on the page focuses its field', (
      tester,
    ) async {
      // The other half of the two-way link. It needs a page that actually
      // rendered, and on the VM there is no PDF renderer — so the fixture is
      // an image artifact, which is the same overlay over pixels this build
      // can draw.
      final total = _region('invoice.total_usd');
      final line = _region('invoice.line_items[1].amount_usd')
          .at(y: 0.6, text: '1,844.00');
      await _pumpOverImage(tester, [
        _verdict(
          path: 'invoice.total_usd',
          label: 'Invoice total',
          value: '4,120.00',
          status: 'missing',
          reason: 'No total was printed.',
          region: total,
        ),
        _verdict(
          path: 'invoice.line_items[1].amount_usd',
          label: 'Line 2 — amount',
          value: '1,844.00',
          status: 'low_confidence',
          reason: 'The amount column was smudged.',
          region: line,
        ),
      ]);

      expect(_selected(tester, 'Invoice total'), findsOneWidget);

      await tester.tap(find.byTooltip(_tooltip(line)));
      await tester.pumpAndSettle();

      expect(_selected(tester, 'Line 2 — amount'), findsOneWidget);
      expect(_selected(tester, 'Invoice total'), findsNothing);
    });
  });

  group('the folded context is a second view, never a second draft', () {
    testWidgets('a lifted line is shown below, not offered twice', (
      tester,
    ) async {
      await _pump(tester, verdicts: [
        _verdict(
          path: 'invoice.line_items[1].amount_usd',
          label: 'Line 2 — amount',
          value: '1,844.00',
          status: 'inconsistent',
          reason: 'Line 2 reads 4 × \$486.00 but bills \$1,844.00.',
          region: _region('invoice.line_items[1].amount_usd'),
        ),
      ]);

      final fold = find.text('THE REST OF THE EXTRACTION');
      await tester.ensureVisible(fold);
      await tester.pumpAndSettle();
      await tester.tap(fold);
      await tester.pumpAndSettle();

      // Three lines, and the flagged one keeps its single input up in the
      // card. Two `TextField`s over one controller is how a clerk ends up
      // typing two answers to one question and the screen keeping the second.
      expect(find.widgetWithText(TextField, 'Qty'), findsNWidgets(3));
      expect(find.text('asked above'), findsOneWidget);

      // And the proof that it is one draft: correcting the quantity in the
      // card moves the number in the folded row too.
      await tester.enterText(find.widgetWithText(TextField, 'Qty').first, '5');
      await tester.pumpAndSettle();
      expect(find.text('\$2430.00'), findsNWidgets(2));
    });
  });

  group('the correction is keyed by the path the run named', () {
    testWidgets('a header field travels under its own path', (tester) async {
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: [
          _verdict(
            path: 'invoice.invoice_number',
            label: 'Invoice number',
            value: 'INV-2481',
            status: 'low_confidence',
            reason: 'faint',
            region: _region('invoice.invoice_number'),
          ),
        ],
        onDecide: decided.add,
      );

      await tester.enterText(_valueField, 'INV-2431');
      await tester.pumpAndSettle();
      await _apply(tester);

      expect(decided, hasLength(1));
      final payload = decided.single.payload;
      expect(decided.single.verdict, 'corrected');
      // Keyed by the path the verdict arrived under — that keying is what lets
      // the correction set pair this against the raw value.
      expect(payload['fields'], {'invoice.invoice_number': 'INV-2431'});
      // And the patch the applier already reads travels beside it, from the
      // same controller, so the two cannot disagree.
      expect(payload['invoice_number'], 'INV-2431');
    });

    testWidgets('a line field reports the value its own path names', (
      tester,
    ) async {
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: [
          _verdict(
            path: 'invoice.line_items[1].unit_price_usd',
            label: 'Line 2 — unit price',
            value: '486.00',
            status: 'low_confidence',
            reason: 'The price column was smudged.',
            region: _region('invoice.line_items[1].unit_price_usd'),
          ),
        ],
        onDecide: decided.add,
      );

      await tester.enterText(find.widgetWithText(TextField, 'Unit price'),
          '461.00');
      await tester.pumpAndSettle();
      await _apply(tester);

      final payload = decided.single.payload;
      expect(payload['fields'], {
        'invoice.line_items[1].unit_price_usd': '461.00',
      });
      final lines = payload['lines'] as List<dynamic>;
      expect((lines[1] as Map)['unit_price_usd'], 461.0);
      // The other two lines travel untouched — the patch is the whole
      // extraction, not only what changed.
      expect((lines[0] as Map)['unit_price_usd'], 120.0);
    });

    testWidgets('a header field the folded form has no input for travels in '
        'both channels', (tester) async {
      // `invoice.total_usd` is the contract's own first example and the thing
      // "subtotal + freight + tax ≈ total" implicates. v6's form has no total
      // input, so the correction has to reach the applier by name as well as
      // by path — otherwise the flow recomputes the total from the lines and
      // the clerk's number is silently gone.
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: [
          _verdict(
            path: 'invoice.total_usd',
            label: 'Invoice total',
            value: '4,120.00',
            status: 'inconsistent',
            reason: 'Subtotal plus freight plus tax is \$4,220.00.',
            region: _region('invoice.total_usd'),
          ),
        ],
        onDecide: decided.add,
      );

      // Seeded from what the run read, so opening the screen is not an edit.
      expect(find.text('Approve as-is'), findsOneWidget);

      await tester.enterText(_valueField, '4220.00');
      await tester.pumpAndSettle();
      await _apply(tester);

      final payload = decided.single.payload;
      expect(payload['fields'], {'invoice.total_usd': '4220.00'});
      expect(payload['total_usd'], 4220.0);
    });

    testWidgets('a path the screen cannot place is still editable and still '
        'reported', (tester) async {
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: [
          _verdict(
            path: 'invoice.remit_to_address',
            label: 'Remit-to address',
            value: '1400 Ridgeline Way',
            status: 'low_confidence',
            reason: 'The remit-to block was partly cut off.',
            region: _region('invoice.remit_to_address'),
          ),
        ],
        onDecide: decided.add,
      );

      // Nothing on this screen owns `remit_to_address`, and the field must not
      // vanish for it: the check flagged it, so a clerk is asked about it.
      expect(find.text('Remit-to address'), findsOneWidget);
      await tester.enterText(_valueField, '1400 Ridgeline Way, Suite 200');
      await tester.pumpAndSettle();
      await _apply(tester);

      expect(decided.single.payload['fields'], {
        'invoice.remit_to_address': '1400 Ridgeline Way, Suite 200',
      });
      // And it travels *only* by path — inventing a patch key for a field the
      // applier never named would be a write nobody asked for.
      expect(decided.single.payload.containsKey('remit_to_address'), isFalse);
    });

    testWidgets('a field the clerk looked at and left alone is not a '
        'correction', (tester) async {
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: [
          _verdict(
            path: 'invoice.invoice_number',
            label: 'Invoice number',
            value: 'INV-2481',
            status: 'low_confidence',
            reason: 'faint',
          ),
          _verdict(
            path: 'invoice.line_items[0].quantity',
            label: 'Line 1 — quantity',
            value: '10',
            status: 'low_confidence',
            reason: 'The quantity column was faint here too.',
          ),
        ],
        onDecide: decided.add,
      );

      await tester.enterText(_valueField, 'INV-2431');
      await tester.pumpAndSettle();
      await _apply(tester);

      // Only the one that moved. A pair whose halves are equal is not a
      // correction, and the correction set is the artifact this system is
      // meant to be able to tune on.
      expect(decided.single.payload['fields'], {
        'invoice.invoice_number': 'INV-2431',
      });
    });

    testWidgets('approving as-is sends no payload at all', (tester) async {
      final decided = <ReviewOutcome>[];
      await _pump(tester, verdicts: const [], onDecide: decided.add);

      await tester.tap(find.text('Approve as-is'));
      await tester.pumpAndSettle();

      expect(decided.single.verdict, 'approved');
      expect(decided.single.payload, isEmpty);
    });
  });

  group('the vendor question, folded into the same page', () {
    test('the block parses off the wire as the server sends it', () {
      // The one cross-language check on this shape. The server tests assert on
      // Python objects and every widget test below builds a Dart fixture, so
      // both sides can be green while the JSON between them disagrees.
      final vendor = TaskProposal.fromJson(
        jsonDecode(
          '{"action": "post-invoice", "target": "ap-records", "summary": "", '
          '"vendor": $_serverVendor}',
        ) as Map<String, dynamic>,
      ).vendor!;

      // Null and not empty: the resolver settled nothing, which is the only
      // reason the block is on the task at all.
      expect(vendor.resolved, isNull);
      expect(vendor.printed, 'Acme Industrial Supply');
      expect(vendor.candidates, hasLength(2));
      expect(vendor.candidates.first.id, 'vendor:acme-industrial');
      expect(vendor.candidates.first.name, 'Acme Industrial Inc.');
      expect(vendor.candidates.first.score, closeTo(0.86, 1e-9));
      // Ranked as they arrived. Re-sorting here would put a different name
      // under a clerk's first glance than the resolver had ranked first.
      expect(vendor.candidates.last.score, closeTo(0.71, 1e-9));
    });

    test('no vendor key at all is a settled vendor, not an empty picker', () {
      // Every v8 task, and every v9 task whose resolver settled it.
      final proposal = TaskProposal.fromJson(
        jsonDecode(
          '{"action": "post-invoice", "target": "ap-records", "summary": ""}',
        ) as Map<String, dynamic>,
      );
      expect(proposal.vendor, isNull);
    });

    testWidgets('an absent block leaves the screen exactly as it was', (
      tester,
    ) async {
      await _pump(tester, verdicts: const []);

      expect(find.text('Which vendor issued this'), findsNothing);
      expect(find.textContaining('is a new vendor'), findsNothing);
      // And the clean-invoice lede is untouched: approving is still the whole
      // job when there is no second question on the page.
      expect(find.text('Nothing needed a person'), findsOneWidget);
      expect(find.textContaining('Approving is the whole job'), findsOneWidget);
    });

    testWidgets('the candidates arrive with their scores and the printed name',
        (tester) async {
      await _pump(tester, verdicts: const [], vendor: _vendor());

      // The name on the page, which is what a clerk compares against.
      expect(find.textContaining('printed: “Acme Industrial Supply”'),
          findsOneWidget);
      expect(find.text('Acme Industrial Inc.'), findsOneWidget);
      expect(find.text('Acme Supply Co.'), findsOneWidget);
      // The scores, because 86 against 71 is a different call from 86 against
      // 31 and a screen that hid them would hide how close it is.
      expect(find.text('86% · vendor:acme-industrial'), findsOneWidget);
      expect(find.text('71% · vendor:acme-supply-co'), findsOneWidget);
      // Nothing is pre-selected: the run's reason for asking is that it could
      // not choose, and a ticked box would collect a signature on a choice
      // nobody made. What *approving* an unanswered question does is said out
      // loud instead, because the applier reads an absent id on an approval as
      // "take the top candidate" — and a clerk who answers nothing and presses
      // Approve should know they have picked one.
      expect(find.text('Approve as-is'), findsOneWidget);
      expect(
        find.text('Approving without naming one accepts the top candidate: '
            'Acme Industrial Inc.'),
        findsOneWidget,
      );
    });

    testWidgets('the top-candidate warning goes once the question is answered',
        (tester) async {
      await _pump(tester, verdicts: const [], vendor: _vendor());

      await tester.tap(find.text('Acme Supply Co.'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('Approving without naming one'),
        findsNothing,
      );
    });

    testWidgets('with no candidates there is no top one to warn about', (
      tester,
    ) async {
      await _pump(tester, verdicts: const [], vendor: _vendor('[]'));

      expect(
        find.textContaining('Approving without naming one'),
        findsNothing,
      );
    });

    testWidgets('a clean invoice with an unsettled vendor is not "nothing '
        'needed a person"', (tester) async {
      await _pump(tester, verdicts: const [], vendor: _vendor());

      expect(find.text('No field is in doubt — the vendor is'), findsOneWidget);
      expect(find.text('Nothing needed a person'), findsNothing);
      // The sentence that would be a lie: naming the vendor is part of the job.
      expect(find.textContaining('Approving is the whole job'), findsNothing);
    });

    testWidgets('naming a candidate rides an approval as vendor_id', (
      tester,
    ) async {
      // The whole point of folding the question in: "it is Acme Industrial"
      // and "post this" are one press, not a correction round.
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: const [],
        vendor: _vendor(),
        onDecide: decided.add,
      );

      await tester.tap(find.text('Acme Industrial Inc.'));
      await tester.pumpAndSettle();
      // Answering the question is not correcting a field, so the bar still
      // offers the approve.
      final approve = find.text('Approve as-is');
      expect(approve, findsOneWidget);
      await tester.tap(approve);
      await tester.pumpAndSettle();

      expect(decided.single.verdict, 'approved');
      expect(decided.single.payload, {'vendor_id': 'vendor:acme-industrial'});
    });

    testWidgets('"none of these" is a correction, and names no id', (
      tester,
    ) async {
      // The asymmetry this defends: on an approval the applier reads an absent
      // id as "take the top candidate", so offering Approve here would post
      // against the company the clerk had just declined. On a correction it
      // reads the same absence as "mint the printed name", which is what was
      // meant.
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: const [],
        vendor: _vendor(),
        onDecide: decided.add,
      );

      await tester.tap(find.textContaining('is a new vendor'));
      await tester.pumpAndSettle();
      expect(find.text('Approve as-is'), findsNothing);
      await _apply(tester);

      expect(decided.single.verdict, 'corrected');
      expect(decided.single.payload.containsKey('vendor_id'), isFalse);
      // And the field patch still travels: declining the candidates does not
      // discard what the clerk typed.
      expect(decided.single.payload['invoice_number'], 'INV-2481');
    });

    testWidgets('a named vendor travels beside a corrected field', (
      tester,
    ) async {
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: [
          _verdict(
            path: 'invoice.total_usd',
            label: 'Invoice total',
            value: '4,120.00',
            status: 'inconsistent',
            reason: 'The lines sum to \$4,220.00.',
          ),
        ],
        vendor: _vendor(),
        onDecide: decided.add,
      );

      // Both questions on one page, answered in one press: the lede says so.
      expect(
        find.textContaining('The vendor is unsettled too'),
        findsOneWidget,
      );
      await tester.tap(find.text('Acme Supply Co.'));
      await tester.pumpAndSettle();
      await tester.enterText(_valueField, '4220.00');
      await tester.pumpAndSettle();
      await _apply(tester);

      final payload = decided.single.payload;
      expect(payload['fields'], {'invoice.total_usd': '4220.00'});
      expect(payload['vendor_id'], 'vendor:acme-supply-co');
    });

    testWidgets('rejecting names no vendor, whatever was picked', (
      tester,
    ) async {
      // A rejection denies the invoice. Asserting an identity on the way out
      // would put a claim in the graph about a document nobody is paying.
      final decided = <ReviewOutcome>[];
      await _pump(
        tester,
        verdicts: const [],
        vendor: _vendor(),
        onDecide: decided.add,
      );

      await tester.tap(find.text('Acme Industrial Inc.'));
      await tester.pumpAndSettle();
      final reject = find.text('Reject');
      await tester.ensureVisible(reject);
      await tester.pumpAndSettle();
      await tester.tap(reject);
      await tester.pumpAndSettle();

      expect(decided.single.verdict, 'rejected');
      expect(decided.single.payload, isEmpty);
    });

    testWidgets('no candidates at all offers the only honest answer', (
      tester,
    ) async {
      // The resolver looked and nothing in the master came close. An empty
      // picker would ask a question with no answers in it.
      await _pump(tester, verdicts: const [], vendor: _vendor('[]'));

      expect(
        find.textContaining('Nothing in the master came close to that name'),
        findsOneWidget,
      );
      expect(
        find.textContaining('“Acme Industrial Supply” is a new vendor'),
        findsOneWidget,
      );
    });
  });

  group('the drafts both versions share', () {
    test('the patch is the whole extraction, by ordinal', () {
      final invoice = _invoice();
      final lines = invoiceLineDrafts(invoice);
      expect(
        invoicePatch(
          invoiceNumber: ' INV-2481 ',
          poNumber: 'PO-6013',
          lines: lines,
        ),
        {
          'invoice_number': 'INV-2481',
          'po_number': 'PO-6013',
          'lines': [
            {'ordinal': 1, 'quantity': 10.0, 'unit_price_usd': 120.0},
            {'ordinal': 2, 'quantity': 4.0, 'unit_price_usd': 486.0},
            {'ordinal': 3, 'quantity': 1.0, 'unit_price_usd': 976.0},
          ],
        },
      );
    });

    test('a re-rendered value is not an edit', () {
      // The tolerances are the precisions the fields are displayed at. Too
      // tight and Approve turns into Apply for a clerk who touched nothing.
      final invoice = _invoice();
      final lines = invoiceLineDrafts(invoice);
      expect(
        invoiceEdited(
          invoice: invoice,
          invoiceNumber: 'INV-2481',
          poNumber: 'PO-6013',
          lines: lines,
        ),
        isFalse,
      );
      lines[1].unitPrice.text = '461.00';
      expect(
        invoiceEdited(
          invoice: invoice,
          invoiceNumber: 'INV-2481',
          poNumber: 'PO-6013',
          lines: lines,
        ),
        isTrue,
      );
    });
  });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

Future<void> _pump(
  WidgetTester tester, {
  required List<FieldVerdict>? verdicts,
  VendorQuestion? vendor,
  void Function(ReviewOutcome)? onDecide,
}) async {
  // The test font draws every glyph square, so the panel's prose needs more
  // room here than in the browser.
  tester.view.physicalSize = const Size(2200, 3000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: InvoiceReviewPanel(
          task: _task(),
          invoice: _invoice(),
          verdicts: verdicts,
          vendor: vendor,
          artifact: Future.value((_document(), Uint8List(0))),
          onDecide: onDecide ?? (_) {},
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

/// Verbatim from the server: the `vendor` block a v9 review carries when the
/// resolver could not settle the party. The scores are the ones
/// `server/tests/test_payables_v9.py` pins — above the fuzzy floor, below the
/// bar that would let the run assert on its own.
const String _serverVendor = '''
{"resolved": null, "printed": "Acme Industrial Supply",
 "candidates": [
   {"id": "vendor:acme-industrial", "name": "Acme Industrial Inc.",
    "score": 0.86},
   {"id": "vendor:acme-supply-co", "name": "Acme Supply Co.", "score": 0.71}]}
''';

/// The whole proposal, so the vendor block is read the way a task delivers it
/// rather than hand-built beside the parser that has to read it.
VendorQuestion _vendor([String candidates = '''
  [{"id": "vendor:acme-industrial", "name": "Acme Industrial Inc.",
    "score": 0.86},
   {"id": "vendor:acme-supply-co", "name": "Acme Supply Co.", "score": 0.71}]
''']) => TaskProposal.fromJson(
  jsonDecode(
    '{"action": "post-invoice", "target": "ap-records", "summary": "", '
    '"vendor": {"resolved": null, "printed": "Acme Industrial Supply", '
    '"candidates": $candidates}}',
  ) as Map<String, dynamic>,
).vendor!;

/// The same panel over an artifact this build can actually draw.
///
/// `runAsync` is load-bearing: the overlay is laid out over the *decoded*
/// image, and without giving the codec real time the frame is zero-sized, the
/// boxes collapse, and a tap that should land on one lands on nothing. A test
/// that skipped it would pass while asserting nothing.
Future<void> _pumpOverImage(
  WidgetTester tester,
  List<FieldVerdict> verdicts,
) async {
  tester.view.physicalSize = const Size(2200, 3000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.runAsync(() async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: InvoiceReviewPanel(
            task: _task(),
            invoice: _invoice(),
            verdicts: verdicts,
            artifact: Future.value((_image(), _onePixelPng)),
            onDecide: (_) {},
          ),
        ),
      ),
    );
    // Two waits, for two different asynchronies: the artifact future has to
    // deliver before the image widget exists at all, and only then does the
    // codec have anything to decode.
    await tester.idle();
    await tester.pump();
    await Future<void>.delayed(const Duration(milliseconds: 80));
    await tester.pump();
    await Future<void>.delayed(const Duration(milliseconds: 80));
  });
  await tester.pumpAndSettle();
}

/// What [_RegionBox] puts on a box, and so the way to find one.
String _tooltip(DocumentRegion region) => '${region.field}\n${region.text}\n'
    '${(region.confidence * 100).round()}% confident';

/// A 1×1 PNG. Small enough to inline, real enough to decode — which is the
/// only property this fixture needs.
final _onePixelPng = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ'
  'DwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
);

/// The input on a verdict card that is not a line — a header field, or a path
/// the screen could not place. Labelled by what typing there means rather than
/// by the field's name, which the card's title already carries.
final _valueField = find.widgetWithText(TextField, 'As it should read');

/// Press the correct-and-recheck control, wherever the scroll has put it.
Future<void> _apply(WidgetTester tester) async {
  final apply = find.text('Apply and re-check');
  await tester.ensureVisible(apply);
  await tester.pumpAndSettle();
  await tester.tap(apply);
  await tester.pumpAndSettle();
}

/// The selected card, found by the tint the console selects rows with.
Finder _selected(WidgetTester tester, String label) => find.ancestor(
  of: find.text(label),
  matching: find.byWidgetPredicate(
    (w) => w is Material && w.color == CumulusColors.accentSoft,
  ),
);

FieldVerdict _verdict({
  required String path,
  required String label,
  required String status,
  required String reason,
  String value = '',
  double confidence = 0.5,
  DocumentRegion? region,
}) => FieldVerdict(
  path: path,
  label: label,
  value: value,
  confidence: confidence,
  status: status,
  reason: reason,
  region: region,
);

DocumentRegion _region(String field, {int page = 1}) => DocumentRegion(
  field: field,
  page: page,
  x: 0.62,
  y: 0.18,
  width: 0.22,
  height: 0.06,
  text: '',
  confidence: 0.9,
);

extension on DocumentRegion {
  /// The same box, somewhere else down the page — so two of them can be told
  /// apart by a pointer.
  DocumentRegion at({required double y, String text = ''}) => DocumentRegion(
    field: field,
    page: page,
    x: x,
    y: y,
    width: width,
    height: height,
    text: text,
    confidence: confidence,
  );
}

QueueTask _task() => const QueueTask(
  id: 'task_ap_1',
  runId: 'run_ap_1',
  kind: 'decision',
  title: 'Review invoice INV-2481 · Ridgeline Supply',
  summary: '',
  queue: 'ap-clerk',
  priority: 'normal',
  status: 'open',
  flowId: 'accounts-payable',
  flowVersion: 8,
  caseRef: 'inv_1',
  refs: [],
  evidence: [],
  proposal: null,
  resumes: '',
  createdAtIso: '2026-09-02T09:00:00Z',
  decision: null,
);

/// The same artifact, filed as an image — the only kind of page the VM build
/// can rasterise, and so the only one whose boxes a test can point at.
StoredDocument _image() => const StoredDocument(
  id: 'doc_28fee8830bfcf03c',
  sha256: '28fee8830bfcf03c',
  filename: 'ridgeline-inv-2481.png',
  mediaType: 'image/png',
  byteSize: 68,
  kind: 'invoice',
  pages: 1,
  regionCount: 0,
  collectionId: 'audit-documents',
  retentionUntilIso: '',
  uploadedBy: 'dms-sync',
  uploadedAtIso: '2026-09-02T08:00:00Z',
  runIds: ['run_ap_1'],
  hasBytes: true,
  regions: [],
);

StoredDocument _document() => const StoredDocument(
  id: 'doc_28fee8830bfcf03c',
  sha256: '28fee8830bfcf03c',
  filename: 'ridgeline-inv-2481.pdf',
  mediaType: 'application/pdf',
  byteSize: 18422,
  kind: 'invoice',
  pages: 2,
  regionCount: 0,
  collectionId: 'audit-documents',
  retentionUntilIso: '2033-09-02T00:00:00Z',
  uploadedBy: 'dms-sync',
  uploadedAtIso: '2026-09-02T08:00:00Z',
  runIds: ['run_ap_1'],
  hasBytes: true,
  regions: [],
);

PayableInvoice _invoice() => const PayableInvoice(
  id: 'inv_1',
  runId: 'run_ap_1',
  documentId: 'doc_28fee8830bfcf03c',
  vendorId: 'vendor:ridgeline-supply',
  vendorResolution: 'linked',
  vendorMethod: 'exact',
  subtotalUsd: 4120,
  freightUsd: 0,
  taxUsd: 0,
  currency: 'USD',
  lines: [
    InvoiceLine(
      sku: 'RS-1180',
      description: 'Conduit clamp, 1in',
      quantity: 10,
      unitPriceUsd: 120,
      amountUsd: 1200,
      kind: 'goods',
    ),
    InvoiceLine(
      sku: 'RS-4420',
      description: 'Panelboard, 42-circuit',
      quantity: 4,
      unitPriceUsd: 486,
      amountUsd: 1844,
      kind: 'goods',
    ),
    InvoiceLine(
      sku: 'RS-9001',
      description: 'Freight',
      quantity: 1,
      unitPriceUsd: 976,
      amountUsd: 976,
      kind: 'freight',
    ),
  ],
  matchVerdict: 'matched',
  matchSummary: '3 of 3 lines matched',
  matchRef: 'match:1',
  matchPairings: [],
  anomalyFindings: [],
  anomalyBasis: '',
  graphRef: 'graph:Invoice/inv_1',
  submittedBy: 'ap-intake',
  submittedAtIso: '2026-09-02T08:00:00Z',
  vendorSlug: 'ridgeline-supply',
  vendorName: 'Ridgeline Supply',
  vendorConfidence: 0.98,
  vendorTaxId: '',
  invoiceNumber: 'INV-2481',
  invoiceDate: '2026-08-28',
  dueDate: '2026-09-27',
  poNumber: 'PO-6013',
  totalUsd: 4120,
  lineSummary: '3 lines',
  threeWayMatch: 'matched',
  fraudScreen: 'clear',
  state: 'pending',
  verdictReason: 'above the governed auto-post limit',
  policyRef: 'knowledge:ap-posting-policy@7',
  postLimitUsd: 0,
  taskId: 'task_ap_1',
  decidedBy: '',
  decisionNote: '',
  journal: [],
);
