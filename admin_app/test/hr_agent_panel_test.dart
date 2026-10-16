import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/employee.dart';
import 'package:cumulus_admin/services/api_client.dart';
import 'package:cumulus_admin/widgets/hr_agent_panel.dart';

/// The HR Agent's behaviour, which is mostly about what it refuses to imply.
///
/// The panel used to be a private widget inside the hub screen with three
/// problems nothing could assert about: it sent one message with no
/// conversation around it, so a follow-up was answered as if it were the first
/// question; it had no way to end a conversation, so a transcript from three
/// screens ago was still sitting there and still conditioning the next answer;
/// and it printed a citation chip with a title on it and nothing else, so
/// "the governing document is X" arrived with no way to check either half of
/// the claim — which document, and under what rule.
void main() {
  group('HrAgentPanel', () {
    testWidgets('a follow-up carries the conversation with it', (tester) async {
      final sent = <(String, List<HrTurn>)>[];
      await _pump(
        tester,
        ask: (message, history) async {
          sent.add((message, history));
          return _reply('Dental is covered at 100% preventive.');
        },
      );

      await _ask(tester, 'what does the medical plan cover?');
      await _ask(tester, 'what about dental?');

      // The first question stands alone; the second arrives with the exchange
      // that made it a question. Without this the server sees two words and
      // answers about whatever else says "dental".
      expect(sent.first.$2, isEmpty);
      expect(sent.last.$2.map((t) => t.text), [
        'what does the medical plan cover?',
        'Dental is covered at 100% preventive.',
      ]);
      expect(sent.last.$2.map((t) => t.fromAgent), [false, true]);
    });

    testWidgets('new chat ends the conversation rather than hiding it', (
      tester,
    ) async {
      final sent = <(String, List<HrTurn>)>[];
      await _pump(
        tester,
        ask: (message, history) async {
          sent.add((message, history));
          return _reply('17.5 days.');
        },
      );

      // Nothing asked yet, so the control is inert rather than merely
      // ineffective: pressing it must not restart a greeting that is already
      // the whole transcript.
      await tester.tap(find.text('New chat'));
      await tester.pumpAndSettle();
      expect(find.byType(ActionChip), findsWidgets);

      await _ask(tester, 'how much PTO do I have?');
      expect(find.text('how much PTO do I have?'), findsOneWidget);

      await tester.tap(find.text('New chat'));
      await tester.pumpAndSettle();
      expect(find.text('how much PTO do I have?'), findsNothing);

      // And the next question starts clean — the point of the control is the
      // context it drops, not the pixels it clears.
      await _ask(tester, 'what is the 401(k) match?');
      expect(sent.last.$2, isEmpty);
    });

    testWidgets('a citation carries its authority, its state and a way in', (
      tester,
    ) async {
      final opened = <String>[];
      await _pump(
        tester,
        onOpenAsset: opened.add,
        ask: (message, history) async => _reply(
          'The governing document is “Paid Time Off Policy” (policy).',
          citations: const [
            HrCitation(
              id: 'pto-policy',
              title: 'Paid Time Off Policy',
              path: 'people/policies/pto',
              authorityLevel: 'policy',
              grounding: 1.0,
            ),
            HrCitation(
              id: 'leave-draft',
              title: 'Leave Policy (rewrite)',
              path: 'people/policies/leave',
              authorityLevel: 'guideline',
              status: 'draft',
              grounding: 0.5,
            ),
          ],
        ),
      );
      await _ask(tester, 'how much PTO do I have?');

      // The level is on the chip, because "policy" is what makes the sentence
      // above it true.
      expect(find.text('policy'), findsOneWidget);
      // A draft is retrievable and not authoritative, and the difference has
      // to be visible where a reader looks before believing the answer.
      expect(find.text('draft'), findsOneWidget);

      await tester.tap(find.text('Paid Time Off Policy'));
      await tester.pumpAndSettle();
      expect(opened, ['pto-policy']);
    });

    testWidgets('an answer discloses the rule it ranked under', (tester) async {
      await _pump(
        tester,
        ask: (message, history) async => _reply(
          'The governing document is “Paid Time Off Policy” (policy).',
          citations: const [
            HrCitation(id: 'pto-policy', title: 'Paid Time Off Policy'),
          ],
          grounding: const HrGrounding(
            policyRef: 'knowledge:retrieval-authority@rev3',
            precedence: 'policy > standard > guideline > how-to > note',
            minCoverage: 0.34,
            belowFloor: 4,
            query: 'how much PTO do I have?',
          ),
        ),
      );
      await _ask(tester, 'how much PTO do I have?');

      // Four documents were retrieved and thrown away. An agent that discards
      // them silently and names the fifth looks identical to one that found a
      // single clean answer, which is the difference this line exists to show.
      expect(
        find.textContaining('4 below the floor'),
        findsOneWidget,
      );
      await tester.tap(find.textContaining('How this was grounded'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('policy > standard > guideline'),
        findsWidgets,
      );
      expect(
        find.textContaining('knowledge:retrieval-authority@rev3'),
        findsOneWidget,
      );
    });

    testWidgets('a failed send is offered again rather than left dead', (
      tester,
    ) async {
      var attempts = 0;
      await _pump(
        tester,
        ask: (message, history) async {
          attempts++;
          if (attempts == 1) throw ApiException(503, 'upstream unavailable');
          return _reply('17.5 days.');
        },
      );
      await _ask(tester, 'how much PTO do I have?');
      expect(find.textContaining('upstream unavailable'), findsOneWidget);

      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('17.5 days.'), findsOneWidget);
      expect(attempts, 2);
    });

    testWidgets('the opening promises what this reader can actually get', (
      tester,
    ) async {
      // An operator has no balance and no enrolment. Offering them "how much
      // PTO do I have?" is inviting the one question the account cannot
      // answer, which is how the hub used to open every conversation.
      await _pump(tester, inWorkforce: false, ask: (m, h) async => _reply(''));
      expect(find.textContaining('not on the org chart'), findsOneWidget);
      expect(find.text('How much PTO do I have?'), findsNothing);
      expect(find.text('What is our expense approval policy?'), findsOneWidget);
    });

    testWidgets('a suggestion is a question, and asks it', (tester) async {
      final sent = <String>[];
      await _pump(
        tester,
        ask: (message, history) async {
          sent.add(message);
          return _reply('4% employer match.');
        },
      );
      await tester.tap(find.text('What is the 401(k) match?'));
      await tester.pumpAndSettle();
      expect(sent, ['What is the 401(k) match?']);
      // And they get out of the way once the conversation has started — the
      // question itself is still on screen, in the transcript where it belongs.
      expect(find.byType(ActionChip), findsNothing);
    });
  });
}

// ---- harness ---------------------------------------------------------------

HrChatReply _reply(
  String text, {
  List<HrCitation> citations = const [],
  HrGrounding grounding = const HrGrounding(minCoverage: 0.34),
}) => HrChatReply(
  reply: text,
  citations: citations,
  backend: 'retrieval',
  grounding: grounding,
);

Future<void> _pump(
  WidgetTester tester, {
  required HrAsk ask,
  bool inWorkforce = true,
  void Function(String)? onOpenAsset,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 620,
          height: 700,
          child: HrAgentPanel(
            ask: ask,
            inWorkforce: inWorkforce,
            onOpenAsset: onOpenAsset,
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _ask(WidgetTester tester, String question) async {
  await tester.enterText(find.byType(TextField), question);
  await tester.testTextInput.receiveAction(TextInputAction.done);
  await tester.pumpAndSettle();
}

