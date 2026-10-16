import 'package:cumulus_admin/models/run.dart';
import 'package:cumulus_admin/services/api_client.dart';
import 'package:cumulus_admin/widgets/run_workbench.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// A step tile's evidence, live rather than printed.
///
/// The journal rendered a step's read-set and write-set as plain text and its
/// output not at all, so the one screen that says what a run did was the one
/// screen from which none of it could be opened. These are the three claims
/// that fixes: the refs dereference, the artifact is one click away even when
/// the step named it only in its output, and the payload is there without
/// being in the way.
void main() {
  group('stepDocuments', () {
    test('an extractor names its document in its output and nowhere else', () {
      // The real shape: `extract-invoice` reads knowledge, writes nothing, and
      // hands the id downstream. Before this the invoice was unreachable from
      // the step that read it.
      final step = _step(
        refs: ['knowledge:ap/extraction-prompt@rev3'],
        output: {'document_id': 'doc_28fee8830bfcf03c', 'total_usd': 4120.0},
      );
      expect(stepDocuments(step), ['document:doc_28fee8830bfcf03c']);
    });

    test('a ref that is already qualified is not qualified twice', () {
      final step = _step(refs: ['document:doc_a1'], output: const {});
      expect(stepDocuments(step), ['document:doc_a1']);
    });

    test('read, wrote and returned are one set, deduplicated', () {
      final step = _step(
        refs: ['document:doc_a1', 'graph:Invoice/inv_1'],
        writes: ['document:doc_a1', 'link:vendor'],
        output: {'document_id': 'doc_a1'},
      );
      expect(stepDocuments(step), ['document:doc_a1']);
    });

    test('a step that touched several artifacts offers all of them', () {
      final step = _step(
        output: {
          'document_ids': ['doc_a1', 'document:doc_b2'],
        },
      );
      expect(stepDocuments(step), ['document:doc_a1', 'document:doc_b2']);
    });

    test('a step with no artifact offers none', () {
      expect(stepDocuments(_step(output: {'verdict': 'post'})), isEmpty);
      expect(stepDocuments(_step(output: null)), isEmpty);
    });

    test('an output that is not a map is not rummaged through', () {
      expect(stepDocuments(_step(output: 'posted')), isEmpty);
    });
  });

  group('the journal renders a step as evidence', () {
    testWidgets('refs and writes are chips, and the artifact is a button', (
      tester,
    ) async {
      await _pump(
        tester,
        _run([
          _step(
            refs: ['knowledge:ap/extraction-prompt@rev3'],
            writes: ['graph:Invoice/inv_1'],
            output: {'document_id': 'doc_28fee8830bfcf03c'},
          ),
        ]),
      );

      // The read-set dereferences from the step, not only from the run.
      expect(find.text('knowledge:ap/extraction-prompt@rev3'), findsWidgets);
      expect(find.text('graph:Invoice/inv_1'), findsWidgets);
      expect(find.text('read'), findsOneWidget);
      expect(find.text('wrote'), findsOneWidget);
      // The document is offered by its bare id — a button, not a chip in a
      // list of refs.
      expect(find.text('doc_28fee8830bfcf03c'), findsOneWidget);
    });

    testWidgets('the output is there, folded, until it is asked for', (
      tester,
    ) async {
      await _pump(
        tester,
        _run([
          _step(output: {'total_usd': 4120.0, 'vendor': 'Ridgeline Supply'}),
        ]),
      );

      expect(find.text('OUTPUT'), findsOneWidget);
      expect(find.textContaining('Ridgeline Supply'), findsNothing);

      await tester.tap(find.text('OUTPUT'));
      await tester.pumpAndSettle();

      expect(find.textContaining('"vendor": "Ridgeline Supply"'), findsOneWidget);
    });

    testWidgets('a step that returned nothing offers no toggle', (
      tester,
    ) async {
      await _pump(tester, _run([_step(output: null)]));
      expect(find.text('OUTPUT'), findsNothing);
    });

    testWidgets('the journal says which version the run executed', (
      tester,
    ) async {
      await _pump(tester, _run([_step(output: null)], flowVersion: 6));
      expect(find.text('ran v6'), findsOneWidget);
    });

    testWidgets('a run that pinned no version claims none', (tester) async {
      await _pump(tester, _run([_step(output: null)]));
      expect(find.textContaining('ran v'), findsNothing);
    });
  });
}

/// The test font draws every glyph as a square of the font size, so a line of
/// prose is several times wider here than in the browser. The surface is set
/// wide enough that the journal's own captions are not the thing under test.
Future<void> _pump(WidgetTester tester, FlowRun run) async {
  tester.view.physicalSize = const Size(1800, 1600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: RunJournalView(api: ApiClient(), run: run),
        ),
      ),
    ),
  );
}

FlowRun _run(List<RunStep> steps, {int flowVersion = 0}) => FlowRun.fromJson({
  'id': 'run_ap_1',
  'flow_id': 'accounts-payable',
  'flow_version': flowVersion,
  'status': 'succeeded',
  'steps': [
    for (final step in steps)
      {
        'step_id': step.stepId,
        'name': step.name,
        'kind': step.kind,
        'status': step.status,
        'summary': step.summary,
        'refs': step.refs,
        'writes': step.writes,
        'output': step.output,
      },
  ],
});

RunStep _step({
  List<String> refs = const [],
  List<String> writes = const [],
  Object? output,
}) => RunStep.fromJson({
  'step_id': 'm1',
  'name': 'extract-invoice',
  'kind': 'modelcall',
  'status': 'done',
  'summary': 'Read the invoice.',
  'refs': refs,
  'writes': writes,
  'output': output,
});
