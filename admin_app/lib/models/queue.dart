/// Desks: where work lands, and who holds them.
///
/// Mirrors `server/app/queues.py`. A queue used to be a bare string on a Task
/// and a hardcoded tuple in Naviant's own code — the wrong owner for who sits
/// at the customer's AP desk. It is now a stored object the customer edits,
/// with the persona roster as its bootstrap rather than its definition.
library;

class QueueMember {
  final String email;
  final String label;
  final String addedAtIso;
  final String addedBy;

  /// Declared on the persona roster rather than added here. Shown as such and
  /// refused to the delete path: removing it would last until the next restart
  /// and then silently come back.
  final bool fromRoster;

  const QueueMember({
    required this.email,
    required this.label,
    required this.addedAtIso,
    required this.addedBy,
    required this.fromRoster,
  });

  String get display => label.isNotEmpty ? label : email;

  factory QueueMember.fromJson(Map<String, dynamic> json) => QueueMember(
    email: json['email'] as String? ?? '',
    label: json['label'] as String? ?? '',
    addedAtIso: json['added_at_iso'] as String? ?? '',
    addedBy: json['added_by'] as String? ?? '',
    fromRoster: json['from_roster'] as bool? ?? false,
  );
}

class WorkQueue {
  final String slug;
  final String name;
  final String description;
  final List<QueueMember> members;

  /// How long a claim on this desk holds before it lapses. Per-desk because
  /// the work differs: an AP clerk holds an invoice for minutes, a steward may
  /// sit with a merge for half an hour.
  final int claimTtlSeconds;

  /// One person's own desk, minted by a flow resolving an approver rather than
  /// created by hand.
  final bool personal;
  final String personEmail;

  /// Registry membership unioned with the persona roster, because the decide
  /// gate accepts either and a screen showing one of them would report an
  /// unheld desk that three people can in fact decide.
  final List<String> holders;
  final int openTasks;
  final int highPriority;
  final bool yours;

  const WorkQueue({
    required this.slug,
    required this.name,
    required this.description,
    required this.members,
    required this.claimTtlSeconds,
    required this.personal,
    required this.personEmail,
    required this.holders,
    required this.openTasks,
    required this.highPriority,
    required this.yours,
  });

  /// The finding this screen exists for: work with nobody to do it. No
  /// personal inbox anywhere would ever surface it, because a queue routes to
  /// a desk and not to a person.
  bool get unheldWithWork => holders.isEmpty && openTasks > 0;

  String get ttlLabel {
    final minutes = (claimTtlSeconds / 60).round();
    return minutes >= 60
        ? '${(minutes / 60).toStringAsFixed(minutes % 60 == 0 ? 0 : 1)}h'
        : '${minutes}m';
  }

  factory WorkQueue.fromJson(Map<String, dynamic> json) => WorkQueue(
    slug: json['slug'] as String? ?? '',
    name: json['name'] as String? ?? '',
    description: json['description'] as String? ?? '',
    members: (json['members'] as List<dynamic>? ?? const [])
        .map((e) => QueueMember.fromJson(e as Map<String, dynamic>))
        .toList(),
    claimTtlSeconds: (json['claim_ttl_seconds'] as num? ?? 1200).toInt(),
    personal: json['personal'] as bool? ?? false,
    personEmail: json['person_email'] as String? ?? '',
    holders: (json['holders'] as List<dynamic>? ?? const [])
        .map((e) => e as String)
        .toList(),
    openTasks: (json['open_tasks'] as num? ?? 0).toInt(),
    highPriority: (json['high_priority'] as num? ?? 0).toInt(),
    yours: json['yours'] as bool? ?? false,
  );
}
