/// A minted machine credential. The secret itself is only ever present on
/// [MintedApiKey], returned once at creation time.
class ApiKey {
  final String id;
  final String name;

  /// Who or what the key acts as. Null only for keys minted before identities
  /// existed — those authenticate nothing and are revoked on sight.
  final String? identityUid;
  final String identityName;

  /// `person`, `agent`, or `service`; empty when unattributed.
  final String identityKind;

  /// Inherited from the holding identity, not stored on the key.
  final String role;

  /// Masked token, e.g. `cumk_a1b2c3d4e5f6_Xy9z…` — safe to display.
  final String display;
  final DateTime createdAt;
  final String createdBy;
  final DateTime? expiresAt;
  final DateTime? revokedAt;
  final String? revokedBy;
  final DateTime? lastUsedAt;
  final int requestCount;

  /// `active`, `revoked`, or `expired` — computed server-side.
  final String status;

  ApiKey({
    required this.id,
    required this.name,
    required this.identityUid,
    required this.identityName,
    required this.identityKind,
    required this.role,
    required this.display,
    required this.createdAt,
    required this.createdBy,
    required this.expiresAt,
    required this.revokedAt,
    required this.revokedBy,
    required this.lastUsedAt,
    required this.requestCount,
    required this.status,
  });

  bool get isActive => status == 'active';

  /// Minted before identities existed: dead, and shown as such.
  bool get isUnattributed => identityUid == null;

  factory ApiKey.fromJson(Map<String, dynamic> json) => ApiKey(
    id: json['id'] as String,
    name: json['name'] as String,
    identityUid: json['identity_uid'] as String?,
    identityName: json['identity_name'] as String? ?? '',
    identityKind: json['identity_kind'] as String? ?? '',
    role: json['role'] as String? ?? '',
    display: json['display'] as String? ?? '',
    createdAt: DateTime.parse(json['created_at'] as String),
    createdBy: json['created_by'] as String? ?? '',
    expiresAt: _date(json['expires_at']),
    revokedAt: _date(json['revoked_at']),
    revokedBy: json['revoked_by'] as String?,
    lastUsedAt: _date(json['last_used_at']),
    requestCount: json['request_count'] as int? ?? 0,
    status: json['status'] as String? ?? 'active',
  );

  static DateTime? _date(Object? v) =>
      v == null ? null : DateTime.parse(v as String);
}

/// Creation response: the one and only time the secret is available.
class MintedApiKey {
  final ApiKey key;
  final String token;
  final Map<String, dynamic> mcpConfig;

  MintedApiKey({
    required this.key,
    required this.token,
    required this.mcpConfig,
  });

  factory MintedApiKey.fromJson(Map<String, dynamic> json) => MintedApiKey(
    key: ApiKey.fromJson(json['key'] as Map<String, dynamic>),
    token: json['token'] as String,
    mcpConfig: json['mcp_config'] as Map<String, dynamic>,
  );
}

/// One UTC day of usage for one key.
class ApiKeyUsageDay {
  final String day;
  final int count;
  final Map<String, int> endpoints;
  final DateTime? lastUsedAt;

  ApiKeyUsageDay({
    required this.day,
    required this.count,
    required this.endpoints,
    required this.lastUsedAt,
  });

  factory ApiKeyUsageDay.fromJson(Map<String, dynamic> json) => ApiKeyUsageDay(
    day: json['day'] as String,
    count: json['count'] as int? ?? 0,
    endpoints: ((json['endpoints'] as Map<String, dynamic>?) ?? {}).map(
      (k, v) => MapEntry(k, (v as num).toInt()),
    ),
    lastUsedAt: json['last_used_at'] == null
        ? null
        : DateTime.parse(json['last_used_at'] as String),
  );
}
