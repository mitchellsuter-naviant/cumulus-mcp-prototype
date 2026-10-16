import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/tasks/field_documents/models.dart';
import 'package:cumulus_admin/tasks/field_documents/workspace.dart';
import 'package:cumulus_admin/widgets/ui_kit.dart';

/// The Field Documents workspace, and the three claims it would be dishonest
/// without.
///
/// A packet is not a folder, and every instinct a reader brings to a document
/// screen is the folder one: that a revision replaced what came before it, and
/// that reassigning a job moved its documents somewhere else. Neither act
/// exists here, so the screen has to say so in what it draws — the governed
/// types each carry the revision standing today, the crew holding the job now
/// and the crews that held it before are both present and cannot be mistaken
/// for one another, and a superseded revision says the word rather than
/// disappearing.
void main() {
  group('the packet', () {
    testWidgets('renders every governed type with its version', (tester) async {
      await _pump(tester, _screen());
      await _open(tester);

      final detail = find.byType(PacketDetail);
      for (final kind in const [
        'truss placement plan',
        'site map',
        'crew hours sheet',
        'load ticket',
      ]) {
        expect(
          find.descendant(of: detail, matching: find.text(kind)),
          findsOneWidget,
          reason: '$kind is a governed type and belongs on the packet',
        );
      }

      // Three are filed at a revision; the fourth is not filed at all, which
      // is a normal Tuesday and never a Task on its own.
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining('260118-tpp.pdf · version 2'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining('260118-map.pdf · version 1'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining('260118-hours.pdf · version 3'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(of: detail, matching: find.text('not filed')),
        findsOneWidget,
      );
    });

    testWidgets('shows the crew now and the crews before, apart', (
      tester,
    ) async {
      await _pump(tester, _screen());
      await _open(tester);

      final detail = find.byType(PacketDetail);
      // Two labels, not one list: "who has it" and "who had it" are different
      // questions, and a single roster answers neither.
      expect(
        find.descendant(of: detail, matching: find.text('assigned now')),
        findsOneWidget,
      );
      expect(
        find.descendant(of: detail, matching: find.text('held until')),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining('Crew B · foreman Dale Mattson'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining(
            'Crew A held this job until 2026-09-02T13:20:00Z',
          ),
        ),
        findsOneWidget,
      );
      // And the reason it is still on screen at all.
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining('Nothing was deleted'),
        ),
        findsOneWidget,
      );
    });

    testWidgets('a replaced revision reads as superseded, not as absent', (
      tester,
    ) async {
      await _pump(tester, _screen());
      await _open(tester);

      final detail = find.byType(PacketDetail);
      expect(
        find.descendant(of: detail, matching: find.text('superseded')),
        findsOneWidget,
      );
      // The old revision keeps its filename and its version, because a crew
      // holding a printed copy has to be able to find out what they have.
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining(
            'truss placement plan · 260118-tpp.pdf · version 1',
          ),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining('kept, not removed'),
        ),
        findsOneWidget,
      );
    });

    testWidgets('an unresolved crew reads as an identity question', (
      tester,
    ) async {
      await _pump(tester, _screen());
      await _open(tester, label: '260144 - Harbour Lot 9');

      // The packet on that job is complete. The red badge means nobody can
      // say who is carrying it — a document screen's most available reading
      // of red is the one that would be wrong here.
      final detail = find.byType(PacketDetail);
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining(
            'an identity question, not a document one',
          ),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: detail,
          matching: find.textContaining(
            'No document is missing on account of this',
          ),
        ),
        findsOneWidget,
      );
    });
  });
}

// ---------------------------------------------------------------------------

/// The test font draws every glyph as a square of the font size, so a row of
/// prose is several times wider here than in a browser. The surface is set
/// wide enough that the table's own column widths are not the thing under
/// test.
Future<void> _pump(WidgetTester tester, FieldDocumentsScreen screen) async {
  tester.view.physicalSize = const Size(2400, 3000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: FieldDocumentsBody(
          screen: screen,
          heading: const PageHeading(
            eyebrow: 'Workspace',
            title: 'Field Documents',
          ),
        ),
      ),
    ),
  );
}

/// Open a packet. The row is a disclosure, so everything the packet claims is
/// one tap from the table.
Future<void> _open(
  WidgetTester tester, {
  String label = '260118 - Cedar Ridge Lot 42',
}) async {
  await tester.tap(find.text(label));
  await tester.pumpAndSettle();
}

