import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/dashboard.dart';
import 'package:cumulus_admin/widgets/system_state.dart';

/// The tile states that have to be legible, and every one of them is a state
/// where a plausible-looking number would be a lie.
///
/// - A conformance count a tenant may not read is `—` with the reason, never a
///   reassuring zero.
/// - A spend figure over calls nobody priced is `—` with the reason, never
///   `$0.00`.
/// - A pull that found no delta and a row that arrived byte-identical share the
///   word *unchanged* and are different facts, so they never share a phrase.
/// - Fewer entities than records is the natural key working, and the row says
///   so — unless the book is shared, in which case the comparison means
///   nothing and the sentence is withheld.
/// - A desk with work and nobody bound is a finding; a desk with nobody bound
///   and nothing on it is not.
/// - A sweep that never ran is stated, never drawn as a sweep that found
///   nothing.
void main() {
  group('MapPanel', () {
    testWidgets('a tenant reads why conformance is withheld, not a zero',
        (tester) async {
      await _pump(tester, MapPanel(map: _map(invariants: null)));

      expect(find.text('—'), findsOneWidget);
      expect(
        find.textContaining('withheld — a TBox violation is a Naviant bug'),
        findsOneWidget,
      );
    });

    testWidgets('a clean graph says it conforms', (tester) async {
      await _pump(tester, MapPanel(map: _map(invariants: 0, kinds: const {})));

      expect(
        find.text('the graph conforms to its own ontology'),
        findsOneWidget,
      );
    });

    testWidgets('violations are named by kind, not just counted',
        (tester) async {
      await _pump(tester, MapPanel(
        map: _map(invariants: 3, kinds: const {'orphan': 2, 'weak-key': 1}),
      ));

      expect(find.text('3'), findsWidgets);
      expect(find.text('2 orphan · 1 weak-key'), findsOneWidget);
    });

    testWidgets('an empty map is a book with nothing in it, not a broken one',
        (tester) async {
      await _pump(tester, MapPanel(
        map: const MapState(
          objects: 0,
          links: 0,
          conflicted: 0,
          collections: [
            CollectionState(
              id: 'workforce',
              label: 'Workforce',
              role: 'data',
              origin: 'synced',
              custody: 'copy',
              retention: 'indefinite',
              authoritative: true,
              types: [
                TypeState(
                  name: 'Employee',
                  label: 'Employee',
                  instances: 0,
                  conflicted: 0,
                ),
              ],
              objects: 0,
              conflicted: 0,
            ),
          ],
          invariants: 0,
          invariantKinds: {},
        ),
      ));

      expect(
        find.text(
          'Nothing has landed in the map yet. Every Collection below is '
          'declared and empty — the shape of the book, without the book.',
        ),
        findsOneWidget,
      );
      // The Collection is still drawn: the shape of the book is declared.
      expect(find.text('Workforce'), findsOneWidget);
      expect(find.text('Employee 0'), findsOneWidget);
    });

    testWidgets('a Collection carries its governed properties beside its count',
        (tester) async {
      await _pump(tester, MapPanel(map: _map()));

      expect(find.text('Employee 135 · Team 9'), findsOneWidget);
      expect(find.text('authoritative'), findsOneWidget);
      expect(
        find.text('custody copy · retained indefinitely'),
        findsOneWidget,
      );
      expect(find.text('2 in conflict'), findsOneWidget);
    });
  });

  group('FeedHealthRow', () {
    testWidgets('a pull that found no delta is not a row that arrived '
        'unchanged', (tester) async {
      await _pump(tester, FeedHealthRow(feed: _feed(
        deliveries: 2,
        recordsLanded: 17,
        created: 8,
        unchangedRows: 4,
        pulls: true,
        lastPullOutcome: 'unchanged',
        lastPullAgeSeconds: 120,
        watermarkAgeSeconds: 7200,
        lastStatus: 'succeeded',
      )));

      // The connector's word: nothing was fetched at all.
      expect(
        find.textContaining(
          'the estate reported no delta, so nothing was fetched',
        ),
        findsOneWidget,
      );
      // The row counter's word: rows that did arrive, byte-identical.
      expect(
        find.textContaining('4 redelivered byte-identical'),
        findsOneWidget,
      );
      expect(find.textContaining('pulled 2m ago'), findsOneWidget);
      expect(find.textContaining('read up to 2h ago'), findsOneWidget);
    });

    testWidgets('a bind feed holding fewer entities than rows is explained, '
        'not left looking like a dropped delivery', (tester) async {
      await _pump(tester, FeedHealthRow(feed: _feed(
        deliveries: 1,
        recordsLanded: 17,
        lastStatus: 'succeeded',
        entities: const [
          EntityCount(type: 'Employee', instances: 8, feeds: 1),
          EntityCount(type: 'Team', instances: 7, feeds: 1),
        ],
      )));

      // A bind mapping lands nothing of its own, so its rows *are* these
      // types — "binds to", not "resolves against", and never a heading about
      // landing over a book the rows never passed through.
      expect(find.text('binds to Employee 8 · Team 7'), findsOneWidget);
      expect(
        find.text(
          'Landed 17 rows against a book holding 15: the natural key collapsed '
          'the difference — two source rows under one key are one entity, not '
          'a dropped delivery.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('minting more rows than the whole book holds is a collapse '
        'even when the book is shared', (tester) async {
      // The live finding this rule was written for: fourteen ERP vendor rows,
      // thirteen Vendors. Two feeds resolve against that book, and it does not
      // spoil the inference — another feed can only make the book larger.
      await _pump(tester, FeedHealthRow(feed: _feed(
        source: 'erp-vendors',
        deliveries: 1,
        recordsLanded: 14,
        created: 14,
        landsAs: 'SourceRecord',
        landedType:
            const EntityCount(type: 'SourceRecord', instances: 300, feeds: 4),
        lastStatus: 'succeeded',
        entities: const [EntityCount(type: 'Vendor', instances: 13, feeds: 2)],
      )));

      // Both halves, never merged: the rows *became* SourceRecords and then
      // resolved against Vendor, and printing the second under a heading about
      // landing described a book those rows are not in.
      expect(
        find.text(
          'lands as SourceRecord 300 (4 feeds land into it) · '
          'resolves against Vendor 13 (2 feeds resolve against it)',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'Minted 14 rows against a book holding 13: the natural key collapsed '
          'the difference — two source rows under one key are one entity, not '
          'a dropped delivery.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a reify feed claims nothing from its row count alone',
        (tester) async {
      // 109 rows landed as SourceRecords first, so they are not comparable to
      // the counterparty book those rows then resolved against. Nothing is
      // claimed rather than a plausible sentence about a number that does not
      // mean what it looks like.
      await _pump(tester, FeedHealthRow(feed: _feed(
        source: 'crm-book',
        deliveries: 1,
        records: 170,
        recordsLanded: 109,
        created: 36,
        landsAs: 'SourceRecord',
        landedType:
            const EntityCount(type: 'SourceRecord', instances: 300, feeds: 4),
        lastStatus: 'succeeded',
        entities: const [
          EntityCount(type: 'Company', instances: 40, feeds: 2),
        ],
      )));

      expect(
        find.textContaining(
          'resolves against Company 40 (2 feeds resolve against it)',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('natural key collapsed'), findsNothing);
      // Received and landed are two facts: 170 rows arrived and 109 of them
      // became landing records.
      expect(
        find.textContaining('170 received · 109 newly landed'),
        findsOneWidget,
      );
    });

    testWidgets('a contract that resolves nothing still names where its rows '
        'went', (tester) async {
      // The employment-event ledger reifies and stops. Its destination line
      // used to be suppressed entirely, because the only thing that could fill
      // it was a resolution target and this contract declares none.
      await _pump(tester, FeedHealthRow(feed: _feed(
        source: 'hris-employment-events',
        deliveries: 1,
        records: 159,
        recordsLanded: 159,
        unchangedRows: 0,
        landsAs: 'SourceRecord',
        landedType:
            const EntityCount(type: 'SourceRecord', instances: 300, feeds: 4),
        lastStatus: 'succeeded',
      )));

      expect(
        find.text('lands as SourceRecord 300 (4 feeds land into it)'),
        findsOneWidget,
      );
      // It reports `unchanged` and nothing else, so the two it does not report
      // are absent rather than zero — and the one it does is printed.
      expect(
        find.text(
          '1 delivery(s) · 159 received · 159 newly landed · '
          '0 redelivered byte-identical',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a bind feed reports no disposition rather than three zeros',
        (tester) async {
      await _pump(tester, FeedHealthRow(feed: _feed(
        deliveries: 1,
        records: 145,
        recordsLanded: 145,
        lastStatus: 'succeeded',
      )));

      expect(
        find.text(
          '1 delivery(s) · 145 received · 145 newly landed · '
          'disposition not reported by this mapping',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a feed under no Mapping says so on the row', (tester) async {
      await _pump(tester, FeedHealthRow(feed: _feed(
        source: 'wms-receiving',
        deliveries: 3,
        recordsLanded: 12,
        lastStatus: 'succeeded',
      )));

      expect(find.text('no contract'), findsOneWidget);
    });

    testWidgets('a registered feed that has never delivered says that, not '
        'zero of everything', (tester) async {
      await _pump(tester, FeedHealthRow(feed: _feed(pulls: true)));

      expect(find.text('never delivered'), findsOneWidget);
      expect(
        find.text('Nothing delivered yet — no run, and nothing landed.'),
        findsOneWidget,
      );
      expect(find.textContaining('never pulled'), findsOneWidget);
    });

    testWidgets('a failed pull prints what the estate said, verbatim',
        (tester) async {
      const wire = 'GET https://…-hris.web.app/api/naviant/v1/employees → 503';
      await _pump(tester, FeedHealthRow(feed: _feed(
        pulls: true,
        lastPullOutcome: 'failed',
        lastPullError: wire,
        lastPullAgeSeconds: 30,
      )));

      expect(find.text('Last pull failed: $wire'), findsOneWidget);
    });
  });

  group('QueueDepthRow', () {
    testWidgets('a desk with work and nobody bound is a finding',
        (tester) async {
      await _pump(tester, const QueueDepthRow(
        queue: QueueState(
          queue: 'data-stewardship',
          open: 4,
          oldestOpenAtIso: '2026-08-01T00:00:00+00:00',
          oldestOpenAgeSeconds: 90000,
          byKind: {'merge_review': 4},
          bound: [],
        ),
        max: 4,
      ));

      expect(find.text('nobody bound'), findsOneWidget);
      // The kind a person reads, not the enum a Task stores — the same
      // `kindLabel` My Tasks uses, so a desk and the queue it opens are not
      // two names for one thing.
      expect(
        find.text('oldest waiting 1d · 4 proposed merge'),
        findsOneWidget,
      );
    });

    testWidgets('a desk with nobody bound and nothing on it is not an alarm',
        (tester) async {
      await _pump(tester, const QueueDepthRow(
        queue: QueueState(queue: 'knowledge-curation', open: 0, bound: []),
        max: 4,
      ));

      expect(find.text('nothing open'), findsOneWidget);
      expect(find.text('no persona bound'), findsOneWidget);
      expect(find.text('nobody bound'), findsNothing);
    });

    testWidgets('a held desk names who holds it', (tester) async {
      await _pump(tester, const QueueDepthRow(
        queue: QueueState(
          queue: 'ap-clerk',
          open: 2,
          highPriority: 1,
          oldestOpenAgeSeconds: 300,
          byKind: {'decision': 2},
          bound: ['Connie Alberts', 'Raymond Fitzgerald'],
        ),
        max: 4,
      ));

      expect(
        find.text('held by Connie Alberts, Raymond Fitzgerald'),
        findsOneWidget,
      );
      expect(find.text('1 high'), findsOneWidget);
    });
  });

  group('JudgmentPanel', () {
    testWidgets('the regimes are drawn with their refusals beside their commits',
        (tester) async {
      await _pump(tester, JudgmentPanel(judgment: _judgment()));

      expect(find.text('3 committed'), findsOneWidget);
      expect(
        find.text('2 committed · 1 refused at the write'),
        findsOneWidget,
      );
      // Named, not slugged. The three regimes are only distinguishable to
      // somebody who already knows the distinction the Gateway draws, and
      // setting the wire value in code font did not teach it to anybody.
      expect(
        find.text('Observation — a system asserting what it saw'),
        findsOneWidget,
      );
      expect(find.text('Decision — a person answering'), findsOneWidget);
      expect(find.text('Resolution — two records ruled one'), findsOneWidget);
    });

    testWidgets('a write that broke is named apart from one that was refused',
        (tester) async {
      // The two are different news. A refusal is the Gateway saying no and
      // nothing happened; a failure is a write it allowed that then broke,
      // which is the only ending where nobody can say what landed. Folding
      // them into one number would file an outage as governance working.
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(
          ledger: const LedgerWindow(
            days: 30,
            scanned: 8,
            inWindow: 8,
            refused: 1,
            failed: 1,
            byRegime: [
              RegimeCount(regime: 'observation', committed: 3, refused: 0),
              RegimeCount(
                regime: 'decision',
                committed: 2,
                refused: 1,
                failed: 1,
              ),
              RegimeCount(regime: 'resolution', committed: 1, refused: 0),
            ],
          ),
        ),
      ));

      // Every part is named, and the three add to the 4 the bar draws.
      expect(
        find.text(
          '2 committed · 1 refused at the write · '
          '1 failed after the Gateway allowed it',
        ),
        findsOneWidget,
      );
      // A regime nothing broke in says so by omission, not by a "0 failed"
      // that reads as a category of trouble — and its line still adds up.
      expect(find.text('3 committed'), findsOneWidget);
      // And the tile above the bars carries the same fact.
      expect(
        find.textContaining('8 governed operations, 1 refused, 1 failed'),
        findsOneWidget,
      );
    });

    testWidgets('an empty ledger window states the three regimes exist anyway',
        (tester) async {
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(
          ledger: const LedgerWindow(
            days: 30,
            scanned: 0,
            inWindow: 0,
            byRegime: [
              RegimeCount(regime: 'observation', committed: 0, refused: 0),
              RegimeCount(regime: 'decision', committed: 0, refused: 0),
              RegimeCount(regime: 'resolution', committed: 0, refused: 0),
            ],
          ),
        ),
      ));

      expect(
        find.textContaining(
          'Nothing has been committed through the Action Gateway in the last '
          '30 days',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a sweep that never ran is said, not drawn as one that found '
        'nothing', (tester) async {
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(
          identity: const IdentityState(
            books: [
              IdentityBook(
                type: 'Company',
                label: 'Company',
                stewardQueue: 'data-stewardship',
                objects: 0,
                standing: 0,
                confirmed: 0,
                withdrawn: 0,
                yours: false,
              ),
            ],
          ),
        ),
      ));

      expect(
        find.textContaining('The identity sweep has never run over these books'),
        findsOneWidget,
      );
      expect(
        find.text(
          '0 in the book · 0 standing · 0 confirmed · 0 withdrawn',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a standing proposal with no Task is called an anomaly',
        (tester) async {
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(
          identity: const IdentityState(standing: 3, withoutTask: 1),
        ),
      ));

      expect(
        find.textContaining('carry no Task, so there is nowhere to decide'),
        findsOneWidget,
      );
    });

    testWidgets('an unowned desk holding work is called out above the list',
        (tester) async {
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(unowned: const ['nobody-is-bound-here']),
      ));

      expect(
        find.textContaining(
          'Work is sitting on nobody-is-bound-here, which no persona is bound '
          'to.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('the disagreement tile reads off the sweep and links to the '
        'desk', (tester) async {
      await _pump(tester, JudgmentPanel(judgment: _judgment()));

      expect(find.text('STANDING DISAGREEMENTS'), findsOneWidget);
      expect(
        find.text('1 carry a Task the order desk can answer'),
        findsOneWidget,
      );
      // The sweep's own arithmetic, quoted rather than recomputed — including
      // the honest non-finding: an order with no won deal behind it at all.
      expect(
        find.textContaining(
          '2 held order(s) examined, 1 pair(s) in the window, 1 '
          'disagreement(s) found, 1 order(s) with no won deal behind them at '
          'all.',
        ),
        findsOneWidget,
      );
      expect(find.text('Open the order desk'), findsOneWidget);
    });

    testWidgets('blocked evidence says in words what confirming a merge would '
        'buy, and offers the way there', (tester) async {
      // The number that sells the identity work. On its own it is a count of
      // nothing a reader can picture, so the sentence says what was withheld
      // and why confirming an identity is what admits it.
      await _pump(tester, JudgmentPanel(judgment: _judgment()));

      expect(
        find.textContaining(
          '1 piece(s) of evidence were withheld from this pass because a '
          'merge nobody has confirmed was hiding them.',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'it admits records a third system holds about the same commitment',
        ),
        findsOneWidget,
      );
      expect(find.text('Unblock it in the counterparty book'), findsOneWidget);
    });

    testWidgets('a release Cumulus made is counted apart from one it found '
        'already done', (tester) async {
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(
          disagreement: _disagreement(
            standing: 0,
            awaitingDecision: 0,
            blockedOnIdentity: 0,
            outcomes: const DisagreementOutcomes(
              days: 30,
              resolved: 3,
              byCumulus: 2,
              externally: 1,
              dismissed: 1,
            ),
          ),
        ),
      ));

      expect(
        find.text(
          'Settled in 30 days: 3 released (2 written by Cumulus and read '
          'back, 1 already done elsewhere) · 1 left as they were.',
        ),
        findsOneWidget,
      );
      expect(
        find.text('Nothing is standing against the order desk.'),
        findsOneWidget,
      );
      // Nothing blocked, so nothing to unblock — and no dead link offering it.
      expect(find.text('Unblock it in the counterparty book'), findsNothing);
    });

    testWidgets('a book nothing has swept says so rather than reporting zeros',
        (tester) async {
      // A "0 blocked on identity" over a book nothing has ever swept is the
      // most misleading sentence this panel could print: it reads as "the
      // systems agree" when it means "nobody has asked".
      await _pump(tester, JudgmentPanel(
        judgment: _judgment(
          disagreement: _disagreement(
            standing: 0,
            awaitingDecision: 0,
            blockedOnIdentity: 0,
            sweep: null,
          ),
        ),
      ));

      expect(
        find.textContaining(
          'What is below is empty because nobody has asked, not because the '
          'systems agree.',
        ),
        findsOneWidget,
      );
      expect(find.text('the two books have never been swept'), findsOneWidget);
      expect(
        find.text('Nothing is standing against the order desk.'),
        findsNothing,
      );
    });
  });

  group('GovernancePanel', () {
    testWidgets('spend nobody priced is a dash with the reason, never \$0.00',
        (tester) async {
      await _pump(tester, GovernancePanel(
        governance: _governance(
          spend: const SpendState(
            days: 30,
            runs: 40,
            modelCalls: 12,
            pricedModelCalls: 0,
            costUsd: 0,
            basis: 'unrecorded',
          ),
        ),
      ));

      expect(find.text('—'), findsOneWidget);
      expect(find.text('\$0.00'), findsNothing);
      expect(
        find.textContaining(
          '12 model call(s) over 30d, none carrying a cost',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a genuinely idle window is zero and says why', (tester) async {
      await _pump(tester, GovernancePanel(
        governance: _governance(
          spend: const SpendState(days: 30, runs: 3, basis: 'no-model-calls'),
        ),
      ));

      expect(find.text('\$0.00'), findsOneWidget);
      expect(
        find.text(
          'nothing called a model in 30d, so this is genuinely zero',
        ),
        findsOneWidget,
      );
    });

    testWidgets('the two gap directions are never merged into one sentence',
        (tester) async {
      await _pump(tester, const FlowGapRow(gap: FlowGap(
        flowId: 'accounts-payable',
        label: 'Accounts payable',
        state: 'observed',
        totalRuns: 6,
        undeclaredReads: ['PurchaseOrder'],
        unobservedWrites: ['ReceivingReport'],
      )));

      expect(
        find.text(
          'reads PurchaseOrder — no Collection declares it may',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'declares it writes ReceivingReport — no run ever has',
        ),
        findsOneWidget,
      );
      expect(find.text('undeclared'), findsOneWidget);
      expect(find.text('6 run(s)'), findsOneWidget);
    });

    testWidgets('a flow that never ran wears that rather than "undeclared"',
        (tester) async {
      await _pump(tester, const FlowGapRow(gap: FlowGap(
        flowId: 'psa-sync',
        label: 'Psa sync',
        state: 'never_ran',
        unobservedWrites: ['Project'],
      )));

      expect(find.text('never ran'), findsOneWidget);
      expect(find.text('undeclared'), findsNothing);
    });

    testWidgets('a tenant reads why the registry report is withheld',
        (tester) async {
      await _pump(tester, GovernancePanel(
        governance: _governance(problems: null),
      ));

      expect(
        find.textContaining(
          'Withheld — a Mapping naming a type that does not exist',
        ),
        findsOneWidget,
      );
    });

    testWidgets('feeds under no contract are named, with a way to go read them',
        (tester) async {
      await _pump(tester, GovernancePanel(
        governance: _governance(
          withoutContract: const ['legacy-ap-extract', 'wms-receiving'],
        ),
      ));

      expect(
        find.textContaining(
          'Rows are arriving under no declared Mapping from '
          'legacy-ap-extract, wms-receiving',
        ),
        findsOneWidget,
      );
      expect(find.text('See them on Sources'), findsOneWidget);
    });
  });

  group('SystemStateNotYours', () {
    testWidgets('an own-scoped persona is told it is not theirs, not that the '
        'system is empty', (tester) async {
      await _pump(tester, const SystemStateNotYours());

      expect(find.text('This is not scoped to you'), findsOneWidget);
      // Second person, and never "this persona" — the persona is what you are
      // here, not a third party the screen is discussing about you.
      expect(
        find.textContaining('so there is nothing here for you rather than '
            'something withheld'),
        findsOneWidget,
      );
      expect(find.textContaining('persona'), findsNothing);
      // No retry on a refusal that can never succeed.
      expect(find.bySubtype<OutlinedButton>(), findsNothing);
    });
  });
}

Future<void> _pump(WidgetTester tester, Widget child) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(child: SizedBox(width: 1200, child: child)),
    ),
  ));
}

MapState _map({int? invariants = 0, Map<String, int>? kinds = const {}}) =>
    MapState(
      objects: 1766,
      links: 3468,
      conflicted: 2,
      collections: const [
        CollectionState(
          id: 'workforce',
          label: 'Workforce',
          role: 'data',
          origin: 'synced',
          custody: 'copy',
          retention: 'indefinite',
          authoritative: true,
          types: [
            TypeState(
              name: 'Employee',
              label: 'Employee',
              instances: 135,
              conflicted: 2,
            ),
            TypeState(name: 'Team', label: 'Team', instances: 9, conflicted: 0),
          ],
          objects: 144,
          conflicted: 2,
        ),
      ],
      invariants: invariants,
      invariantKinds: invariants == null ? null : kinds,
    );

FeedState _feed({
  String source = 'hris-workforce',
  String kind = '',
  String contractId = 'hris-workforce',
  String landsAs = '',
  EntityCount? landedType,
  int deliveries = 0,
  int? records,
  int recordsLanded = 0,
  // Null by default: a bind mapping never reports a disposition, and passing
  // zeros here is exactly the coercion the wire stopped doing.
  int? created,
  int? linked,
  int? unchangedRows,
  int awaitingSteward = 0,
  String lastStatus = '',
  List<EntityCount> entities = const [],
  bool pulls = false,
  String lastPullOutcome = '',
  String lastPullError = '',
  int? lastPullAgeSeconds,
  int? watermarkAgeSeconds,
}) => FeedState(
  source: source,
  kind: kind,
  contractId: source == 'wms-receiving' ? '' : contractId,
  landsAs: landsAs,
  // A reify feed's landing type is served beside its resolution targets; a
  // fixture that names one without the other is not a shape the server emits.
  landedType: landedType ??
      (landsAs.isEmpty ? null : EntityCount(type: landsAs, instances: 0, feeds: 1)),
  deliveries: deliveries,
  records: records ?? recordsLanded,
  recordsLanded: recordsLanded,
  created: created,
  linked: linked,
  unchangedRows: unchangedRows,
  awaitingSteward: awaitingSteward,
  lastStatus: lastStatus,
  entities: entities,
  pulls: pulls,
  lastPullOutcome: lastPullOutcome,
  lastPullError: lastPullError,
  lastPullAgeSeconds: lastPullAgeSeconds,
  watermarkAgeSeconds: watermarkAgeSeconds,
);

JudgmentState _judgment({
  LedgerWindow? ledger,
  IdentityState? identity,
  DisagreementState? disagreement,
  List<String> unowned = const [],
}) => JudgmentState(
  disagreement: disagreement ?? _disagreement(),
  openTasks: 6,
  highPriority: 1,
  queues: const [
    QueueState(
      queue: 'ap-clerk',
      open: 2,
      highPriority: 1,
      byKind: {'decision': 2},
      oldestOpenAgeSeconds: 600,
      bound: ['Connie Alberts'],
    ),
    QueueState(queue: 'nobody-is-bound-here', open: 4, bound: []),
  ],
  unownedWithWork: unowned,
  ledger: ledger ??
      const LedgerWindow(
        days: 30,
        scanned: 6,
        inWindow: 6,
        refused: 1,
        newestAtIso: '',
        byRegime: [
          RegimeCount(regime: 'observation', committed: 3, refused: 0),
          RegimeCount(regime: 'decision', committed: 2, refused: 1),
          RegimeCount(regime: 'resolution', committed: 1, refused: 0),
        ],
      ),
  identity: identity ??
      const IdentityState(
        standing: 4,
        awaitingDecision: 4,
        books: [
          IdentityBook(
            type: 'Company',
            label: 'Company',
            stewardQueue: 'data-stewardship',
            objects: 40,
            standing: 3,
            confirmed: 1,
            withdrawn: 0,
            yours: true,
          ),
        ],
        lastSweep: SweepRun(
          runId: 'run_sweep_1',
          atIso: '',
          status: 'succeeded',
          compared: 780,
          considered: 12,
          proposed: 4,
          standing: 4,
          stewardTasks: 4,
          ratifications: 2,
        ),
      ),
);

/// The order desk's tile, over the estate's real Voss case: one standing
/// finding the desk can answer, and one piece of evidence an unconfirmed merge
/// is holding back.
DisagreementState _disagreement({
  int standing = 1,
  int awaitingDecision = 1,
  int blockedOnIdentity = 1,
  DisagreementOutcomes outcomes = const DisagreementOutcomes(),
  StateSweepRun? sweep = _stateSweep,
}) => DisagreementState(
  standing: standing,
  awaitingDecision: awaitingDecision,
  blockedOnIdentity: blockedOnIdentity,
  outcomes: outcomes,
  lastSweep: sweep,
);

const _stateSweep = StateSweepRun(
  runId: 'run_ds_89c7bf74fb',
  atIso: '',
  status: 'succeeded',
  ordersExamined: 2,
  candidates: 1,
  found: 1,
  blockedOnIdentity: 1,
  orderWithoutDeal: 1,
  ambiguous: 0,
  deskTasks: 1,
);

GovernanceState _governance({
  SpendState? spend,
  List<String>? problems = const [],
  List<String> withoutContract = const [],
}) => GovernanceState(
  flowsTotal: 13,
  gaps: const [
    FlowGap(
      flowId: 'accounts-payable',
      label: 'Accounts payable',
      state: 'observed',
      totalRuns: 6,
      undeclaredReads: ['PurchaseOrder'],
      unobservedWrites: ['ReceivingReport'],
    ),
  ],
  feedsWithoutContract: withoutContract,
  registryProblems: problems,
  spend: spend ?? const SpendState(days: 30, basis: 'no-model-calls'),
);
