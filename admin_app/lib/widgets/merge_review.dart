import 'package:flutter/material.dart';

import '../models/identity_screen.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ref_chips.dart';
import 'ui_kit.dart';

/// Reading a proposed merge, and answering it.
///
/// The one screen in this console where the subject of the judgment is an
/// assertion that is *already in the graph*. An expense review corrects facts
/// and re-runs the checks; a match review makes the first write on a record
/// nothing has asserted. This one looks at an edge the sweep wrote at
/// `in_review` and decides whether it stands — which is why every consequence
/// sentence here is about the edge and none of them is about the two records.
///
/// **Nothing is collapsed on either verdict.** Confirming marks the link
/// confirmed; withdrawing reverses it without erasing that it was made. Both
/// records keep their own natural keys, claims, source links and history. That
/// is stated on the control, not in a tooltip, because it is the single fact a
/// steward most needs before pressing anything: this is not a merge button.
///
/// Split from `review_screens.dart` because it is used twice — as the review
/// screen a `merge_review` Task opens, and as the detail pane of the identity
/// workbench — and one of those two has no Task at all.
class MergeReviewPanel extends StatefulWidget {
  final IdentityProposal proposal;

  /// Resolves the governed policy chip. Never called until the chip is tapped.
  final ApiClient api;

  /// Null when this panel is read-only: an observer, a ruled proposal, or one
  /// the sweep never raised a Task for. The gate's sentence is drawn instead.
  final void Function(String verdict, String note)? onDecide;

  /// Set while a decision is in flight, so the controls disable together.
  final bool busy;

  /// Shown under the controls when a decision came back refused.
  final String? error;

  const MergeReviewPanel({
    super.key,
    required this.proposal,
    required this.api,
    this.onDecide,
    this.busy = false,
    this.error,
  });

  @override
  State<MergeReviewPanel> createState() => _MergeReviewPanelState();
}

class _MergeReviewPanelState extends State<MergeReviewPanel> {
  final _note = TextEditingController();

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  IdentityProposal get p => widget.proposal;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _StandingBanner(proposal: p),
        const SizedBox(height: 18),
        _Comparison(proposal: p),
        const SizedBox(height: 20),
        _Evidence(proposal: p, api: widget.api),
        const SizedBox(height: 20),
        _Reversal(proposal: p),
        const SizedBox(height: 20),
        const Divider(height: 1),
        const SizedBox(height: 16),
        _footer(),
      ],
    );
  }

  Widget _footer() {
    final decide = widget.onDecide;
    if (decide == null) return _GateStatement(proposal: p);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _note,
          enabled: !widget.busy,
          minLines: 1,
          maxLines: 2,
          decoration: const InputDecoration(
            labelText:
                'Note (kept on the decision, and quoted in the withdrawal '
                'reason if you reject)',
            isDense: true,
          ),
        ),
        if (widget.error != null) ...[
          const SizedBox(height: 10),
          Text(
            widget.error!,
            style: CumulusText.meta.copyWith(color: CumulusColors.danger),
          ),
        ],
        const SizedBox(height: 14),
        // Each control states its consequence above itself rather than in a
        // shared line, because the two consequences are genuinely different
        // and a single sentence would have to describe neither.
        _ConsequenceButton(
          tone: CumulusTone.success,
          icon: Icons.link,
          label: 'Confirm they are one party',
          consequence:
              'Confirms these are one ${p.pairKind == 'person' ? 'person' : 'counterparty'}. '
              'Nothing is collapsed: the link is marked confirmed, reversibly, '
              'and downstream flows may now treat them as one. Both records '
              'keep their own key, claims and history.',
          onPressed: widget.busy
              ? null
              : () => widget.onDecide!('approved', _note.text.trim()),
        ),
        const SizedBox(height: 10),
        _ConsequenceButton(
          tone: CumulusTone.danger,
          icon: Icons.link_off,
          label: 'They are different parties',
          consequence:
              'Withdraws the proposal. The edge is reversed rather than '
              'deleted, so the record that the machine proposed it and you '
              'said no survives — and a later sweep will not re-open it.',
          filled: false,
          onPressed: widget.busy
              ? null
              : () => widget.onDecide!('rejected', _note.text.trim()),
        ),
        if (widget.busy) ...[
          const SizedBox(height: 12),
          const Center(
            child: SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          ),
        ],
      ],
    );
  }
}

