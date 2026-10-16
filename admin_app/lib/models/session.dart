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

/// Whether this deployment has a workspace at all, and why not when it does not.
///
/// Mirrors `WorkspaceAvailability` in `server/app/ui_api.py`. The console used
/// to answer this with a compiled boolean per workspace — a second copy of a
/// rule the server already holds, which could be shipped believing in a
/// deployment it had never reached. The two facts that actually decide it are
/// the server's: whether anybody in this deployment's cast holds the desk the
/// workspace *is*, and whether estate discovery says the namespace runs the
/// app it rests on.
///
/// [reason] is written to be shown verbatim, and its estate half is the same
/// sentence the Sources screen gives for a feed with nothing to pull — one
/// finding, said one way.
class WorkspaceAvailability {
  /// The console's section slug, so navigation and availability are keyed on
  /// one string.
  final String slug;
  final bool available;
  final String reason;

  const WorkspaceAvailability({
    required this.slug,
    required this.available,
    required this.reason,
  });

  factory WorkspaceAvailability.fromJson(Map<String, dynamic> json) =>
      WorkspaceAvailability(
        slug: json['slug'] as String? ?? '',
        available: json['available'] as bool? ?? false,
        reason: json['reason'] as String? ?? '',
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

  /// Whether the identity workbench has anything for this persona.
  ///
  /// Answered by the server rather than derived here from [scope], because the
  /// rule is not the one the rest of the rail's governance group uses: a
  /// steward reads only its own work and still owns the counterparty desk. The
  /// alternative was a copy of the queue names in Dart, drifting the moment a
  /// Mapping changed hands.
  final bool mayOpenIdentity;

  /// One entry per workspace the rail can offer. Empty from a server that
  /// predates the field, which every reader below treats as "no opinion" —
  /// the rail then behaves exactly as it did before, which is the right
  /// degrade for a console talking to an older deployment.
  final List<WorkspaceAvailability> workspaces;
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
    required this.mayOpenIdentity,
    required this.workspaces,
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
    mayOpenIdentity: json['may_open_identity'] as bool? ?? false,
    workspaces: [
      for (final row in (json['workspaces'] as List<dynamic>? ?? const []))
        WorkspaceAvailability.fromJson((row as Map).cast<String, dynamic>()),
    ],
    groups: (json['groups'] as List<dynamic>? ?? [])
        .map((g) => PersonaGroup.fromJson(g as Map<String, dynamic>))
        .toList(),
  );

  String get displayName => person.isEmpty ? label : person;

  /// What the server says about one workspace, or null when it said nothing
  /// about it — an older deployment, or a slug this build knows and that one
  /// does not.
  WorkspaceAvailability? workspaceFor(String slug) {
    for (final row in workspaces) {
      if (row.slug == slug) return row;
    }
    return null;
  }

  /// Whether this deployment has the workspace. Silence is *yes*: offering a
  /// stop that turns out not to exist is the better of the two errors, and the
  /// route behind it refuses in a sentence.
  bool workspaceAvailable(String slug) => workspaceFor(slug)?.available ?? true;

  /// Why a workspace is not here, or empty when it is.
  String workspaceReason(String slug) => workspaceFor(slug)?.reason ?? '';

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