FieldDocumentsScreen _screen() => FieldDocumentsScreen.fromJson({
  'jobs': [
    {
      'job_number': '260118',
      'packet_label': '260118 - Cedar Ridge Lot 42',
      'label_rule':
          'the job number, then the customer and lot, per the convention',
      'customer': 'Cedar Ridge Homes',
      'builder': 'Vantage Builders',
      'plant_site': 'kaukauna',
      'status': 'scheduled',
      'load_date': '2026-09-14',
      'current_crew': {
        'team_slug': 'crew-b',
        'name': 'Crew B',
        'foreman': 'Dale Mattson',
      },
      'former_crews': [
        {
          'team_slug': 'crew-a',
          'name': 'Crew A',
          'until_iso': '2026-09-02T13:20:00Z',
        },
      ],
      'unresolved_crew_id': '',
      'documents': [
        {
          'kind': 'truss placement plan',
          'filename': '260118-tpp.pdf',
          'version': '2',
          'filed_at': '2026-09-03T10:02:00Z',
          'superseded': false,
        },
        {
          'kind': 'truss placement plan',
          'filename': '260118-tpp.pdf',
          'version': '1',
          'filed_at': '2026-08-28T09:14:00Z',
          'superseded': true,
        },
        {
          'kind': 'site map',
          'filename': '260118-map.pdf',
          'version': '1',
          'filed_at': '2026-08-30T11:41:00Z',
          'superseded': false,
        },
        {
          'kind': 'crew hours sheet',
          'filename': '260118-hours.pdf',
          'version': '3',
          'filed_at': '2026-09-05T16:20:00Z',
          'superseded': false,
        },
      ],
      'missing': ['load ticket'],
      'complete': false,
      'empty_windows': 0,
      'last_run_id': 'run_fd_9c1',
      'last_run_at_iso': '2026-09-06T06:00:00Z',
      'object_ref': 'graph:job/260118@7',
    },
    {
      'job_number': '260144',
      'packet_label': '260144 - Harbour Lot 9',
      'label_rule':
          'the job number, then the customer and lot, per the convention',
      'customer': 'Harbour Homes',
      'builder': 'Vantage Builders',
      'plant_site': 'kaukauna',
      'status': 'scheduled',
      'load_date': '2026-09-16',
      'current_crew': null,
      'former_crews': [],
      'unresolved_crew_id': 'CRW-77',
      'documents': [
        {
          'kind': 'truss placement plan',
          'filename': '260144-tpp.pdf',
          'version': '1',
          'filed_at': '2026-09-04T08:00:00Z',
          'superseded': false,
        },
        {
          'kind': 'site map',
          'filename': '260144-map.pdf',
          'version': '1',
          'filed_at': '2026-09-04T08:02:00Z',
          'superseded': false,
        },
        {
          'kind': 'crew hours sheet',
          'filename': '260144-hours.pdf',
          'version': '1',
          'filed_at': '2026-09-05T08:02:00Z',
          'superseded': false,
        },
        {
          'kind': 'load ticket',
          'filename': '260144-load.pdf',
          'version': '1',
          'filed_at': '2026-09-05T08:04:00Z',
          'superseded': false,
        },
      ],
      'missing': [],
      'complete': true,
      'empty_windows': 0,
      'last_run_id': 'run_fd_9c1',
      'last_run_at_iso': '2026-09-06T06:00:00Z',
      'object_ref': 'graph:job/260144@3',
    },
  ],
  'crews': [
    {
      'team_slug': 'crew-b',
      'name': 'Crew B',
      'foreman': 'Dale Mattson',
      'jobs': 6,
      'complete': 4,
    },
  ],
  'stats': {
    'jobs': 2,
    'in_horizon': 2,
    'packets_complete': 1,
    'missing': 1,
    'unresolved_crews': 1,
  },
  'policy_clauses': [
    {
      'id': 'document-types',
      'label': 'Document types',
      'value':
          'truss placement plan, site map, crew hours sheet, load ticket',
      'ref': 'knowledge:field-document-policy@4',
    },
  ],
  'gap_task_ids': <String>[],
  'policy_ref': 'knowledge:field-document-policy@4',
  'policy_found': true,
});
