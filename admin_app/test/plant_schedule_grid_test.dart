import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/tasks/plant_schedule/models.dart';
import 'package:cumulus_admin/tasks/plant_schedule/workspace.dart';
import 'package:cumulus_admin/widgets/ui_kit.dart';

/// The Plant Schedule day panel, and the three claims a signature rests on.
///
/// The incentive is the argument this plant has been having for years: the
/// figure is per-hour, it starts negative, and where it crosses zero is the
/// whole conversation — so the panel has to say which side of zero the day is
/// on, and show the arithmetic that puts it there. The hours have three
/// governed marks and not one: past the goal but inside the fudge is accepted
/// by the page and is not an exception, which a bar with a single line could
/// not express. And the export is live only on an approved day, because
/// `/api/v1/schedule/export` answers 409 to anything else — a button that
/// produced that error would teach people the system is broken while it is in
/// fact working.
void main() {
  group('the incentive', () {
    testWidgets('says which side of zero the day is on', (tester) async {
      await _pump(tester, _cell());

      expect(find.text('out of the hole'), findsOneWidget);
      expect(find.text('still in the hole'), findsNothing);
      expect(
        find.textContaining(
          'The governed figures cross zero at 248.6 h; this day holds 268 h.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a thin day says it is still in the hole', (tester) async {
      await _pump(
        tester,
        _cell(totalHours: 210, incentivePerHour: -2.8, outOfTheHole: false),
      );

      expect(find.text('still in the hole'), findsOneWidget);
      expect(find.text('out of the hole'), findsNothing);
    });

    testWidgets('shows arithmetic a reader can check', (tester) async {
      await _pump(tester, _cell());

      // Every term, at the precision the page states it in: rounding the rate
      // to cents would leave the equation not reaching the answer beside it.
      expect(
        find.text('-\$18.00 + \$0.0724/h × 268 h = \$1.40 per hour'),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'As the flow wrote it: -\$18.00 + \$0.0724/h × 268 h = \$1.40',
        ),
        findsOneWidget,
      );
    });
  });

  group('the hours bar', () {
    testWidgets('draws the fudge band between the goal and the ceiling', (
      tester,
    ) async {
      await _pump(tester, _cell());

      final band = find.byKey(HoursBar.fudgeBandKey);
      expect(band, findsOneWidget);

      // The band is the ten hours of latitude the page allows past the goal,
      // drawn against a bar scaled to the absolute max plus that latitude.
      final bar = tester.getSize(find.byType(HoursBar)).width;
      final width = tester.getSize(band).width;
      expect(width, greaterThan(0));
      expect(width, closeTo(bar * 10 / 290, 0.5));

      // And the fill is the hours the day holds, on the same scale.
      final fill = tester.getSize(find.byKey(HoursBar.fillKey)).width;
      expect(fill, closeTo(bar * 268 / 290, 0.5));
    });

    testWidgets('names the fudge band rather than calling it an exception', (
      tester,
    ) async {
      await _pump(tester, _cell());

      expect(
        find.textContaining('Inside the fudge band'),
        findsOneWidget,
      );
      expect(find.text('fudge band +10 h'), findsOneWidget);
      expect(find.text('goal max 260 h'), findsOneWidget);
      expect(find.text('absolute max 280 h'), findsOneWidget);
    });
  });

  group('the export', () {
    testWidgets('refuses a day nobody has signed, and names the Task', (
      tester,
    ) async {
      await _pump(
        tester,
        _cell(
          status: 'pending_approval',
          exportable: false,
          approvedBy: '',
          pendingTaskId: 'task_sched_truss_fab_2026_09_08',
        ),
      );

      final button = tester.widget<FilledButton>(
        find.byKey(ScheduleExportButton.buttonKey),
      );
      expect(button.onPressed, isNull);
      expect(
        find.textContaining(
          'answers 409 while 2026-09-08 is pending approval, naming the open '
          'Task task_sched_truss_fab_2026_09_08',
        ),
        findsOneWidget,
      );
    });

    testWidgets('is live once the day is approved', (tester) async {
      await _pump(tester, _cell());

      final button = tester.widget<FilledButton>(
        find.byKey(ScheduleExportButton.buttonKey),
      );
      expect(button.onPressed, isNotNull);
      expect(
        find.textContaining(
          'GET /api/v1/schedule/export?week=2026-W37&department=truss-fab',
        ),
        findsOneWidget,
      );
      // Nothing is kept: the file is a derived Dataset regenerated on request.
      expect(find.textContaining('stored nowhere'), findsOneWidget);
    });
  });

  group('a cell with no day', () {
    testWidgets('reads as no day opened, never as zero hours', (tester) async {
      await _pump(
        tester,
        ScheduleDayCell.fromJson({
          'department': 'wall-panel',
          'date': '2026-09-09',
          'status': '',
          'exists': false,
        }),
      );

      expect(find.text('no day opened'), findsOneWidget);
      expect(
        find.textContaining('That is not a day scheduled for zero hours'),
        findsOneWidget,
      );
      expect(find.text('0 h'), findsNothing);
      // There is nothing to export from a day that was never opened.
      expect(find.byKey(ScheduleExportButton.buttonKey), findsNothing);
    });
  });

  group('the rows', () {
    testWidgets('a row a person placed is labelled as one', (tester) async {
      await _pump(tester, _cell());

      expect(find.text('placed by a person'), findsOneWidget);
      expect(find.text('placed by the fill'), findsOneWidget);
      // The rule that placed each one, so a supervisor can argue with it.
      expect(find.text('next in load order'), findsOneWidget);
      expect(
        find.text('added by the supervisor after the fill ran'),
        findsOneWidget,
      );
    });

    testWidgets('a deferred share carries its clause and its sentence', (
      tester,
    ) async {
      await _pump(tester, _cell());

      expect(find.text('goal-max-with-fudge'), findsWidgets);
      expect(
        find.textContaining('the day is at the goal plus its fudge'),
        findsOneWidget,
      );
    });
  });

  group('the grid', () {
    testWidgets('draws a row per declared department, days or not', (
      tester,
    ) async {
      await _pumpGrid(tester);

      // wall-panel had no day opened all week. A grid assembled from what
      // exists would have dropped the row, and dropping it is how a plant
      // that scheduled nothing becomes invisible.
      expect(find.text('truss-fab'), findsOneWidget);
      expect(find.text('wall-panel'), findsOneWidget);
      expect(find.text('no day opened'), findsNWidgets(2));
      expect(find.text('Mon 09-07'), findsOneWidget);
      expect(find.text('Tue 09-08'), findsOneWidget);
    });

    testWidgets('selecting a cell opens that day', (tester) async {
      await _pumpGrid(tester);

      // The panel opens on the first day that was actually written.
      expect(find.text('TRUSS-FAB · 2026-09-07'), findsOneWidget);

      await tester.tap(find.text('268 h'));
      await tester.pumpAndSettle();
      expect(find.text('TRUSS-FAB · 2026-09-08'), findsOneWidget);
      expect(find.text('out of the hole'), findsOneWidget);
    });
  });

  group('the week', () {
    test('steps over real dates rather than over week numbers', () {
      expect(shiftIsoWeek('2026-W37', 1), '2026-W38');
      expect(shiftIsoWeek('2026-W37', -1), '2026-W36');
      // 2026 has 53 ISO weeks and 2027 does not, which is the case that week
      // arithmetic done on the number silently gets wrong.
      expect(shiftIsoWeek('2026-W53', 1), '2027-W01');
      expect(shiftIsoWeek('2026-W01', -1), '2025-W52');
    });

    test('refuses a string that is not an ISO week', () {
      expect(shiftIsoWeek('2026-09-07', 1), isNull);
      expect(shiftIsoWeek('', 1), isNull);
      expect(shiftIsoWeek('2026-W99', 1), isNull);
    });
  });
}

// ---------------------------------------------------------------------------

Future<void> _pump(WidgetTester tester, ScheduleDayCell cell) async {
  tester.view.physicalSize = const Size(2400, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(child: ScheduleDayDetail(cell: cell)),
      ),
    ),
  );
}

