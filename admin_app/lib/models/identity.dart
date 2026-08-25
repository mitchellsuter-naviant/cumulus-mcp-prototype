/// Anything that can hold credentials: a person who signs in, or an agent or
/// service client that only ever presents an API key.
///
/// People are Firebase Auth accounts projected into this shape by the server;
/// agents and services live in their own registry and have no password.
class Identity {
  final String uid;

  /// `person`, `agent`, or `service`.
  final String kind;
  final String displayName;
  final String email;
  final String role;
  final String description;
  final String owner;
  final bool disabled;
  final DateTime? createdAt;
  final String createdBy;

  Identity({
    required this.uid,
    required this.kind,
    required this.displayName,
    required this.email,
    required this.role,
    required this.description,
    required this.owner,
    required this.disabled,
    required this.createdAt,
    required this.createdBy,
  });

  bool get isPerson => kind == 'person';

  /// What to call it in the UI, falling back through the fields the server
  /// guarantees are populated.
  String get label =>
      displayName.isNotEmpty ? displayName : (email.isNotEmpty ? email : uid);

  factory Identity.fromJson(Map<String, dynamic> json) => Identity(
    uid: json['uid'] as String,
    kind: json['kind'] as String? ?? 'person',
    displayName: json['display_name'] as String? ?? '',
    email: json['email'] as String? ?? '',
    role: json['role'] as String? ?? 'user',
    description: json['description'] as String? ?? '',
    owner: json['owner'] as String? ?? '',
    disabled: json['disabled'] as bool? ?? false,
    createdAt: json['created_at'] == null
        ? null
        : DateTime.parse(json['created_at'] as String),
    createdBy: json['created_by'] as String? ?? '',
  );
}
