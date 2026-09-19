import 'package:flutter/material.dart';

import '../models/disagreement_screen.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ref_chips.dart';
import 'ui_kit.dart';

/// Reading a disagreement between two of the customer's own systems, and
/// answering it.
///
/// The merge review's sibling, and the one screen in this console where
/// approving **leaves Cumulus**. A steward confirming a merge changes what this
/// system believes, reversibly, and both records keep everything they hold. A
/// controller approving here changes a row in an ERP Cumulus does not own, and
/// there is no undo — moving the order back is a second decision somebody makes
/// on purpose. Every sentence on the approve control exists because of that
/// asymmetry.
///
/// Three things are drawn and none of them is composed here:
///
/// - **The two columns** are each book's own vocabulary. This is not a
///   comparison table: a deal has a stage, an amount and a close date; an order
///   has a status, a total and an order date. There is no shared key, and
///   drawing one would be a grid of empty cells.
/// - **The evidence** is the sweep's own signals with their weights, the way
///   the merge review renders them. The blend is addition, so a controller can
///   disagree with the number by disagreeing with a row.
/// - **The before and after** is read off the write table server-side — the
///   same declaration the release Action commits through. What does *not*
///   change is derived there from the column above it rather than typed, which
///   is why this file has no list of untouched fields in it.
///
/// Split from `review_screens.dart` for `merge_review.dart`'s reason: the panel
/// is the thing worth testing on its own, in each of the states a finding can
/// be read in, without a Task or a dialog around it.
class DisagreementReviewPanel extends StatelessWidget {
  final DisagreementCase finding;

  /// Resolves the governed policy chip. Never called until the chip is tapped.
  final ApiClient api;

  /// Null when this panel is read-only: an observer, a settled finding, or one
  /// with no Task carrying it. The gate's sentence is drawn instead.
  final void Function(String verdict, String note)? onDecide;

  /// Set while a decision is in flight, so the controls disable together.
  final bool busy;

  /// Shown under the controls when a decision came back refused or broken.
  final String? error;

