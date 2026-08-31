import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/data_layer.dart';
import 'package:cumulus_admin/widgets/sources_plane.dart';

/// The four states a pulled feed's row has to be legible in, and the one an
/// unpulled feed keeps.
///
/// They are asserted here rather than by looking at the screen because three
/// of them are states the runs cannot produce: a feed that has never pulled
/// has no run, a pull that found nothing starts none, and a pull that failed
/// leaves none behind. A row assembled only from runs draws all three as a
/// feed that has simply gone quiet, and every one of them is a different thing
/// for an operator to do next.
void main() {
  group('SourceFeedRow', () {
    testWidgets('a registered feed that has never pulled says so, and offers '
        'the pull that would start it', (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(pull: _pull()),
        onSyncNow: () {},
      ));

      expect(find.text('· never pulled'), findsOneWidget);
      expect(find.text('never delivered'), findsOneWidget);
      expect(
        find.text('Nothing delivered yet — no run, and nothing landed.'),
        findsOneWidget,
      );
      // The consequence, before the press: a first pull has no watermark to
      // read from, so it reads the whole feed.
      expect(find.text('first pull reads everything'), findsOneWidget);
      expect(_syncButton(tester).onPressed, isNotNull);
    });

    testWidgets('a pulled feed carries its cursor and how long ago it ran',
        (tester) async {
      final when = DateTime.now().subtract(const Duration(minutes: 3));
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          deliveries: 2,
          recordsLanded: 17,
          lastRunAtIso: when.toIso8601String(),
          lastStatus: 'succeeded',
          pull: _pull(
            lastPullAt: when.toIso8601String(),
            lastOutcome: 'delivered',
            watermarks: const {
              'departments': '2026-08-16T22:16:24.438947+00:00',
              'jobs': '2026-08-16T22:16:21.929959+00:00',
              'employees': '2026-08-29T20:42:09.970713+00:00',
            },
          ),
        ),
        onSyncNow: () {},
      ));

      expect(find.text('· pulled 3m ago'), findsOneWidget);
      expect(find.text('· delivered'), findsOneWidget);
      // One cursor per resource, because they advance independently and the
      // next pull asks each route from its own. Collapsing them to the
      // furthest stated a position two of the three have not reached — and
      // then the Sync now caption promised to read from it.
      expect(
        find.text('· employees up to 2026-08-29T20:42:09.970713+00:00 (UTC)'),
        findsOneWidget,
      );
      expect(
        find.text('· jobs up to 2026-08-16T22:16:21.929959+00:00 (UTC)'),
        findsOneWidget,
      );
      expect(
        find.text('· departments up to 2026-08-16T22:16:24.438947+00:00 (UTC)'),
        findsOneWidget,
      );
      expect(find.text('each route since its own cursor'), findsOneWidget);
    });

    testWidgets('a single-resource feed still states one cursor, in UTC',
        (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          deliveries: 1,
          recordsLanded: 14,
          lastStatus: 'succeeded',
          pull: _pull(
            lastPullAt: DateTime.now().toIso8601String(),
            lastOutcome: 'delivered',
            watermarks: const {
              'vendors': '2026-08-29T20:42:09.970713+00:00',
            },
          ),
        ),
        onSyncNow: () {},
      ));

      expect(
        find.text('· cursor 2026-08-29T20:42:09.970713+00:00 (UTC)'),
        findsOneWidget,
      );
      // Trimming for the caption is what dropped the offset, so the short form
      // says which zone it is in — a reader in Madison had every reason to
      // read `2026-08-29 20:42` as local.
      expect(find.text('since 2026-08-29 20:42 UTC'), findsOneWidget);
    });

    testWidgets('a scheduled next pull is stated in the tense it is in',
        (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          deliveries: 1,
          recordsLanded: 3,
          lastStatus: 'succeeded',
          pull: _pull(
            lastPullAt: DateTime.now().toIso8601String(),
            lastOutcome: 'delivered',
            nextPullAt: DateTime.now()
                .add(const Duration(minutes: 14, seconds: 30))
                .toIso8601String(),
          ),
        ),
        onSyncNow: () {},
      ));

      // `agoLabel` rendered this as "next just now" and, once the clock passed
      // it, "next 3m ago" — a future stated as a past.
      expect(find.text('· next pull in 14m'), findsOneWidget);
    });

    testWidgets('a bind feed reports no disposition rather than three zeros',
        (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          deliveries: 1,
          records: 145,
          recordsLanded: 145,
          lastStatus: 'succeeded',
        ),
        onSyncNow: () {},
      ));

      expect(
        find.text(
          '1 delivery(s) · 145 received · 145 newly landed · '
          'disposition not reported by this mapping',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a reify feed keeps a fraction of what it carries, and says '
        'both numbers', (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          source: 'psa-book',
          deliveries: 1,
          records: 374,
          recordsLanded: 13,
          created: 6,
          linked: 1,
          unchanged: 0,
          lastStatus: 'succeeded',
        ),
        onSyncNow: () {},
      ));

      // `landed` means new landing records here and every bound row on a bind
      // feed, so one number could not mean the same thing twice.
      expect(
        find.text(
          '1 delivery(s) · 374 received · 13 newly landed · '
          '0 unchanged (byte-identical redelivery) · '
          '1 linked to an existing entity · 6 minted',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a failed pull prints what the estate said, verbatim',
        (tester) async {
      const wire =
          'GET https://cumulus-demo-mitchell-suter-hris.web.app/api/naviant/'
          'v1/employees → 503';
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          pull: _pull(
            lastPullAt: DateTime.now().toIso8601String(),
            lastOutcome: 'failed',
            lastError: wire,
          ),
        ),
        onSyncNow: () {},
      ));

      expect(find.text('Last pull failed: $wire'), findsOneWidget);
      // Still pressable: the cursor did not advance, so a retry re-asks the
      // same window rather than skipping it.
      expect(_syncButton(tester).onPressed, isNotNull);
    });

    testWidgets('a pull in flight disables its own control and says what it '
        'is doing', (tester) async {
      var pressed = 0;
      await _pump(tester, SourceFeedRow(
        feed: _feed(pull: _pull()),
        syncing: true,
        onSyncNow: () => pressed++,
      ));

      expect(find.text('Syncing…'), findsOneWidget);
      expect(find.text('Sync now'), findsNothing);
      expect(find.text('pulling 3 route(s)'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(_syncButton(tester).onPressed, isNull);

      await tester.tap(find.bySubtype<OutlinedButton>(), warnIfMissed: false);
      expect(pressed, 0);
    });

    testWidgets('the outcome of the last pull is reported on the row itself',
        (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(recordsLanded: 17, deliveries: 1, pull: _pull()),
        note: SyncNote.of(
          SyncNowOutcome(
            source: 'hris-workforce',
            outcome: 'delivered',
            pulled: const {'departments': 7, 'jobs': 5, 'employees': 10},
            delivered: 22,
            chunks: 1,
            error: '',
            row: _feed(recordsLanded: 17, deliveries: 1, pull: _pull()),
          ),
          _feed(pull: _pull()),
        ),
        onSyncNow: () {},
      ));

      // The delta, not a second total: the row under it says 17 landed, and
      // this pull is what put them there. No "0 unchanged": this feed runs no
      // hash check, so there is no such count to take a delta of.
      expect(
        find.text(
          'Pulled 22 record(s) (7 departments · 5 jobs · 10 employees) '
          '· 17 newly landed',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a feed that does suppress redeliveries reports the delta',
        (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(source: 'erp-vendors', recordsLanded: 14, deliveries: 1),
        note: SyncNote.of(
          SyncNowOutcome(
            source: 'erp-vendors',
            outcome: 'delivered',
            pulled: const {'vendors': 14},
            delivered: 14,
            chunks: 1,
            error: '',
            row: _feed(
              source: 'erp-vendors',
              recordsLanded: 14,
              unchanged: 2,
              deliveries: 1,
            ),
          ),
          _feed(source: 'erp-vendors', unchanged: 0),
        ),
        onSyncNow: () {},
      ));

      expect(
        find.text(
          'Pulled 14 record(s) (14 vendors) · 14 newly landed, 2 unchanged',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a pull that changed nothing is not a pull that failed',
        (tester) async {
      final note = SyncNote.of(
        const SyncNowOutcome(
          source: 'hris-workforce',
          outcome: 'unchanged',
          pulled: {'departments': 0, 'jobs': 0, 'employees': 0},
          delivered: 0,
          chunks: 0,
          error: '',
          row: null,
        ),
        _feed(pull: _pull()),
      );

      expect(note.failed, isFalse);
      await _pump(tester, SourceFeedRow(
        feed: _feed(pull: _pull()),
        note: note,
        onSyncNow: () {},
      ));
      expect(
        find.text(
          'Nothing has changed since the last watermark, so nothing was '
          'delivered.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a persona that may not pull reads the reason, not a button '
        'that would refuse', (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          pull: _pull(
            syncNowAllowed: false,
            syncNowRefusal: 'Pulling a feed delivers through the door, which '
                'writes. That needs admin authority.',
          ),
        ),
        onSyncNow: () {},
      ));

      expect(find.bySubtype<OutlinedButton>(), findsNothing);
      expect(
        find.text(
          'Pulling a feed delivers through the door, which writes. That '
          'needs admin authority.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a feed that pushes is unchanged — no cursor, no control',
        (tester) async {
      await _pump(tester, SourceFeedRow(
        feed: _feed(
          source: 'erp-vendors',
          kind: 'vendors',
          flowId: 'vendor-sync',
          deliveries: 3,
          recordsLanded: 12,
          lastStatus: 'succeeded',
          lastRunAtIso: DateTime.now().toIso8601String(),
          pull: null,
        ),
      ));

      expect(find.bySubtype<OutlinedButton>(), findsNothing);
      expect(find.textContaining('pull ·'), findsNothing);
      expect(find.textContaining('never pulled'), findsNothing);
      expect(find.text('vendor-sync'), findsOneWidget);
      expect(find.text('last delivery'), findsOneWidget);
    });
  });

  group('EstateHeaderCard', () {
    testWidgets('no estate registered is a state with a next step in it, not '
        'an alarm', (tester) async {
      await _pump(tester, const EstateHeaderCard(estate: EstateCard.none));

      expect(find.text('NO ESTATE IS REGISTERED'), findsOneWidget);
      expect(
        find.text('nothing is pulled — every feed below was pushed here'),
        findsOneWidget,
      );
      expect(find.textContaining('ESTATE_PROJECT'), findsOneWidget);
    });

    testWidgets('a registered estate reports what discovery answered',
        (tester) async {
      await _pump(tester, const EstateHeaderCard(estate: EstateCard(
        registered: true,
        project: 'cumulus-demo-mitchell-suter',
        namespace: 'naviant',
        discoveryUrl:
            'https://cumulus-demo-mitchell-suter-admin.web.app/admin/namespaces',
        authenticated: false,
        pullIntervalSeconds: 0,
        discoveryError: '',
        industry: 'software-reseller',
        apps: ['crm', 'erp', 'office', 'hris', 'dms'],
        processes: ['employee_lifecycle', 'deal_to_order', 'procure_to_pay'],
        pulseEnabled: true,
        pulseIntensity: 'low',
        feedsRegistered: 1,
        feedsActive: 1,
      )));

      expect(find.text('ESTATE · NAVIANT'), findsOneWidget);
      expect(find.text('cumulus-demo-mitchell-suter'), findsOneWidget);
      expect(find.text('1 of 1 feeds active'), findsOneWidget);
      expect(
        find.text('5 apps · crm, erp, office, hris, dms'),
        findsOneWidget,
      );
      // "pulse on · low" and "3 processes running" both read as operational
      // health. What they say is that everything arriving from this estate is
      // fabricated on a timer, and the pill has to lead with that.
      expect(
        find.text(
          'simulated estate · 3 process scripts · synthetic activity: low',
        ),
        findsOneWidget,
      );
      expect(find.text('3 processes running'), findsNothing);
      // A fact about this process, not about the customer's system: it is
      // `bool(ESTATE_API_KEY)`, and "unauthenticated estate" described the
      // estate as open to the world.
      expect(find.text('no credential configured'), findsOneWidget);
      // No warm component of our own: absent a loop, the cadence belongs to
      // whatever is doing the calling.
      expect(
        find.text('pulled on demand — no loop of our own'),
        findsOneWidget,
      );
    });

    testWidgets('an unreachable estate is reported as itself, not as an '
        'estate that runs no apps', (tester) async {
      await _pump(tester, const EstateHeaderCard(estate: EstateCard(
        registered: true,
        project: 'cumulus-demo-mitchell-suter',
        namespace: 'naviant',
        discoveryUrl: 'https://…/admin/namespaces',
        authenticated: false,
        pullIntervalSeconds: 0,
        discoveryError: 'GET https://…/admin/namespaces → 503',
        industry: '',
        apps: [],
        processes: [],
        pulseEnabled: null,
        pulseIntensity: '',
        feedsRegistered: 1,
        feedsActive: 0,
      )));

      expect(
        find.text(
          'Discovery failed, so what this namespace runs is unknown: '
          'GET https://…/admin/namespaces → 503',
        ),
        findsOneWidget,
      );
      expect(find.text('no apps discovered'), findsOneWidget);
      expect(find.text('0 of 1 feeds active'), findsOneWidget);
      // A pulse it never reported is not a pulse that is off.
      expect(find.textContaining('pulse'), findsNothing);
    });
  });
}

Future<void> _pump(WidgetTester tester, Widget child) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(child: SizedBox(width: 900, child: child)),
    ),
  ));
}

