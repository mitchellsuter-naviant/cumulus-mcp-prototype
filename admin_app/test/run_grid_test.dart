import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/run.dart';
import 'package:cumulus_admin/screens/flow_detail_screen.dart';
import 'package:cumulus_admin/screens/flows_screen.dart' show FleetCol;
import 'package:cumulus_admin/theme/cumulus_theme.dart';
import 'package:cumulus_admin/widgets/ui_kit.dart';

/// The Runs tab is the fleet grid in a second subject: the same pinned head,
/// the same row geometry, the same tabular figures — over one flow's history
/// instead of the estate's definitions.
///
/// The properties worth holding:
///
/// - A row is **one line**. It replaced a 60px list row whose two-line
///   subtitle restated every figure the table now has a column for, and the
///   whole point of the grid is that a hundred runs read in one sweep.
/// - Columns drop in a stated order as the window narrows, and the three that
///   identify a run — status, id, when — never drop.
/// - Only a **failure** rules a row. A suspended run is waiting on a person,
///   which is the design working.
/// - The pinned block declares its height before it lays out, so that height
///   has to match what is actually put in it.
/// - The run log opens on the page's global timeframe, so the header's
///   figures and the log beneath them cover the same days.
FlowRunSummary _run({
  String id = 'run_a1b2c3',
  String status = 'succeeded',
  String actor = 'dana.ruiz@naviant.com',
  String caseRef = 'INV-4471',
  int modelCalls = 3,
  int durationMs = 2400,
  String suspendedAtStep = '',
  String error = '',
  String openTaskId = '',
}) => FlowRunSummary(
  id: id,
  flowId: 'ap-invoice',
  status: status,
  caseRef: caseRef,
  actor: actor,
  steps: 6,
  modelCalls: modelCalls,
  costUsd: 0.014,
  durationMs: durationMs,
  openTaskId: openTaskId,
  suspendedAtStep: suspendedAtStep,
  resumeCount: 0,
  error: error,
  startedAtIso: DateTime.now()
      .subtract(const Duration(hours: 3))
      .toIso8601String(),
  finishedAtIso: '',
  refs: const ['asset:ap-policy@7'],
  outputs: const {},
);

/// The default 800x600 test surface is narrower than most of the widths this
/// grid is laid out for, and a Row that does not fit squeezes its children
/// until the text wraps — which would measure a one-line row as three lines
/// and blame the design. So the surface is set to the width under test.
Future<void> _surface(WidgetTester tester, double width) async {
  await tester.binding.setSurfaceSize(Size(width, 600));
  addTearDown(() => tester.binding.setSurfaceSize(null));
}

Future<void> _pumpRow(
  WidgetTester tester,
  FlowRunSummary run, {
  double width = 1200,
  bool open = false,
}) async {
  await _surface(tester, width);
  await tester.pumpWidget(
    MaterialApp(
      theme: buildCumulusTheme(),
      home: Scaffold(
        body: SizedBox(
          width: width,
          // A Column, because that is the constraint the row actually meets:
          // it is built into a SliverList, where its height is its own to
          // decide. Handed a bounded height instead, the severity rule — a
          // Container with a colour and no child — fills whatever it is given,
          // and the row would measure as tall as the viewport.
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              RunGridRow(
                run: run,
                columns: RunColumns.forWidth(width),
                open: open,
                onOpen: () {},
              ),
            ],
          ),
        ),
      ),
    ),
  );
}

/// The rule down a row's leading edge, as the colour it was actually painted.
Color _ruleColor(WidgetTester tester) {
  final rule = tester.widget<Container>(
    find.byWidgetPredicate(
      (w) => w is Container && w.constraints?.maxWidth == FleetCol.rule,
    ),
  );
  return rule.color!;
}

