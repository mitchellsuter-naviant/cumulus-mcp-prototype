import 'package:flutter/material.dart';

import '../models/calendar.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// The week view: one person's calendar, mirrored from the provider.
///
/// A read surface on purpose. The provider is the system of record for a
/// schedule, so there is no "new event" button here — a block created in
/// Cumulus would be a fact the mirror could not defend, and the next sync
/// would delete it. What the console adds instead is the join the provider
/// cannot make: each block carries what work it is held against, so a Friday
/// task block and the queue item it exists for are visibly the same thing.
class CalendarSection extends StatefulWidget {
  final ApiClient api;

  const CalendarSection({super.key, required this.api});

  @override
  State<CalendarSection> createState() => _CalendarSectionState();
}

class _CalendarSectionState extends State<CalendarSection> {
  /// The Monday the grid starts on.
  late DateTime _weekStart = mondayOf(DateTime.now());
  Agenda? _agenda;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final agenda = await widget.api.calendarAgenda(
        start: isoDate(_weekStart),
        days: 7,
      );
      if (!mounted) return;
      setState(() {
        _agenda = agenda;
        _error = null;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  void _move(int weeks) {
    setState(() => _weekStart = _weekStart.add(Duration(days: 7 * weeks)));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final agenda = _agenda;
    final end = _weekStart.add(const Duration(days: 6));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: agenda == null || agenda.name.isEmpty
                ? 'Calendar · yours'
                : 'Calendar · ${agenda.name}'
                      '${agenda.title.isEmpty ? '' : ' · ${agenda.title}'}',
            title: _rangeLabel(_weekStart, end),
            subtitle:
                'Mirrored from the calendar provider and read out of the '
                'graph: each block is an event on your calendar, linked to '
                'the people in it and to the work it is held against.',
            trailing: _WeekControls(
              onPrevious: () => _move(-1),
              onToday: () {
                setState(() => _weekStart = mondayOf(DateTime.now()));
                _load();
              },
              onNext: () => _move(1),
            ),
          ),
        ),
        const SizedBox(height: 14),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 28),
          child: Row(
            children: [
              for (final kind in const [
                'meeting',
                'focus',
                'task-block',
                'deadline',
                'out-of-office',
              ])
                Padding(
                  padding: const EdgeInsets.only(right: 16),
                  child: _LegendDot(kind: kind),
                ),
              const Spacer(),
              if (agenda?.calendar != null)
                Text(
                  '${agenda!.calendar!.address} · '
                  '${agenda.calendar!.provider} · '
                  '${agenda.calendar!.timezone}',
                  style: CumulusText.meta,
                ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Expanded(child: _body()),
      ],
    );
  }

  Widget _body() {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load the week',
        message: _error!,
      );
    }
    final agenda = _agenda;
    if (agenda == null || _loading) return const CumulusLoading();
    return Padding(
      padding: const EdgeInsets.fromLTRB(28, 0, 28, 28),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (agenda.personaNote.isNotEmpty) ...[
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: CumulusColors.warnSoft,
                borderRadius: BorderRadius.circular(CumulusRadius.control),
              ),
              child: Text(
                agenda.personaNote,
                style: CumulusText.meta.copyWith(color: CumulusColors.warn),
              ),
            ),
            const SizedBox(height: 12),
          ],
          Expanded(child: _WeekGrid(weekStart: _weekStart, agenda: agenda)),
        ],
      ),
    );
  }
}

class _WeekControls extends StatelessWidget {
  final VoidCallback onPrevious;
  final VoidCallback onToday;
  final VoidCallback onNext;

  const _WeekControls({
    required this.onPrevious,
    required this.onToday,
    required this.onNext,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton(
          tooltip: 'Previous week',
          icon: const Icon(Icons.chevron_left, size: 20),
          onPressed: onPrevious,
        ),
        OutlinedButton(onPressed: onToday, child: const Text('Today')),
        IconButton(
          tooltip: 'Next week',
          icon: const Icon(Icons.chevron_right, size: 20),
          onPressed: onNext,
        ),
      ],
    );
  }
}

class _LegendDot extends StatelessWidget {
  final String kind;

