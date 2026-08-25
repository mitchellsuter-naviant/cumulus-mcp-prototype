import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:cumulus_admin/models/asset.dart';
import 'package:cumulus_admin/widgets/answer_view.dart';

void main() {
  testWidgets('renders markdown answer with citations', (tester) async {
    final answer = AskAnswer(
      answer: 'Corporate travel guidelines include:\n\n'
          '* **Booking:** book through the portal [travel-booking-guideline].\n'
          '* **Expenses:** governed by policy '
          '[travel-booking-guideline, expense-approval-policy].\n'
          '- Unknown ref stays literal [not-a-real-doc].\n\n'
          'Use `finance/guidelines` to browse.',
      citations: [
        Citation(
          assetId: 'travel-booking-guideline',
          title: 'Corporate Travel Booking Guideline',
          path: 'finance/guidelines/travel-booking',
          authorityLevel: 'guideline',
          status: 'published',
          snippet: '',
        ),
        Citation(
          assetId: 'expense-approval-policy',
          title: 'Expense Approval Policy',
          path: 'finance/policies/expense-approval',
          authorityLevel: 'policy',
          status: 'published',
          snippet: '',
        ),
      ],
      steps: [
        AskStep(tool: 'search', arguments: {'q': 'travel'}, summary: '3 hits'),
      ],
      backend: 'vertex',
      truncated: false,
    );

    final tapped = <String>[];
    for (final dense in [true, false]) {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: 640,
              child: SingleChildScrollView(
                child: AnswerPanel(
                  answer: answer,
                  onOpenAsset: tapped.add,
                  dense: dense,
                ),
              ),
            ),
          ),
        ),
      );
      expect(tester.takeException(), isNull);
      expect(find.text('ANSWER'), findsOneWidget);
      expect(find.text('SOURCES'), findsOneWidget);
      expect(find.text('Corporate Travel Booking Guideline'), findsOneWidget);
      expect(find.byType(CitationChip), findsNWidgets(3));
      expect(find.textContaining('How this was answered'), findsOneWidget);
      await tester.tap(find.byType(CitationChip).first);
      expect(tapped, contains('travel-booking-guideline'));
      tapped.clear();
    }
  });
}