void main() {
  group('the window the log opens on', () {
    test('follows the console\'s global timeframe', () {
      expect(RunWindow.forDays(30), RunWindow.month);
      expect(RunWindow.forDays(60), RunWindow.twoMonths);
      expect(RunWindow.forDays(90), RunWindow.quarter);
    });

    test('falls back to 30 days, never to all time', () {
      // `all` would put the log over a wider span than the figures above it,
      // which is the one disagreement this whole wiring exists to prevent.
      expect(RunWindow.forDays(7), RunWindow.month);
      expect(RunWindow.forDays(0), RunWindow.month);
    });

    test('every long window has a span the filter can cut on', () {
      for (final window in [
        RunWindow.month,
        RunWindow.twoMonths,
        RunWindow.quarter,
      ]) {
        expect(window.span, isNotNull, reason: '${window.name} needs a span');
      }
      expect(RunWindow.all.span, isNull);
    });

    test('a 90-day window keeps a run the 30-day one drops', () {
      final now = DateTime.utc(2026, 9, 1, 12);
      final runs = [
        _run(id: 'old').copyAt(now.subtract(const Duration(days: 45))),
      ];
      expect(filterRuns(runs, window: RunWindow.month, now: now), isEmpty);
      expect(
        filterRuns(runs, window: RunWindow.quarter, now: now).single.id,
        'old',
      );
    });
  });

  group('durations', () {
    test('read in the coarsest unit that still says something', () {
      expect(runDuration(840), '840ms');
      expect(runDuration(2400), '2.4s');
      expect(runDuration(195000), '3m 15s');
    });

    test('an untimed run says so rather than claiming zero', () {
      // Every seeded run reports 0ms. Printing "0ms" claims a measurement of
      // zero, which is a different statement from having no measurement.
      expect(runDuration(0), 'not timed');
      expect(runDuration(-1), 'not timed');
    });
  });

  group('column geometry', () {
    test('drops columns in a stated order as the window narrows', () {
      expect(RunColumns.forWidth(1200).actor, isTrue);
      expect(RunColumns.forWidth(950).actor, isFalse);
      expect(RunColumns.forWidth(950).calls, isTrue);
      expect(RunColumns.forWidth(800).calls, isFalse);
      expect(RunColumns.forWidth(800).duration, isTrue);
      expect(RunColumns.forWidth(700).duration, isFalse);
    });

    test('the pinned block is exactly what gets laid out inside it', () {
      // A pinned sliver states its height before its child is laid out, so
      // the two have to be one rule. Both arrangements are stated here.
      for (final width in [1400.0, 1000.0]) {
        final rows = runStickyRowsFor(width);
        expect(
          runStickyExtentFor(width),
          1 + 12 + (34 * rows) + (8 * (rows - 1)) + 10 + 34 + 1,
          reason: 'the $rows-row arrangement at ${width}px',
        );
      }
      expect(runStickyRowsFor(1400), 1);
      expect(runStickyRowsFor(1000), 2);
    });
  });

  group('a run row', () {
    testWidgets('is one line, whatever it has to say', (tester) async {
      await _pumpRow(tester, _run(error: 'vendor lookup timed out'));
      // Nothing wrapped: the row is as tall as one line of content plus its
      // 7px padding, not the 60px the list row it replaced spent.
      expect(tester.getSize(find.byType(RunGridRow)).height, lessThan(40));
    });

    testWidgets('carries the columns its width can seat', (tester) async {
      await _pumpRow(tester, _run());
      expect(find.text('dana.ruiz@naviant.com'), findsOneWidget);
      expect(find.text('2.4s'), findsOneWidget);
      expect(find.text('3'), findsOneWidget);

      // Narrow enough to drop the actor, wide enough to keep the figures.
      await _pumpRow(tester, _run(), width: 950);
      expect(find.text('dana.ruiz@naviant.com'), findsNothing);
      expect(find.text('2.4s'), findsOneWidget);

      // The three that identify a run never drop.
      await _pumpRow(tester, _run(), width: 700);
      expect(find.text('run_a1b2c3'), findsOneWidget);
      expect(find.text('SUCCEEDED'), findsOneWidget);
    });

    testWidgets('falls back to the case when there is no actor', (
      tester,
    ) async {
      await _pumpRow(tester, _run(actor: ''));
      expect(find.text('INV-4471'), findsOneWidget);
    });

    testWidgets('only a failure rules the row', (tester) async {
      await _pumpRow(tester, _run(status: 'failed', error: 'vendor timeout'));
      expect(_ruleColor(tester), CumulusTone.danger.foreground);
      // And the reason rides on the same line — the one thing a run has to
      // say that no column has a home for.
      expect(find.text('vendor timeout'), findsOneWidget);

      // Suspended is a run waiting on a person, which is the design working.
      await _pumpRow(
        tester,
        _run(status: 'suspended', suspendedAtStep: 'k2_review'),
      );
      expect(_ruleColor(tester), isNot(CumulusTone.danger.foreground));
      expect(find.text('at k2_review'), findsOneWidget);

      await _pumpRow(tester, _run());
      expect(_ruleColor(tester), isNot(CumulusTone.danger.foreground));
    });

    testWidgets('an open row is marked without being alarming', (tester) async {
      await _pumpRow(tester, _run(), open: true);
      expect(_ruleColor(tester), CumulusColors.accent);
    });

    testWidgets('an untimed run does not read as an instant one', (
      tester,
    ) async {
      await _pumpRow(tester, _run(durationMs: 0));
      expect(find.text('not timed'), findsOneWidget);
      expect(find.text('0ms'), findsNothing);
    });
  });

  group('the column heads', () {
    testWidgets('fit the height the pinned block reserved for them', (
      tester,
    ) async {
      // Overflowing here is what makes a pinned header report one height and
      // paint another, so the assertion is that pumping it inside its stated
      // box raises nothing.
      await _surface(tester, 1400);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildCumulusTheme(),
          home: const Scaffold(
            body: SizedBox(
              width: 1400,
              height: 34,
              child: RunGridHead(
                columns: RunColumns(actor: true, duration: true, calls: true),
                caption: '27 of 27',
              ),
            ),
          ),
        ),
      );
      expect(tester.takeException(), isNull);
      expect(find.text('STATUS'), findsOneWidget);
      expect(find.text('RUN ID'), findsOneWidget);
      expect(find.text('MODEL CALLS'), findsOneWidget);
      expect(find.text('27 of 27'), findsOneWidget);
    });
  });
}

/// A copy of this run started at [when]. Only the timestamp differs, which is
/// all the window filter reads.
extension on FlowRunSummary {
  FlowRunSummary copyAt(DateTime when) => FlowRunSummary(
    id: id,
    flowId: flowId,
    status: status,
    caseRef: caseRef,
    actor: actor,
    steps: steps,
    modelCalls: modelCalls,
    costUsd: costUsd,
    durationMs: durationMs,
    openTaskId: openTaskId,
    suspendedAtStep: suspendedAtStep,
    resumeCount: resumeCount,
    error: error,
    startedAtIso: when.toIso8601String(),
    finishedAtIso: finishedAtIso,
    refs: refs,
    outputs: outputs,
  );
}