/// The verdict button and the sentence that says what it does, as one control.
class _ConsequenceButton extends StatelessWidget {
  final CumulusTone tone;
  final IconData icon;
  final String label;
  final String consequence;
  final bool filled;
  final VoidCallback? onPressed;

  const _ConsequenceButton({
    required this.tone,
    required this.icon,
    required this.label,
    required this.consequence,
    required this.onPressed,
    this.filled = true,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Text(
              consequence,
              style: CumulusText.body.copyWith(color: tone.foreground),
            ),
          ),
          const SizedBox(width: 16),
          if (filled)
            FilledButton.icon(
              onPressed: onPressed,
              icon: Icon(icon, size: 16),
              label: Text(label),
            )
          else
            OutlinedButton.icon(
              onPressed: onPressed,
              style: OutlinedButton.styleFrom(foregroundColor: tone.foreground),
              icon: Icon(icon, size: 16),
              label: Text(label),
            ),
        ],
      ),
    );
  }
}

/// Where the proposal stands, and who put it there.
///
/// A standing proposal names no decider on purpose: the sweep stamps itself on
/// the edge so it always says who stands behind it, and printing that as
/// "decided by identity-sweep" would put a machine's name where a person's
/// belongs.
class _StandingBanner extends StatelessWidget {
  final IdentityProposal proposal;

  const _StandingBanner({required this.proposal});

  ({CumulusTone tone, IconData icon, String headline, String detail})
  get _story {
    final p = proposal;
    if (p.isConfirmed) {
      return (
        tone: CumulusTone.success,
        icon: Icons.verified_outlined,
        headline: 'Confirmed as one party.',
        detail:
            '${p.decidedBy} confirmed it${_at(p.decidedAt)}. The machine\'s '
            'own score of ${p.score} is kept rather than promoted — it is what '
            'the proposal was worth before anybody looked at it. Nothing was '
            'collapsed: both records still stand.',
      );
    }
    if (p.isWithdrawn) {
      return (
        tone: CumulusTone.neutral,
        icon: Icons.link_off,
        headline: 'Withdrawn — a person said these are not one party.',
        detail: p.withdrawnBecause.isEmpty
            ? '${p.decidedBy} withdrew it${_at(p.decidedAt)}. The edge is '
                  'reversed rather than deleted, so the proposal stays readable.'
            : p.withdrawnBecause,
      );
    }
    return (
      tone: CumulusTone.warn,
      icon: Icons.help_outline,
      headline: 'Proposed, and nothing has been merged.',
      detail:
          'The sweep scored these two at ${p.score} on the evidence below and '
          'wrote a link at `in_review`. No object was touched, no claim '
          'rewritten, and no flow treats them as one party until this is '
          'confirmed.',
    );
  }

  static String _at(String iso) {
    final when = DateTime.tryParse(iso);
    return when == null ? '' : ' ${agoLabel(when.toLocal())}';
  }

