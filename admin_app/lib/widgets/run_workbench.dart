import 'package:flutter/material.dart';

import '../models/run.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// The Flow Runs list, and the journal behind one run.
///
/// Shared by both workspaces because a run is a run: the expense flow and the
/// AP flow differ in what their steps *do*, not in what a run *is*. What this
/// shows that a table of rows cannot:
///
/// - **the gates**. How many times a person was asked, and what they decided.
///   A row says "pending"; a run says "suspended at k2_1, second review round".
/// - **the read-set**. Which governed documents and graph objects each step
///   read, at which revision — so a decision is explainable against the
///   policy as it was, not as it is.
/// - **replay**. `resume_count` is how many times the journal was re-entered,
///   which is the number that makes durability visible rather than claimed.
class FlowRunsCard extends StatefulWidget {
  final ApiClient api;
  final String flowId;

  /// Shown under the card title.
  final String subtitle;

  const FlowRunsCard({
    super.key,
    required this.api,
    required this.flowId,
    this.subtitle = 'every run of this flow, newest first · click one for its journal',
  });

  @override
  State<FlowRunsCard> createState() => _FlowRunsCardState();
}

class _FlowRunsCardState extends State<FlowRunsCard> {
  List<FlowRunSummary>? _runs;
  String? _error;
  String _lens = 'all';
  String? _expandedRunId;
  FlowRun? _expanded;
  bool _loadingRun = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final runs = await widget.api.listRuns(flowId: widget.flowId, limit: 100);
      if (mounted) {
        setState(() {
          _runs = runs;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _toggle(String runId) async {
    if (_expandedRunId == runId) {
      setState(() {
        _expandedRunId = null;
        _expanded = null;
      });
      return;
    }
    setState(() {
      _expandedRunId = runId;
      _expanded = null;
      _loadingRun = true;
    });
    try {
      final run = await widget.api.getRun(runId);
      if (mounted && _expandedRunId == runId) {
        setState(() {
          _expanded = run;
          _loadingRun = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loadingRun = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final runs = _runs;
    if (_error != null && runs == null) {
      return CumulusCard(child: ErrorNotice(message: _error!, onRetry: _load));
    }
    if (runs == null) {
      return const CumulusCard(child: CumulusLoading());
    }
    final visible = _lens == 'all'
        ? runs
        : runs.where((r) => r.status == _lens).toList();
    return ListCard(
      title: 'Flow runs',
      subtitle: widget.subtitle,
      headerTrailing: Wrap(
        spacing: 6,
        children: [
          for (final lens in const ['all', 'succeeded', 'suspended', 'failed'])
            LensChip(
              label: lens.toUpperCase(),
              tone: _toneForStatus(lens),
              selected: _lens == lens,
              onTap: () => setState(() => _lens = lens),
            ),
        ],
      ),
      children: [
        if (visible.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 22),
            child: Center(
              child: Text('No runs in this state.', style: CumulusText.meta),
            ),
          ),
        for (final run in visible) ...[
          _RunRow(
            run: run,
            expanded: _expandedRunId == run.id,
            onTap: () => _toggle(run.id),
          ),
          if (_expandedRunId == run.id)
            _loadingRun
                ? const Padding(
                    padding: EdgeInsets.symmetric(vertical: 18),
                    child: CumulusLoading(),
                  )
                : RunJournalView(run: _expanded),
        ],
      ],
    );
  }
}

CumulusTone _toneForStatus(String status) => switch (status) {
  'succeeded' => CumulusTone.success,
  'suspended' => CumulusTone.warn,
  'failed' => CumulusTone.danger,
  'running' => CumulusTone.accent,
  _ => CumulusTone.neutral,
};

class _RunRow extends StatelessWidget {
  final FlowRunSummary run;
  final bool expanded;
  final VoidCallback onTap;

  const _RunRow({
    required this.run,
    required this.expanded,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final tone = _toneForStatus(run.status);
    return CumulusListRow(
      onTap: onTap,
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: tone.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(
          switch (run.status) {
            'succeeded' => Icons.check,
            'suspended' => Icons.pause,
            'failed' => Icons.priority_high,
            _ => Icons.play_arrow,
          },
          size: 17,
          color: tone.foreground,
        ),
      ),
      title: Row(
        children: [
          Flexible(child: Text(run.caseRef.isEmpty ? run.id : run.caseRef)),
          const SizedBox(width: 10),
          StatusBadge(run.status, tone: tone, dense: true),
          if (run.suspendedAtStep.isNotEmpty && run.status == 'suspended') ...[
            const SizedBox(width: 6),
            StatusBadge('at ${run.suspendedAtStep}', dense: true),
          ],
          if (run.resumeCount > 0) ...[
            const SizedBox(width: 6),
            Tooltip(
              message:
                  'The journal was re-entered ${run.resumeCount} time(s); '
                  'completed steps replayed rather than re-running.',
              child: StatusBadge(
                '${run.resumeCount}× resumed',
                tone: CumulusTone.accent,
                dense: true,
              ),
            ),
          ],
        ],
      ),
      subtitle: Text(
        '${run.steps} step(s) · ${run.modelCalls} ModelCall(s) · '
        '${run.durationMs}ms · ${run.refs.length} governed read(s)'
        '${run.actor.isEmpty ? '' : ' · ${run.actor}'}'
        '${run.error.isEmpty ? '' : ' · ${run.error}'}',
      ),
      trailing: [
        SizedBox(
          width: 96,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                _ago(run.startedAtIso),
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text(run.id, style: CumulusText.statCaption,
                  overflow: TextOverflow.ellipsis),
            ],
          ),
        ),
        Icon(
          expanded ? Icons.expand_less : Icons.expand_more,
          size: 20,
          color: CumulusColors.inkMuted,
        ),
      ],
    );
  }
}

/// One run's journal: the steps, in order, with what each read and returned.
class RunJournalView extends StatelessWidget {
  final FlowRun? run;

  const RunJournalView({super.key, required this.run});

  @override
  Widget build(BuildContext context) {
    final run = this.run;
    if (run == null) {
      return const Padding(
        padding: EdgeInsets.symmetric(horizontal: 20, vertical: 16),
        child: Text('Could not load this run.', style: CumulusText.meta),
      );
    }
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.55),
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: [
              MetaPill('${run.humanRounds} human round(s)'),
              MetaPill('${run.modelCalls} ModelCall(s)'),
              if (run.costUsd > 0)
                MetaPill('\$${run.costUsd.toStringAsFixed(4)} audited spend'),
              MetaPill('${run.durationMs}ms'),
              if (run.finishedAtIso.isNotEmpty)
                MetaPill('finished ${_ago(run.finishedAtIso)}'),
            ],
          ),
          if (run.writes.isNotEmpty) ...[
            const SizedBox(height: 14),
            Row(
              children: [
                Text('WRITE-SET', style: CumulusText.statLabel),
                const SizedBox(width: 10),
                Text(
                  '${run.writes.length} record(s) created or changed, in the '
                  'order the steps wrote them',
                  style: CumulusText.statCaption,
                ),
              ],
            ),
            const SizedBox(height: 6),
            _RefChips(refs: run.writes, tone: CumulusTone.success),
          ],
          if (run.refs.isNotEmpty) ...[
            const SizedBox(height: 14),
            Text('READ-SET', style: CumulusText.statLabel),
            const SizedBox(height: 6),
            _RefChips(
              refs: run.refs,
              tooltip:
                  'Pinned at the revision the run actually read, so the '
                  'decision stays explainable against the record as it was.',
            ),
          ],
          const SizedBox(height: 16),
          Text('JOURNAL', style: CumulusText.statLabel),
          const SizedBox(height: 8),
          for (final step in run.steps) _StepTile(step: step),
        ],
      ),
    );
  }
}

