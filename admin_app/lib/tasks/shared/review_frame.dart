/// The chrome every task review screen is built in, and the answer they all
/// return.
///
/// These are the custom human-in-the-loop screens: where a person edits the
/// *facts* rather than overriding a rule.
///
/// That is the difference between a review queue and an approval button. A
/// reviewer looking at an elevated expense is usually not disagreeing with the
/// policy — the extraction read a crumpled total wrong, or a receipt covers two
/// charges, or nobody said what the dinner was for. So the screen lets them
/// change the amount, split a document into two lines, add a line the model
/// missed, and supply a purpose; the run then **re-runs the same checks** over
/// the corrected facts. Approving despite the flags is still available, and is
/// recorded as exactly that.
///
/// Everything a reviewer changes travels as a structured `payload` on the
/// decision, is applied by the flow that raised the Task, and lands in the
/// graph as a `human:` claim that outranks the model's — with the model's
/// original reading still on the record.
///
/// The screens themselves live per flow and per version under `lib/tasks/`;
/// [ReviewFrame], [DecisionBar] and [WhyElevated] are what they share, so a
/// v6 invoice screen and a v9 expense screen cannot drift into two different
/// shapes of the same moment. `lib/tasks/registry.dart` is the one place that
/// picks between them.
library;

import 'package:flutter/material.dart';

import '../../theme/cumulus_theme.dart';
import '../../widgets/ui_kit.dart';

/// What a review screen answers with. It never commits: the Task pane posts
/// the verdict, so every decision travels one authorization path.
class ReviewOutcome {
  final String verdict; // approved | rejected | corrected
  final String note;
  final Map<String, dynamic> payload;

  const ReviewOutcome({
    required this.verdict,
    required this.note,
    this.payload = const {},
  });
}

class ReviewFrame extends StatelessWidget {
  final String eyebrow;
  final String title;
  final String subtitle;
  final Widget child;
  final Widget footer;

  const ReviewFrame({
    super.key,
    required this.eyebrow,
    required this.title,
    required this.subtitle,
    required this.child,
    required this.footer,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 24, 20, 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(eyebrow.toUpperCase(), style: CumulusText.eyebrow),
                    const SizedBox(height: 6),
                    Text(title, style: CumulusText.sectionTitle),
                    const SizedBox(height: 6),
                    Text(subtitle, style: CumulusText.meta),
                  ],
                ),
              ),
              IconButton(
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close, size: 18),
                tooltip: 'Close without deciding',
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(28, 18, 28, 24),
            child: child,
          ),
        ),
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 14, 28, 18),
          child: footer,
        ),
      ],
    );
  }
}

class DecisionBar extends StatelessWidget {
  final TextEditingController note;
  final bool dirty;
  final String correctLabel;
  final VoidCallback onApprove;
  final VoidCallback onReject;
  final VoidCallback onCorrect;

  const DecisionBar({
    super.key,
    required this.note,
    required this.dirty,
    required this.correctLabel,
    required this.onApprove,
    required this.onReject,
    required this.onCorrect,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: note,
          maxLines: 2,
          minLines: 1,
          decoration: const InputDecoration(
            labelText: 'Note (kept on the record, and quoted in the journal)',
            isDense: true,
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: Text(
                dirty
                    ? 'Your edits will be applied and the checks will run '
                        'again over the corrected facts.'
                    : 'Approving accepts the flags as they stand, and is '
                        'recorded as exactly that.',
                style: CumulusText.meta,
              ),
            ),
            const SizedBox(width: 16),
            OutlinedButton.icon(
              onPressed: onReject,
              icon: const Icon(Icons.block, size: 16),
              label: const Text('Reject'),
              style: OutlinedButton.styleFrom(
                foregroundColor: CumulusColors.danger,
              ),
            ),
            const SizedBox(width: 10),
            if (dirty)
              FilledButton.icon(
                onPressed: onCorrect,
                icon: const Icon(Icons.edit_note, size: 16),
                label: Text(correctLabel),
              )
            else
              FilledButton.icon(
                onPressed: onApprove,
                icon: const Icon(Icons.check, size: 16),
                label: const Text('Approve as-is'),
              ),
          ],
        ),
      ],
    );
  }
}

class WhyElevated extends StatelessWidget {
  final List<String> reasons;

  const WhyElevated({super.key, required this.reasons});

  @override
  Widget build(BuildContext context) {
    if (reasons.isEmpty) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 18),
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: CumulusTone.warn.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('WHY THIS STOPPED', style: CumulusText.statLabel),
          const SizedBox(height: 8),
          for (final reason in reasons)
            Padding(
              padding: const EdgeInsets.only(bottom: 3),
              child: Text('· $reason', style: CumulusText.body),
            ),
        ],
      ),
    );
  }
}
