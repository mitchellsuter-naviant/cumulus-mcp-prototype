import 'package:flutter/material.dart';

import '../models/run.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ref_chips.dart';
import 'ui_kit.dart';

/// How long a run took, or an honest silence.
///
/// Every seeded run reports `0ms`, because the durable engine records a
/// duration only for steps that actually waited on something and the stub
/// readers wait on nothing. Printing "0ms" claims a measurement of zero;
/// "not timed" says there is no measurement, which is the true statement and
/// the one that stops a reader believing the flow is instantaneous.
String _duration(int ms) => ms <= 0 ? 'not timed' : '${ms}ms';

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
    this.subtitle =
        'every run of this flow, newest first · click one for its journal',
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
      return CumulusCard(
        child: ErrorNotice(message: _error!, onRetry: _load),
      );
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
              tone: runTone(lens),
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
                : RunJournalView(api: widget.api, run: _expanded),
        ],
      ],
    );
  }
}

/// One vocabulary for run status across the console: the four states
/// `runs.py` actually stores.
CumulusTone runTone(String status) => switch (status) {
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
    final tone = runTone(run.status);
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
        '${_duration(run.durationMs)} · ${run.refs.length} governed read(s)'
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
                agoIso(run.startedAtIso),
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text(
                run.id,
                style: CumulusText.statCaption,
                overflow: TextOverflow.ellipsis,
              ),
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
  final ApiClient api;
  final FlowRun? run;

  const RunJournalView({super.key, required this.api, required this.run});

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
              MetaPill(_duration(run.durationMs)),
              if (run.finishedAtIso.isNotEmpty)
                MetaPill('finished ${agoIso(run.finishedAtIso)}'),
            ],
          ),
          if (run.inputs.isNotEmpty) ...[
            const SizedBox(height: 14),
            Row(
              children: [
                Text('INPUTS', style: CumulusText.statLabel),
                const SizedBox(width: 10),
                Text(
                  'what the trigger was handed — the run reads these and '
                  'nothing else',
                  style: CumulusText.statCaption,
                ),
              ],
            ),
            const SizedBox(height: 6),
            _RunInputs(inputs: run.inputs),
          ],
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
            RefChips(api: api, refs: run.writes, tone: CumulusTone.success),
          ],
          if (run.refs.isNotEmpty) ...[
            const SizedBox(height: 14),
            Text('READ-SET', style: CumulusText.statLabel),
            const SizedBox(height: 6),
            RefChips(
              api: api,
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

/// The trigger's own inputs, which the journal never showed.
///
/// The read-set, the write-set and every step were rendered here and the thing
/// the run was actually *given* was not: a submitter's commentary is a governed
/// input the projection reads and the journal quotes, and it was visible
/// nowhere in the console.
///
/// Base64 is summarised rather than printed. It is the artifact, it is already
/// held under retention with a `document:` ref pointing at it, and a screenful
/// of it would bury the fields a person came here to read.
class _RunInputs extends StatelessWidget {
  final Map<String, dynamic> inputs;

  const _RunInputs({required this.inputs});

  @override
  Widget build(BuildContext context) {
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
          for (final entry in inputs.entries)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 150,
                    child: Text(entry.key, style: CumulusText.mono),
                  ),
                  Expanded(
                    child: Text(
                      _render(entry.value),
                      style: CumulusText.body,
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  static String _render(dynamic value) {
    if (value is List) {
      return value.isEmpty
          ? '(none)'
          : value.map(_render).join('\n');
    }
    if (value is Map) {
      final parts = <String>[];
      value.forEach((key, item) {
        if (item == null) return;
        final text = '$item';
        if (text.isEmpty) return;
        // The artifact itself: named and measured, never printed. It is held
        // under retention with a `document:` chip in the write-set pointing
        // at it, which is the thing to click.
        parts.add(
          key == 'content_b64'
              ? 'content: ${_approxBytes(text.length)} (stored — open the '
                    'document chip below)'
              : '$key: $text',
        );
      });
      return parts.isEmpty ? '(empty)' : parts.join('  ·  ');
    }
    final text = '$value';
    return text.isEmpty ? '(empty)' : text;
  }

  /// Base64 inflates by a third, so the decoded size is what to report.
  static String _approxBytes(int base64Length) {
    final size = (base64Length * 3) ~/ 4;
    return size < 1024
        ? '$size B'
        : size < 1024 * 1024
        ? '${(size / 1024).toStringAsFixed(1)} KB'
        : '${(size / 1024 / 1024).toStringAsFixed(1)} MB';
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
