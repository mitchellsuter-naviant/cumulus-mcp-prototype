import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/tasks/compliance/models.dart';
import 'package:cumulus_admin/tasks/compliance/workspace.dart';
import 'package:cumulus_admin/widgets/ui_kit.dart';

/// The Compliance workspace, and the three things a spreadsheet loses.
///
/// The rows arrive in the order the sweep works them — days to expiry
/// ascending, dates nobody could read last — and a screen that re-sorted by
/// default would be substituting its own opinion about urgency for the
/// governed one. The notice trail has to distinguish a stage that was sent
/// from a stage that is owed, or it cannot say whether anything is still
/// outstanding. And an untracked unit is not an overdue one: it is excluded at
/// the unit, so it belongs in its own list and nowhere near the expiry table,
/// where a supervisor would go looking for a lapsed inspection that never
/// existed.
void main() {
  group('the expiry table', () {
    testWidgets('is rendered in the order it was served', (tester) async {
      await _pump(tester, _screen());

      final served = tester
          .widgetList<ComplianceRowTile>(find.byType(ComplianceRowTile))
          .map((tile) => tile.row.subject)
          .toList();
      expect(served, [
        'Marisol Vega', // 12 days
        'Dale Mattson', // 44 days
        'Yard loader 3', // unreadable, and therefore last
      ]);
    });

    testWidgets('a date nobody could read is words, never a zero', (
      tester,
    ) async {
      await _pump(tester, _screen());

      expect(find.text('date could not be read'), findsOneWidget);
      // The failure this guards against: `null` rendered through a numeric
      // formatter, which files "nobody could read this" under "expires
      // today".
      expect(find.text('0 day(s) to expiry'), findsNothing);
      expect(find.text('expires today'), findsNothing);
      // And the issuing system's own text is kept, because it is the only
      // evidence for why the sweep could not read it either.
      expect(find.text('19//2026'), findsOneWidget);
    });

    testWidgets('the stages sent are distinguishable from the stages owed', (
      tester,
    ) async {
      await _pump(tester, _screen());

      // One row carries the 60- and 30-day notices; every row shows the
      // 15-day stage the page declares and nobody has sent.
      expect(find.text('60d sent'), findsOneWidget);
      expect(find.text('30d sent'), findsOneWidget);
      expect(find.text('15d'), findsNWidgets(3));
      expect(find.text('60d'), findsNWidgets(2));
    });
  });

  group('untracked units', () {
    testWidgets('are named as untracked and kept out of the table', (
      tester,
    ) async {
      await _pump(tester, _screen());

      final untracked = find.byKey(complianceUntrackedKey);
      expect(untracked, findsOneWidget);
      expect(
        find.descendant(
          of: untracked,
          matching: find.textContaining('Yard crane 2 · untracked'),
        ),
        findsOneWidget,
      );
      // Its own sentence for why, off the governed page rather than invented
      // here.
      expect(
        find.descendant(
          of: untracked,
          matching: find.textContaining(
            'the register holds no next-due date for this unit',
          ),
        ),
        findsOneWidget,
      );
      // And the whole point: it is not a row in the expiry table.
      expect(
        find.descendant(
          of: find.byKey(complianceTableKey),
          matching: find.textContaining('Yard crane 2'),
        ),
        findsNothing,
      );
      expect(
        find.descendant(
          of: untracked,
          matching: find.textContaining('NOT overdue'),
        ),
        findsOneWidget,
      );
    });
  });

  group('retention', () {
    testWidgets('says the horizon and that nothing is deleted', (tester) async {
      await _pump(tester, _screen());

      expect(find.textContaining('Retention horizon: 2019-09-07'), findsOneWidget);
      expect(find.textContaining('Nothing is deleted'), findsOneWidget);
    });
  });
}

// ---------------------------------------------------------------------------

Future<void> _pump(WidgetTester tester, ComplianceScreen screen) async {
  tester.view.physicalSize = const Size(2400, 3200);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: ComplianceBody(
          screen: screen,
          heading: const PageHeading(
            eyebrow: 'Workspace',
            title: 'Compliance',
          ),
        ),
      ),
    ),
  );
}

ComplianceScreen _screen() => ComplianceScreen.fromJson({
  // Served in the sweep's own order. Nothing below re-sorts it, which is the
  // property the first test holds.
  'rows': [
    {
      'kind': 'credential',
      'subject': 'Marisol Vega',
      'subject_kind': 'employee',
      'credential_type': 'forklift certification',
      'category': 'equipment',
      'expires_on': '2026-09-19',
      'days_to_expiry': 12,
      'status': 'expiring',
      'last_notified_on': '2026-09-04',
      'last_notice_stage': 30,
      'stages_sent': [60, 30],
      'supervisor': 'Dale Mattson',
      'exception': '',
      'retain_until': '2033-09-19',
      'object_ref': 'graph:credential/vega-forklift@3',
    },
    {
      'kind': 'credential',
      'subject': 'Dale Mattson',
      'subject_kind': 'employee',
      'credential_type': 'first aid',
      'category': 'safety',
      'expires_on': '2026-10-21',
      'days_to_expiry': 44,
      'status': 'current',
      'last_notified_on': '',
      'last_notice_stage': null,
      'stages_sent': <int>[],
      'supervisor': 'Ann Pyle',
      'exception': '',
      'retain_until': '2033-10-21',
      'object_ref': 'graph:credential/mattson-first-aid@1',
    },
    {
      'kind': 'inspection',
      'subject': 'Yard loader 3',
      'subject_kind': 'equipment',
      'credential_type': 'annual inspection',
      'category': 'lifting',
      // Verbatim, because a date nothing could parse is the evidence.
      'expires_on': '19//2026',
      'days_to_expiry': null,
      'status': 'unknown',
      'last_notified_on': '',
      'last_notice_stage': null,
      'stages_sent': <int>[],
      'supervisor': 'Ann Pyle',
      'exception': 'unreadable_date',
      'retain_until': '',
      'object_ref': 'graph:equipment/EQ-3310@5',
    },
  ],
  'untracked_assets': [
    {
      'equipment_id': 'EQ-4471',
      'name': 'Yard crane 2',
      'category': 'lifting',
      'home_site': 'kaukauna',
      'why': 'the register holds no next-due date for this unit',
      'object_ref': 'graph:equipment/EQ-4471@2',
    },
  ],
  'stats': {
    'rows': 3,
    'expiring_60': 1,
    'expiring_30': 1,
    'expiring_15': 0,
    'expired': 0,
    'untracked': 1,
    'exceptions_by_class': {
      'unreadable_date': 1,
      'sla_floor': 0,
      'no_supervisor': 0,
      'past_retention': 2,
    },
    'retention_flagged': 2,
  },
  'policy_clauses': [
    {
      'id': 'stages',
      'label': 'Notice stages',
      'value': '60 day, 30 day, 15 day',
      'ref': 'knowledge:credential-alert-policy@2',
    },
    {
      'id': 'retention',
      'label': 'Retention',
      'value': 'kept seven years past expiry, never deleted',
      'ref': 'knowledge:credential-alert-policy@2',
    },
  ],
  'exception_task_ids': ['task_cred_ex_18'],
  'retention_horizon': '2019-09-07',
  'policy_ref': 'knowledge:credential-alert-policy@2',
  'policy_found': true,
});
