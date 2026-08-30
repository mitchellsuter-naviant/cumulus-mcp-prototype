/// Calendars: one person's schedule, mirrored from the provider.
///
/// Mirrors `server/app/calendars.py`. Times are **wall-clock local** in the
/// calendar's own zone, exactly as the provider stores them — no offset, no
/// `Z` — so `DateTime.parse` yields the time that is written on the invite
/// rather than one shifted into the browser's zone.
library;

class Attendee {
  final String email;
  final String name;
  final bool required;
  final String response;

  /// True when this is the person whose copy of the event is being read.
  final bool isOwner;

  const Attendee({
    required this.email,
    required this.name,
    required this.required,
    required this.response,
    required this.isOwner,
  });

  factory Attendee.fromJson(Map<String, dynamic> json) => Attendee(
    email: json['email'] as String? ?? '',
    name: json['name'] as String? ?? '',
    required: json['required'] as bool? ?? true,
    response: json['response'] as String? ?? 'accepted',
    isOwner: json['is_owner'] as bool? ?? false,
  );

  String get shortName => name.isEmpty ? email.split('@').first : name;
}

class AgendaEvent {
  final String id;
  final String ref;
  final String title;

  /// meeting | focus | task-block | deadline | out-of-office
  final String kind;
  final String startsAt;
  final String endsAt;
  final bool allDay;
  final String location;
  final String status;
  final String organizerEmail;
  final String organizerName;

  /// The task, run or case this block is held for. A string on purpose: Tasks
  /// and runs are execution state, not graph objects, so the server keeps it
  /// as an observation rather than a Link.
  final String agendaRef;
  final String notes;
  final List<Attendee> attendees;

  const AgendaEvent({
    required this.id,
    required this.ref,
    required this.title,
    required this.kind,
    required this.startsAt,
    required this.endsAt,
    required this.allDay,
    required this.location,
    required this.status,
    required this.organizerEmail,
    required this.organizerName,
    required this.agendaRef,
    required this.notes,
    required this.attendees,
  });

  factory AgendaEvent.fromJson(Map<String, dynamic> json) => AgendaEvent(
    id: json['id'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
    title: json['title'] as String? ?? '',
    kind: json['kind'] as String? ?? 'meeting',
    startsAt: json['starts_at'] as String? ?? '',
    endsAt: json['ends_at'] as String? ?? '',
    allDay: json['all_day'] as bool? ?? false,
    location: json['location'] as String? ?? '',
    status: json['status'] as String? ?? 'confirmed',
    organizerEmail: json['organizer_email'] as String? ?? '',
    organizerName: json['organizer_name'] as String? ?? '',
    agendaRef: json['agenda_ref'] as String? ?? '',
    notes: json['notes'] as String? ?? '',
    attendees: (json['attendees'] as List<dynamic>? ?? const [])
        .map((e) => Attendee.fromJson(e as Map<String, dynamic>))
        .toList(),
  );

  DateTime? get start => DateTime.tryParse(startsAt);
  DateTime? get end => DateTime.tryParse(endsAt);

  /// Minutes of wall clock this block occupies. All-day blocks report zero —
  /// they are rendered as a band, not as a span.
  int get minutes {
    final from = start, to = end;
    if (allDay || from == null || to == null) return 0;
    return to.difference(from).inMinutes;
  }

  /// Everyone but you. What a row wants to say is who else is in the room.
  List<Attendee> get others => attendees.where((a) => !a.isOwner).toList();
}

class CalendarSummary {
  final String id;
  final String ref;
  final String address;
  final String name;
  final String provider;
  final String timezone;
  final String ownerEmail;
  final String ownerName;
  final String ownerTitle;
  final int events;
  final String nextEventAt;
  final String nextEventTitle;

  const CalendarSummary({
    required this.id,
    required this.ref,
    required this.address,
    required this.name,
    required this.provider,
    required this.timezone,
    required this.ownerEmail,
    required this.ownerName,
    required this.ownerTitle,
    required this.events,
    required this.nextEventAt,
    required this.nextEventTitle,
  });

  factory CalendarSummary.fromJson(Map<String, dynamic> json) =>
      CalendarSummary(
        id: json['id'] as String? ?? '',
        ref: json['ref'] as String? ?? '',
        address: json['address'] as String? ?? '',
        name: json['name'] as String? ?? '',
        provider: json['provider'] as String? ?? '',
        timezone: json['timezone'] as String? ?? '',
        ownerEmail: json['owner_email'] as String? ?? '',
        ownerName: json['owner_name'] as String? ?? '',
        ownerTitle: json['owner_title'] as String? ?? '',
        events: json['events'] as int? ?? 0,
        nextEventAt: json['next_event_at'] as String? ?? '',
        nextEventTitle: json['next_event_title'] as String? ?? '',
      );
}

class Agenda {
  final String email;
  final String name;
  final String title;
  final CalendarSummary? calendar;
  final String startDate;
  final int days;
  final List<AgendaEvent> events;

  /// Non-empty when the signed-in principal is not in the workforce and a
  /// demo persona's calendar is being shown instead. Said out loud rather
  /// than silently substituted.
  final String personaNote;

  const Agenda({
    required this.email,
    required this.name,
    required this.title,
    required this.calendar,
    required this.startDate,
    required this.days,
    required this.events,
    required this.personaNote,
  });

  static const empty = Agenda(
    email: '',
    name: '',
    title: '',
    calendar: null,
    startDate: '',
    days: 7,
    events: [],
    personaNote: '',
  );

  factory Agenda.fromJson(Map<String, dynamic> json) => Agenda(
    email: json['email'] as String? ?? '',
    name: json['name'] as String? ?? '',
    title: json['title'] as String? ?? '',
    calendar: json['calendar'] == null
        ? null
        : CalendarSummary.fromJson(json['calendar'] as Map<String, dynamic>),
    startDate: json['start_date'] as String? ?? '',
    days: json['days'] as int? ?? 7,
    events: (json['events'] as List<dynamic>? ?? const [])
        .map((e) => AgendaEvent.fromJson(e as Map<String, dynamic>))
        .toList(),
    personaNote: json['persona_note'] as String? ?? '',
  );

  /// The events that fall on one day, in start order.
  List<AgendaEvent> on(DateTime day) => events.where((e) {
    final start = e.start;
    return start != null &&
        start.year == day.year &&
        start.month == day.month &&
        start.day == day.day;
  }).toList();
}
