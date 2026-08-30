import 'package:flutter/material.dart';

import '../models/task.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'review_screens.dart';
import 'ui_kit.dart';

/// My Tasks — the queue as a destination rather than a drawer.
///
/// It used to hang off a badged button in the top bar, opened as a 660px
/// panel, and that was the wrong shape for the work: deciding a task means
/// reading evidence, following the refs it rests on, and often opening the
/// full review to correct the facts. A rail stop with a list and a review pane
/// gives that room, and it puts the queue where the other things a person owns
/// already are — next to their day and their week.
///
/// The doctrine is unchanged and visible in the layout: an agent proposes
/// (the proposal block), a person decides (the verdict row), the system
/// resumes (the "on decide" line). Tasks route to Queues, never to people, so
/// the list groups and filters by queue.
class TasksSection extends StatefulWidget {
  /// The shell's client — the shell owns its lifecycle, so this never closes
  /// it.
  final ApiClient api;

  /// Fired whenever the open count may have moved, so the rail badge can
  /// follow a decision made here.
  final VoidCallback? onQueueChanged;

  const TasksSection({super.key, required this.api, this.onQueueChanged});

  @override
  State<TasksSection> createState() => _TasksSectionState();
}

class _TasksSectionState extends State<TasksSection> {
  List<QueueTask>? _tasks;
  String? _error;
  bool _showDone = false;
  String _queue = '';
  String? _selectedId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final tasks = await widget.api.listTasks();
      if (!mounted) return;
      setState(() {
        _tasks = tasks;
        _error = null;
        _selectedId ??= tasks
            .where((t) => t.isOpen)
            .map((t) => t.id)
            .firstOrNull;
      });
      widget.onQueueChanged?.call();
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  void _replace(QueueTask decided) {
    setState(() {
      _tasks = [
        for (final t in _tasks ?? <QueueTask>[])
          if (t.id == decided.id) decided else t,
      ];
    });
    widget.onQueueChanged?.call();
  }

  List<QueueTask> get _visible {
    final all = _tasks ?? const <QueueTask>[];
    final band = all.where((t) => t.isOpen != _showDone).toList();
    final scoped = _queue.isEmpty
        ? band
        : band.where((t) => t.queue == _queue).toList();
    // Decided tasks read newest-first — it is a record, not a worklist.
    return _showDone ? scoped.reversed.toList() : scoped;
  }

  @override
  Widget build(BuildContext context) {
    final tasks = _tasks;
    final open = (tasks ?? const <QueueTask>[]).where((t) => t.isOpen).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: 'Task queue · yours',
            title: 'My Tasks',
            subtitle:
                'An agent proposes, you decide, the system resumes. Tasks '
                'route to Queues rather than to people, so work survives '
                'absence — and every decision is recorded and attributed.',
            pills: [
              if (open.isNotEmpty) MetaPill('${open.length} open'),
              if (open.any((t) => t.isHigh))
                MetaPill(
                  '${open.where((t) => t.isHigh).length} high priority',
                  tone: CumulusTone.danger,
                ),
            ],
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
        ),
        const SizedBox(height: 16),
        Expanded(child: _body(tasks)),
      ],
    );
  }

  Widget _body(List<QueueTask>? tasks) {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load the queue',
        message: _error!,
      );
    }
    if (tasks == null) return const CumulusLoading();

    final open = tasks.where((t) => t.isOpen).toList();
    final done = tasks.where((t) => !t.isOpen).toList();
    final selected = tasks.where((t) => t.id == _selectedId).firstOrNull;

    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= 1040;
        final list = _list(open.length, done.length);
        if (!wide) {
          // Narrow: the list is the screen, and a task opens over it.
          return list;
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(width: 440, child: list),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(6, 0, 28, 24),
                child: selected == null
                    ? const CumulusEmptyState(
                        icon: Icons.task_alt,
                        title: 'Nothing selected',
                        message:
                            'Pick a task to read its evidence and decide it.',
                      )
                    : _TaskReview(
                        key: ValueKey(selected.id),
                        task: selected,
                        api: widget.api,
                        onDecided: _replace,
                      ),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _list(int openCount, int doneCount) {
    final queues = <String, int>{};
    for (final t in (_tasks ?? const <QueueTask>[])) {
      if (t.isOpen != _showDone) {
        queues[t.queue] = (queues[t.queue] ?? 0) + 1;
      }
    }
    final visible = _visible;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 0, 14, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SegmentedTabs(
                labels: ['Open · $openCount', 'Decided · $doneCount'],
                selectedIndex: _showDone ? 1 : 0,
                onChanged: (i) => setState(() {
                  _showDone = i == 1;
                  _queue = '';
                  _selectedId = _visible.firstOrNull?.id;
                }),
              ),
              if (queues.length > 1) ...[
                const SizedBox(height: 12),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    LensChip(
                      label: 'All queues',
                      selected: _queue.isEmpty,
                      onTap: () => setState(() => _queue = ''),
                    ),
                    for (final entry in queues.entries)
                      LensChip(
                        label: '${entry.key} · ${entry.value}',
                        selected: _queue == entry.key,
                        onTap: () => setState(() {
                          _queue = _queue == entry.key ? '' : entry.key;
                          _selectedId = _visible.firstOrNull?.id;
                        }),
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
        Expanded(
          child: visible.isEmpty
              ? CumulusEmptyState(
                  icon: _showDone ? Icons.history : Icons.task_alt,
                  title: _showDone ? 'No decisions yet' : 'Queue is clear',
                  message: _showDone
                      ? 'Decided tasks stay here — the record is the point.'
                      : 'Nothing is waiting on your judgment right now.',
                )
              : ListView.builder(
                  padding: const EdgeInsets.fromLTRB(28, 0, 14, 32),
                  itemCount: visible.length,
                  itemBuilder: (context, i) => Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: _TaskRow(
                      task: visible[i],
                      selected: visible[i].id == _selectedId,
                      onTap: () =>
                          setState(() => _selectedId = visible[i].id),
                    ),
                  ),
                ),
        ),
      ],
    );
  }
}