/// One approved day, over the goal and inside the fudge — the arrangement the
/// governed page says is accepted rather than exceptional.
ScheduleDayCell _cell({
  String status = 'approved',
  bool exportable = true,
  double totalHours = 268,
  double incentivePerHour = 1.4,
  bool outOfTheHole = true,
  String approvedBy = 'dale',
  String pendingTaskId = '',
}) => ScheduleDayCell.fromJson({
  'department': 'truss-fab',
  'date': '2026-09-08',
  'status': status,
  'exists': true,
  'total_hours': totalHours,
  'goal_max_hours': 260.0,
  'abs_max_hours': 280.0,
  'fudge_hours': 10.0,
  'capacity_row': '14 in the crew → 260 goal / 280 absolute',
  'incentive_per_hour': incentivePerHour,
  'incentive_basis': '-\$18.00 + \$0.0724/h × 268 h = \$1.40',
  'incentive_start_usd': -18.0,
  'incentive_rate_usd_per_hour': 0.0724,
  'incentive_crossing_hours': 248.6,
  'out_of_the_hole': outOfTheHole,
  'below_capacity': false,
  'stopped_at': 'goal-max-with-fudge',
  'headcount': 14.0,
  'headcount_as_of': '2026-09-01',
  'headcount_stale': false,
  'entries': [
    {
      'job_number': '260118',
      'job_label': 'Cedar Ridge Lot 42',
      'labor_hours': 42.0,
      'basis': 'next in load order',
      'sequence': 1.0,
      'row_kind': 'job',
      'vehicle_ref': 'TR-3',
      'placed_by': 'flow',
      'object_ref': 'graph:schedule_entry/260118@1',
    },
    {
      'job_number': '260131',
      'job_label': 'Harbour Lot 2',
      'labor_hours': 18.0,
      'basis': 'added by the supervisor after the fill ran',
      'sequence': 2.0,
      'row_kind': 'job',
      'vehicle_ref': '',
      'placed_by': 'human',
      'object_ref': 'graph:schedule_entry/260131@1',
    },
  ],
  'deferred': [
    {
      'job_number': '260144',
      'clause': 'goal-max-with-fudge',
      'reason': 'the day is at the goal plus its fudge',
      'labor_hours': 36.0,
    },
  ],
  'approved_by': approvedBy,
  'approved_at': approvedBy.isEmpty ? '' : '2026-09-07T13:40:02Z',
  'pending_task_id': pendingTaskId,
  'run_id': 'run_ds_41c',
  'exportable': exportable,
  'export_url': '/api/v1/schedule/export?week=2026-W37&department=truss-fab',
  'object_ref': 'graph:schedule_day/truss-fab-2026-09-08@2',
});

