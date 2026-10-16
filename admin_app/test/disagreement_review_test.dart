import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/disagreement_screen.dart';
import 'package:cumulus_admin/models/task.dart';
import 'package:cumulus_admin/models/task_screens.dart';
import 'package:cumulus_admin/services/api_client.dart';
import 'package:cumulus_admin/tasks/registry.dart';
import 'package:cumulus_admin/tasks/shared/disagreement_review.dart';

/// The states a cross-system disagreement has to be legible in, and the one
/// thing that must be true in all of them.
///
/// The thing: **approving leaves Cumulus.** This is the only screen in the
/// console where pressing a button changes a row in a system Cumulus does not
/// own, and there is no undo — moving the order back is a second decision
/// somebody makes on purpose. Every state below either says so on the control
/// before it is pressed, or says why there is no control.
///
/// The states, and why each is asserted rather than eyeballed:
///
/// - **Standing, and yours.** Both controls, each with its own consequence on
///   it, and the approve one naming the order, the two states and the absence
///   of an undo.
/// - **The write plane refuses.** No release control at all, and the refusal in
///   words. A screen that drew the button disabled would be promising a
///   transition it cannot stand behind and then explaining nothing.
/// - **The rows moved.** The finding was true when it was written and is not
///   now. Said out loud, because a controller pressing release against a stale
///   finding is the one mistake this screen exists to prevent.
/// - **Standing, and somebody else's desk.** A sentence naming the queue.
/// - **Standing, and no Task at all.** Named as both readings — a purged Task,
///   or a finding the desk already settled — never as "sweep again".
/// - **Settled.** Released by Cumulus, released by somebody else, and left as
///   it is are three different events and read as three.
/// - **Refused at the Gateway.** The error is a statement, not a Retry.
///
/// The fixture is the estate's real Voss case, recorded from
/// `/api/ui/v1/disagreements` — the deal that closed won on the 14th against
/// the order the ERP still held on the 17th.
void main() {
  group('DisagreementReviewPanel', () {
    testWidgets('a standing finding states what releasing does before you can '
        'press it', (tester) async {
      final decided = <String>[];
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(),
          api: ApiClient(),
          onDecide: (verdict, note) => decided.add('$verdict:$note'),
        ),
      );

      expect(
        find.text('Two systems, one commitment, two answers.'),
        findsOneWidget,
      );
      expect(
        find.textContaining('wrote a finding at `standing`'),
        findsOneWidget,
      );

      // The consequence is on the control, and it names the system it reaches,
      // the two states, and the fact that there is no way back.
      expect(
        find.textContaining('This writes to Naviant\'s ERP.'),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'Sales order SO5037 moves from Pending Approval to '
          'Pending Fulfillment.',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining('Nothing else on the order changes.'),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'Cumulus re-reads the order afterwards to confirm the change took, '
          'and tells you if it did not.',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'There is no undo here — moving it back is a second decision.',
        ),
        findsOneWidget,
      );

      final release = find.text('Release SO5037 in the ERP');
      await tester.ensureVisible(release);
      await tester.pumpAndSettle();
      await tester.tap(release);
      expect(decided, ['approved:']);
    });

    testWidgets('leaving it as it is says what a dismissal is and how long it '
        'lasts', (tester) async {
      final decided = <String>[];
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(),
          api: ApiClient(),
          onDecide: (verdict, note) => decided.add('$verdict:$note'),
        ),
      );

      // The promise the sweep's own guard makes structural: `raise_desk_tasks`
      // refuses to re-raise a finding that is not `standing`.
      expect(
        find.textContaining(
          'The disagreement stays on the record, marked dismissed. It will '
          'not be raised again unless the two systems change.',
        ),
        findsOneWidget,
      );

      final leave = find.text('Leave it as it is');
      await tester.ensureVisible(leave);
      await tester.pumpAndSettle();
      await tester.tap(leave);
      expect(decided, ['rejected:']);
    });

    testWidgets('the two columns are each book\'s own vocabulary, not a '
        'comparison table', (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(finding: _finding(), api: ApiClient()),
      );

      expect(find.text('WHAT THE CRM SAYS'), findsOneWidget);
      expect(find.text('WHAT THE ERP SAYS'), findsOneWidget);

      // A deal has a stage, an amount and a close date.
      expect(find.text('Stage'), findsOneWidget);
      expect(find.text('closed-won'), findsOneWidget);
      expect(find.text('\$128,000.00'), findsOneWidget);
      expect(find.text('2026-08-14'), findsOneWidget);

      // An order has a status, a total, lines, an order date and a customer.
      // There is no field both hold, which is why this is two columns.
      expect(find.text('Status'), findsOneWidget);
      expect(find.text('pending-approval'), findsOneWidget);
      expect(find.text('\$34,960.00'), findsOneWidget);
      expect(find.text('Lines'), findsOneWidget);
      expect(find.text('2026-08-17'), findsOneWidget);

      // And each column names the feed that landed it.
      expect(find.text('crm-book'), findsOneWidget);
      expect(find.text('erp-sales'), findsOneWidget);
    });

    testWidgets('the evidence is every signal with its weight, and the policy '
        'that set the weights', (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(finding: _finding(), api: ApiClient()),
      );

      expect(find.text('+0.50'), findsOneWidget);
      expect(find.text('+0.20'), findsOneWidget);
      expect(find.text('+0.12'), findsOneWidget);
      expect(find.text('+0.10'), findsOneWidget);
      expect(find.text('required-conditions'), findsOneWidget);
      expect(find.text('amount-containment'), findsOneWidget);
      // The blend is arithmetic over the rows above, which is what lets a
      // controller disagree with the number by disagreeing with a row.
      expect(find.text('blended correlation'), findsOneWidget);
      expect(find.text('0.92'), findsWidgets);
      expect(
        find.text('knowledge:state-agreement-policy@rev1'),
        findsOneWidget,
      );
    });

    testWidgets('the before and after names what changes and what does not',
        (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(finding: _finding(), api: ApiClient()),
      );

      expect(find.text('CHANGES'), findsOneWidget);
      expect(
        find.text('status  Pending Approval → Pending Fulfillment'),
        findsOneWidget,
      );
      // The half that is usually missing. Derived server-side from the column
      // above it, so it cannot drift from what the order actually shows.
      expect(find.text('UNCHANGED'), findsOneWidget);
      expect(
        find.text('total · lines · order date · customer · memo'),
        findsOneWidget,
      );
      // And the address it would be written at, in the ERP's own terms.
      expect(
        find.textContaining('sales-order-status · erp.salesOrders/5037'),
        findsOneWidget,
      );
    });

    testWidgets('a write the table does not permit offers no release, and says '
        'why', (tester) async {
      // The order moved to `billed` after the finding was written. Not a
      // failure and not the finding being wrong — but a control whose sentence
      // would be false is a control that must not be drawn.
      final decided = <String>[];
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            erp: _erp(state: 'billed'),
            movedSinceFound: true,
            change: _refusedChange,
          ),
          api: ApiClient(),
          onDecide: (verdict, note) => decided.add('$verdict:$note'),
        ),
      );

      expect(find.text('Release SO5037 in the ERP'), findsNothing);
      expect(find.text('No release to offer'), findsOneWidget);
      expect(
        find.textContaining('which the write table may not move an order out of'),
        findsWidgets,
      );
      // Dismissing is still a decision somebody can make, and it is still
      // offered — the refusal is about the ERP, not about the desk.
      expect(find.text('Leave it as it is'), findsOneWidget);
      expect(
        find.textContaining(
          'Leaving it as it is is still a decision you can make.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a row that moved since the finding was written says so',
        (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            erp: _erp(state: 'billed'),
            movedSinceFound: true,
            change: _refusedChange,
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.textContaining(
          'the order was pending-approval and now reads billed',
        ),
        findsOneWidget,
      );
      // What will happen, rather than a dead control: the release re-reads the
      // ERP itself and raises what it finds back to this desk.
      expect(
        find.textContaining(
          'an order that has moved is raised back to this desk rather than '
          'released',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a foreign persona reads which desk owns it, not a disabled '
        'button', (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            gate: const DisagreementGate(
              taskId: 'task_disagreement_79204716aefd',
              taskStatus: 'open',
              queue: 'order-desk',
              mayDecide: false,
              whyNot: "this is order-desk's work; you are bound to no queue",
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(find.text('Not yours to decide'), findsOneWidget);
      expect(
        find.text("this is order-desk's work; you are bound to no queue"),
        findsOneWidget,
      );
      expect(
        find.text(
          'It is waiting as task_disagreement_79204716aefd in the order-desk '
          'queue.',
        ),
        findsOneWidget,
      );
      expect(find.bySubtype<FilledButton>(), findsNothing);
      expect(find.bySubtype<OutlinedButton>(), findsNothing);
    });

    testWidgets('a finding with no Task names both readings, never "sweep '
        'again"', (tester) async {
      // The merge review's third state says the opposite thing on purpose: a
      // proposal with no Task is an anomaly the next sweep repairs, where a
      // finding with no Task may be one the desk already settled — and the
      // sweep will never re-raise it.
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            gate: const DisagreementGate(
              taskId: '',
              taskStatus: '',
              queue: '',
              mayDecide: false,
              whyNot:
                  'No Task carries this finding, so there is nowhere to '
                  'decide it. The sweep raises one for every finding it '
                  'writes at `standing`, and never re-raises one that has '
                  'been settled — so this either predates that or its Task '
                  'was purged.',
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(find.text('Nowhere to decide it'), findsOneWidget);
      expect(find.text('Not yours to decide'), findsNothing);
      expect(
        find.textContaining('never re-raises one that has been settled'),
        findsOneWidget,
      );
      expect(find.bySubtype<FilledButton>(), findsNothing);
      expect(find.textContaining('It is waiting as'), findsNothing);
    });

    testWidgets('a release Cumulus made says it wrote and read back',
        (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            status: 'resolved',
            resolvedHow: 'by_cumulus',
            decidedBy: 'raymond.fitzgerald@naviant.com',
            decidedAt: DateTime.now()
                .toUtc()
                .subtract(const Duration(minutes: 3))
                .toIso8601String(),
            gate: _settled,
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.text('Released. Cumulus wrote to the ERP and read it back.'),
        findsOneWidget,
      );
      expect(
        find.textContaining('the change was verified against the ERP\'s own copy'),
        findsOneWidget,
      );
      expect(find.text('Already decided'), findsOneWidget);
      expect(find.bySubtype<FilledButton>(), findsNothing);
    });

    testWidgets('a release somebody else made does not claim the credit',
        (tester) async {
      // The whole reason `resolved_how` is two numbers off one status. A
      // system that changed nothing must not read like one that changed a
      // customer's order.
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            status: 'resolved',
            resolvedHow: 'externally',
            decidedBy: 'raymond.fitzgerald@naviant.com',
            decidedAt: DateTime.now().toUtc().toIso8601String(),
            gate: _settled,
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.text('Already released — by somebody else, not by Cumulus.'),
        findsOneWidget,
      );
      expect(
        find.textContaining('Cumulus changed nothing'),
        findsOneWidget,
      );
      expect(
        find.text('Released. Cumulus wrote to the ERP and read it back.'),
        findsNothing,
      );
    });

    testWidgets('a dismissed finding stays on the record, in the desk\'s own '
        'words', (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            status: 'dismissed',
            decidedBy: 'raymond.fitzgerald@naviant.com',
            decidedAt: DateTime.now().toUtc().toIso8601String(),
            resolution:
                'order desk left the disagreement as it is '
                '(task_disagreement_79204716aefd): Phase 1 is not invoiceable '
                'until the pilot signs off.',
            gate: _settled,
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.text('Left as it is — the two systems may disagree here.'),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'Phase 1 is not invoiceable until the pilot signs off.',
        ),
        findsOneWidget,
      );
      expect(find.bySubtype<FilledButton>(), findsNothing);
    });

    testWidgets('a refused decision is a statement and not a Retry',
        (tester) async {
      // The validated no-Retry idiom. The Task is already open again and
      // retrying is the same press — a Retry button here would be a second way
      // to make one decision.
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(),
          api: ApiClient(),
          onDecide: (_, _) {},
          error:
              'The Action Gateway refused to commit this decision: '
              'release-sales-order not bound to order-desk. The task is open '
              'again — nothing was recorded and nothing changed.',
        ),
      );

      expect(
        find.textContaining('nothing was recorded and nothing changed'),
        findsOneWidget,
      );
      expect(find.text('Retry'), findsNothing);
    });

    testWidgets('a row the book no longer holds is a finding, not a gap',
        (tester) async {
      await _pump(
        tester,
        DisagreementReviewPanel(
          finding: _finding(
            erp: const DisagreementSide(
              id: 'salesorder_bc804bb12fdd',
              ref: 'graph:salesorder_bc804bb12fdd',
              type: '',
              label: '',
              naturalKey: '',
              book: 'the ERP',
              state: '',
              stateWhenFound: 'pending-approval',
              facts: [],
              sources: [],
              missing: true,
            ),
            change: const DisagreementChange(
              field: '',
              fromValue: '',
              toValue: '',
              unchanged: [],
              target: '',
              app: '',
              resource: '',
              recordId: '',
              permitted: false,
              whyNot:
                  'the finding\'s order salesorder_bc804bb12fdd is not in the '
                  'graph',
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.textContaining('This row is no longer in the book'),
        findsOneWidget,
      );
      expect(find.text('Release SO5037 in the ERP'), findsNothing);
    });
  });

  group('the queue reaches it', () {
    tearDown(() => TaskScreens.manifest = null);

    test('a disagreement Task opens the screen rather than a bare Approve', () {
      // Two things have to agree for this screen to be reachable at all: the
      // server has to name it, and this build has to have a constructor under
      // the id it names. A kind the server declares with no widget behind it —
      // or a widget with no declaration — is a screen nobody can get to, on
      // the one kind where the fallback is not merely thin but wrong: the
      // generic pane's whole account of releasing an order into a customer's
      // ERP was the word "Approve".
      //
      // The declaration is the deployment's, so it arrives rather than being
      // compiled in — `SHARED_SCREENS` in `server/app/task_screens.py`. Its
      // strings are written out here rather than imported, because a test that
      // read them from the same place the code does would pass through a
      // rewording of the sentence a controller reads before pressing the
      // button.
      TaskScreens.manifest = const TaskScreenManifest(
        shared: {
          'state_disagreement': TaskScreenSpec(
            screenId: 'shared/disagreement-review',
            label: 'Open the disagreement',
            hint: 'Opens what each system says, the evidence that correlated '
                'them, and exactly what releasing would change in the ERP — '
                'and what it would leave alone.',
          ),
        },
      );

      expect(hasReviewScreen(_task), isTrue);
      expect(reviewLabel(_task), 'Open the disagreement');
      expect(
        reviewHint(_task),
        contains('exactly what releasing would change in the ERP'),
      );
      expect(
        taskScreenFactories.containsKey('shared/disagreement-review'),
        isTrue,
      );
    });

    test('and with no manifest it is the pane, not another flow’s screen', () {
      // The console cannot reach its server. What a controller loses is this
      // screen; what they must not get is a different one — a label from a
      // compiled table would send them to an invoice review over an order.
      TaskScreens.manifest = null;
      expect(hasReviewScreen(_task), isFalse);
      expect(reviewLabel(_task), isNot('Open the invoice review'));
    });
  });
}