  const _LegendDot({required this.kind});

  @override
  Widget build(BuildContext context) {
    final tone = eventTone(kind);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 7,
          height: 7,
          decoration: BoxDecoration(
            color: tone.foreground,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 7),
        Text(
          eventKindLabel(kind).toUpperCase(),
          style: CumulusText.statLabel,
        ),
      ],
    );
  }
}

/// The grid itself: seven day columns over an hour ruler, with all-day blocks
/// banded above it.
class _WeekGrid extends StatelessWidget {
  final DateTime weekStart;
  final Agenda agenda;

  const _WeekGrid({required this.weekStart, required this.agenda});

  /// The window the grid draws. Wide enough for the firm's earliest block and
  /// its latest, and no wider — an empty 3am row is scroll distance nobody
  /// wants to travel.
  static const _firstHour = 7;
  static const _lastHour = 19;
  static const _hourHeight = 52.0;
  static const _gutter = 60.0;

  @override
  Widget build(BuildContext context) {
    final days = [
      for (var i = 0; i < 7; i++) weekStart.add(Duration(days: i)),
    ];
    final today = DateTime.now();
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _header(days, today),
          const Divider(height: 1),
          _allDayBand(days),
          Expanded(
            child: SingleChildScrollView(
              child: SizedBox(
                height: (_lastHour - _firstHour) * _hourHeight,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SizedBox(width: _gutter, child: _hourRuler()),
                    for (final day in days)
                      Expanded(
                        child: _DayColumn(
                          day: day,
                          events: agenda
                              .on(day)
                              .where((e) => !e.allDay)
                              .toList(),
                          firstHour: _firstHour,
                          lastHour: _lastHour,
                          hourHeight: _hourHeight,
                          isToday: _sameDay(day, today),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _header(List<DateTime> days, DateTime today) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        children: [
          const SizedBox(width: _gutter),
          for (final day in days)
            Expanded(
              child: Column(
                children: [
                  Text(
                    shortWeekday(day.weekday).toUpperCase(),
                    style: CumulusText.statLabel,
                  ),
                  const SizedBox(height: 6),
                  Container(
                    width: 28,
                    height: 28,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: _sameDay(day, today)
                          ? CumulusColors.accent
                          : Colors.transparent,
                      shape: BoxShape.circle,
                    ),
                    child: Text(
                      '${day.day}',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: _sameDay(day, today)
                            ? Colors.white
                            : CumulusColors.ink,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  /// All-day blocks — deadlines, out-of-office — are not spans, so they get a
  /// band rather than a position on the ruler.
  Widget _allDayBand(List<DateTime> days) {
    final any = days.any(
      (day) => agenda.on(day).any((event) => event.allDay),
    );
    if (!any) return const SizedBox.shrink();
    return Container(
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: CumulusColors.border)),
      ),
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: _gutter,
            child: Padding(
              padding: const EdgeInsets.only(top: 8, right: 8),
              child: Text(
                'ALL DAY',
                textAlign: TextAlign.right,
                style: CumulusText.statLabel,
              ),
            ),
          ),
          for (final day in days)
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final event
                      in agenda.on(day).where((event) => event.allDay))
                    Padding(
                      padding: const EdgeInsets.fromLTRB(2, 6, 2, 0),
                      child: _EventChip(event: event, compact: true),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _hourRuler() {
    return Column(
      children: [
        for (var hour = _firstHour; hour < _lastHour; hour++)
          SizedBox(
            height: _hourHeight,
            child: Align(
              alignment: Alignment.topRight,
              child: Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Transform.translate(
                  offset: const Offset(0, -6),
                  child: Text(hourLabel(hour), style: CumulusText.meta),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _DayColumn extends StatelessWidget {
  final DateTime day;
  final List<AgendaEvent> events;
  final int firstHour;
  final int lastHour;
  final double hourHeight;
  final bool isToday;

  const _DayColumn({
    required this.day,
    required this.events,
    required this.firstHour,
    required this.lastHour,
    required this.hourHeight,
    required this.isToday,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        return DecoratedBox(
          decoration: BoxDecoration(
            color: isToday
                ? CumulusColors.accentSoft.withValues(alpha: 0.35)
                : Colors.transparent,
            border: const Border(
              left: BorderSide(color: CumulusColors.border),
            ),
          ),
          child: Stack(
            children: [
              for (var hour = firstHour + 1; hour < lastHour; hour++)
                Positioned(
                  top: (hour - firstHour) * hourHeight,
                  left: 0,
                  right: 0,
                  child: const Divider(height: 1),
                ),
              for (final placed in _place(events))
                Positioned(
                  top: _offset(placed.event),
                  height: _height(placed.event),
                  left: 2 + placed.slot * (width - 4) / placed.slots,
                  width: (width - 4) / placed.slots - 2,
                  child: _EventChip(event: placed.event),
                ),
            ],
          ),
        );
      },
    );
  }

  double _offset(AgendaEvent event) {
    final start = event.start;
    if (start == null) return 0;
    final hours = start.hour + start.minute / 60 - firstHour;
    return hours.clamp(0, (lastHour - firstHour).toDouble()) * hourHeight;
  }

  double _height(AgendaEvent event) {
    // A 15-minute standup still has to be readable, so blocks have a floor.
    final minutes = event.minutes <= 0 ? 30 : event.minutes;
    return (minutes / 60 * hourHeight).clamp(26.0, lastHour * hourHeight);
  }

  /// Overlapping blocks share the column width rather than hiding each other.
  /// A cluster is a run of events that overlap transitively; every member of
  /// a cluster gets the same number of slots, so the columns line up.
  List<_Placed> _place(List<AgendaEvent> events) {
    final sorted = [...events]..sort((a, b) => a.startsAt.compareTo(b.startsAt));
    final out = <_Placed>[];
    var cluster = <AgendaEvent>[];
    DateTime? clusterEnd;

    void flush() {
      for (var i = 0; i < cluster.length; i++) {
        out.add(_Placed(cluster[i], i, cluster.length));
      }
      cluster = [];
      clusterEnd = null;
    }

    for (final event in sorted) {
      final start = event.start;
      final end = event.end ?? start;
      if (start == null) continue;
      if (clusterEnd != null && start.isBefore(clusterEnd!)) {
        cluster.add(event);
        if (end != null && end.isAfter(clusterEnd!)) clusterEnd = end;
      } else {
        flush();
        cluster.add(event);
        clusterEnd = end;
      }
    }
    flush();
    return out;
  }
}

class _Placed {
  final AgendaEvent event;
  final int slot;
  final int slots;

  const _Placed(this.event, this.slot, this.slots);
}

/// One block, drawn. Tapping opens the detail — who is in it, where, and what
/// work it is held against.
class _EventChip extends StatelessWidget {
  final AgendaEvent event;
  final bool compact;

  const _EventChip({required this.event, this.compact = false});

  @override
  Widget build(BuildContext context) {
    final tone = eventTone(event.kind);
    final start = event.start;
    return Material(
      color: tone.background,
      borderRadius: BorderRadius.circular(7),
      child: InkWell(
        borderRadius: BorderRadius.circular(7),
        onTap: () => _showEvent(context, event),
        child: Container(
          padding: const EdgeInsets.fromLTRB(7, 4, 6, 4),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(7),
            border: Border(
              left: BorderSide(color: tone.foreground, width: 2.5),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                event.title,
                maxLines: compact ? 1 : 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 11.5,
                  height: 1.25,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              if (!compact && start != null && event.minutes >= 45)
                Text(
                  clockLabel(start),
                  style: CumulusText.meta.copyWith(fontSize: 10.5),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

Future<void> _showEvent(BuildContext context, AgendaEvent event) {
  final start = event.start;
  final end = event.end;
  return showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(event.title),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                StatusBadge(
                  eventKindLabel(event.kind),
                  tone: eventTone(event.kind),
                  dense: true,
                ),
                const SizedBox(width: 8),
                Text(
                  event.allDay || start == null
                      ? 'All day'
                      : '${weekdayName(start.weekday)} '
                            '${monthDayLabel(start)} · '
                            '${clockLabel(start)}'
                            '${end == null ? '' : ' – ${clockLabel(end)}'}',
                  style: CumulusText.meta,
                ),
              ],
            ),
            if (event.location.isNotEmpty) ...[
              const SizedBox(height: 12),
              const FieldLabel('Where'),
              Text(event.location, style: CumulusText.body),
            ],
            if (event.notes.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(event.notes, style: CumulusText.body),
            ],
            if (event.attendees.isNotEmpty) ...[
              const SizedBox(height: 12),
              FieldLabel(
                'Who · ${event.organizerName.isEmpty ? '' : ''
                    '${event.organizerName} called it'}',
              ),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final person in event.attendees)
                    MetaPill(
                      person.shortName +
                          (person.isOwner ? ' (you)' : '') +
                          (person.required ? '' : ' · optional'),
                      tone: person.isOwner ? CumulusTone.accent : null,
                    ),
                ],
              ),
            ],
            if (event.agendaRef.isNotEmpty) ...[
              const SizedBox(height: 12),
              const FieldLabel('Held against'),
              Text(
                event.agendaRef,
                style: CumulusText.mono.copyWith(fontSize: 12),
              ),
            ],
            const SizedBox(height: 12),
            Text(event.ref, style: CumulusText.meta.copyWith(fontSize: 11)),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Close'),
        ),
      ],
    ),
  );
}

// ---------------------------------------------------------------------------
// Shared calendar vocabulary
// ---------------------------------------------------------------------------

/// One colour per kind of block, used by both the home schedule and this
/// grid, so a focus block is the same green in either place.
CumulusTone eventTone(String kind) => switch (kind) {
  'meeting' => CumulusTone.accent,
  'focus' => CumulusTone.success,
  'task-block' => CumulusTone.warn,
  'deadline' => CumulusTone.danger,
  'out-of-office' => CumulusTone.teal,
  _ => CumulusTone.neutral,
};

String eventKindLabel(String kind) => switch (kind) {
  'task-block' => 'Task block',
  'out-of-office' => 'Out of office',
  '' => 'Block',
  _ => '${kind[0].toUpperCase()}${kind.substring(1)}',
};

/// The one line under a block's title: who else is in it, where it is, and
/// what work it is held against.
String describeEvent(AgendaEvent event) {
  final others = event.others;
  final people = switch (others.length) {
    0 => '',
    1 => 'w/ ${others.first.shortName}',
    _ => 'w/ ${others.first.shortName} +${others.length - 1}',
  };
  return [
    eventKindLabel(event.kind),
    if (people.isNotEmpty) people,
    if (event.location.isNotEmpty) event.location,
    if (event.agendaRef.isNotEmpty) event.agendaRef,
  ].join(' · ');
}

DateTime mondayOf(DateTime day) =>
    DateTime(day.year, day.month, day.day - (day.weekday - 1));

bool _sameDay(DateTime a, DateTime b) =>
    a.year == b.year && a.month == b.month && a.day == b.day;

String clockLabel(DateTime at) {
  final hour = at.hour % 12 == 0 ? 12 : at.hour % 12;
  final minute = at.minute.toString().padLeft(2, '0');
  return '$hour:$minute ${at.hour < 12 ? 'AM' : 'PM'}';
}

String hourLabel(int hour) {
  final display = hour % 12 == 0 ? 12 : hour % 12;
  return '$display ${hour < 12 ? 'AM' : 'PM'}';
}

String isoDate(DateTime day) =>
    '${day.year.toString().padLeft(4, '0')}-'
    '${day.month.toString().padLeft(2, '0')}-'
    '${day.day.toString().padLeft(2, '0')}';

const _weekdayNames = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];
const _monthNames = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

String weekdayName(int weekday) => _weekdayNames[(weekday - 1) % 7];

String shortWeekday(int weekday) => weekdayName(weekday).substring(0, 3);

String monthDayLabel(DateTime day) =>
    '${_monthNames[day.month - 1]} ${day.day}';

String _rangeLabel(DateTime start, DateTime end) {
  final sameMonth = start.month == end.month;
  final left = monthDayLabel(start);
  final right = sameMonth ? '${end.day}' : monthDayLabel(end);
  return '$left – $right, ${end.year}';
}