/// One week, two departments, one of which was never scheduled.
Future<void> _pumpGrid(WidgetTester tester) async {
  tester.view.physicalSize = const Size(2400, 3600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  final screen = PlantScheduleScreen.fromJson({
    'week': '2026-W37',
    'departments': ['truss-fab', 'wall-panel'],
    'dates': ['2026-09-07', '2026-09-08'],
    'cells': <Map<String, dynamic>>[],
    'stats': {
      'days': 2,
      'pending_approval': 1,
      'approved': 1,
      'deferred': 1,
      'below_capacity': 0,
    },
    'policy_clauses': <Map<String, dynamic>>[],
    'policy_ref': 'knowledge:scheduling-parameters@6',
    'policy_found': true,
  });
  final cells = <ScheduleDayCell>[
    _cellOn('truss-fab', '2026-09-07',
        status: 'pending_approval', exportable: false, totalHours: 244),
    _cellOn('truss-fab', '2026-09-08'),
    _absent('wall-panel', '2026-09-07'),
    _absent('wall-panel', '2026-09-08'),
  ];
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: PlantScheduleBody(
          screen: PlantScheduleScreen(
            week: screen.week,
            departments: screen.departments,
            dates: screen.dates,
            cells: cells,
            stats: screen.stats,
            policyClauses: screen.policyClauses,
            policyRef: screen.policyRef,
            policyFound: screen.policyFound,
          ),
          heading: const PageHeading(
            eyebrow: 'Workspace',
            title: 'Plant Schedule',
          ),
        ),
      ),
    ),
  );
}

ScheduleDayCell _cellOn(
  String department,
  String date, {
  String status = 'approved',
  bool exportable = true,
  double totalHours = 268,
}) {
  final base = _cell(
    status: status,
    exportable: exportable,
    totalHours: totalHours,
    outOfTheHole: totalHours > 248.6,
  );
  return ScheduleDayCell.fromJson({
    'department': department,
    'date': date,
    'status': base.status,
    'exists': true,
    'total_hours': base.totalHours,
    'goal_max_hours': base.goalMaxHours,
    'abs_max_hours': base.absMaxHours,
    'fudge_hours': base.fudgeHours,
    'capacity_row': base.capacityRow,
    'incentive_per_hour': base.incentivePerHour,
    'incentive_basis': base.incentiveBasis,
    'incentive_start_usd': base.incentiveStartUsd,
    'incentive_rate_usd_per_hour': base.incentiveRateUsdPerHour,
    'incentive_crossing_hours': base.incentiveCrossingHours,
    'out_of_the_hole': base.outOfTheHole,
    'below_capacity': base.belowCapacity,
    'stopped_at': base.stoppedAt,
    'headcount': base.headcount,
    'headcount_as_of': base.headcountAsOf,
    'headcount_stale': base.headcountStale,
    'entries': <Map<String, dynamic>>[],
    'deferred': <Map<String, dynamic>>[],
    'approved_by': base.approvedBy,
    'approved_at': base.approvedAt,
    'pending_task_id': base.pendingTaskId,
    'run_id': base.runId,
    'exportable': base.exportable,
    'export_url': base.exportUrl,
    'object_ref': base.objectRef,
  });
}

ScheduleDayCell _absent(String department, String date) =>
    ScheduleDayCell.fromJson({
      'department': department,
      'date': date,
      'status': '',
      'exists': false,
    });
