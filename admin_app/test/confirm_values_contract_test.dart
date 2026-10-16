/// The confirm-values wire contract, pinned against the server's own payload —
/// and what the screen draws out of it.
///
/// Same argument as `field_verdict_contract_test.dart`, one flow over: the step
/// that builds the proposal and the screen that renders it were written against
/// a contract stated in prose, which is the right way to split the work and
/// also the way a seam silently rots. Nothing else in either suite runs the two
/// against each other — the server tests assert on Python objects and the
/// screen tests build Dart fixtures, so both can be green while the JSON
/// between them disagrees.
///
/// The payload below is the one `_build_confirm_task` emits (see
/// `server/app/tasks/expense_reimbursement/domain.py`, and
/// `server/tests/test_reimbursements.py` for the run that produces it): one row
/// per document, carrying the stored artifact, the submitter's own memo about
/// it, and the extraction summary for it. If a change to either side breaks
/// this test, that is the seam breaking, and the fix is a conversation between
/// both halves rather than an edit to whichever one is closer to hand.
///
/// The second half of the file asserts the *geometry*: which of three ledes the
/// screen leads with, that each memo is shown beside its own reading rather
/// than pooled at the top, that the receipt carrying a finding leads, and that
/// a correction leaves keyed by the ordinal the applier patches on. A parser
/// that survived the wire into a screen nobody could read would pass the first
/// half alone.
library;

import 'dart:convert';

import 'package:cumulus_admin/models/task.dart';
import 'package:cumulus_admin/tasks/expense_reimbursement/models.dart';
import 'package:cumulus_admin/tasks/expense_reimbursement/v10/confirm_values.dart';
import 'package:cumulus_admin/tasks/shared/review_frame.dart';
import 'package:cumulus_admin/widgets/ui_kit.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Verbatim from the server: the proposal on a two-receipt confirmation — a
/// hotel folio the submitter split, and a lunch the reader was unsure of.
const String _serverProposal = '''
{"action": "record-expense", "target": "expense-records",
 "summary": "Record reimb_18bcbface3 as approved",
 "fields": [
   {"document_id": "doc_9c1f2ab77d10", "filename": "hotel.pdf",
    "memo": "Conference block rate — only claiming the room, not the parking.",
    "extraction": {"vendor": "harbor-hotel", "date": "2026-08-28",
                   "total_usd": 612.0, "category": "lodging",
                   "confidence": 0.91}},
   {"document_id": "doc_44b0e7cc9188", "filename": "lunch.jpg",
    "memo": "Lunch with the Acme finance team.",
    "extraction": {"vendor": "corner-bistro", "date": "2026-08-28",
                   "total_usd": 38.5, "category": "meals",
                   "confidence": 0.42}}]}
''';

/// The request row the screen fetches beside the Task. The lines are here and
/// not on the proposal on purpose: a second copy on the Task would be the same
/// facts able to disagree with themselves.
const String _serverRequest = '''
{"id": "reimb_18bcbface3", "run_id": "run_er_18bcbface3",
 "employee": "nathan.pryce@naviant.com",
 "submitted_at_iso": "2026-09-03T09:14:02.000Z",
 "commentary": "Finance summit in Chicago.",
 "documents": [
   {"document_id": "doc_9c1f2ab77d10", "filename": "hotel.pdf",
    "memo": "Conference block rate — only claiming the room, not the parking.",
    "byte_size": 18422, "media_type": "application/pdf"},
   {"document_id": "doc_44b0e7cc9188", "filename": "lunch.jpg",
    "memo": "Lunch with the Acme finance team.",
    "byte_size": 90112, "media_type": "image/jpeg"}],
 "lines": [
   {"ordinal": 1, "document_id": "doc_9c1f2ab77d10", "vendor": "harbor-hotel",
    "date": "2026-08-28", "category": "lodging", "description": "",
    "document_total_usd": 612.0, "requested_usd": 540.0,
    "purpose": "Two nights at the finance summit.", "confidence": 0.91,
    "origin": "model", "corrected_by": "", "graph_ref": ""},
   {"ordinal": 2, "document_id": "doc_44b0e7cc9188",
    "vendor": "corner-bistro", "date": "2026-08-28", "category": "meals",
    "description": "", "document_total_usd": 38.5, "requested_usd": 38.5,
    "purpose": "", "confidence": 0.42, "origin": "model",
    "corrected_by": "", "graph_ref": ""}],
 "requested_usd": 578.5, "approved_usd": 0.0, "state": "pending",
 "verdict_reason": "the reader was unsure of line 2",
 "escalations": [
   {"kind": "extraction-uncertain", "line_ordinal": 2,
    "detail": "line 2 was read at 0.42 confidence, below the governed floor of 0.70"}],
 "review_rounds": 0, "human_override": false,
 "policy_ref": "knowledge:expense-approval-policy@rev3",
 "policy_limit_usd": 200.0, "model": "gemini-3.7-flash",
 "task_id": "task_run_er_18bcbface3_k1_1", "decided_by": "", "decision_note": "",
 "graph_ref": "", "journal": []}
''';

