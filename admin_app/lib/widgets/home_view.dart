import 'package:flutter/material.dart';
import '../models/session.dart';

import '../models/calendar.dart';
import '../models/payable.dart';
import '../models/task.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'calendar_view.dart';
import 'ui_kit.dart';

/// Home — the first thing the console shows, and deliberately the thinnest.
///
/// Two questions, no more: **what does my day look like**, and **where does
/// the payables process stand**. Both are read from the same machinery the
/// rest of the console runs on — the schedule traverses the calendar mirror in
/// the graph, the AP panel reads the payables service and the task queue — so
/// nothing here is a summary invented for a landing page. A number on this
/// screen is clickable in the sense that matters: it exists somewhere else,
/// under governance, and this is only where it is first noticed.
class HomeSection extends StatefulWidget {
  final ApiClient api;

  /// The acting session, so the page can tell "nothing happened" from "you
  /// cannot see it". Null only before the first session load.
  final Session? session;

  /// Where the two panels hand off to: the week view and the AP workspace.
  final VoidCallback? onOpenCalendar;
  final VoidCallback? onOpenPayables;
  final VoidCallback? onOpenTasks;

  const HomeSection({
    super.key,
    required this.api,
    this.session,
    this.onOpenCalendar,
    this.onOpenPayables,
    this.onOpenTasks,
  });

  @override
  State<HomeSection> createState() => _HomeSectionState();
}

class _HomeSectionState extends State<HomeSection> {
  /// Whether the AP queue is this persona's work. The panel below is scoped
  /// server-side, so an unbound persona sees an empty table either way — this
  /// is only how the page decides what that emptiness *means*.
  bool get _seesTheQueue {
    final s = widget.session;
    if (s == null) return true;
    return s.scope != 'own' || s.queues.contains('ap-clerk');
  }