/// One row in the queue list: what kind of judgment, how urgent, how old.
class _TaskRow extends StatelessWidget {
  final QueueTask task;
  final bool selected;
  final VoidCallback onTap;

  const _TaskRow({
    required this.task,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final tone = _toneFor(task);
    return CumulusCard(
      onTap: onTap,
      padding: const EdgeInsets.fromLTRB(16, 14, 14, 14),
      borderColor: selected ? CumulusColors.accent : null,
      borderWidth: selected ? 1.4 : 1,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (task.isOpen)
                _PriorityTag(high: task.isHigh)
              else
                StatusBadge(
                  task.decision?.verdict ?? 'done',
                  tone: switch (task.decision?.verdict) {
                    'approved' => CumulusTone.success,
                    'rejected' => CumulusTone.danger,
                    _ => CumulusTone.warn,
                  },
                  dense: true,
                ),
              const Spacer(),
              Text(_age(task.createdAtIso), style: CumulusText.meta),
            ],
          ),
          const SizedBox(height: 10),
          Text(task.title, style: CumulusText.rowTitle),
          const SizedBox(height: 6),
          Row(
            children: [
              Icon(_iconFor(task.kind), size: 13, color: tone.foreground),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  [
                    kindLabel(task.kind).toUpperCase(),
                    task.queue,
                  ].join('  ·  '),
                  style: CumulusText.statLabel,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _PriorityTag extends StatelessWidget {
  final bool high;

  const _PriorityTag({required this.high});

  @override
  Widget build(BuildContext context) {
    final tone = high ? CumulusTone.danger : CumulusTone.neutral;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 7,
          height: 7,
          decoration: BoxDecoration(color: tone.dot, shape: BoxShape.circle),
        ),
        const SizedBox(width: 7),
        Text(
          high ? 'HIGH' : 'NORMAL',
          style: CumulusText.statLabel.copyWith(color: tone.foreground),
        ),
      ],
    );
  }
}

/// The review pane: everything the decision rests on, then the decision.
class _TaskReview extends StatefulWidget {
  final QueueTask task;
  final ApiClient api;
  final ValueChanged<QueueTask> onDecided;

  const _TaskReview({
    super.key,
    required this.task,
    required this.api,
    required this.onDecided,
  });

  @override
  State<_TaskReview> createState() => _TaskReviewState();
}

class _TaskReviewState extends State<_TaskReview> {
  final _note = TextEditingController();
  final _correction = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _note.dispose();
    _correction.dispose();
    super.dispose();
  }