TaskProposal _proposal([String json = _serverProposal]) =>
    TaskProposal.fromJson(jsonDecode(json) as Map<String, dynamic>);

Reimbursement _request([String json = _serverRequest]) =>
    Reimbursement.fromJson(jsonDecode(json) as Map<String, dynamic>);

void main() {
  group('the server payload parses into what the screen reads', () {
    test('every document row survives the wire', () {
      final rows = confirmRows(_proposal())!;
      expect(rows, hasLength(2));

      final hotel = rows.first;
      expect(hotel.documentId, 'doc_9c1f2ab77d10');
      expect(hotel.filename, 'hotel.pdf');
      // The submitter's own words, whole. This is the string the whole version
      // exists to keep out of the extractor's prompt, and it reaches the
      // person who wrote it unedited.
      expect(
        hotel.memo,
        'Conference block rate — only claiming the room, not the parking.',
      );

      final reading = hotel.reading!;
      expect(reading.vendor, 'harbor-hotel');
      expect(reading.date, '2026-08-28');
      expect(reading.totalUsd, closeTo(612.0, 1e-9));
      expect(reading.category, 'lodging');
      expect(reading.confidence, closeTo(0.91, 1e-9));
    });

    test('the rows are per document, in the order they were submitted', () {
      // Not per line: one receipt can be two lines, and a row per line would
      // show one memo twice and one page twice beside it.
      final rows = confirmRows(_proposal())!;
      expect(
        [for (final row in rows) row.documentId],
        ['doc_9c1f2ab77d10', 'doc_44b0e7cc9188'],
      );
    });

    test('these rows are not field verdicts, which is why they parse apart',
        () {
      // `proposal.fields` is untyped by contract — the flow that raised the
      // Task is the only thing that interprets it — and accounts payable puts
      // `FieldVerdict`s there. Reading a document row as a verdict does not
      // throw; it yields a verdict with no path, no status and no reason,
      // which is worse than throwing, and is the entire reason the raw rows
      // are kept beside the typed projection.
      final proposal = _proposal();
      expect(proposal.fields, hasLength(2));
      expect(proposal.fields!.first.path, '');
      expect(proposal.fields!.first.reason, '');
      expect(proposal.rawFields!.first['document_id'], 'doc_9c1f2ab77d10');
    });

    test('a memo recorded under the old key is still shown', () {
      // `memo` is v10's name for it and `note` is what a run recorded before
      // the rename put there. A resumed run whose documents were written under
      // the old key must not come back with the submitter's words missing.
      final rows = confirmRows(_proposal(
        '{"action": "record-expense", "target": "", "summary": "", "fields": ['
        '{"document_id": "doc_1", "filename": "r.jpg", '
        '"note": "Cab from the airport."}]}',
      ))!;
      expect(rows.single.memo, 'Cab from the airport.');
    });
  });

  group('absent and empty stay different facts', () {
    test('no fields key at all is null — no per-receipt reading was reported',
        () {
      // A v9 task answered through this screen. Not "nothing was read" — the
      // version never said what it read per document — and the screen leads
      // with that rather than claiming a clean reading.
      final proposal = _proposal(
        '{"action": "record-expense", "target": "expense-records", '
        '"summary": ""}',
      );
      expect(proposal.rawFields, isNull);
      expect(confirmRows(proposal), isNull);
    });

    test('a row with no extraction is a document nobody read', () {
      // Stored bytes with no reading against them. Different from a reading
      // whose fields came back blank, and the screen says which.
      final rows = confirmRows(_proposal(
        '{"action": "a", "target": "t", "summary": "", "fields": ['
        '{"document_id": "doc_1", "filename": "r.jpg", "memo": ""}]}',
      ))!;
      expect(rows.single.reading, isNull);
      expect(rows.single.memo, isEmpty);
    });

    test('a sparse row defaults rather than throwing', () {
      // Defensive on purpose: a task already raised, whose payload predates a
      // field, must still open a screen. A parse that threw here would take
      // out the desk rather than one row.
      final rows = confirmRows(_proposal(
        '{"action": "a", "target": "t", "summary": "", '
        '"fields": [{"document_id": "doc_1", "extraction": {}}]}',
      ))!;
      final row = rows.single;
      expect(row.filename, '');
      // With no filename the id is what a person is shown: a row labelled
      // with nothing at all cannot be told from the receipt beside it.
      expect(row.label, 'doc_1');
      expect(row.reading, isNotNull);
      expect(row.reading!.vendor, '');
      // Zero confidence is the reader saying nothing, not saying "wrong".
      expect(row.reading!.confidence, 0.0);
    });
  });

  group('what the screen draws out of that payload', () {
    testWidgets('each receipt carries its own reading and its own memo', (
      tester,
    ) async {
      await _pump(tester);

      // Both readings, as values rather than as a second set of inputs. The
      // vendor appears twice per receipt on purpose — once as what was read,
      // once in the editable line under it — and that pairing is the screen:
      // you are shown the reading and you correct the fact.
      expect(find.text('harbor-hotel'), findsNWidgets(2));
      expect(find.text('corner-bistro'), findsNWidgets(2));
      // The document total is the reading's alone; the line beside it carries
      // what is being claimed, which here is less.
      expect(find.text('\$612.00'), findsOneWidget);
      expect(find.text('\$38.50'), findsOneWidget);
      // And the confidence per receipt, which is what makes the second one
      // worth a second look.
      expect(find.text('91%'), findsOneWidget);
      expect(find.text('42%'), findsOneWidget);

      // And each memo beside the reading it is about, not pooled at the top.
      expect(
        find.text('“Conference block rate — only claiming the room, not the '
            'parking.”'),
        findsOneWidget,
      );
      expect(
        find.text('“Lunch with the Acme finance team.”'),
        findsOneWidget,
      );
      // Which is a different thing from what was said about the request as a
      // whole, and both are shown.
      expect(find.text('“Finance summit in Chicago.”'), findsOneWidget);
    });

    testWidgets('the receipt with a finding leads', (tester) async {
      await _pump(tester);

      // The lunch was submitted second and is read first, because that is
      // where a second look is worth something. The finding rides with it, in
      // the check's own words.
      final lunch = tester.getTopLeft(find.textContaining('lunch.jpg')).dy;
      final hotel = tester.getTopLeft(find.textContaining('hotel.pdf')).dy;
      expect(lunch, lessThan(hotel));
      expect(
        find.textContaining('below the governed floor of 0.70'),
        findsWidgets,
      );
    });

    testWidgets('a finding is context on the answer, not the reason for '
        'asking', (tester) async {
      await _pump(tester);

      // The lede is the difference between this screen and v9's. A person
      // told "confirm this" and a person told "this was flagged" read the
      // same page differently, and only the first is true of every run.
      expect(find.text('One thing is worth a second look'), findsOneWidget);
      expect(
        find.textContaining('it did not send it to anybody else'),
        findsOneWidget,
      );
    });

    testWidgets('a clean submission still asks, and says why', (tester) async {
      await _pump(tester, request: _cleanRequest());

      expect(
        find.text('Nothing was flagged — this is yours to confirm'),
        findsOneWidget,
      );
      expect(
        find.textContaining('a model read a photograph of your receipt'),
        findsOneWidget,
      );
      // Nothing is dirty, so the bar offers the confirmation rather than a
      // correction.
      expect(find.text('Approve as-is'), findsOneWidget);
    });

    testWidgets('a task with no per-receipt reading says so rather than '
        'claiming one', (tester) async {
      await _pump(tester, rows: null);

      expect(
        find.text('This run reported no per-receipt reading'),
        findsOneWidget,
      );
      expect(find.text('Nothing was flagged — this is yours to confirm'),
          findsNothing);
      // The documents are still there, off the request row, so the lines are
      // still grouped under the receipt they belong to.
      expect(find.textContaining('hotel.pdf'), findsWidgets);
    });

    testWidgets('a correction leaves keyed by the ordinal the applier patches '
        'on', (tester) async {
      final decided = <ReviewOutcome>[];
      await _pump(tester, onDecide: decided.add);

      // The submitter fixing what the reader misread on their own receipt.
      await tester.enterText(_claiming.first, '40.00');
      await tester.pumpAndSettle();
      await _apply(tester);

      expect(decided, hasLength(1));
      expect(decided.single.verdict, 'corrected');
      final lines = decided.single.payload['lines'] as List<dynamic>;
      // Every kept line travels, not only the one that moved: the applier
      // patches by ordinal, and the flow pairs each patched value against
      // what the model read — so a total quietly fixed becomes a raw→corrected
      // pair even though no check had doubted it.
      expect(lines, hasLength(2));
      final lunch = lines.firstWhere((l) => (l as Map)['ordinal'] == 2) as Map;
      expect(lunch['requested_usd'], closeTo(40.0, 1e-9));
      expect(lunch['category'], 'meals');
      expect(lunch['date'], '2026-08-28');
      // Nothing was added and nothing was dropped, so neither key is sent —
      // an empty `add_lines` would tell the applier to renumber for nothing.
      expect(decided.single.payload.containsKey('add_lines'), isFalse);
      expect(decided.single.payload.containsKey('remove_ordinals'), isFalse);
    });

    testWidgets('the screen says whose expense it is', (tester) async {
      // `subject_email` is first-class on the Task rather than display text in
      // an evidence row, and it is what makes "your receipts" checkable: the
      // governed routing sends this back to the submitter, and the one case
      // where it does not — no personal desk could be opened, so it landed on
      // the shared expense desk — is the case where somebody else is reading
      // it. A screen that assumed the reader is the subject would be silent in
      // exactly that case.
      final task = QueueTask.fromJson(
        jsonDecode(
          '{"id": "task_run_er_18bcbface3_k1_1", "kind": "decision", '
          '"title": "Confirm your expense", "queue": '
          '"desk-nathan-pryce-naviant-com", "flow_id": '
          '"expense-reimbursement", "flow_version": 10, "case_ref": '
          '"reimb_18bcbface3", "subject_email": "nathan.pryce@naviant.com"}',
        ) as Map<String, dynamic>,
      );
      expect(task.subjectEmail, 'nathan.pryce@naviant.com');

      await _pump(tester, task: task);
      expect(find.text('about nathan.pryce@naviant.com'), findsOneWidget);
    });

    testWidgets('a case about nobody says nothing about a subject', (
      tester,
    ) async {
      // A sim-filed intake, or a record raised before the field existed. Null
      // on the wire reads as empty rather than as a value to go looking for,
      // and the caption is absent rather than filled in with the desk's name.
      await _pump(tester, task: _task(subjectEmail: ''));
      // No caption at all, rather than one over an empty value. Matched on the
      // pill itself: the prose on this screen says "about" in other places
      // and a text search would pass over a broken caption.
      expect(_subjectPill, findsNothing);
      await _pump(tester);
      expect(_subjectPill, findsOneWidget);
    });

    testWidgets('confirming as-is sends no payload at all', (tester) async {
      final decided = <ReviewOutcome>[];
      await _pump(tester, request: _cleanRequest(), onDecide: decided.add);

      await tester.tap(find.text('Approve as-is'));
      await tester.pumpAndSettle();

      expect(decided.single.verdict, 'approved');
      expect(decided.single.payload, isEmpty);
    });

    testWidgets('a line the reading missed can be added against its receipt', (
      tester,
    ) async {
      final decided = <ReviewOutcome>[];
      await _pump(tester, onDecide: decided.add);

      // The second receipt was stapled behind the first: a compound receipt is
      // normal, not exceptional. The first Add-a-line belongs to the receipt
      // the screen led with, which is the flagged one.
      await tester.tap(find.text('Add a line').first);
      await tester.pumpAndSettle();
      // The blank claiming input is the added line's — found by being blank
      // rather than by a position in the tree, which is the kind of thing a
      // later reordering breaks silently.
      await tester.enterText(_blankClaiming, '45.00');
      await tester.pumpAndSettle();
      await _apply(tester);

      final added = decided.single.payload['add_lines'] as List<dynamic>;
      expect(added, hasLength(1));
      // Bound to the receipt it was added under, so the graph write can hang
      // it off the document rather than off nothing.
      expect((added.single as Map)['document_id'], 'doc_44b0e7cc9188');
      expect((added.single as Map)['requested_usd'], closeTo(45.0, 1e-9));
      // A human-entered line claims exactly what it says it claims: there is
      // no paper total of its own to be over.
      expect(
        (added.single as Map)['document_total_usd'],
        closeTo(45.0, 1e-9),
      );
    });
  });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/// The panel over the server's own payload, with no artifact fetcher: the page
/// is the slowest thing on the screen and none of these claims is about it.
Future<void> _pump(
  WidgetTester tester, {
  QueueTask? task,
  Reimbursement? request,
  List<ConfirmRow>? rows = const [],
  void Function(ReviewOutcome)? onDecide,
}) async {
  // The test font draws every glyph square, so the panel's prose needs more
  // room here than in the browser.
  tester.view.physicalSize = const Size(2200, 3400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: ConfirmValuesPanel(
          task: task ?? _task(),
          request: request ?? _request(),
          // `const []` is the sentinel for "use the server's payload"; an
          // explicit null is the case where the version reported no rows.
          rows: rows != null && rows.isEmpty
              ? confirmRows(_proposal())
              : rows,
          artifact: null,
          onDecide: onDecide ?? (_) {},
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

/// The quiet caption naming who the case is about.
final _subjectPill = find.byWidgetPredicate(
  (w) => w is MetaPill && w.label.startsWith('about '),
);

/// The claiming inputs, top to bottom — the flagged receipt's line comes first
/// because its receipt does.
final _claiming = find.widgetWithText(TextField, 'Claiming');

/// The one claiming input with nothing in it: a line the submitter just added.
final _blankClaiming = find.byWidgetPredicate(
  (w) =>
      w is TextField &&
      w.decoration?.labelText == 'Claiming' &&
      (w.controller?.text ?? '').isEmpty,
);

/// Press the correct-and-recheck control, wherever the scroll has put it.
Future<void> _apply(WidgetTester tester) async {
  final apply = find.text('Apply and re-check');
  await tester.ensureVisible(apply);
  await tester.pumpAndSettle();
  await tester.tap(apply);
  await tester.pumpAndSettle();
}

/// The same submission with nothing flagged: the common case, and the one v9
/// auto-approved with nobody in the loop.
Reimbursement _cleanRequest() => Reimbursement.fromJson({
  ...jsonDecode(_serverRequest) as Map<String, dynamic>,
  'escalations': const [],
  'verdict_reason': 'in flight',
});

QueueTask _task({String subjectEmail = 'nathan.pryce@naviant.com'}) =>
    QueueTask(
  id: 'task_run_er_18bcbface3_k1_1',
  runId: 'run_er_18bcbface3',
  kind: 'decision',
  title: 'Confirm your expense: harbor-hotel \$578.50',
  summary: 'Check what was read off your receipts.',
  // The submitter's own desk, which is the point of the version.
  queue: 'desk-nathan-pryce-naviant-com',
  priority: 'normal',
  status: 'open',
  flowId: 'expense-reimbursement',
  flowVersion: 10,
  caseRef: 'reimb_18bcbface3',
  subjectEmail: subjectEmail,
  refs: const [],
  evidence: const [],
  proposal: null,
  resumes: '',
  createdAtIso: '2026-09-03T09:14:05.000Z',
  decision: null,
);
