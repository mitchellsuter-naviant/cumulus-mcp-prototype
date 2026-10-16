/// Desks: the admin screen for where work lands and who holds it.
///
/// This screen exists because a queue used to be a bare string. Membership
/// lived in a hardcoded tuple in Naviant's own source, which is the wrong
/// owner for the question "who sits at the customer's AP desk" — and nothing
/// anywhere could answer the reverse of it, so a signed-in account was bound
/// to no desk at all while the persona naming that same human held every one.
///
/// Two things it is careful about:
///
/// - **A binding is authority.** Adding somebody to a desk grants them the
///   power to decide everything that routes there, so the controls are
///   admin-only and say what they are doing rather than reading as a tidy-up.
/// - **A desk with work and nobody on it is the finding.** Tasks route to a
///   queue and never to a person precisely so work survives absence — which
///   also means an unheld desk accumulates silently, and no personal inbox
///   anywhere would ever surface it.
library;

import 'package:flutter/material.dart';

import '../models/queue.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'skeleton.dart';
import 'ui_kit.dart';

class QueuesView extends StatefulWidget {
  final ApiClient api;

  /// Whether this principal may edit bindings. Read off the session rather
  /// than discovered by pressing a button and being refused — the same rule
  /// the task queue now follows.
  final bool canAdminister;

  const QueuesView({super.key, required this.api, this.canAdminister = false});

  @override
  State<QueuesView> createState() => _QueuesViewState();
}

