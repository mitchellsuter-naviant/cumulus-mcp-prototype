/// Who you are acting as, and who else you could be.
///
/// Mirrors `Session`, `PersonaGroup` and `PersonaOption` in
/// `server/app/ui_api.py`. The server answers all of it in one call on
/// purpose, so the shell never has a half-drawn state where it knows the
/// persona but not the roster it belongs to.
library;

class PersonaOption {
  final String id;

  /// The identity this persona acts as, which is what `impersonated_by` on a
  /// [Session] names — so the bar can resolve an impersonation back to the
  /// person running it.
  final String uid;

  final String label;
  final String title;
  final String person;
  final String email;
  final String authority;
  final String scope;
  final List<String> queues;
  final String blurb;

  /// False when this persona has no `Employee` behind it, so the console can
  /// say why the personal surfaces are empty rather than look broken.
  final bool inWorkforce;

  const PersonaOption({
    required this.id,
    required this.uid,
    required this.label,
    required this.title,
    required this.person,
    required this.email,
    required this.authority,
    required this.scope,
    required this.queues,
    required this.blurb,
    required this.inWorkforce,
  });

  factory PersonaOption.fromJson(Map<String, dynamic> json) => PersonaOption(
    id: json['id'] as String? ?? '',
    uid: json['uid'] as String? ?? '',
    label: json['label'] as String? ?? '',
    title: json['title'] as String? ?? '',
    person: json['person'] as String? ?? '',
    email: json['email'] as String? ?? '',
    authority: json['authority'] as String? ?? '',
    scope: json['scope'] as String? ?? '',
    queues:
        (json['queues'] as List<dynamic>? ?? []).map((q) => '$q').toList(),
    blurb: json['blurb'] as String? ?? '',
    inWorkforce: json['in_workforce'] as bool? ?? false,
  );

  /// The person behind the persona, falling back to the role. Naviant Support
  /// is nobody, and showing an empty name would be worse than showing the role.
  String get displayName => person.isEmpty ? label : person;
}

class PersonaGroup {
  final String label;
  final List<PersonaOption> personas;

  const PersonaGroup({required this.label, required this.personas});

  factory PersonaGroup.fromJson(Map<String, dynamic> json) => PersonaGroup(
    label: json['label'] as String? ?? '',
    personas: (json['personas'] as List<dynamic>? ?? [])
        .map((p) => PersonaOption.fromJson(p as Map<String, dynamic>))
        .toList(),
  );
}

class Session {
  final String uid;
  final String email;
  final String personaId;
  final String label;
  final String person;
  final String title;
  final String authority;
  final String scope;
  final List<String> queues;
  final bool inWorkforce;
  final String? impersonatedBy;
  final bool maySwitchPersona;
  final List<PersonaGroup> groups;

  const Session({
    required this.uid,
    required this.email,
    required this.personaId,
    required this.label,
    required this.person,
    required this.title,
    required this.authority,
    required this.scope,
    required this.queues,
    required this.inWorkforce,
    required this.impersonatedBy,
    required this.maySwitchPersona,
    required this.groups,
  });

  factory Session.fromJson(Map<String, dynamic> json) => Session(
    uid: json['uid'] as String? ?? '',
    email: json['email'] as String? ?? '',
    personaId: json['persona_id'] as String? ?? '',
    label: json['label'] as String? ?? '',
    person: json['person'] as String? ?? '',
    title: json['title'] as String? ?? '',
    authority: json['authority'] as String? ?? '',
    scope: json['scope'] as String? ?? '',
    queues:
        (json['queues'] as List<dynamic>? ?? []).map((q) => '$q').toList(),
    inWorkforce: json['in_workforce'] as bool? ?? false,
    impersonatedBy: json['impersonated_by'] as String?,
    maySwitchPersona: json['may_switch_persona'] as bool? ?? false,
    groups: (json['groups'] as List<dynamic>? ?? [])
        .map((g) => PersonaGroup.fromJson(g as Map<String, dynamic>))
        .toList(),
  );

  String get displayName => person.isEmpty ? label : person;

  /// True when the operator is watching the console as somebody else.
  bool get impersonating => impersonatedBy != null;

  /// The real operator behind an impersonation, when they are themselves on
  /// the roster. A deployed Firebase operator is not, so this is null there
  /// and the bar can only say that an impersonation is running.
  PersonaOption? get operator {
    final by = impersonatedBy;
    if (by == null) return null;
    for (final option in groups.expand((g) => g.personas)) {
      if (option.uid == by) return option;
    }
    return null;
  }

  /// Who to name in the account menu — the person actually signed in, never
  /// the persona they are watching as. The bar says "acting as John Smith"; the
  /// account menu is the one anchor that has to keep saying who you really
  /// are, and it used to say `dev@localhost` regardless of who was signed in.
  String get operatorEmail => operator?.email ?? email;
}
