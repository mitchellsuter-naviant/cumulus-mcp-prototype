import 'package:flutter/material.dart';

import '../models/task.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'side_drawer.dart';
import 'ui_kit.dart';

/// The Task Queue's front door: a badged button in the top bar's account
/// cluster, present on every screen — judgment is requested from wherever you
/// are, not from a place you visit. Opens "My Tasks" as a side drawer.
///
/// The badge shows the open count and refreshes when the shell first builds
/// and every time the drawer closes, so a decision made in the panel is
/// reflected the moment it lands. It does not poll: the prototype has no
/// push channel yet, and a silently wrong number is worse than a slightly
/// stale one that corrects on every interaction.
class TaskQueueButton extends StatefulWidget {
  final ApiClient api;

  const TaskQueueButton({super.key, required this.api});

  @override
  State<TaskQueueButton> createState() => _TaskQueueButtonState();
}

class _TaskQueueButtonState extends State<TaskQueueButton> {
  TaskStats _stats = TaskStats.empty;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final stats = await widget.api.taskStats();
      if (mounted) setState(() => _stats = stats);
    } on ApiException {
      // The badge is a convenience; a failed count never blocks the shell.
    }
  }

  Future<void> _open() async {
    await showSideDrawer(
      context: context,
      title: 'My Tasks',
      subtitle:
          'An agent proposes, you decide, the system resumes. '
          'Tasks route to Queues; every decision is recorded and attributed.',
      width: 660,
      builder: (_) => _TaskQueuePanel(api: widget.api),
    );
    // Whatever was decided in there, the badge tells the truth on close.
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final urgent = _stats.highPriority > 0;
    return Tooltip(
      message: _stats.open == 0
          ? 'Task queue'
          : 'Task queue · ${_stats.open} open'
                '${urgent ? ' · ${_stats.highPriority} high priority' : ''}',
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: _open,
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: CumulusColors.surface,
                shape: BoxShape.circle,
                border: Border.all(color: CumulusColors.border),
                boxShadow: CumulusShadows.card,
              ),
              child: const Icon(
                Icons.inbox_outlined,
                size: 17,
                color: CumulusColors.inkSecondary,
              ),
            ),
            if (_stats.open > 0)
              Positioned(
                top: -4,
                right: -6,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 5,
                    vertical: 1.5,
                  ),
                  decoration: BoxDecoration(
                    color: urgent ? CumulusColors.danger : CumulusColors.ink,
                    borderRadius: BorderRadius.circular(CumulusRadius.pill),
                    border: Border.all(color: CumulusColors.canvas, width: 1.5),
                  ),
                  child: Text(
                    _stats.open > 99 ? '99+' : '${_stats.open}',
                    style: const TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      color: Colors.white,
                      height: 1.2,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _TaskQueuePanel extends StatefulWidget {
  final ApiClient api;

  const _TaskQueuePanel({required this.api});

  @override
  State<_TaskQueuePanel> createState() => _TaskQueuePanelState();
}

class _TaskQueuePanelState extends State<_TaskQueuePanel> {
  List<QueueTask>? _tasks;
  String? _error;
  bool _showDone = false;
  String? _expanded;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final tasks = await widget.api.listTasks();
      if (mounted) {
        setState(() {
          _tasks = tasks;
          _error = null;
        });
      }
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
      _expanded = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final tasks = _tasks;
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load the queue',
        message: _error!,
      );
    }
    if (tasks == null) {
      return const Padding(
        padding: EdgeInsets.only(top: 48),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2.5)),
      );
    }

    final open = tasks.where((t) => t.isOpen).toList();
    final done = tasks.where((t) => !t.isOpen).toList();
    final visible = _showDone ? done : open;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            SegmentedTabs(
              labels: ['Open · ${open.length}', 'Decided · ${done.length}'],
              selectedIndex: _showDone ? 1 : 0,
              onChanged: (i) => setState(() {
                _showDone = i == 1;
                _expanded = null;
              }),
            ),
            const Spacer(),
            IconButton(
              tooltip: 'Refresh',
              icon: const Icon(Icons.refresh, size: 17),
              color: CumulusColors.inkSecondary,
              onPressed: _load,
            ),
          ],
        ),
        const SizedBox(height: 14),
        if (visible.isEmpty)
          CumulusEmptyState(
            icon: _showDone ? Icons.history : Icons.task_alt,
            title: _showDone ? 'No decisions yet' : 'Queue is clear',
            message: _showDone
                ? 'Decided tasks stay here — the record is the point.'
                : 'Nothing is waiting on your judgment right now.',
          )
        else if (_showDone)
          ..._flat(done)
        else
          ..._grouped(open),
      ],
    );
  }

  /// Open tasks read queue by queue: the Queue is the doctrinal unit of
  /// work distribution, so it is also the unit of display.
  List<Widget> _grouped(List<QueueTask> open) {
    final queues = <String, List<QueueTask>>{};
    for (final t in open) {
      queues.putIfAbsent(t.queue, () => []).add(t);
    }
    final out = <Widget>[];
    for (final entry in queues.entries) {
      out.add(
        Padding(
          padding: const EdgeInsets.only(top: 8, bottom: 8),
          child: Row(
            children: [
              Text(entry.key.toUpperCase(), style: CumulusText.sectionTitle),
              const SizedBox(width: 8),
              Text('${entry.value.length} waiting', style: CumulusText.meta),
            ],
          ),
        ),
      );
      out.addAll(entry.value.map(_row));
      out.add(const SizedBox(height: 10));
    }
    return out;
  }

  List<Widget> _flat(List<QueueTask> done) =>
      [for (final t in done.reversed) _row(t)];

  Widget _row(QueueTask task) {
    final expanded = _expanded == task.id;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: CumulusCard(
        padding: EdgeInsets.zero,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            InkWell(
              borderRadius: BorderRadius.circular(CumulusRadius.card),
              onTap: () =>
                  setState(() => _expanded = expanded ? null : task.id),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
                child: Row(
                  children: [
                    _KindGlyph(kind: task.kind),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(task.title, style: CumulusText.rowTitle),
                          const SizedBox(height: 3),
                          Text(
                            [
                              _kindLabel(task.kind),
                              if (task.flowId.isNotEmpty) task.flowId,
                              if (task.caseRef.isNotEmpty) task.caseRef,
                            ].join(' · '),
                            style: CumulusText.meta,
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (task.isOpen && task.isHigh)
                      const Padding(
                        padding: EdgeInsets.only(right: 8),
                        child: StatusBadge(
                          'high',
                          tone: CumulusTone.danger,
                          dense: true,
                          uppercase: true,
                        ),
                      ),
                    if (!task.isOpen)
                      StatusBadge(
                        task.decision?.verdict ?? 'done',
                        tone: switch (task.decision?.verdict) {
                          'approved' => CumulusTone.success,
                          'rejected' => CumulusTone.danger,
                          _ => CumulusTone.warn,
                        },
                        dense: true,
                      ),
                    Icon(
                      expanded ? Icons.expand_less : Icons.expand_more,
                      size: 18,
                      color: CumulusColors.inkMuted,
                    ),
                  ],
                ),
              ),
            ),
            if (expanded)
              _TaskDetail(
                task: task,
                api: widget.api,
                onDecided: _replace,
              ),
          ],
        ),
      ),
    );
  }
}

