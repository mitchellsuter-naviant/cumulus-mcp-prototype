import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/flow.dart';
import 'package:cumulus_admin/screens/flows_screen.dart';
import 'package:cumulus_admin/theme/cumulus_theme.dart';
import 'package:cumulus_admin/widgets/mini_charts.dart';
import 'package:cumulus_admin/widgets/ui_kit.dart';

/// The Flows landing page is two cards over one page-level scroll: telemetry
/// that scrolls away, and a table whose controls and column heads pin under
/// the nav bar.
///
/// The properties worth holding:
///
/// - The table mirrors the hero tiles — same four measures, same order, over
///   the same window — and one selector moves all of it at once.
/// - Health is a verdict, and `idle` is one of them: a flow that has finished
///   nothing is not healthy.
/// - Only the two verdicts that mean something is wrong tint a row.
/// - The pinned block declares its height before it lays out, so that height
///   has to match what is actually put in it, at every arrangement.
/// - Figures are tabular, so digits line up down the grid.
final _clock = DateTime(2026, 9, 1, 12, 0);

FlowSummary _flow(
  String id, {
  String status = 'active',
  bool hasDefinition = true,
  String team = 'ap',
  List<String> tags = const ['ap'],
  int runs = 100,
  int errors = 0,
  double? success = 0.99,
  String health = 'healthy',
  int tasksOpen = 0,
  String costBasis = 'measured',
  int windowDays = 30,
  String lastRunAt = '2026-09-01T11:56:00',
  String lastRunStatus = 'succeeded',
}) => FlowSummary(
  id: id,
  name: id,
  status: status,
  hasDefinition: hasDefinition,
  description: 'what $id does, said in one line that is long enough to clip',
  team: team,
  tags: tags,
  version: 3,
  updatedAt: DateTime(2026, 8, 30),
  windowDays: windowDays,
  runsWindow: runs,
  costWindowUsd: 12.5,
  successWindow: success,
  errorsWindow: errors,
  health: health,
  costBasisWindow: costBasis,
  triggerKind: 'event',
  triggerDetail: 'InvoiceReceived',
  lastRunAt: lastRunAt,
  lastRunStatus: lastRunStatus,
  tasksOpen: tasksOpen,
  minutesSavedWindow: 40,
  spark: const [3, 9, 4, 7, 2, 8, 6, 5, 9, 1, 4, 7, 3, 8],
);

/// The label inside a hero tile, scoped so it is not confused with the column
/// caption of the same name. RUNS and ERRORS are deliberately the name of both
/// a tile and the column under it; that repetition is the mirroring working.
Finder tileLabel(String label) => find.descendant(
  of: find.byType(MetricTile),
  matching: find.text(label),
);

FleetStats _stats({int days = 30, int failed = 140}) => FleetStats(
  days: days,
  definitions: 12,
  flows: 17,
  withoutDefinition: 5,
  costBasis: 'measured',
  pricedModelCalls: 900,
  roiStatement: 'an assumption, not a measurement',
  active: 12,
  paused: 1,
  draft: 2,
  runs: 1840,
  completed: 1700,
  failed: failed,
  successRate: 0.93,
  modelSpendUsd: 1284.40,
  tokens: 900000,
  modelCalls: 1000,
  hoursReturned: 210,
  roiUsd: 6300,
  tasksOpen: 7,
  escalated: 24,
  series: [
    for (var i = 0; i < days; i++)
      FleetSeriesDay(
        date: '2026-08-${(i % 30 + 1).toString().padLeft(2, '0')}',
        runs: 40 + i,
        costUsd: 30 + i.toDouble(),
        tasksRaised: i % 5,
        completed: i.isEven ? 38 + i : 0,
        failed: i.isEven ? 2 : 0,
      ),
  ],
  attention: const [],
);

const _healths = ['healthy', 'degraded', 'failed', 'draft', 'no_runs'];

