import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/identity_screen.dart';
import 'package:cumulus_admin/services/api_client.dart';
import 'package:cumulus_admin/widgets/merge_review.dart';

/// The four states a proposed merge has to be legible in, and the one thing
/// that must be true in all of them.
///
/// The thing: **nothing is collapsed.** This is the one screen in the console
/// where a reader could plausibly believe that pressing Approve merges two
/// rows into one, and it does not — it flips a link's status, reversibly, and
/// both records keep their key, their claims and their history. Every state
/// below says so, and the confirm control says it before it is pressed rather
/// than in a tooltip afterwards.
///
/// The states, and why each is asserted rather than eyeballed:
///
/// - **Standing, and yours.** Controls, with their consequences on them.
/// - **Standing, and nobody was paged.** The proposal cleared the governed
///   confidence bar, so the sweep raised no Task and there is no decision path
///   at all. A screen that drew controls here would offer a button with
///   nothing to commit through.
/// - **Standing, and somebody else's desk.** A sentence naming the queue, not
///   a disabled button.
/// - **Ruled.** Confirmed says who and keeps the machine's score; withdrawn
///   says the edge was reversed rather than deleted, in the steward's words.
///
/// The fixture is the estate's real Northwind pair, recorded from
/// `/api/ui/v1/identity` — a pair that agrees on nothing and disagrees on
/// four fields, which is the case a comparison table has to read well for.
void main() {
  group('MergeReviewPanel', () {
    testWidgets('a standing proposal states what confirming does before you '
        'can press it', (tester) async {
      final decided = <String>[];
      await _pump(
        tester,
        MergeReviewPanel(
          proposal: _proposal(),
          api: ApiClient(),
          onDecide: (verdict, note) => decided.add('$verdict:$note'),
        ),
      );

      expect(find.text('Proposed, and nothing has been merged.'), findsOneWidget);
      expect(
        find.textContaining('wrote a link at `in_review`'),
        findsOneWidget,
      );
      // The consequence is on the control, in the words the doctrine uses.
      expect(
        find.textContaining(
          'Nothing is collapsed: the link is marked confirmed, reversibly',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining('The edge is reversed rather than deleted'),
        findsOneWidget,
      );

      final confirm = find.text('Confirm they are one party');
      await tester.ensureVisible(confirm);
      await tester.pumpAndSettle();
      await tester.tap(confirm);
      expect(decided, ['approved:']);
    });

    testWidgets('the evidence is every signal with its weight, and the '
        'policy that set the weights', (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(proposal: _proposal(), api: ApiClient()),
      );

      expect(find.text('+0.08'), findsOneWidget);
      expect(find.text('+0.20'), findsOneWidget);
      expect(find.text('+0.51'), findsOneWidget);
      expect(find.text('shared-area-code'), findsOneWidget);
      expect(find.text("same first word — 'northwind'"), findsOneWidget);
      // The blend, next to the number it produced — the score is arithmetic
      // over the rows above, which is why a steward can disagree with a row.
      expect(find.text('blended confidence'), findsOneWidget);
      expect(find.text('0.79'), findsWidgets);
      // Governed knowledge, as a chip that dereferences.
      expect(
        find.text('knowledge:entity-resolution-policy@rev1'),
        findsOneWidget,
      );
    });

    testWidgets('the comparison marks only fields both records hold',
        (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(proposal: _proposal(), api: ApiClient()),
      );

      expect(
        find.textContaining('4 field(s) disagree · 0 agree'),
        findsOneWidget,
      );
      // `website` and `industry` are on the left record only. Saying "not
      // held" rather than leaving the cell blank: an absent field is a fact
      // about the record, and a blank reads as a rendering gap.
      expect(find.text('not held'), findsNWidgets(2));
      // Each record says which feeds asserted it — one row two systems agree
      // on is a different kind of duplicate from one only the ERP has seen.
      expect(find.text('crm-book'), findsOneWidget);
      expect(find.text('erp-sales'), findsNWidgets(2));
      // And a record whose own sources already disagree says so.
      expect(
        find.text('its own sources disagree on name'),
        findsOneWidget,
      );
    });

    testWidgets('a proposal above the confidence bar says nobody was paged, '
        'rather than drawing a control with nothing behind it', (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(
          proposal: _proposal(
            confidence: 0.95,
            gate: const ProposalGate(
              taskId: '',
              taskStatus: '',
              queue: '',
              mayDecide: false,
              whyNot:
                  'Nobody was paged: it scored 0.95, at or above the governed '
                  '0.80 bar under which the sweep asks a steward. It stands '
                  'as a proposal until something acts on it.',
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(find.text('Not yours to decide'), findsOneWidget);
      expect(find.textContaining('Nobody was paged'), findsOneWidget);
      expect(find.bySubtype<FilledButton>(), findsNothing);
      expect(find.textContaining('It is waiting as'), findsNothing);
    });

    testWidgets('a foreign persona reads which queue owns it, not a disabled '
        'button', (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(
          proposal: _proposal(
            gate: const ProposalGate(
              taskId: 'task_merge_0ddc53779a91',
              taskStatus: 'open',
              queue: 'data-stewardship',
              mayDecide: false,
              whyNot:
                  "this is data-stewardship's work; you are bound to no queue",
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(find.text('Not yours to decide'), findsOneWidget);
      expect(
        find.text("this is data-stewardship's work; you are bound to no queue"),
        findsOneWidget,
      );
      expect(
        find.text(
          'It is waiting as task_merge_0ddc53779a91 in the data-stewardship '
          'queue.',
        ),
        findsOneWidget,
      );
      expect(find.bySubtype<FilledButton>(), findsNothing);
      expect(find.bySubtype<OutlinedButton>(), findsNothing);
    });

    testWidgets('a confirmed proposal names the person and keeps the '
        "machine's score", (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(
          proposal: _proposal(
            status: 'confirmed',
            decidedBy: 'raymond.fitzgerald@naviant.com',
            decidedAt: DateTime.now()
                .toUtc()
                .subtract(const Duration(minutes: 4))
                .toIso8601String(),
            gate: const ProposalGate(
              taskId: 'task_merge_0ddc53779a91',
              taskStatus: 'approved',
              queue: 'data-stewardship',
              mayDecide: false,
              whyNot: 'Already decided in data-stewardship; the record is '
                  'below.',
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(find.text('Confirmed as one party.'), findsOneWidget);
      expect(
        find.textContaining('raymond.fitzgerald@naviant.com confirmed it'),
        findsOneWidget,
      );
      // Not promoted to 1.0 — the score is what the proposal was worth before
      // anybody looked at it, and it is the number a later tuning pass needs.
      expect(
        find.textContaining("machine's own score of 0.79 is kept"),
        findsOneWidget,
      );
      expect(find.textContaining('Nothing was collapsed'), findsOneWidget);
      expect(find.text('Already decided'), findsOneWidget);
      expect(find.bySubtype<FilledButton>(), findsNothing);
    });

    testWidgets('a withdrawn proposal is reversed rather than deleted, in the '
        "steward's own words", (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(
          proposal: _proposal(
            status: 'withdrawn',
            decidedBy: 'raymond.fitzgerald@naviant.com',
            decidedAt: DateTime.now().toUtc().toIso8601String(),
            withdrawnBecause:
                'steward rejected the proposed merge (task_merge_0ddc53779a91): '
                'Different customers — the 2019 row is a distributor.',
            gate: const ProposalGate(
              taskId: 'task_merge_0ddc53779a91',
              taskStatus: 'rejected',
              queue: 'data-stewardship',
              mayDecide: false,
              whyNot: 'Already decided in data-stewardship; the record is '
                  'below.',
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.text('Withdrawn — a person said these are not one party.'),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'Different customers — the 2019 row is a distributor.',
        ),
        findsOneWidget,
      );
      expect(find.bySubtype<FilledButton>(), findsNothing);
    });

    testWidgets('a record the book no longer holds is a finding, not a gap',
        (tester) async {
      await _pump(
        tester,
        MergeReviewPanel(
          proposal: _proposal(
            right: const IdentitySide(
              id: 'company_f7261c66cb14',
              ref: 'graph:company_f7261c66cb14',
              type: '',
              label: '',
              naturalKey: '',
              status: 'missing',
              fields: {},
              sources: [],
              conflicted: [],
            ),
          ),
          api: ApiClient(),
        ),
      );

      expect(
        find.textContaining('This record is no longer in the book'),
        findsOneWidget,
      );
    });
  });
}

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
// The fixture: the estate's real Northwind pair
// ---------------------------------------------------------------------------

const _left = IdentitySide(
  id: 'company_f890ef68693f',
  ref: 'graph:Company/company_f890ef68693f@rev3',
  type: 'Company',
  label: 'Northwind Traders',
  naturalKey: 'Northwind Traders',
  status: 'active',
  fields: {
    'name': 'Northwind Traders Inc.',
    'slug': 'northwind-traders',
    'industry': 'Wholesale Distribution',
    'website': 'https://northwindtraders.example.com',
    'phone': '(312) 555-0134',
    'payment_terms': 'Net 30',
  },
  sources: ['crm-book', 'erp-sales'],
  conflicted: ['name'],
);

const _right = IdentitySide(
  id: 'company_f7261c66cb14',
  ref: 'graph:Company/company_f7261c66cb14@rev2',
  type: 'Company',
  label: 'Northwind Trading Co.',
  naturalKey: 'Northwind Trading Co.',
  status: 'active',
  fields: {
    'name': 'Northwind Trading Co.',
    'slug': 'northwind-trading',
    'phone': '(312) 555-0100',
    'payment_terms': 'Net 60',
  },
  sources: ['erp-sales'],
  conflicted: [],
);

const _openToController = ProposalGate(
  taskId: 'task_merge_0ddc53779a91',
  taskStatus: 'open',
  queue: 'data-stewardship',
  mayDecide: true,
  whyNot: '',
);

IdentityProposal _proposal({
  String status = 'in_review',
  double confidence = 0.791,
  String decidedBy = '',
  String decidedAt = '',
  String withdrawnBecause = '',
  IdentitySide? right,
  ProposalGate gate = _openToController,
}) => IdentityProposal(
  linkId: 'link_0ddc53779a91',
  linkType: 'company_same_as',
  caseRef: 'link:company_same_as/link_0ddc53779a91',
  pairKind: 'organization',
  status: status,
  confidence: confidence,
  method: 'fuzzy',
  signals: const [
    IdentitySignal(
      name: 'shared-area-code',
      weight: 0.08,
      detail: 'same area code (312), different numbers',
    ),
    IdentitySignal(
      name: 'shared-anchor',
      weight: 0.2,
      detail: "same first word — 'northwind'",
    ),
    IdentitySignal(
      name: 'name-similarity',
      weight: 0.511,
      detail: "'Northwind Traders' ~ 'Northwind Trading Co.' at 0.64",
    ),
  ],
  reversibleWith:
      'withdraw this company_same_as link; both objects (Northwind Traders, '
      'Northwind Trading Co.) keep their own claims, source links and history, '
      'none of which this proposal touched',
  policyRef: 'knowledge:entity-resolution-policy@rev1',
  proposedAt: '2026-08-30T22:06:54.127125+00:00',
  runId: 'run_is_3abbd42fb4',
  decidedBy: decidedBy,
  decidedAt: decidedAt,
  withdrawnBecause: withdrawnBecause,
  left: _left,
  right: right ?? _right,
  agree: const [],
  conflict: const ['name', 'payment_terms', 'phone', 'slug'],
  gate: gate,
);
