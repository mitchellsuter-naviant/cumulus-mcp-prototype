/// A team: the durable owner of knowledge, and the unit that administers its
/// own access.
///
/// People move between teams; the team keeps owning the document. Authority is
/// a pair — a platform role on the identity, and a team role here — so a team
/// can run its own membership and credentials without platform-wide rights.
class Team {
  final String slug;
  final String name;
  final String description;
  final List<TeamMember> members;

  /// Assets whose `owner` is this team's slug.
  final int assetCount;

  final DateTime? createdAt;
  final String createdBy;

  /// The signed-in viewer's role in this team, or null if they are not a
  /// member. Resolved server-side so the console never replays the rules.
  final String? myTeamRole;

  /// True when the viewer may manage this team — platform admin, or admin of
  /// this team specifically.
  final bool canAdminister;

  Team({
    required this.slug,
    required this.name,
    required this.description,
    required this.members,
    required this.assetCount,
    required this.createdAt,
    required this.createdBy,
    required this.myTeamRole,
    required this.canAdminister,
  });

  List<TeamMember> get admins =>
      members.where((m) => m.teamRole == 'admin').toList();

  List<TeamMember> get people => members.where((m) => m.isPerson).toList();

  List<TeamMember> get machines => members.where((m) => !m.isPerson).toList();

  factory Team.fromJson(Map<String, dynamic> json) => Team(
    slug: json['slug'] as String,
    name: json['name'] as String? ?? '',
    description: json['description'] as String? ?? '',
    members: (json['members'] as List<dynamic>? ?? [])
        .map((m) => TeamMember.fromJson(m as Map<String, dynamic>))
        .toList(),
    assetCount: json['asset_count'] as int? ?? 0,
    createdAt: _date(json['created_at']),
    createdBy: json['created_by'] as String? ?? '',
    myTeamRole: json['my_team_role'] as String?,
    canAdminister: json['can_administer'] as bool? ?? false,
  );

  static DateTime? _date(Object? v) => v == null || (v as String).isEmpty
      ? null
      : DateTime.tryParse(v);
}

/// One identity's place on a team, carrying enough of the identity to render a
/// row without a second lookup.
class TeamMember {
  final String identityUid;
  final String label;

  /// `person`, `agent`, or `service`.
  final String kind;
  final String email;

  /// Platform-wide role, which this identity's keys inherit.
  final String globalRole;

  /// `admin` or `member`, scoped to this team.
  final String teamRole;

  final bool disabled;
  final DateTime? addedAt;
  final String addedBy;

  /// The identity no longer exists but a membership row remained.
  final bool missing;

  TeamMember({
    required this.identityUid,
    required this.label,
    required this.kind,
    required this.email,
    required this.globalRole,
    required this.teamRole,
    required this.disabled,
    required this.addedAt,
    required this.addedBy,
    required this.missing,
  });

  bool get isPerson => kind == 'person';

  bool get isTeamAdmin => teamRole == 'admin';

  factory TeamMember.fromJson(Map<String, dynamic> json) => TeamMember(
    identityUid: json['identity_uid'] as String,
    label: json['label'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    email: json['email'] as String? ?? '',
    globalRole: json['global_role'] as String? ?? 'user',
    teamRole: json['team_role'] as String? ?? 'member',
    disabled: json['disabled'] as bool? ?? false,
    addedAt: Team._date(json['added_at']),
    addedBy: json['added_by'] as String? ?? '',
    missing: json['missing'] as bool? ?? false,
  );
}