  const DisagreementReviewPanel({
    super.key,
    required this.finding,
    required this.api,
    this.onDecide,
    this.busy = false,
    this.error,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _StandingBanner(finding: finding),
        if (finding.movedSinceFound) ...[
          const SizedBox(height: 12),
          _MovedNotice(finding: finding),
        ],
        const SizedBox(height: 18),
        _TwoBooks(finding: finding),
        const SizedBox(height: 20),
        _Evidence(finding: finding, api: api),
        const SizedBox(height: 20),
        _BeforeAndAfter(finding: finding),
        const SizedBox(height: 20),
        const Divider(height: 1),
        const SizedBox(height: 16),
        _Footer(
          finding: finding,
          onDecide: onDecide,
          busy: busy,
          error: error,
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Where it stands
// ---------------------------------------------------------------------------

/// What has happened to this finding, and what has not.
///
/// A standing finding names no decider on purpose: the sweep stamps itself on
/// the edge so it always says who stands behind it, and printing that as
/// "decided by disagreement-sweep" would put a machine's name where a person's
/// belongs. The two resolved readings are kept apart for the reason the server
/// keeps two numbers off one status — a release Cumulus made and one it found
/// already done are different events, and one sentence for both would claim
/// credit for somebody else's work.
class _StandingBanner extends StatelessWidget {
  final DisagreementCase finding;

  const _StandingBanner({required this.finding});

  ({CumulusTone tone, IconData icon, String headline, String detail})
  get _story {
    final f = finding;
    if (f.isResolved && f.releasedByCumulus) {
      return (
        tone: CumulusTone.success,
        icon: Icons.outbound_outlined,
        headline: 'Released. Cumulus wrote to the ERP and read it back.',
        detail:
            '${f.decidedBy} approved it${_at(f.decidedAt)}. The order moved, '
            'the change was verified against the ERP\'s own copy, and the '
            'finding is closed. There is no undo here — moving it back is a '
            'second decision.',
      );
    }
    if (f.isResolved) {
      return (
        tone: CumulusTone.neutral,
        icon: Icons.done_all,
        headline: 'Already released — by somebody else, not by Cumulus.',
        detail:
            '${f.decidedBy} approved it${_at(f.decidedAt)} and the ERP was '
            'found already holding the released value, with nothing in the '
            'write journal asking for it. Cumulus changed nothing; the '
            'finding stopped being true on its own.',
      );
    }
    if (f.isDismissed) {
      return (
        tone: CumulusTone.neutral,
        icon: Icons.do_not_disturb_on_outlined,
        headline: 'Left as it is — the two systems may disagree here.',
        detail: f.resolution.isEmpty
            ? '${f.decidedBy} dismissed it${_at(f.decidedAt)}. The finding '
                  'stays on the record rather than being deleted: it was '
                  'true, and what was decided is that it does not need '
                  'acting on.'
            : f.resolution,
      );
    }
    if (!f.isStanding) {
      return (
        tone: CumulusTone.neutral,
        icon: Icons.history_toggle_off,
        headline: 'Superseded — the rows moved out from under it.',
        detail:
            'A later pass withdrew this finding because one of the two rows '
            'it joined no longer holds the state it was found in. Nothing was '
            'decided and nothing was written.',
      );
    }
    return (
      tone: CumulusTone.warn,
      icon: Icons.compare_arrows,
      headline: 'Two systems, one commitment, two answers.',
      detail:
          'The sweep correlated these two rows at ${f.score} on the evidence '
          'below and wrote a finding at `standing`. It has changed nothing, '
          'touched neither record, and written to no system — the whole '
          'output of the pass was an edge and a Task.',
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
                    MetaPill('correlation ${finding.score}'),
                    if (finding.findingClass.isNotEmpty)
                      MetaPill(finding.findingClass),
                    if (finding.counterparty.isNotEmpty)
                      MetaPill(finding.counterparty),
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

/// The one fact a controller must have before pressing anything: a row has
/// moved since the finding was written.
///
/// Not a refusal. The release re-reads the ERP itself and raises what it finds
/// to this desk rather than writing — so this says what changed and what will
/// happen, instead of disabling a control and explaining nothing.
class _MovedNotice extends StatelessWidget {
  final DisagreementCase finding;

  const _MovedNotice({required this.finding});

  @override
  Widget build(BuildContext context) {
    final moved = <String>[
      if (finding.crm.moved)
        'the deal was ${finding.crm.stateWhenFound} and now reads '
            '${finding.crm.state}',
      if (finding.erp.moved)
        'the order was ${finding.erp.stateWhenFound} and now reads '
            '${finding.erp.state}',
    ];
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: CumulusTone.danger.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.change_circle_outlined,
            size: 18,
            color: CumulusTone.danger.foreground,
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Text(
              'This was true when it was found, and one of the rows has moved '
              'since: ${moved.join('; ')}. Cumulus re-reads the ERP before '
              'writing anything, so an order that has moved is raised back to '
              'this desk rather than released.',
              style: CumulusText.body.copyWith(
                color: CumulusTone.danger.foreground,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The two books
// ---------------------------------------------------------------------------

/// What the CRM says, and what the ERP says.
///
/// Two columns and deliberately **not** a comparison table. The merge review
/// draws one table because both sides are the same type and a steward reads
/// across a row; here the two rows are different types with different fields,
/// and the disagreement is not in any one field — it is in the pair.
class _TwoBooks extends StatelessWidget {
  final DisagreementCase finding;

  const _TwoBooks({required this.finding});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FieldLabel(
          'One commitment, two accounts of it',
          hint: finding.counterparty.isEmpty
              ? 'joined on the counterparty book, not on a name'
              : 'joined on ${finding.counterparty} in the counterparty book, '
                    'not on a name',
        ),
        IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(child: _BookColumn(side: finding.crm)),
              const SizedBox(width: 12),
              Expanded(child: _BookColumn(side: finding.erp)),
            ],
          ),
        ),
      ],
    );
  }
}

class _BookColumn extends StatelessWidget {
  final DisagreementSide side;

  const _BookColumn({required this.side});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'What ${side.book} says'.toUpperCase(),
            style: CumulusText.eyebrow,
          ),
          const SizedBox(height: 8),
          if (side.missing)
            // Named rather than blank: a finding pointing at a row nothing
            // holds is itself a finding, and an empty column reads as a
            // rendering gap.
            Text(
              'This row is no longer in the book. The finding points at '
              '${side.id}, which nothing holds — leave it as it is, or sweep '
              'again.',
              style: CumulusText.meta.copyWith(color: CumulusColors.danger),
            )
          else ...[
            Text(side.label, style: CumulusText.rowTitle),
            const SizedBox(height: 3),
            Text(
              side.naturalKey,
              style: CumulusText.mono.copyWith(fontSize: 11.5),
            ),
            const SizedBox(height: 10),
            for (final fact in side.facts)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 88,
                      child: Text(fact.label, style: CumulusText.meta),
                    ),
                    Expanded(
                      child: SelectableText(
                        fact.value.isEmpty ? 'not held' : fact.value,
                        style: fact.value.isEmpty
                            ? CumulusText.meta
                            : CumulusText.body,
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 4),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final source in side.sources)
                  StatusBadge(source, tone: CumulusTone.teal, dense: true),
                if (side.sources.isEmpty)
                  StatusBadge('no source record', dense: true),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The evidence
// ---------------------------------------------------------------------------

/// Every signal, what it was worth, and the governed document that set the
/// weights. The merge review's table, over the other sweep's evidence — the two
/// passes record their signals in one shape, so there is one way to read them.
class _Evidence extends StatelessWidget {
  final DisagreementCase finding;
  final ApiClient api;

  const _Evidence({required this.finding, required this.api});

  @override
  Widget build(BuildContext context) {
    final f = finding;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FieldLabel(
          'Why these two rows',
          hint:
              '${f.signals.length} signal(s), blended to ${f.score} — no '
              'model, so every row is a date, a ratio or a name a person can '
              'check',
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
              if (f.signals.isEmpty)
                Text(
                  'This finding carries no recorded evidence. It was written '
                  'before the sweep kept its signals, or by something other '
                  'than the sweep.',
                  style: CumulusText.meta,
                )
              else
                for (final signal in f.signals)
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
                children: [
                  SizedBox(
                    width: 52,
                    child: Text(
                      f.score,
                      style: CumulusText.mono.copyWith(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(
                    width: 158,
                    child: Text('blended correlation', style: CumulusText.meta),
                  ),
                  Expanded(
                    child: Text(
                      'The window, the bands and the class table are governed '
                      'knowledge, not code. Open the policy to see where the '
                      'lines fall — and to move them.',
                      style: CumulusText.meta,
                    ),
                  ),
                ],
              ),
              if (f.policyRef.isNotEmpty) ...[
                const SizedBox(height: 10),
                RefChips(api: api, refs: [f.policyRef]),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// The write, before it happens
// ---------------------------------------------------------------------------

/// What approving would change, and — the half that is usually missing — what
/// it would leave exactly as it is.
///
/// Both halves are read off the write table server-side. The unchanged list is
/// derived there from the column above rather than typed here, so this screen
/// cannot promise something the Action would not honour.
class _BeforeAndAfter extends StatelessWidget {
  final DisagreementCase finding;

  const _BeforeAndAfter({required this.finding});

  @override
  Widget build(BuildContext context) {
    final change = finding.change;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FieldLabel(
          'What approving would do',
          hint: change.target.isEmpty
              ? 'read off the write table'
              : 'off the write table — ${change.target} · '
                    '${change.app}.${change.resource}/${change.recordId}',
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
              if (!change.permitted)
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(
                      Icons.block,
                      size: 15,
                      color: CumulusColors.danger,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        change.whyNot.isEmpty
                            ? 'Nothing about this order is writable, and the '
                                  'write table does not say why.'
                            : change.whyNot,
                        style: CumulusText.body.copyWith(
                          color: CumulusColors.danger,
                        ),
                      ),
                    ),
                  ],
                )
              else ...[
                _Line(
                  tone: CumulusTone.warn,
                  icon: Icons.east,
                  head: 'CHANGES',
                  body:
                      '${change.field}  ${change.fromValue} → '
                      '${change.toValue}',
                ),
                const SizedBox(height: 8),
                _Line(
                  tone: CumulusTone.success,
                  icon: Icons.lock_outline,
                  head: 'UNCHANGED',
                  body: change.unchanged.isEmpty
                      ? 'nothing else is shown for this order'
                      : change.unchanged.join(' · '),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

class _Line extends StatelessWidget {
  final CumulusTone tone;
  final IconData icon;
  final String head;
  final String body;

  const _Line({
    required this.tone,
    required this.icon,
    required this.head,
    required this.body,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 15, color: tone.foreground),
        const SizedBox(width: 8),
        SizedBox(
          width: 96,
          child: Text(
            head,
            style: CumulusText.statLabel.copyWith(color: tone.foreground),
          ),
        ),
        Expanded(
          child: SelectableText(
            body,
            style: CumulusText.mono.copyWith(fontSize: 12.5),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// The decision
// ---------------------------------------------------------------------------

class _Footer extends StatefulWidget {
  final DisagreementCase finding;
  final void Function(String verdict, String note)? onDecide;
  final bool busy;
  final String? error;

  const _Footer({
    required this.finding,
    required this.onDecide,
    required this.busy,
    required this.error,
  });

  @override
  State<_Footer> createState() => _FooterState();
}

class _FooterState extends State<_Footer> {
  final _note = TextEditingController();

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final decide = widget.onDecide;
    if (decide == null) return _GateStatement(finding: widget.finding);

    final f = widget.finding;
    final change = f.change;
    final order = f.erp.label.isEmpty ? 'this order' : f.erp.label;
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
                'Note (kept on the decision, and quoted in the run that '
                'carries it out)',
            isDense: true,
          ),
        ),
        if (widget.error != null) ...[
          const SizedBox(height: 10),
          // The no-Retry idiom: a refused or broken decision is a statement,
          // not a control. Retrying is the same press, and the Task is already
          // open again — a Retry button here would be a second way to make one
          // decision.
          Text(
            widget.error!,
            style: CumulusText.meta.copyWith(color: CumulusColors.danger),
          ),
        ],
        const SizedBox(height: 14),
        // Each control states its consequence above itself. They are genuinely
        // different consequences — one reaches another company's system and one
        // does not — and a shared sentence would have to describe neither.
        if (change.permitted)
          _ConsequenceButton(
            tone: CumulusTone.warn,
            icon: Icons.outbound,
            label: 'Release $order in the ERP',
            consequence:
                'This writes to Naviant\'s ERP. Sales order $order moves from '
                '${change.fromValue} to ${change.toValue}. Nothing else on the '
                'order changes. Cumulus re-reads the order afterwards to '
                'confirm the change took, and tells you if it did not. There '
                'is no undo here — moving it back is a second decision.',
            onPressed: widget.busy
                ? null
                : () => decide('approved', _note.text.trim()),
          )
        else
          // No control, because there is no consequence this screen can stand
          // behind. Stated rather than drawn disabled: a dead button that
          // cannot say why is what makes a refusal read as a bug.
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: CumulusColors.surfaceMuted,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('No release to offer', style: CumulusText.statLabel),
                const SizedBox(height: 6),
                Text(
                  change.whyNot.isEmpty
                      ? 'The write table does not permit a release for this '
                            'order, and does not say why.'
                      : change.whyNot,
                  style: CumulusText.body,
                ),
                const SizedBox(height: 6),
                Text(
                  'Leaving it as it is is still a decision you can make.',
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
        const SizedBox(height: 10),
        _ConsequenceButton(
          tone: CumulusTone.neutral,
          icon: Icons.do_not_disturb_on_outlined,
          label: 'Leave it as it is',
          consequence:
              'The disagreement stays on the record, marked dismissed. It '
              'will not be raised again unless the two systems change.',
          filled: false,
          onPressed: widget.busy
              ? null
              : () => decide('rejected', _note.text.trim()),
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

/// Why there are no controls. Always a sentence with the reason in it.
class _GateStatement extends StatelessWidget {
  final DisagreementCase finding;

  const _GateStatement({required this.finding});

  @override
  Widget build(BuildContext context) {
    final gate = finding.gate;
    final settled = !finding.isStanding;
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
            settled
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
          if (!settled && gate.hasTask) ...[
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
