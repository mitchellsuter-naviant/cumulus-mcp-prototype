import 'package:flutter/material.dart';

import '../../models/task.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/ui_kit.dart';
import 'review_frame.dart';

/// The resolution stream of the queue. Nothing was asserted while this Task was
/// open — a batch feed lands the row and escalates rather than guessing — so
/// the steward's decision here is the *first* write.
///
/// Keyed by task kind rather than by flow: any flow's resolver can raise a
/// `match_review`, and what a steward reads is the same question every time.
class EntityMatchReview extends StatefulWidget {
  final QueueTask task;

  const EntityMatchReview({super.key, required this.task});

  @override
  State<EntityMatchReview> createState() => _EntityMatchReviewState();
}

class _EntityMatchReviewState extends State<EntityMatchReview> {
  final _note = TextEditingController();
  String? _chosen;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  /// Candidate ids come off the task's refs; their labels and scores come off
  /// the evidence the flow attached. Both were written by the run that raised
  /// this, so there is nothing to fetch.
  List<({String id, String label})> get _candidates {
    final ids = widget.task.refs
        .where((ref) => ref.startsWith('graph:'))
        .map((ref) => ref.split('/').last.split('@').first)
        .toList();
    final labels = widget.task.evidence
        .where((e) => e.label.startsWith('Candidate'))
        .map((e) => '${e.label} — ${e.value}')
        .toList();
    return [
      for (var i = 0; i < ids.length; i++)
        (id: ids[i], label: i < labels.length ? labels[i] : ids[i]),
    ];
  }

  void _decide(String verdict) {
    Navigator.of(context).pop(ReviewOutcome(
      verdict: verdict,
      note: _note.text.trim(),
      payload: verdict == 'corrected' && _chosen != null
          ? {'object_id': _chosen}
          : const {},
    ));
  }

  @override
  Widget build(BuildContext context) {
    final candidates = _candidates;
    return ReviewFrame(
      eyebrow: 'Entity resolution · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Nothing has been asserted yet. Linking writes a reversible identity '
          'edge; rejecting mints a new entity instead.',
      footer: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            controller: _note,
            maxLines: 2,
            minLines: 1,
            decoration: const InputDecoration(
              labelText: 'Note (kept on the link, and on the record)',
              isDense: true,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Text(
                  _chosen == null
                      ? 'Approve links it to the top candidate. Reject treats '
                          'it as a new entity.'
                      : 'Linking to the selected candidate.',
                  style: CumulusText.meta,
                ),
              ),
              const SizedBox(width: 16),
              OutlinedButton.icon(
                onPressed: () => _decide('rejected'),
                icon: const Icon(Icons.add_business_outlined, size: 16),
                label: const Text('It is a new entity'),
              ),
              const SizedBox(width: 10),
              FilledButton.icon(
                onPressed: () =>
                    _decide(_chosen == null ? 'approved' : 'corrected'),
                icon: const Icon(Icons.link, size: 16),
                label: const Text('Link it'),
              ),
            ],
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(widget.task.summary, style: CumulusText.body),
          const SizedBox(height: 18),
          const FieldLabel('What arrived'),
          for (final evidence in widget.task.evidence
              .where((e) => !e.label.startsWith('Candidate')))
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                children: [
                  SizedBox(
                    width: 190,
                    child: Text(evidence.label, style: CumulusText.meta),
                  ),
                  Expanded(
                    child: Text(evidence.value, style: CumulusText.body),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 18),
          const FieldLabel(
            'Candidates',
            hint: 'ranked by the resolver, with the reason for each score',
          ),
          if (candidates.isEmpty)
            Text(
              'The resolver found nothing to compare against — this is the '
              'first entity of its type.',
              style: CumulusText.meta,
            )
          else
            RadioGroup<String>(
              groupValue: _chosen,
              onChanged: (value) => setState(() => _chosen = value),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final candidate in candidates)
                    RadioListTile<String>(
                      value: candidate.id,
                      dense: true,
                      title: Text(candidate.label, style: CumulusText.body),
                      subtitle: Text(candidate.id, style: CumulusText.mono),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