  Agenda? _agenda;
  PayableStats? _payables;
  List<PayableInvoice> _pending = const [];
  TaskStats _tasks = TaskStats.empty;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final today = DateTime.now();
    try {
      // One day's worth: the week lives one rail stop down, and asking for
      // seven here would be loading a screen nobody is looking at.
      final agenda = await widget.api.calendarAgenda(
        start: isoDate(today),
        days: 1,
      );
      final payables = await widget.api.payableStats();
      final pending = await widget.api.listPayables(state: 'pending');
      final tasks = await widget.api.taskStats();
      if (!mounted) return;
      setState(() {
        _agenda = agenda;
        _payables = payables;
        _pending = pending;
        _tasks = tasks;
        _error = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final agenda = _agenda;
    final now = DateTime.now();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow:
                'Home · ${weekdayName(now.weekday)}, ${monthDayLabel(now)}',
            title: agenda == null || agenda.name.isEmpty
                ? 'Your day'
                : '${_salutation(now)}, ${agenda.name.split(' ').first}',
            subtitle:
                'Your schedule, and where the accounts payable process '
                'stands this morning.',
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
        ),
        Expanded(child: _body()),
      ],
    );
  }

  Widget _body() {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load your day',
        message: _error!,
      );
    }
    if (_agenda == null) return const CumulusLoading();
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= 1080;
        final schedule = _schedule();
        final payables = _payablesPanel();
        return SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(28, 18, 28, 48),
          child: wide
              ? IntrinsicHeight(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(flex: 5, child: schedule),
                      const SizedBox(width: 16),
                      Expanded(flex: 4, child: payables),
                    ],
                  ),
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [schedule, const SizedBox(height: 16), payables],
                ),
        );
      },
    );
  }

  // -- the schedule -------------------------------------------------------

  Widget _schedule() {
    final agenda = _agenda!;
    final today = DateTime.now();
    final events = agenda.on(today);
    // The first block that has not finished yet: what "next" honestly means
    // at 2pm on a day whose 9am standup is long over.
    final next = events
        .where((e) => !e.allDay && (e.end ?? today).isAfter(today))
        .firstOrNull;
    return SectionCard(
      title: 'Today',
      subtitle:
          '${weekdayName(today.weekday)} ${monthDayLabel(today)} · '
          '${events.length} block${events.length == 1 ? '' : 's'}',
      headerTrailing: TextButton.icon(
        onPressed: widget.onOpenCalendar,
        icon: const Icon(Icons.calendar_month_outlined, size: 15),
        label: const Text('Open the week'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (agenda.personaNote.isNotEmpty) ...[
            _PersonaNote(note: agenda.personaNote),
            const SizedBox(height: 14),
          ],
          if (events.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 22),
              child: CumulusEmptyState(
                icon: Icons.free_breakfast_outlined,
                title: 'Nothing on the calendar',
                message: 'No blocks are mirrored for today.',
              ),
            )
          else
            for (final event in events)
              _ScheduleRow(event: event, isNext: identical(event, next)),
          const SizedBox(height: 6),
          const Divider(height: 1),
          const SizedBox(height: 12),
          Row(
            children: [
              const Icon(
                Icons.cloud_sync_outlined,
                size: 14,
                color: CumulusColors.inkMuted,
              ),
              const SizedBox(width: 7),
              Expanded(
                child: Text(
                  'Mirrored from ${agenda.calendar?.provider ?? 'the calendar '
                          'provider'} · times are wall clock in '
                  '${agenda.calendar?.timezone ?? 'the calendar\'s zone'}',
                  style: CumulusText.meta,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  // -- the AP process -----------------------------------------------------

  Widget _payablesPanel() {
    final stats = _payables;
    final waiting = _tasks.byQueue['ap-clerk'] ?? 0;
    return SectionCard(
      title: 'Accounts payable',
      subtitle: 'where invoices are this morning',
      headerTrailing: TextButton.icon(
        onPressed: widget.onOpenPayables,
        icon: const Icon(Icons.open_in_new, size: 15),
        label: const Text('Open the workspace'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (stats == null)
            const CumulusLoading()
          else ...[
            Row(
              children: [
                Expanded(
                  child: _Metric(
                    label: 'Posted',
                    value: '${stats.posted}',
                    caption:
                        '\$${stats.postedUsd.toStringAsFixed(2)} to the ledger',
                    tone: CumulusTone.success,
                  ),
                ),
                Expanded(
                  child: _Metric(
                    label: 'Waiting on a person',
                    value: '${stats.pending}',
                    caption: waiting == 0
                        ? 'no open clerk tasks'
                        : '$waiting in the ap-clerk queue',
                    tone: stats.pending > 0
                        ? CumulusTone.warn
                        : CumulusTone.neutral,
                  ),
                ),
                Expanded(
                  child: _Metric(
                    label: 'Match blocked',
                    value: '${stats.matchBlocked + stats.matchVariance}',
                    caption: '${stats.matched} matched clean',
                    tone: stats.matchBlocked > 0
                        ? CumulusTone.danger
                        : CumulusTone.neutral,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            // No receiving clause. This estate has no receiving system: the
            // feed is registered and has never run, the book holds no
            // ReceivingReport, and a match here pairs an invoice line with a
            // purchase-order line and nothing else. Promising a three-way
            // match described machinery the reader cannot go and find.
            Text(
              'Every invoice runs the same path: extract, resolve the vendor '
              'against the master, match the billed lines against the '
              'purchase order — and against the receipts too, wherever a '
              'receiving feed delivers them — screen it against the vendor\'s '
              'own history, then post under the governed limit or route it to '
              'a clerk. Anything on this list stopped at that last gate.',
              style: CumulusText.body,
            ),
            const SizedBox(height: 16),
            const FieldLabel('Waiting on judgment'),
            if (_pending.isEmpty)
              // An empty list has two causes and they are opposite claims:
              // nothing is held, or nothing is *yours to see*. The system
              // knows which — a persona bound to no AP queue is scoped out of
              // the table entirely — and saying the first when it means the
              // second tells the person least able to check that the business
              // is fine when it is not. Two invoices really are held here.
              Text(
                _seesTheQueue
                    ? 'Nothing is held. Every invoice this window cleared '
                          'the gate on its own.'
                    : 'You are not bound to the AP queue, so held invoices '
                          'are not shown here. This is what you can see, not '
                          'what there is.',
                style: CumulusText.meta,
              )
            else
              for (final invoice in _pending.take(4))
                _InvoiceRow(invoice: invoice),
            if (_pending.length > 4) ...[
              const SizedBox(height: 8),
              Text(
                '+ ${_pending.length - 4} more in the workspace',
                style: CumulusText.meta,
              ),
            ],
            if (_tasks.open > 0) ...[
              const SizedBox(height: 16),
              const Divider(height: 1),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    // `taskStats` is queue-scoped server-side, so "across
                    // every queue" claimed a reach the number does not have —
                    // for an own-scoped persona it is their own bindings and
                    // nothing else.
                    child: Text(
                      '${_tasks.open} task${_tasks.open == 1 ? '' : 's'} '
                      'across your queues ${_tasks.open == 1 ? 'is' : 'are'} '
                      'waiting on a person'
                      '${_tasks.highPriority > 0 ? ', ${_tasks.highPriority} '
                                'of them high priority' : ''}.',
                      style: CumulusText.meta,
                    ),
                  ),
                  const SizedBox(width: 10),
                  TextButton(
                    onPressed: widget.onOpenTasks,
                    child: const Text('My Tasks'),
                  ),
                ],
              ),
            ],
          ],
        ],
      ),
    );
  }
}

/// One block on today's schedule.
class _ScheduleRow extends StatelessWidget {
  final AgendaEvent event;
  final bool isNext;

  const _ScheduleRow({required this.event, required this.isNext});

  @override
  Widget build(BuildContext context) {
    final tone = eventTone(event.kind);
    final start = event.start;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 66,
            child: Padding(
              padding: const EdgeInsets.only(top: 11),
              child: Text(
                event.allDay
                    ? 'all day'
                    : start == null
                    ? ''
                    : clockLabel(start),
                style: CumulusText.meta.copyWith(
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.inkSecondary,
                ),
              ),
            ),
          ),
          Container(
            width: 3,
            height: 44,
            margin: const EdgeInsets.only(top: 8, right: 12),
            decoration: BoxDecoration(
              color: tone.foreground,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          event.title,
                          style: CumulusText.rowTitle,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (isNext) ...[
                        const SizedBox(width: 8),
                        const StatusBadge(
                          'next',
                          tone: CumulusTone.accent,
                          dense: true,
                          uppercase: true,
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 3),
                  Text(
                    describeEvent(event),
                    style: CumulusText.meta,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _InvoiceRow extends StatelessWidget {
  final PayableInvoice invoice;

  const _InvoiceRow({required this.invoice});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  invoice.vendorName.isEmpty
                      ? invoice.vendorSlug
                      : invoice.vendorName,
                  style: CumulusText.rowTitle,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  [
                    if (invoice.invoiceNumber.isNotEmpty) invoice.invoiceNumber,
                    if (invoice.matchVerdict.isNotEmpty)
                      'match: ${invoice.matchVerdict}',
                  ].join(' · '),
                  style: CumulusText.meta,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Text(
            '\$${invoice.totalUsd.toStringAsFixed(2)}',
            style: CumulusText.mono.copyWith(fontSize: 12.5),
          ),
        ],
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  final String label;
  final String value;
  final String caption;
  final CumulusTone tone;

  const _Metric({
    required this.label,
    required this.value,
    required this.caption,
    required this.tone,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label.toUpperCase(), style: CumulusText.statLabel),
        const SizedBox(height: 8),
        Text(
          value,
          style: CumulusText.statValue.copyWith(
            fontSize: 24,
            color: tone == CumulusTone.neutral ? null : tone.foreground,
          ),
        ),
        const SizedBox(height: 5),
        Text(caption, style: CumulusText.statCaption),
      ],
    );
  }
}

/// Said out loud rather than silently substituted: whose calendar this is,
/// when the signed-in principal is not in the workforce.
class _PersonaNote extends StatelessWidget {
  final String note;

  const _PersonaNote({required this.note});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: CumulusColors.warnSoft,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.person_search_outlined,
            size: 15,
            color: CumulusColors.warn,
          ),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              note,
              style: CumulusText.meta.copyWith(color: CumulusColors.warn),
            ),
          ),
        ],
      ),
    );
  }
}

String _salutation(DateTime now) => now.hour < 12
    ? 'Good morning'
    : now.hour < 18
    ? 'Good afternoon'
    : 'Good evening';