  /// Open the bespoke review screen for this task, if it has one.
  ///
  /// This pane is for judgment on the evidence as given. Anything that needs a
  /// person to *edit the facts* — correct an amount, split a receipt, pick the
  /// right vendor — happens in the full screen, where the checks then run
  /// again over what was changed.
  Future<void> _openReview() async {
    final outcome = await openReviewScreen(
      context: context,
      api: widget.api,
      task: widget.task,
    );
    if (outcome == null || !mounted) return;
    await _decide(
      outcome.verdict,
      note: outcome.note,
      payload: outcome.payload,
    );
  }

  Future<void> _decide(
    String verdict, {
    String? note,
    Map<String, dynamic> payload = const {},
  }) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final decided = await widget.api.decideTask(
        widget.task.id,
        verdict: verdict,
        note: (note ?? _note.text).trim(),
        correction: _correction.text.trim(),
        payload: payload,
      );
      widget.onDecided(decided);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = e.statusCode == 409
              ? 'Someone already decided this task.'
              : e.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(24, 22, 24, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 6,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      if (task.isOpen)
                        StatusBadge(
                          task.isHigh ? 'high' : 'normal',
                          tone: task.isHigh
                              ? CumulusTone.danger
                              : CumulusTone.neutral,
                          dense: true,
                          uppercase: true,
                        ),
                      StatusBadge(
                        kindLabel(task.kind),
                        tone: _toneFor(task),
                        dense: true,
                      ),
                      MetaPill(task.queue),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                Text(
                  task.flowId.isEmpty
                      ? 'Raised by the console'
                      : 'Raised by ${task.flowId}',
                  style: CumulusText.meta,
                ),
              ],
            ),
            const SizedBox(height: 14),
            Text(task.title, style: CumulusText.pageTitle),
            if (task.summary.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(task.summary, style: CumulusText.pageSubtitle),
            ],
            const SizedBox(height: 18),
            const Divider(height: 1),
            const SizedBox(height: 18),
            if (task.evidence.isNotEmpty) ...[
              const FieldLabel('Why this needs you'),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: CumulusColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final e in task.evidence)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 7),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SizedBox(
                              width: 180,
                              child: Text(e.label, style: CumulusText.meta),
                            ),
                            Expanded(
                              child: SelectableText(
                                e.value,
                                style: CumulusText.body.copyWith(
                                  color: CumulusColors.ink,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 18),
            ],
            if (task.refs.isNotEmpty) ...[
              const FieldLabel('Sources it rests on'),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final ref in task.refs)
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 9,
                        vertical: 5,
                      ),
                      decoration: BoxDecoration(
                        color: CumulusColors.surface,
                        borderRadius: BorderRadius.circular(7),
                        border: Border.all(color: CumulusColors.border),
                      ),
                      child: Text(
                        ref,
                        style: CumulusText.mono.copyWith(fontSize: 11.5),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 18),
            ],
            if (task.proposal != null) ...[
              const FieldLabel('Proposed action'),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: CumulusColors.accentSoft,
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      task.proposal!.target.isEmpty
                          ? task.proposal!.action
                          : '${task.proposal!.action} → '
                                '${task.proposal!.target}',
                      style: CumulusText.mono.copyWith(
                        fontSize: 12,
                        color: CumulusColors.accentInk,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (task.proposal!.summary.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(task.proposal!.summary, style: CumulusText.body),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 10),
            ],
            if (task.resumes.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Text(
                  'On decide: ${task.resumes}',
                  style: CumulusText.meta,
                ),
              ),
            if (task.isOpen) ..._decisionControls(task) else _decided(task),
          ],
        ),
      ),
    );
  }

  List<Widget> _decisionControls(QueueTask task) => [
    if (hasReviewScreen(task)) ...[
      SizedBox(
        width: double.infinity,
        child: FilledButton.icon(
          onPressed: _busy ? null : _openReview,
          icon: const Icon(Icons.open_in_full, size: 16),
          label: Text(reviewLabel(task)),
        ),
      ),
      const SizedBox(height: 8),
      Text(
        'Opens the full screen, where the facts themselves can be corrected '
        '— the checks then run again over what you changed.',
        style: CumulusText.meta,
      ),
      const SizedBox(height: 18),
      const Divider(height: 1),
      const SizedBox(height: 18),
    ],
    TextField(
      controller: _note,
      enabled: !_busy,
      decoration: const InputDecoration(
        labelText: 'Decision note (optional, recorded)',
        isDense: true,
      ),
    ),
    const SizedBox(height: 10),
    TextField(
      controller: _correction,
      enabled: !_busy,
      onChanged: (_) => setState(() {}),
      decoration: const InputDecoration(
        labelText: 'Correction — fill to decide as “corrected”',
        isDense: true,
      ),
    ),
    if (_error != null) ...[
      const SizedBox(height: 10),
      Text(
        _error!,
        style: CumulusText.meta.copyWith(color: CumulusColors.danger),
      ),
    ],
    const SizedBox(height: 16),
    Row(
      children: [
        FilledButton.icon(
          onPressed: _busy ? null : () => _decide('approved'),
          icon: const Icon(Icons.check, size: 16),
          label: const Text('Approve'),
        ),
        const SizedBox(width: 10),
        OutlinedButton.icon(
          onPressed: _busy || _correction.text.trim().isEmpty
              ? null
              : () => _decide('corrected'),
          icon: const Icon(Icons.edit_outlined, size: 16),
          label: const Text('Correct'),
        ),
        const SizedBox(width: 10),
        OutlinedButton.icon(
          onPressed: _busy ? null : () => _decide('rejected'),
          style: OutlinedButton.styleFrom(
            foregroundColor: CumulusColors.danger,
          ),
          icon: const Icon(Icons.close, size: 16),
          label: const Text('Reject'),
        ),
        if (_busy) ...[
          const SizedBox(width: 14),
          const SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ],
      ],
    ),
  ];

  Widget _decided(QueueTask task) {
    final decision = task.decision;
    if (decision == null) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const FieldLabel('Decision'),
        Text(
          '${decision.verdict} by ${decision.decidedBy}',
          style: CumulusText.body.copyWith(color: CumulusColors.ink),
        ),
        if (decision.correction.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              'Correction: ${decision.correction}',
              style: CumulusText.body,
            ),
          ),
        if (decision.note.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text('“${decision.note}”', style: CumulusText.body),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Shared vocabulary
// ---------------------------------------------------------------------------

String kindLabel(String kind) => switch (kind) {
  'decision' => 'Flow decision',
  'match_review' => 'Entity match review',
  'curation' => 'Knowledge curation',
  'eval_review' => 'Eval review',
  'exception' => 'Exception',
  _ => kind,
};

IconData _iconFor(String kind) => switch (kind) {
  'decision' => Icons.gavel_outlined,
  'match_review' => Icons.join_left_outlined,
  'curation' => Icons.auto_stories_outlined,
  'eval_review' => Icons.grading_outlined,
  'exception' => Icons.report_gmailerrorred_outlined,
  _ => Icons.inbox_outlined,
};

CumulusTone _toneFor(QueueTask task) => switch (task.kind) {
  'decision' => CumulusTone.accent,
  'match_review' => CumulusTone.teal,
  'curation' => CumulusTone.violet,
  'eval_review' => CumulusTone.neutral,
  'exception' => CumulusTone.danger,
  _ => CumulusTone.neutral,
};

/// Which tasks have a bespoke review screen. Everything else gets the pane's
/// approve/reject, which is the honest affordance when there is nothing
/// structured to edit.
bool hasReviewScreen(QueueTask task) =>
    task.kind == 'match_review' ||
    task.flowId == 'expense-reimbursement' ||
    task.flowId == 'accounts-payable';

String reviewLabel(QueueTask task) {
  if (task.kind == 'match_review') return 'Open the match review';
  if (task.flowId == 'expense-reimbursement') return 'Open the expense review';
  if (task.kind == 'curation') return 'Open the extraction verification';
  return 'Open the invoice review';
}

/// `12m`, `2h`, `3d` — how long this has been waiting.
String _age(String iso) {
  final at = DateTime.tryParse(iso);
  if (at == null) return '';
  final delta = DateTime.now().toUtc().difference(at.toUtc());
  if (delta.inMinutes < 60) return '${delta.inMinutes.clamp(0, 59)}m';
  if (delta.inHours < 48) return '${delta.inHours}h';
  return '${delta.inDays}d';
}