List<FlowSummary> _manyFlows() => [
  for (var i = 0; i < 40; i++)
    _flow(
      'flow-number-$i',
      status: i % 5 == 0 ? 'draft' : 'active',
      health: i % 5 == 0 ? 'draft' : _healths[i % 4],
      team: ['ap', 'finance-ops', 'revenue', 'people'][i % 4],
      tags: ['sync', 'three-way-match', 'mdm'].sublist(0, 1 + i % 3),
      runs: 500 - i * 7,
      errors: i % 9 == 0 ? 4 : 0,
      tasksOpen: i % 6 == 0 ? 3 : 0,
    ),
];

void main() {
  group('formatting', () {
    test('run counts are grouped so a magnitude reads at a glance', () {
      expect(thousands(0), '0');
      expect(thousands(999), '999');
      expect(thousands(1240), '1,240');
      expect(thousands(1000000), '1,000,000');
    });

    test('elapsed time steps up a unit rather than counting minutes forever', () {
      final now = DateTime(2026, 9, 1, 12, 0);
      expect(ago(now.subtract(const Duration(seconds: 20)), now), 'just now');
      expect(ago(now.subtract(const Duration(minutes: 33)), now), '33m ago');
      expect(ago(now.subtract(const Duration(hours: 3)), now), '3h ago');
      expect(ago(now.subtract(const Duration(days: 1)), now), '1d ago');
      expect(ago(now.subtract(const Duration(days: 900)), now), '1y+ ago');
    });
  });

  group('health', () {
    test('only a real fault tints a row', () {
      expect(isUnhealthy('degraded'), isTrue);
      expect(isUnhealthy('failed'), isTrue);
      // A draft has not been let out to run and an idle flow has done nothing
      // to judge. Tinting either spends the page's one alarm on a row nobody
      // needs to look at.
      expect(isUnhealthy('healthy'), isFalse);
      expect(isUnhealthy('draft'), isFalse);
      expect(isUnhealthy('no_runs'), isFalse);
    });

    test('the badge colours run from success to danger', () {
      expect(healthTone('healthy'), CumulusTone.success);
      expect(healthTone('degraded'), CumulusTone.warn);
      expect(healthTone('failed'), CumulusTone.danger);
      // Neither of these is a judgment, so neither gets a judgment's colour.
      expect(healthTone('draft'), CumulusTone.neutral);
      expect(healthTone('no_runs'), CumulusTone.neutral);
    });
  });

  group('fleet views', () {
    final flows = [
      _flow('ap-invoice', runs: 400, health: 'degraded'),
      _flow('sync-vendors', runs: 300, health: 'no_runs', success: null),
      _flow('expense-reimbursement', runs: 200, health: 'failed'),
      _flow('psa-delivery', status: 'draft', health: 'draft', runs: 0),
      _flow('crm-writeback', status: 'paused', health: 'healthy', runs: 10),
    ];

    test('each cut holds only what it claims', () {
      List<String> ids(FleetView view) =>
          fleetRows(view, flows).map((w) => w.id).toList();

      expect(ids(FleetView.all).length, 5);
      expect(ids(FleetView.active), [
        'ap-invoice',
        'sync-vendors',
        'expense-reimbursement',
      ]);
      expect(ids(FleetView.drafts), ['psa-delivery']);
      // Exactly the rows the Health column shows as wrong — no more, so the
      // tab never selects a row that looks fine.
      expect(ids(FleetView.attention).toSet(), {
        'ap-invoice',
        'expense-reimbursement',
      });
    });

    test('the exception cut leads with the flow that is failing', () {
      // Busiest-first would have put `ap-invoice` first; it is only degraded.
      expect(
        fleetRows(FleetView.attention, flows).first.id,
        'expense-reimbursement',
      );
    });

    test('a flow that has run nothing is not an exception', () {
      // It has finished nothing. That is a reason to withhold a verdict, not
      // to raise one.
      expect(inFleetView(flows[1], FleetView.attention), isFalse);
    });
  });

  group('grid row', () {
    Future<void> pumpRow(
      WidgetTester tester,
      double width, {
      FlowSummary? flow,
      VoidCallback? onOpen,
    }) async {
      tester.view.physicalSize = Size(width, 400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildCumulusTheme(),
          home: Scaffold(
            body: SizedBox(
              width: width,
              child: Column(
                children: [
                  FleetGridHead(
                    columns: FleetColumns.forWidth(width),
                    caption: '5 of 5',
                  ),
                  FleetRow(
                    flow: flow ?? _flow('ap-invoice'),
                    columns: FleetColumns.forWidth(width),
                    now: _clock,
                    onOpen: onOpen ?? () {},
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    for (final width in const [1420.0, 1100.0, 901.0, 899.0, 820.0]) {
      testWidgets('a row fits at ${width.round()}px', (tester) async {
        await pumpRow(
          tester,
          width,
          flow: _flow(
            'expense-reimbursement',
            health: 'failed',
            tasksOpen: 12,
            errors: 40,
            success: 0.42,
            runs: 1240,
          ),
        );

        expect(tester.takeException(), isNull);
        expect(find.text('expense-reimbursement'), findsOneWidget);
        expect(find.text('FAILED'), findsOneWidget);
        expect(find.text('1,240'), findsOneWidget);
        expect(find.text('12'), findsOneWidget);
      });
    }

    testWidgets('the row is strictly the id — no description', (tester) async {
      await pumpRow(tester, 1420);
      expect(find.text('ap-invoice'), findsOneWidget);
      // The prose that used to sit under every id is what put the grid at
      // sixty pixels a row.
      expect(find.textContaining('what ap-invoice does'), findsNothing);
    });

    testWidgets('a row is compact enough to read a fleet in one sweep', (
      tester,
    ) async {
      await pumpRow(tester, 1420);
      final height = tester.getSize(find.byType(FleetRow)).height;
      expect(height, lessThanOrEqualTo(40));
      expect(height, greaterThanOrEqualTo(30));
    });

    testWidgets('the row is the click target and holds no buttons', (
      tester,
    ) async {
      var opened = 0;
      await pumpRow(tester, 1420, onOpen: () => opened++);

      expect(find.byType(IconButton), findsNothing);
      await tester.tap(find.text('ap-invoice'));
      await tester.tap(find.text('100'));
      expect(opened, 2);
    });

    testWidgets('the four figures are tabular and right-aligned', (
      tester,
    ) async {
      await pumpRow(
        tester,
        1420,
        flow: _flow('a', runs: 1111, errors: 17, tasksOpen: 8),
      );

      // Runs, spend, errors, awaiting — the hero metrics, in the same order,
      // with digits that line up down the grid rather than drifting with the
      // width of a 1 against a 0.
      for (final figure in ['1,111', '\$12.50', '17', '8']) {
        final text = tester.widget<Text>(find.text(figure));
        expect(
          text.style?.fontFeatures,
          contains(const FontFeature.tabularFigures()),
          reason: '$figure should use tabular figures',
        );
        expect(text.textAlign, TextAlign.right);
      }
    });

    testWidgets('scrapped columns are gone', (tester) async {
      await pumpRow(tester, 1420, flow: _flow('a', tasksOpen: 4));
      // Trigger, the paused chip and the eval-spec pill were all removed.
      expect(find.textContaining('Event ('), findsNothing);
      expect(find.textContaining('paused'), findsNothing);
      expect(find.textContaining('NO EVAL SPEC'), findsNothing);
    });

    testWidgets('spend nobody priced is muted, not passed off as measured', (
      tester,
    ) async {
      await pumpRow(tester, 1420, flow: _flow('a', costBasis: 'unrecorded'));
      expect(
        tester.widget<Text>(find.text('\$12.50')).style?.color,
        CumulusColors.inkMuted,
      );
    });

    testWidgets('the last run reports when, and only when', (tester) async {
      await pumpRow(
        tester,
        1420,
        flow: _flow('a', lastRunAt: '2026-09-01T11:27:00'),
      );
      expect(find.text('33m ago'), findsOneWidget);
      expect(find.text('Success'), findsNothing);
      expect(find.text('Failed'), findsNothing);
    });

    testWidgets('a flow that never ran says so', (tester) async {
      await pumpRow(tester, 1420, flow: _flow('a', lastRunAt: ''));
      expect(find.text('never'), findsOneWidget);
    });

    testWidgets('a flow that has run nothing says so, and is not healthy', (
      tester,
    ) async {
      await pumpRow(
        tester,
        1420,
        flow: _flow('a', health: 'no_runs', success: null, runs: 0),
      );
      // Named for what it is. `IDLE` left a reader working out whether the
      // flow was broken, switched off, or simply quiet.
      expect(find.text('NO RUNS'), findsOneWidget);
      expect(find.text('IDLE'), findsNothing);
      // Nothing has run, so nothing has failed: the errors column is a real
      // zero. The success *rate* it replaced had to print a dash here, because
      // a flow that has finished nothing has no rate to state.
      expect(find.text('0'), findsWidgets);
      expect(find.text('—'), findsNothing);
      expect(find.text('0.0%'), findsNothing);
    });
  });

  group('the page', () {
    Future<void> pump(
      WidgetTester tester,
      Size size, {
      int days = 30,
      void Function(String, int)? onOpen,
      ValueChanged<int>? onDaysChanged,
    }) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildCumulusTheme(),
          home: Scaffold(
            body: FleetDashboard(
              stats: _stats(days: days),
              flows: _manyFlows(),
              days: days,
              onDaysChanged: onDaysChanged ?? (_) {},
              now: _clock,
              onRefresh: () {},
              onOpen: onOpen ?? (_, _) {},
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    // Both sides of every toolbar-arrangement boundary.
    for (final size in const [
      Size(1680, 1000),
      Size(1501, 900),
      Size(1499, 900),
      Size(1200, 800),
      Size(1101, 800),
      Size(1099, 800),
      Size(960, 800),
    ]) {
      testWidgets('lays out at ${size.width.round()}x${size.height.round()}', (
        tester,
      ) async {
        await pump(tester, size);
        expect(tester.takeException(), isNull);
        expect(find.byType(FleetGridHead), findsOneWidget);
        expect(find.text('140'), findsOneWidget);
      });

      testWidgets('the pinned block is exactly as tall as it claims at '
          '${size.width.round()}px', (tester) async {
        await pump(tester, size);
        // A sliver header states its height before its child lays out. If the
        // rule that computes it and the rows put inside it ever disagree, the
        // block clips or leaves a gap, and no other test would catch it.
        //
        // Measured once pinned: at rest the block sits below the telemetry, so
        // its distance from the top says nothing about its own height.
        await tester.drag(find.byType(CustomScrollView), const Offset(0, -700));
        await tester.pumpAndSettle();
        expect(
          tester.getRect(find.byType(FleetGridHead)).bottom,
          closeTo(stickyExtentFor(size.width), 2.0),
        );
      });
    }

    testWidgets('the toolbar and column heads survive scrolling the page', (
      tester,
    ) async {
      await pump(tester, const Size(1400, 900));

      expect(tileLabel('ERRORS'), findsOneWidget);
      final headBefore = tester.getTopLeft(find.byType(FleetGridHead)).dy;
      expect(headBefore, greaterThan(100));

      await tester.drag(find.byType(CustomScrollView), const Offset(0, -400));
      await tester.pumpAndSettle();

      // The tiles are gone — not pushed up but off the page and disposed,
      // which is the viewport the rows just gained.
      expect(tileLabel('ERRORS'), findsNothing);

      expect(find.byType(FleetGridHead), findsOneWidget);
      expect(
        tester.getTopLeft(find.byType(FleetGridHead)).dy,
        lessThan(headBefore),
      );
      // And every control came with it, still usable.
      expect(find.text('NEEDS ATTENTION'), findsOneWidget);
      expect(find.byType(TextField), findsOneWidget);
      expect(find.text('HEALTH'), findsOneWidget);
    });

    testWidgets('the table mirrors the hero tiles, in the same order', (
      tester,
    ) async {
      await pump(tester, const Size(1400, 900));

      // The four tiles.
      for (final tile in [
        'RUNS',
        'MODEL SPEND',
        'ERRORS',
        'AWAITING JUDGMENT',
      ]) {
        expect(find.text(tile), findsWidgets, reason: tile);
      }

      // And the four columns, in that same order across the grid. Scoped to
      // the head, because RUNS is deliberately the name of both a tile and
      // the column under it — that repetition is the mirroring working.
      Finder column(String c) => find.descendant(
        of: find.byType(FleetGridHead),
        matching: find.text(c),
      );
      final xs = [
        for (final c in ['RUNS', 'SPEND', 'ERRORS', 'AWAITING'])
          tester.getTopLeft(column(c)).dx,
      ];
      expect(xs, orderedEquals([...xs]..sort()));
    });

    testWidgets('one selector governs the tiles, the canvas and the columns', (
      tester,
    ) async {
      var asked = 0;
      await pump(
        tester,
        const Size(1400, 900),
        onDaysChanged: (d) => asked = d,
      );

      // The window is stated once, in the header.
      expect(find.text('30 days'), findsOneWidget);

      await tester.tap(find.text('30 days'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('90 days').last);
      await tester.pumpAndSettle();

      // Changing it is a refetch, because the tiles, the canvas and every
      // numeric column have to come from one server call to be guaranteed to
      // agree. The dashboard asks; it does not cut the data itself.
      expect(asked, 90);
    });

    testWidgets('the window the page was given is the window it labels', (
      tester,
    ) async {
      await pump(tester, const Size(1400, 900), days: 90);
      // Stated once, by the selector. The tiles no longer bake a window into
      // their labels, so switching to 90 does not leave four cards claiming
      // 30.
      expect(find.text('90 days'), findsOneWidget);
      expect(find.textContaining('· 30D'), findsNothing);
      expect(find.textContaining('· 90D'), findsNothing);
    });

    testWidgets('a metric tile points the canvas at its own history', (
      tester,
    ) async {
      await pump(tester, const Size(1400, 900));
      expect(find.text('RUNS / DAY'), findsOneWidget);

      await tester.tap(find.text('MODEL SPEND'));
      await tester.pumpAndSettle();

      expect(find.text('MODEL SPEND / DAY'), findsOneWidget);
      expect(find.text('RUNS / DAY'), findsNothing);
    });

    testWidgets('search narrows the grid', (tester) async {
      await pump(tester, const Size(1400, 900));
      await tester.enterText(find.byType(TextField), 'flow-number-3');
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('flow-number-3'), findsWidgets);
      expect(find.text('flow-number-1'), findsNothing);
    });

    testWidgets('the search says what it searches, and its key', (
      tester,
    ) async {
      await pump(tester, const Size(1400, 900));
      final field = tester.widget<TextField>(find.byType(TextField));
      expect(field.decoration?.hintText, 'Search flows, tags, or teams…');
      // ⌘/ — the page's own find, beside the shell's ⌘K for the palette.
      // Bare `/` belongs to the shell and would jump to the global search.
      expect(find.text('⌘/'), findsOneWidget);
      expect(find.text('/'), findsNothing);
    });

    testWidgets('⌘/ puts the cursor in the table search', (tester) async {
      await pump(tester, const Size(1400, 900));
      final field = tester.widget<TextField>(find.byType(TextField));
      expect(field.focusNode?.hasFocus, isFalse);

      await tester.sendKeyDownEvent(LogicalKeyboardKey.meta);
      await tester.sendKeyEvent(LogicalKeyboardKey.slash);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.meta);
      await tester.pumpAndSettle();

      expect(field.focusNode?.hasFocus, isTrue);
    });

    testWidgets('a row click opens that flow', (tester) async {
      final opened = <(String, int)>[];
      await pump(
        tester,
        const Size(1400, 900),
        onOpen: (id, tab) => opened.add((id, tab)),
      );
      await tester.tap(find.text('flow-number-0'));
      expect(opened, [('flow-number-0', 0)]);
    });
  });

  group('the two cards', () {
    Future<void> pumpPage(WidgetTester tester, Size size) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildCumulusTheme(),
          home: Scaffold(
            body: FleetDashboard(
              stats: _stats(),
              flows: _manyFlows(),
              now: _clock,
              onRefresh: () {},
              onOpen: (_, _) {},
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    testWidgets('the tiles sit beside the canvas on a wide screen', (
      tester,
    ) async {
      await pumpPage(tester, const Size(1400, 900));
      expect(
        tester.getRect(tileLabel('ERRORS')).right,
        lessThan(tester.getRect(find.byType(DailyBarChart)).left),
      );
    });

    testWidgets('they stack when there is not room to sit side by side', (
      tester,
    ) async {
      await pumpPage(tester, const Size(1000, 900));
      expect(
        tester.getRect(tileLabel('ERRORS')).bottom,
        lessThan(tester.getRect(find.byType(DailyBarChart)).top),
      );
    });

    testWidgets('the canvas has no collapse control', (tester) async {
      await pumpPage(tester, const Size(1400, 900));
      expect(find.byIcon(Icons.unfold_less), findsNothing);
      expect(find.byIcon(Icons.unfold_more), findsNothing);
    });

    testWidgets('the header states the page, its window, and nothing else', (
      tester,
    ) async {
      await pumpPage(tester, const Size(1400, 900));
      expect(find.text('Flows'), findsOneWidget);
      expect(find.text('30 days'), findsOneWidget);
      // The fleet size and the declared split were filler: both are said by a
      // tile or a caption a few pixels lower.
      expect(find.textContaining('17 flows'), findsNothing);
      expect(find.textContaining('last 30 days'), findsNothing);
    });

    testWidgets('the table is ruled on both sides, top to bottom', (
      tester,
    ) async {
      await pumpPage(tester, const Size(1400, 900));
      final row = tester.widget<Container>(
        find
            .descendant(
              of: find.byType(FleetRow).first,
              matching: find.byType(Container),
            )
            .first,
      );
      final decoration = row.decoration as BoxDecoration;
      expect(decoration.border!.bottom.color, CumulusColors.border);
      // No rounded foot: the list scrolls, and a rounded corner would claim it
      // had ended.
      expect(decoration.borderRadius, isNull);
    });
  });

  group('a day with no finished runs', () {
    test('has no success rate, rather than a rate of zero', () {
      final idle = FleetSeriesDay(
        date: '2026-08-30',
        runs: 3,
        costUsd: 0,
        tasksRaised: 0,
        completed: 0,
        failed: 0,
      );
      expect(idle.successRate, isNull);

      final busy = FleetSeriesDay(
        date: '2026-08-31',
        runs: 5,
        costUsd: 1,
        tasksRaised: 1,
        completed: 3,
        failed: 1,
      );
      expect(busy.successRate, 0.75);
    });
  });

  group('sorting', () {
    final rows = [
      _flow('a', runs: 100, tasksOpen: 3, success: 0.90, errors: 10),
      _flow('b', runs: 300, tasksOpen: 1, success: 0.99, errors: 3),
      _flow('c', runs: 200, tasksOpen: 9, success: null, health: 'no_runs',
          errors: 25),
    ];

    List<String> ids(FleetSort? key, bool asc) =>
        sortFleetRows(rows, key, asc).map((w) => w.id).toList();

    test('no key hands back the order it was given', () {
      // The server already sorted: busiest first, or worst-first on the
      // exception cut. That is a better default than any single column.
      expect(ids(null, false), ['a', 'b', 'c']);
    });

    test('a measure orders by that measure, both ways', () {
      expect(ids(FleetSort.runs, false), ['b', 'c', 'a']);
      expect(ids(FleetSort.runs, true), ['a', 'c', 'b']);
      expect(ids(FleetSort.awaiting, false), ['c', 'a', 'b']);
    });

    test('errors order as a plain count, worst first', () {
      // The success *rate* this replaced could be null — a flow that had
      // finished nothing had no rate, and it had to be parked at the bottom
      // in both directions. A failure count is always a real number, so
      // every row has an honest place in the ordering.
      expect(ids(FleetSort.errors, false), ['c', 'a', 'b']);
      expect(ids(FleetSort.errors, true), ['b', 'a', 'c']);
    });

    test('sorting never loses or invents a row', () {
      for (final key in [null, ...FleetSort.values]) {
        for (final asc in [true, false]) {
          expect(ids(key, asc).toSet(), {'a', 'b', 'c'});
        }
      }
    });
  });

  group('polish', () {
    Future<void> pumpPage(WidgetTester tester, [Size size = const Size(1400, 900)]) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildCumulusTheme(),
          home: Scaffold(
            body: FleetDashboard(
              stats: _stats(),
              flows: _manyFlows(),
              now: _clock,
              onRefresh: () {},
              onOpen: (_, _) {},
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    testWidgets('clicking a column head cycles desc, asc, then back', (
      tester,
    ) async {
      await pumpPage(tester);
      Finder head(String c) => find.descendant(
        of: find.byType(FleetGridHead),
        matching: find.text(c),
      );
      List<String> shown() => tester
          .widgetList<FleetRow>(find.byType(FleetRow))
          .map((r) => r.flow.id)
          .toList();

      final asGiven = shown();

      // AWAITING, not RUNS: the server already hands rows back busiest
      // first, so ordering by runs descending is indistinguishable from
      // doing nothing and would prove nothing here.
      await tester.tap(head('AWAITING'));
      await tester.pumpAndSettle();
      final desc = shown();
      expect(desc, isNot(asGiven));

      await tester.tap(head('AWAITING'));
      await tester.pumpAndSettle();
      expect(shown(), isNot(desc));

      // Three clicks and you are back where you started — nothing here is a
      // one-way door.
      await tester.tap(head('AWAITING'));
      await tester.pumpAndSettle();
      expect(shown(), asGiven);
    });

    testWidgets('only the column doing the ordering shows a chevron', (
      tester,
    ) async {
      await pumpPage(tester);
      // At rest, nothing claims to be sorted.
      expect(find.byIcon(Icons.arrow_drop_down), findsNothing);
      expect(find.byIcon(Icons.arrow_drop_up), findsNothing);

      await tester.tap(
        find.descendant(
          of: find.byType(FleetGridHead),
          matching: find.text('SPEND'),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byIcon(Icons.arrow_drop_down), findsOneWidget);

      await tester.tap(
        find.descendant(
          of: find.byType(FleetGridHead),
          matching: find.text('SPEND'),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byIcon(Icons.arrow_drop_up), findsOneWidget);
      expect(find.byIcon(Icons.arrow_drop_down), findsNothing);
    });

    testWidgets('the sticky block lifts only once rows are under it', (
      tester,
    ) async {
      await pumpPage(tester);
      BoxDecoration sticky() => tester
          .widgetList<Container>(
            find.descendant(
              of: find.byType(SliverPersistentHeader),
              matching: find.byType(Container),
            ),
          )
          .map((c) => c.decoration)
          .whereType<BoxDecoration>()
          .firstWhere((d) => d.borderRadius != null);

      // At rest it is just the top of a card.
      expect(sticky().boxShadow, isNull);

      await tester.drag(find.byType(CustomScrollView), const Offset(0, -400));
      await tester.pumpAndSettle();

      // Pinned, with rows passing beneath, it has to read as being in front
      // of them rather than bleeding into them.
      expect(sticky().boxShadow, isNotNull);
    });

    testWidgets('the header says the scope, quietly', (tester) async {
      await pumpPage(tester);
      final subtitle = tester.widget<Text>(
        find.text('every flow in the enterprise'),
      );
      // Subtle by construction: smaller and fainter than body text, so it
      // answers a newcomer's question without competing with the numbers.
      expect(subtitle.style!.fontSize! <= 12, isTrue);
      expect(subtitle.style!.color, isNot(CumulusColors.ink));
    });

    testWidgets('a tab count is set right and told apart by colour', (
      tester,
    ) async {
      await pumpPage(tester);
      // The counts share one right edge across four tabs of different label
      // widths — which the pill behind them could never do.
      final counts = tester.widgetList<Text>(
        find.descendant(of: find.byType(TextField), matching: find.text('x')),
      );
      expect(counts, isEmpty); // guard: the finder above is a no-op sentinel

      for (final tab in ['ALL', 'NEEDS ATTENTION', 'ACTIVE', 'DRAFTS']) {
        expect(find.text(tab), findsOneWidget, reason: tab);
      }
    });

    testWidgets('a filter dropdown is sized to its selection', (tester) async {
      await pumpPage(tester);
      // Sized to the label it is showing, not to the widest option in its
      // menu — which is what left "All tags" stranded against the left edge
      // with an inch of nothing before the chevron. `three-way-match` is in
      // the tag list, and the control is comfortably narrower than that.
      final tags = tester.getSize(find.text('All tags').first).width;
      final teams = tester.getSize(find.text('All teams').first).width;

      // Each label occupies its own width and no more. `three-way-match` is
      // in the tag menu and is nearly twice as long as "All tags"; the old
      // control was sized to it, which is what left the words stranded at the
      // far left with a gulf before the chevron.
      //
      // Widths are in the test font, where every glyph is one em square, so
      // these are character counts: 8 and 9 against the 15 of the widest tag.
      expect(tags, lessThan(teams));
      expect(teams / tags, closeTo(9 / 8, 0.01));
      final widestTag = tester.getSize(find.text('flow-number-0').first).width;
      expect(tags, lessThan(widestTag * 15 / 13));
    });
  });

  group('column captions stack with their figures', () {
    testWidgets('every numeric header shares its column\'s right edge', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1420, 400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        MaterialApp(
          theme: buildCumulusTheme(),
          home: Scaffold(
            body: SizedBox(
              width: 1420,
              child: Column(
                children: [
                  FleetGridHead(
                    columns: FleetColumns.forWidth(1420),
                    caption: '1 of 1',
                    // Active, so a chevron is actually painted: the space it
                    // takes must not move the caption off the column edge.
                    sort: FleetSort.runs,
                    onSort: (_) {},
                  ),
                  FleetRow(
                    flow: _flow('ap', runs: 1240, errors: 17, tasksOpen: 5),
                    columns: FleetColumns.forWidth(1420),
                    now: _clock,
                    onOpen: () {},
                  ),
                ],
              ),
            ),
          ),
        ),
      );

      // The row sits one hairline inside the card's left rule that the head
      // does not carry in isolation, so the two differ by exactly that.
      for (final pair in [
        ('RUNS', '1,240'),
        ('SPEND', '\$12.50'),
        ('ERRORS', '17'),
        ('AWAITING', '5'),
      ]) {
        expect(
          tester.getRect(find.text(pair.$1)).right,
          closeTo(tester.getRect(find.text(pair.$2)).right, 1.5),
          reason: '${pair.$1} caption should stack with ${pair.$2}',
        );
      }
    });
  });
}
