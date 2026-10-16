import 'dart:convert';

import 'package:flutter/material.dart';

import '../models/run.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'document_drawer.dart';
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
              // The definition that ran, not the one that is live. A journal
              // read months later is evidence about a specific graph, and the
              // engine pinned which one at the first step.
              if (run.claimsVersion) MetaPill('ran v${run.flowVersion}'),
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
          for (final step in run.steps) _StepTile(api: api, step: step),
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

/// Every `document:` this step touched, from either side of the journal.
///
/// A step that read an artifact names it in its read-set and a step that
/// stored one names it in its write-set — but an extractor does neither: it
/// hands the id downstream in its output, which is how the invoice a run
/// actually looked at could be named on the tile and reachable from nowhere.
///
/// Bare ids are qualified on the way out, because a journal writes
/// `document_id: doc_28fee883` and a ref is `document:doc_28fee883`, and the
/// drawer opens on either.
List<String> stepDocuments(RunStep step) {
  final found = <String>{};
  void add(Object? value) {
    if (value is! String || value.isEmpty) return;
    found.add(value.startsWith('document:') ? value : 'document:$value');
  }

  for (final ref in [...step.refs, ...step.writes]) {
    if (ref.startsWith('document:')) found.add(ref);
  }
  final output = step.output;
  if (output is Map) {
    for (final key in const ['document_id', 'document_ref']) {
      add(output[key]);
    }
    final many = output['document_ids'];
    if (many is List) {
      for (final value in many) {
        add(value is String ? value : '$value');
      }
    }
  }
  return found.toList();
}

/// One journal entry, with its evidence live rather than printed.
///
/// The read-set and the write-set were rendered here as plain text and the
/// output was not rendered at all: a step could say it read `knowledge:ap/
/// posting-policy@rev4` and there was nothing to click, and what the step
/// actually returned — the thing the next step saw — was visible only from
/// the canvas inspector. Both halves are the same objects the run-level
/// chips already dereference, so they get the same chips.
class _StepTile extends StatefulWidget {
  final ApiClient api;
  final RunStep step;

  const _StepTile({required this.api, required this.step});

  @override
  State<_StepTile> createState() => _StepTileState();
}

class _StepTileState extends State<_StepTile> {
  /// Collapsed by default. A journal is read down the left edge for the shape
  /// of what happened; an output is opened when a particular step is the
  /// question, and a dozen expanded payloads would bury the shape.
  bool _outputOpen = false;

  RunStep get step => widget.step;

  @override
  Widget build(BuildContext context) {
    final tone = _toneForKind(step.kind);
    final failed = step.status == 'failed';
    // A document already standing in the read- or write-set is already a
    // chip; offering it a second time would read as two artifacts.
    final documents = [
      for (final ref in stepDocuments(step))
        if (!step.refs.contains(ref) && !step.writes.contains(ref)) ref,
    ];
    final output = step.output == null
        ? ''
        : const JsonEncoder.withIndent('  ').convert(step.output);
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
                  const SizedBox(height: 6),
                  _EvidenceRow(
                    label: 'read',
                    child: RefChips(
                      api: widget.api,
                      refs: step.refs,
                      tooltip:
                          'Pinned at the revision this step read, so the '
                          'decision stays explainable against the record as '
                          'it was.',
                    ),
                  ),
                ],
                if (step.writes.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  _EvidenceRow(
                    label: 'wrote',
                    child: RefChips(
                      api: widget.api,
                      refs: step.writes,
                      tone: CumulusTone.success,
                    ),
                  ),
                ],
                // The artifact itself, when the sets above did not already
                // name it. An extractor reads knowledge and writes objects and
                // carries its document id only in its output — which is how
                // the invoice a run looked at could be reachable from nowhere.
                if (documents.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  _EvidenceRow(
                    label: 'file',
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 6,
                      children: [
                        for (final ref in documents)
                          _OpenDocumentButton(api: widget.api, ref: ref),
                      ],
                    ),
                  ),
                ],
                if (output.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  _StepOutput(
                    payload: output,
                    open: _outputOpen,
                    onToggle: () =>
                        setState(() => _outputOpen = !_outputOpen),
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

/// A verb in the gutter, and the evidence beside it. One column width across
/// read/wrote/file, so the three sets line up down the journal instead of
/// each starting wherever its label happens to end.
class _EvidenceRow extends StatelessWidget {
  final String label;
  final Widget child;

  const _EvidenceRow({required this.label, required this.child});

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 42,
          child: Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(label, style: CumulusText.statCaption),
          ),
        ),
        Expanded(child: child),
      ],
    );
  }
}

/// Straight into the artifact, from the step that handled it.
///
/// Deliberately not a chip: the read-set and write-set are lists of what was
/// touched, and this is the one thing on the tile a reviewer opens to check
/// the machine's work with their own eyes.
class _OpenDocumentButton extends StatelessWidget {
  final ApiClient api;
  final String ref;

  const _OpenDocumentButton({required this.api, required this.ref});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message:
          'Open the artifact itself — the page, with the boxes the extractor '
          'claims it read each value from.',
      child: Material(
        color: CumulusColors.accentSoft,
        borderRadius: BorderRadius.circular(7),
        child: InkWell(
          onTap: () => openDocumentDrawer(context, api, ref),
          borderRadius: BorderRadius.circular(7),
          hoverColor: CumulusColors.surfaceMuted,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.description_outlined,
                  size: 13,
                  color: CumulusColors.accentInk,
                ),
                const SizedBox(width: 6),
                Text(
                  ref.startsWith('document:') ? ref.substring(9) : ref,
                  style: CumulusText.mono.copyWith(
                    color: CumulusColors.accentInk,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// What the step returned, on request.
///
/// The journal records the whole of what the next step saw — a step whose
/// result cannot be serialized is a step doing too much — so this is not a
/// summary of the output, it is the output. It stays folded because a journal
/// is read for the shape of a run first and one step's payload second.
class _StepOutput extends StatelessWidget {
  final String payload;
  final bool open;
  final VoidCallback onToggle;

  const _StepOutput({
    required this.payload,
    required this.open,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: onToggle,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 2, horizontal: 2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  open ? Icons.expand_less : Icons.expand_more,
                  size: 15,
                  color: CumulusColors.inkMuted,
                ),
                const SizedBox(width: 4),
                Text('OUTPUT', style: CumulusText.statLabel),
                const SizedBox(width: 8),
                Text(
                  open ? 'what the next step saw' : '${_lines(payload)} lines',
                  style: CumulusText.statCaption,
                ),
              ],
            ),
          ),
        ),
        if (open)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(top: 5),
            constraints: const BoxConstraints(maxHeight: 260),
            padding: const EdgeInsets.all(11),
            decoration: BoxDecoration(
              color: CumulusColors.surface,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
              border: Border.all(color: CumulusColors.border),
            ),
            child: SingleChildScrollView(
              child: SelectableText(
                payload,
                style: CumulusText.mono.copyWith(fontSize: 11.5, height: 1.45),
              ),
            ),
          ),
      ],
    );
  }

  static int _lines(String payload) => '\n'.allMatches(payload).length + 1;
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