class _StepTile extends StatelessWidget {
  final RunStep step;

  const _StepTile({required this.step});

  @override
  Widget build(BuildContext context) {
    final tone = _toneForKind(step.kind);
    final failed = step.status == 'failed';
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 26,
            height: 26,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: failed
                  ? CumulusTone.danger.background
                  : step.status == 'suspended'
                      ? CumulusTone.warn.background
                      : tone.background,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Icon(
              _iconForKind(step.kind),
              size: 14,
              color: failed
                  ? CumulusTone.danger.foreground
                  : step.status == 'suspended'
                      ? CumulusTone.warn.foreground
                      : tone.foreground,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      step.name,
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: CumulusColors.ink,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(step.stepId, style: CumulusText.mono),
                    const SizedBox(width: 8),
                    StatusBadge(step.kind, tone: tone, dense: true),
                    if (step.attempts > 1) ...[
                      const SizedBox(width: 6),
                      StatusBadge(
                        '${step.attempts} attempts',
                        tone: CumulusTone.warn,
                        dense: true,
                      ),
                    ],
                    if (step.model.isNotEmpty) ...[
                      const SizedBox(width: 6),
                      StatusBadge(step.model, dense: true),
                    ],
                  ],
                ),
                const SizedBox(height: 3),
                Text(step.summary, style: CumulusText.body),
                if (step.decision != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    'decision: ${step.decision!['verdict']} by '
                    '${step.decision!['decided_by']}'
                    '${(step.decision!['note'] as String? ?? '').isEmpty ? '' : ' — ${step.decision!['note']}'}',
                    style: CumulusText.meta,
                  ),
                ],
                if (step.refs.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    'read  ${step.refs.join('  ·  ')}',
                    style: CumulusText.mono,
                  ),
                ],
                if (step.writes.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    'wrote ${step.writes.join('  ·  ')}',
                    style: CumulusText.mono.copyWith(
                      color: CumulusTone.success.foreground,
                    ),
                  ),
                ],
                if (step.error.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    step.error,
                    style: CumulusText.meta.copyWith(
                      color: CumulusColors.danger,
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: 12),
          Text(
            step.durationMs == 0 ? '' : '${step.durationMs}ms',
            style: CumulusText.statCaption,
          ),
        ],
      ),
    );
  }
}

