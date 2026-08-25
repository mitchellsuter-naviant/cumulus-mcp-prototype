class PlatformUser {
  final String uid;
  final String email;
  final String displayName;
  final String role;
  final bool disabled;

  PlatformUser({
    required this.uid,
    required this.email,
    required this.displayName,
    required this.role,
    required this.disabled,
  });

  factory PlatformUser.fromJson(Map<String, dynamic> json) => PlatformUser(
    uid: json['uid'] as String,
    email: json['email'] as String,
    displayName: json['display_name'] as String? ?? '',
    role: json['role'] as String,
    disabled: json['disabled'] as bool? ?? false,
  );
}