String _kindLabel(String kind) => switch (kind) {
  'decision' => 'Flow decision',
  'match_review' => 'Entity match review',
  'curation' => 'Knowledge curation',
  'eval_review' => 'Eval review',
  'exception' => 'Exception',
  _ => kind,
};

class _KindGlyph extends StatelessWidget {
  final String kind;

  const _KindGlyph({required this.kind});

  @override
  Widget build(BuildContext context) {
    final (icon, tone) = switch (kind) {
      'decision' => (Icons.gavel_outlined, CumulusTone.accent),
      'match_review' => (Icons.join_left_outlined, CumulusTone.teal),
      'curation' => (Icons.auto_stories_outlined, CumulusTone.violet),
      'eval_review' => (Icons.grading_outlined, CumulusTone.neutral),
      'exception' => (Icons.report_gmailerrorred_outlined, CumulusTone.danger),
      _ => (Icons.inbox_outlined, CumulusTone.neutral),
    };
    return Container(
      width: 32,
      height: 32,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Icon(icon, size: 17, color: tone.foreground),
    );
  }
}

class _TaskDetail extends StatefulWidget {
  final QueueTask task;
  final ApiClient api;
  final ValueChanged<QueueTask> onDecided;

  const _TaskDetail({
    required this.task,
    required this.api,
    required this.onDecided,
  });

  @override
  State<_TaskDetail> createState() => _TaskDetailState();
}

class _TaskDetailState extends State<_TaskDetail> {
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

  Future<void> _decide(String verdict) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final decided = await widget.api.decideTask(
        widget.task.id,
        verdict: verdict,
        note: _note.text.trim(),
        correction: _correction.text.trim(),
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
    return Container(
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: CumulusColors.border)),
      ),
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (task.summary.isNotEmpty) ...[
            Text(task.summary, style: CumulusText.body),
            const SizedBox(height: 14),
          ],
          if (task.evidence.isNotEmpty) ...[
            const FieldLabel('Evidence'),
            ...task.evidence.map(
              (e) => Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 170,
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
            ),
            const SizedBox(height: 12),
          ],
          if (task.refs.isNotEmpty) ...[
            const FieldLabel('Rests on'),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final ref in task.refs)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: CumulusColors.surfaceMuted,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: CumulusColors.border),
                    ),
                    child: Text(
                      ref,
                      style: CumulusText.mono.copyWith(fontSize: 11),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 12),
          ],
          if (task.proposal != null) ...[
            const FieldLabel('Proposed action'),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
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
                        : '${task.proposal!.action} → ${task.proposal!.target}',
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
            const SizedBox(height: 12),
          ],
          if (task.resumes.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text('On decide: ${task.resumes}', style: CumulusText.meta),
            ),
          if (task.isOpen) ...[
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
            const SizedBox(height: 14),
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
          ] else if (task.decision != null) ...[
            const FieldLabel('Decision'),
            Text(
              '${task.decision!.verdict} by ${task.decision!.decidedBy}',
              style: CumulusText.body.copyWith(color: CumulusColors.ink),
            ),
            if (task.decision!.correction.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Correction: ${task.decision!.correction}',
                  style: CumulusText.body,
                ),
              ),
            if (task.decision!.note.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  '“${task.decision!.note}”',
                  style: CumulusText.body,
                ),
              ),
          ],
        ],
      ),
    );
  }
}