CumulusTone _toneForKind(String kind) => switch (kind) {
  'modelcall' => CumulusTone.violet,
  'rule' || 'check' => CumulusTone.warn,
  'task' => CumulusTone.accent,
  'action' => CumulusTone.success,
  'resolve' => CumulusTone.teal,
  'sync' => CumulusTone.teal,
  _ => CumulusTone.neutral,
};

IconData _iconForKind(String kind) => switch (kind) {
  'trigger' => Icons.bolt,
  'modelcall' => Icons.auto_awesome,
  'transform' => Icons.tune,
  'rule' => Icons.rule,
  'check' => Icons.fact_check_outlined,
  'task' => Icons.person_outline,
  'action' => Icons.check_circle_outline,
  'event' => Icons.podcasts,
  'sync' => Icons.sync_alt,
  'resolve' => Icons.hub_outlined,
  _ => Icons.circle_outlined,
};

/// Refs as chips. Shared by the read-set and the write-set so the two read as
/// the same kind of thing seen from opposite ends.
class _RefChips extends StatelessWidget {
  final List<String> refs;
  final String tooltip;
  final CumulusTone tone;

  const _RefChips({
    required this.refs,
    this.tooltip = '',
    this.tone = CumulusTone.neutral,
  });

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 6,
      children: [
        for (final ref in refs)
          Tooltip(
            message: tooltip.isEmpty ? _describe(ref) : tooltip,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
              decoration: BoxDecoration(
                color: tone == CumulusTone.neutral
                    ? CumulusColors.surface
                    : tone.background,
                borderRadius: BorderRadius.circular(7),
                border: Border.all(
                  color: tone == CumulusTone.neutral
                      ? CumulusColors.border
                      : Colors.transparent,
                ),
              ),
              child: Text(
                ref,
                style: CumulusText.mono.copyWith(
                  color: tone == CumulusTone.neutral
                      ? CumulusColors.inkSecondary
                      : tone.foreground,
                ),
              ),
            ),
          ),
      ],
    );
  }

  static String _describe(String ref) {
    if (ref.startsWith('link:')) {
      return 'A typed edge this step asserted. Identity edges can be '
          'withdrawn later without erasing that they were made.';
    }
    if (ref.startsWith('graph:')) {
      return 'An entity this step minted or claimed on, at the revision it '
          'left behind.';
    }
    if (ref.startsWith('document:')) {
      return 'An artifact stored under its Collection’s retention.';
    }
    if (ref.startsWith('knowledge:')) {
      return 'A governed-knowledge write — promotion into canonical knowledge '
          'is a decision, never a side effect.';
    }
    return ref;
  }
}

String _ago(String iso) {
  if (iso.isEmpty) return '—';
  final when = DateTime.tryParse(iso);
  return when == null ? '—' : agoLabel(when);
}