const _task = QueueTask(
  id: 'task_disagreement_79204716aefd',
  runId: 'run_ds_89c7bf74fb',
  kind: 'state_disagreement',
  title: 'Voss Automotive Group: the CRM says won, the ERP has not released '
      'SO5037',
  summary: '',
  queue: 'order-desk',
  priority: 'normal',
  status: 'open',
  flowId: 'disagreement-sweep',
  caseRef: 'link:deal_disagrees_with_order/link_79204716aefd',
  refs: [],
  evidence: [],
  proposal: TaskProposal(
    action: 'release-sales-order',
    target: 'sales-records',
    summary: 'Release SO5037 in the ERP, moving it out of pending-approval',
  ),
  resumes: '',
  createdAtIso: '',
  decision: null,
);

Future<void> _pump(WidgetTester tester, Widget child) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SizedBox(width: 1000, child: child),
        ),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// The fixture: the estate's real Voss case
// ---------------------------------------------------------------------------
//
// Recorded from `/api/ui/v1/disagreements` over the two feeds the workplan's
// ground-truth section verified against the running book — a deal closed won on
// the 14th for $128,000, and an order raised on the 17th for $34,960 that the
// ERP has not released.

const _crm = DisagreementSide(
  id: 'opportunity_79d58384b577',
  ref: 'graph:Opportunity/opportunity_79d58384b577@rev2',
  type: 'Opportunity',
  label: 'Voss — Title & Service Records Capture',
  naturalKey: 'crm-book:O1',
  book: 'the CRM',
  state: 'closed-won',
  stateWhenFound: 'closed-won',
  facts: [
    DisagreementFact(label: 'Stage', value: 'closed-won'),
    DisagreementFact(label: 'Amount', value: '\$128,000.00'),
    DisagreementFact(label: 'Close date', value: '2026-08-14'),
  ],
  sources: ['crm-book'],
  missing: false,
);