  @override
  Widget build(BuildContext context) {
    final (:tone, :icon, :headline, :detail) = _story;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: tone.foreground),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  headline,
                  style: CumulusText.rowTitle.copyWith(color: tone.foreground),
                ),
                const SizedBox(height: 5),
                Text(
                  detail,
                  style: CumulusText.body.copyWith(color: tone.foreground),
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    MetaPill('confidence ${proposal.score}'),
                    MetaPill('method ${proposal.method}'),
                    MetaPill(proposal.linkType),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The two records, field by field.
///
/// One table rather than two cards, because the decision is a comparison: a
/// steward is reading across a row, not down a column. Only fields *both*
/// records hold can agree or disagree — one side holding a phone the other has
/// never had is not a disagreement, and marking it as one would make every
/// pair read as conflicted.
class _Comparison extends StatelessWidget {
  final IdentityProposal proposal;

  const _Comparison({required this.proposal});

  @override
  Widget build(BuildContext context) {
    final p = proposal;
    final fields = <String>{...p.left.fields.keys, ...p.right.fields.keys}
        .toList()
      ..sort((a, b) {
        // Disagreements first: they are what the decision turns on.
        final rank = _rank(a).compareTo(_rank(b));
        return rank != 0 ? rank : a.compareTo(b);
      });
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // No "resolved under precedence". Precedence settles two claims on
        // *one* object; these are two objects, and whether they are one is the
        // question this steward is here to answer. Saying it was already
        // resolved told them their decision had been taken.
        FieldLabel(
          'The two records',
          hint:
              '${p.conflict.length} field(s) disagree · '
              '${p.agree.length} agree',
        ),
        Container(
          decoration: BoxDecoration(
            color: CumulusColors.surface,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(color: CumulusColors.border),
          ),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(width: 132),
                    Expanded(child: _SideHead(side: p.left)),
                    const SizedBox(width: 12),
                    Expanded(child: _SideHead(side: p.right)),
                  ],
                ),
              ),
              const Divider(height: 1),
              for (final field in fields) _row(p, field),
            ],
          ),
        ),
      ],
    );
  }

  int _rank(String field) {
    if (proposal.conflict.contains(field)) return 0;
    if (proposal.agree.contains(field)) return 1;
    return 2;
  }

  Widget _row(IdentityProposal p, String field) {
    final conflicts = p.conflict.contains(field);
    final agrees = p.agree.contains(field);
    final tone = conflicts
        ? CumulusTone.warn
        : (agrees ? CumulusTone.success : CumulusTone.neutral);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
      decoration: const BoxDecoration(
        border: Border(
          bottom: BorderSide(color: CumulusColors.border, width: 0.5),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 132,
            child: Row(
              children: [
                Icon(
                  conflicts
                      ? Icons.compare_arrows
                      : (agrees ? Icons.check : Icons.remove),
                  size: 13,
                  color: tone.foreground,
                ),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(
                    field,
                    style: CumulusText.mono.copyWith(fontSize: 12),
                  ),
                ),
              ],
            ),
          ),
          Expanded(child: _value(p.left, field, tone)),
          const SizedBox(width: 12),
          Expanded(child: _value(p.right, field, tone)),
        ],
      ),
    );
  }

  Widget _value(IdentitySide side, String field, CumulusTone tone) {
    final value = side.fields[field];
    if (value == null) {
      // Held by one record and not the other. Named rather than blank: an
      // absent field is a fact about the record, and a blank cell reads as a
      // rendering gap.
      return Text('not held', style: CumulusText.meta);
    }
    return SelectableText(
      value,
      style: CumulusText.body.copyWith(
        color: tone == CumulusTone.warn
            ? CumulusColors.ink
            : CumulusColors.inkSecondary,
        fontWeight: tone == CumulusTone.warn ? FontWeight.w600 : null,
      ),
    );
  }
}

class _SideHead extends StatelessWidget {
  final IdentitySide side;

  const _SideHead({required this.side});

  @override
  Widget build(BuildContext context) {
    if (side.missing) {
      return Text(
        'This record is no longer in the book. The proposal points at '
        '${side.id}, which nothing holds — decide it as different parties, or '
        'sweep again.',
        style: CumulusText.meta.copyWith(color: CumulusColors.danger),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(side.label, style: CumulusText.rowTitle),
        const SizedBox(height: 3),
        Text(side.naturalKey, style: CumulusText.mono.copyWith(fontSize: 11.5)),
        const SizedBox(height: 7),
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            for (final source in side.sources)
              StatusBadge(source, tone: CumulusTone.teal, dense: true),
            if (side.sources.isEmpty)
              StatusBadge('no source record', dense: true),
            if (side.conflicted.isNotEmpty)
              StatusBadge(
                'its own sources disagree on ${side.conflicted.join(", ")}',
                tone: CumulusTone.warn,
                dense: true,
              ),
          ],
        ),
      ],
    );
  }
}