class _QueuesViewState extends State<QueuesView> {
  List<WorkQueue>? _queues;
  String? _error;
  String? _selected;
  bool _showPersonal = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final queues = await widget.api.listQueues();
      if (!mounted) return;
      setState(() {
        _queues = queues;
        _error = null;
        if (_selected != null && !queues.any((q) => q.slug == _selected)) {
          _selected = null;
        }
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  List<WorkQueue> get _visible {
    final all = _queues ?? const <WorkQueue>[];
    return _showPersonal ? all : all.where((q) => !q.personal).toList();
  }

  @override
  Widget build(BuildContext context) {
    final all = _queues ?? const <WorkQueue>[];
    final unheld = all.where((q) => q.unheldWithWork).toList();
    final personal = all.where((q) => q.personal).length;
    final visible = _visible;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: 'Access · work distribution',
            title: 'Desks',
            subtitle:
                'A Queue is where work lands and a Role binds people to it. '
                'Assigning to a desk rather than to a person is what makes '
                'work survive absence, share across a team and load-balance — '
                'so membership here is who may decide, not merely who is '
                'notified.',
            pills: [
              if (all.isNotEmpty) MetaPill('${all.length} desks'),
              if (unheld.isNotEmpty)
                MetaPill(
                  '${unheld.length} with work and nobody on them',
                  tone: CumulusTone.danger,
                ),
            ],
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (widget.canAdminister) ...[
                  FilledButton.icon(
                    onPressed: _createQueue,
                    icon: const Icon(Icons.add, size: 16),
                    label: const Text('New desk'),
                  ),
                  const SizedBox(width: 10),
                ],
                OutlinedButton.icon(
                  onPressed: _load,
                  icon: const Icon(Icons.refresh, size: 16),
                  label: const Text('Refresh'),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 28),
            child: ErrorNotice(message: _error!, onRetry: _load),
          ),
        if (unheld.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(28, 0, 28, 16),
            child: _UnheldNotice(queues: unheld, onOpen: (s) => setState(() => _selected = s)),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 0, 28, 12),
          child: Row(
            children: [
              FilterChip(
                selected: _showPersonal,
                onSelected: (v) => setState(() => _showPersonal = v),
                label: Text(
                  personal == 0
                      ? 'Personal desks'
                      : 'Personal desks · $personal',
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  'A personal desk is one person’s own inbox, minted when a '
                  'flow routes to them by name — "send it to the submitter’s '
                  'manager" — rather than created by hand.',
                  style: CumulusText.statCaption,
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: _queues == null
              // Heading and the personal-desk filter above are real already;
              // the split below them is what the read is for.
              ? const SkeletonPulse(
                  child: Padding(
                    padding: EdgeInsets.fromLTRB(28, 0, 28, 24),
                    child: SkeletonMasterDetail(listWidth: 320, rows: 7),
                  ),
                )
              : Padding(
                  padding: const EdgeInsets.fromLTRB(28, 0, 28, 24),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 320,
                        // Built on demand: a tenant with a personal desk per
                        // employee has a long list here, and a plain ListView
                        // builds every row of it whether or not it is on
                        // screen.
                        child: ListView.builder(
                          itemCount: visible.length,
                          itemBuilder: (context, i) => Padding(
                            padding: const EdgeInsets.only(bottom: 10),
                            child: _QueueRow(
                              queue: visible[i],
                              selected: visible[i].slug == _selected,
                              onTap: () => setState(
                                () => _selected = visible[i].slug,
                              ),
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 20),
                      Expanded(
                        child: SingleChildScrollView(
                          child: _detail(),
                        ),
                      ),
                    ],
                  ),
                ),
        ),
      ],
    );
  }

  Widget _detail() {
    final queue = (_queues ?? const <WorkQueue>[])
        .where((q) => q.slug == _selected)
        .firstOrNull;
    if (queue == null) {
      return const CumulusEmptyState(
        icon: Icons.groups_outlined,
        title: 'Pick a desk',
        message:
            'Its members are who may decide the work that routes there, and '
            'its claim window is how long one of them can hold a case before '
            'it returns to the pool.',
      );
    }
    return _QueueDetail(
      key: ValueKey(queue.slug),
      api: widget.api,
      queue: queue,
      canAdminister: widget.canAdminister,
      onChanged: _load,
    );
  }

  Future<void> _createQueue() async {
    final created = await showDialog<bool>(
      context: context,
      builder: (_) => _NewQueueDialog(api: widget.api),
    );
    if (created == true) await _load();
  }
}

/// The governance finding, above the list rather than inside it.
class _UnheldNotice extends StatelessWidget {
  final List<WorkQueue> queues;
  final ValueChanged<String> onOpen;

  const _UnheldNotice({required this.queues, required this.onOpen});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: CumulusColors.dangerSoft,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.report_problem_outlined,
                  size: 16, color: CumulusColors.danger),
              const SizedBox(width: 8),
              Text('Work with nobody to do it', style: CumulusText.statLabel),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'These desks are holding open tasks and have no members. Nothing '
            'will surface this anywhere else: a task routes to a desk and not '
            'to a person, so it appears in no personal inbox at all.',
            style: CumulusText.body,
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final queue in queues)
                ActionChip(
                  onPressed: () => onOpen(queue.slug),
                  avatar: const Icon(Icons.arrow_forward, size: 14),
                  label: Text('${queue.slug} · ${queue.openTasks} open'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _QueueRow extends StatelessWidget {
  final WorkQueue queue;
  final bool selected;
  final VoidCallback onTap;

  const _QueueRow({
    required this.queue,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      onTap: onTap,
      padding: const EdgeInsets.fromLTRB(16, 14, 14, 14),
      borderColor: selected
          ? CumulusColors.accent
          : queue.unheldWithWork
          ? CumulusColors.danger
          : null,
      borderWidth: selected ? 1.4 : 1,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                queue.personal ? Icons.person_outline : Icons.groups_outlined,
                size: 14,
                color: CumulusColors.inkMuted,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  queue.slug,
                  style: CumulusText.mono,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (queue.yours)
                const StatusBadge('yours', tone: CumulusTone.accent, dense: true),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Text(
                queue.holders.isEmpty
                    ? 'nobody bound'
                    : '${queue.holders.length} holder'
                          '${queue.holders.length == 1 ? '' : 's'}',
                style: CumulusText.statLabel.copyWith(
                  color: queue.holders.isEmpty
                      ? CumulusColors.danger
                      : CumulusColors.inkSecondary,
                ),
              ),
              const Spacer(),
              if (queue.openTasks > 0)
                Text('${queue.openTasks} open', style: CumulusText.meta),
            ],
          ),
        ],
      ),
    );
  }
}

class _QueueDetail extends StatefulWidget {
  final ApiClient api;
  final WorkQueue queue;
  final bool canAdminister;
  final VoidCallback onChanged;

  const _QueueDetail({
    super.key,
    required this.api,
    required this.queue,
    required this.canAdminister,
    required this.onChanged,
  });

  @override
  State<_QueueDetail> createState() => _QueueDetailState();
}