DisagreementSide _erp({String state = 'pending-approval'}) => DisagreementSide(
  id: 'salesorder_bc804bb12fdd',
  ref: 'graph:SalesOrder/salesorder_bc804bb12fdd@rev2',
  type: 'SalesOrder',
  label: 'SO5037',
  naturalKey: 'SO5037',
  book: 'the ERP',
  state: state,
  stateWhenFound: 'pending-approval',
  facts: [
    DisagreementFact(label: 'Status', value: state),
    const DisagreementFact(label: 'Total', value: '\$34,960.00'),
    const DisagreementFact(label: 'Lines', value: '2'),
    const DisagreementFact(label: 'Order date', value: '2026-08-17'),
    const DisagreementFact(label: 'Customer', value: 'Voss Automotive Group'),
    const DisagreementFact(
      label: 'Memo',
      value: 'Title and service records capture — phase 1',
    ),
  ],
  sources: const ['erp-sales'],
  missing: false,
);

const _permittedChange = DisagreementChange(
  field: 'status',
  fromValue: 'Pending Approval',
  toValue: 'Pending Fulfillment',
  unchanged: ['total', 'lines', 'order date', 'customer', 'memo'],
  target: 'sales-order-status',
  app: 'erp',
  resource: 'salesOrders',
  recordId: '5037',
  permitted: true,
  whyNot: '',
);