OutlinedButton _syncButton(WidgetTester tester) =>
    tester.widget<OutlinedButton>(find.bySubtype<OutlinedButton>());

PullState _pull({
  bool active = true,
  String lastPullAt = '',
  String lastOutcome = '',
  String lastError = '',
  String nextPullAt = '',
  Map<String, String> watermarks = const {},
  bool syncNowAllowed = true,
  String syncNowRefusal = '',
}) => PullState(
  active: active,
  estateApp: 'hris',
  namespace: 'naviant',
  resources: const ['departments', 'jobs', 'employees'],
  watermarks: watermarks,
  lastPullAt: lastPullAt,
  lastOutcome: lastOutcome,
  lastError: lastError,
  nextPullAt: nextPullAt,
  syncNowAllowed: syncNowAllowed,
  syncNowRefusal: syncNowRefusal,
);

SourceFeed _feed({
  String source = 'hris-workforce',
  String kind = '',
  String flowId = '',
  String lastRunAtIso = '',
  String lastStatus = '',
  int deliveries = 0,
  int? records,
  int recordsLanded = 0,
  // Null by default, because `hris-workforce` is a bind feed and a bind feed
  // never reports a disposition. Passing zeros here is exactly the coercion
  // the wire stopped doing.
  int? unchanged,
  int? linked,
  int? created,
  int awaitingSteward = 0,
  PullState? pull,
}) => SourceFeed(
  source: source,
  kind: kind,
  flowId: flowId,
  lastRunId: '',
  lastRunAtIso: lastRunAtIso,
  lastStatus: lastStatus,
  lastError: '',
  watermark: '',
  deliveries: deliveries,
  records: records ?? recordsLanded,
  recordsLanded: recordsLanded,
  linked: linked,
  created: created,
  awaitingSteward: awaitingSteward,
  unchanged: unchanged,
  contract: null,
  pull: pull,
  artifacts: const [],
  artifactsTotal: 0,
);