class _QueueDetailState extends State<_QueueDetail> {
  final _email = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
      widget.onChanged();
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final queue = widget.queue;
    return SectionCard(
      title: queue.name,
      subtitle: queue.description,
      headerTrailing: Wrap(
        spacing: 8,
        children: [
          MetaPill('claim window ${queue.ttlLabel}'),
          if (queue.openTasks > 0) MetaPill('${queue.openTasks} open'),
          if (queue.personal) const MetaPill('personal desk'),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SizedBox(height: 6),
          Text(queue.slug, style: CumulusText.mono),
          const SizedBox(height: 18),
          FieldLabel(
            'Holders',
            hint: 'who may decide the work that routes here',
          ),
          if (queue.holders.isEmpty)
            Text(
              'Nobody. Work routed here will sit until somebody is bound to '
              'the desk — it appears in no personal inbox, because a task '
              'routes to a desk and not to a person.',
              style: CumulusText.body.copyWith(color: CumulusColors.danger),
            ),
          for (final member in queue.members)
            _MemberRow(
              member: member,
              canAdminister: widget.canAdminister && !_busy,
              onRemove: () => _run(
                () => widget.api.removeQueueMember(queue.slug, member.email),
              ),
            ),
          if (widget.canAdminister) ...[
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _email,
                    enabled: !_busy,
                    decoration: const InputDecoration(
                      labelText: 'Bind a person by email',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                FilledButton(
                  onPressed: _busy
                      ? null
                      : () => _run(() async {
                          await widget.api.addQueueMember(
                            queue.slug,
                            email: _email.text.trim(),
                          );
                          _email.clear();
                        }),
                  child: const Text('Bind'),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'This grants the authority to decide everything that routes to '
              'this desk. It is not a notification setting.',
              style: CumulusText.meta,
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            ErrorNotice(message: _error!),
          ],
          const SizedBox(height: 20),
          FieldLabel(
            'Claim window',
            hint: 'how long one holder keeps a case before it returns to the '
                'pool',
          ),
          Text(
            'A claim on this desk lasts ${queue.ttlLabel}. It lapses on its '
            'own rather than needing to be released, so a case somebody '
            'started and walked away from comes back to the queue instead of '
            'needing an administrator. An AP clerk holds an invoice for '
            'minutes; a steward may sit with a merge for half an hour.',
            style: CumulusText.body,
          ),
          if (widget.canAdminister) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              children: [
                for (final minutes in const [5, 20, 60])
                  ChoiceChip(
                    selected: queue.claimTtlSeconds == minutes * 60,
                    onSelected: _busy
                        ? null
                        : (_) => _run(
                            () => widget.api.updateQueue(
                              queue.slug,
                              claimTtlSeconds: minutes * 60,
                            ),
                          ),
                    label: Text('${minutes}m'),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _MemberRow extends StatelessWidget {
  final QueueMember member;
  final bool canAdminister;
  final VoidCallback onRemove;

  const _MemberRow({
    required this.member,
    required this.canAdminister,
    required this.onRemove,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          const Icon(Icons.person_outline, size: 15,
              color: CumulusColors.inkMuted),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(member.display, style: CumulusText.body),
                if (member.label.isNotEmpty && member.label != member.email)
                  Text(member.email, style: CumulusText.meta),
              ],
            ),
          ),
          if (member.fromRoster)
            const Tooltip(
              message: 'Declared on the persona roster, not added here. '
                  'Removing it would last until the next restart and then '
                  'come back.',
              child: StatusBadge('roster', dense: true),
            )
          else if (canAdminister)
            IconButton(
              onPressed: onRemove,
              tooltip: 'Unbind from this desk',
              icon: const Icon(Icons.close, size: 16),
            ),
        ],
      ),
    );
  }
}

class _NewQueueDialog extends StatefulWidget {
  final ApiClient api;

  const _NewQueueDialog({required this.api});

  @override
  State<_NewQueueDialog> createState() => _NewQueueDialogState();
}

class _NewQueueDialogState extends State<_NewQueueDialog> {
  final _slug = TextEditingController();
  final _name = TextEditingController();
  final _description = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _slug.dispose();
    _name.dispose();
    _description.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('New desk'),
      content: SizedBox(
        width: 460,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'A desk is where a Flow routes work. Name it after the job '
              'rather than the person who currently does it — that is the '
              'whole reason work survives them leaving.',
              style: CumulusText.body,
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _slug,
              decoration: const InputDecoration(
                labelText: 'Slug — lowercase, hyphenated',
                hintText: 'expense-review-exec',
                isDense: true,
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _name,
              decoration: const InputDecoration(
                labelText: 'Name',
                isDense: true,
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _description,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: 'What lands here',
                isDense: true,
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              ErrorNotice(message: _error!),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _busy ? null : _create,
          child: const Text('Create'),
        ),
      ],
    );
  }

  Future<void> _create() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.createQueue(
        slug: _slug.text.trim(),
        name: _name.text.trim().isEmpty ? _slug.text.trim() : _name.text.trim(),
        description: _description.text.trim(),
      );
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = e.message;
        });
      }
    }
  }
}