const _refusedChange = DisagreementChange(
  field: 'status',
  fromValue: '',
  toValue: '',
  unchanged: ['total', 'lines', 'order date', 'customer', 'memo'],
  target: 'sales-order-status',
  app: 'erp',
  resource: 'salesOrders',
  recordId: '5037',
  permitted: false,
  whyNot:
      'Cumulus\'s copy of SO5037 reads `billed`, which the write table may not '
      'move an order out of. Approving would re-read the ERP and raise what it '
      'finds to the desk rather than write.',
);

const _openToController = DisagreementGate(
  taskId: 'task_disagreement_79204716aefd',
  taskStatus: 'open',
  queue: 'order-desk',
  mayDecide: true,
  whyNot: '',
);

const _settled = DisagreementGate(
  taskId: 'task_disagreement_79204716aefd',
  taskStatus: 'approved',
  queue: 'order-desk',
  mayDecide: false,
  whyNot: 'Already decided in order-desk; the record is below.',
);

DisagreementCase _finding({
  String status = 'standing',
  double confidence = 0.9196,
  String resolvedHow = '',
  String decidedBy = '',
  String decidedAt = '',
  String resolution = '',
  bool movedSinceFound = false,
  DisagreementSide? erp,
  DisagreementChange change = _permittedChange,
  DisagreementGate gate = _openToController,
}) => DisagreementCase(
  linkId: 'link_79204716aefd',
  linkType: 'deal_disagrees_with_order',
  caseRef: 'link:deal_disagrees_with_order/link_79204716aefd',
  findingClass: 'won-not-released',
  status: status,
  confidence: confidence,
  method: 'derived',
  signals: const [
    IdentitySignal(
      name: 'required-conditions',
      weight: 0.5,
      detail:
          "one counterparty (Voss Automotive Group), 'closed-won' against "
          "'pending-approval', and an order 3 day(s) from the close",
    ),
    // Weighted by the similarity itself — 0.25 × 0.80 — which is why the
    // blend below is 0.92 and not the sum of the table's ceilings.
    IdentitySignal(
      name: 'name-proximity',
      weight: 0.1996,
      detail:
          "the deal's subject and the order's memo agree at 0.80 — "
          "'Title & Service Records Capture' ~ 'Title and service records "
          "capture — phase 1'",
    ),
    IdentitySignal(
      name: 'amount-containment',
      weight: 0.12,
      detail:
          'the order is 27% of the deal (\$34,960 of \$128,000), inside the '
          '10%–115% band',
    ),
    IdentitySignal(
      name: 'sole-candidate',
      weight: 0.1,
      detail:
          'the only won deal in the window for this counterparty — there is '
          'nothing to confuse it with',
    ),
  ],
  policyRef: 'knowledge:state-agreement-policy@rev1',
  detectedAt: '2026-08-31T05:01:17.169695+00:00',
  runId: 'run_ds_89c7bf74fb',
  counterparty: 'Voss Automotive Group',
  crm: _crm,
  erp: erp ?? _erp(),
  change: change,
  movedSinceFound: movedSinceFound,
  resolvedHow: resolvedHow,
  decidedAt: decidedAt,
  decidedBy: decidedBy,
  resolution: resolution,
  gate: gate,
);