/// Every signal, what it was worth, and the governed document that set the
/// weights. The blend is arithmetic on purpose, so a steward can disagree with
/// the number by disagreeing with a row.
class _Evidence extends StatelessWidget {
  final IdentityProposal proposal;
  final ApiClient api;

  const _Evidence({required this.proposal, required this.api});

  @override
  Widget build(BuildContext context) {
    final p = proposal;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FieldLabel(
          'What the score rests on',
          hint:
              '${p.signals.length} signal(s), blended to ${p.score} — '
              'no model, so every row is a fact the two records share or do not',
        ),
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (p.signals.isEmpty)
                Text(
                  'This proposal carries no recorded evidence. It was written '
                  'before the sweep kept its signals, or by something other '
                  'than the sweep.',
                  style: CumulusText.meta,
                )
              else
                for (final signal in p.signals)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 7),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          width: 52,
                          child: Text(
                            signal.signed,
                            style: CumulusText.mono.copyWith(
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                              color: signal.argues
                                  ? CumulusColors.danger
                                  : CumulusColors.success,
                            ),
                          ),
                        ),
                        SizedBox(
                          width: 158,
                          child: Text(signal.name, style: CumulusText.meta),
                        ),
                        Expanded(
                          child: Text(signal.detail, style: CumulusText.body),
                        ),
                      ],
                    ),
                  ),
              const SizedBox(height: 4),
              const Divider(height: 1),
              const SizedBox(height: 10),
              Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  SizedBox(
                    width: 52,
                    child: Text(
                      p.score,
                      style: CumulusText.mono.copyWith(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(
                    width: 158,
                    child: Text('blended confidence', style: CumulusText.meta),
                  ),
                  Expanded(
                    child: Text(
                      'The weights are governed knowledge, not code. Open the '
                      'policy to see where the lines fall — and to move them.',
                      style: CumulusText.meta,
                    ),
                  ),
                ],
              ),
              if (p.policyRef.isNotEmpty) ...[
                const SizedBox(height: 10),
                RefChips(api: api, refs: [p.policyRef]),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

/// What withdrawing this would restore, in the sweep's own words.
class _Reversal extends StatelessWidget {
  final IdentityProposal proposal;

  const _Reversal({required this.proposal});

  @override
  Widget build(BuildContext context) {
    if (proposal.reversibleWith.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const FieldLabel(
          'Reversible with',
          hint: 'recorded on the link when it was proposed, not written here',
        ),
        Text(proposal.reversibleWith, style: CumulusText.body),
      ],
    );
  }
}

/// Why there are no controls. Always a sentence with the reason in it — a
/// disabled button that cannot say why is what makes a refusal read as a bug.
class _GateStatement extends StatelessWidget {
  final IdentityProposal proposal;

  const _GateStatement({required this.proposal});

  @override
  Widget build(BuildContext context) {
    final gate = proposal.gate;
    final decided = !proposal.isStanding;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            decided
                ? 'Already decided'
                : gate.hasTask
                ? 'Not yours to decide'
                : 'Nowhere to decide it',
            style: CumulusText.statLabel,
          ),
          const SizedBox(height: 6),
          Text(
            gate.whyNot.isEmpty
                ? 'There is nothing to decide here.'
                : gate.whyNot,
            style: CumulusText.body,
          ),
          if (!decided && gate.hasTask) ...[
            const SizedBox(height: 8),
            Text(
              'It is waiting as ${gate.taskId} in the ${gate.queue} queue.',
              style: CumulusText.meta,
            ),
          ],
        ],
      ),
    );
  }
}
