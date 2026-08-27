/// The Mapping registry — the declared seams between external systems and the
/// graph. Mirrors `server/app/mappings.py`.
library;

class IdentityBinding {
  final String column;
  final String objectType;
  final String key;

  const IdentityBinding({
    required this.column,
    required this.objectType,
    required this.key,
  });

  factory IdentityBinding.fromJson(Map<String, dynamic> json) =>
      IdentityBinding(
        column: json['column'] as String? ?? '',
        objectType: json['object_type'] as String? ?? '',
        key: json['key'] as String? ?? '',
      );
}

class SourceMapping {
  final String id;
  final String source;
  final String system;
  final String summary;
  final String transport; // push | pull | file-drop
  final String payload;
  final String strategy; // reify | bind
  final String landsAs;
  final List<String> resolvesTo;
  final List<IdentityBinding> identity;
  final String precedenceSource;
  final String matchPolicy;
  final String freshness;
  final String custody; // copy | reference
  final String collection;
  final String escalation; // per-row task | windowed aggregate
  final String notes;

  const SourceMapping({
    required this.id,
    required this.source,
    required this.system,
    required this.summary,
    required this.transport,
    required this.payload,
    required this.strategy,
    required this.landsAs,
    required this.resolvesTo,
    required this.identity,
    required this.precedenceSource,
    required this.matchPolicy,
    required this.freshness,
    required this.custody,
    required this.collection,
    required this.escalation,
    required this.notes,
  });

  factory SourceMapping.fromJson(Map<String, dynamic> json) => SourceMapping(
    id: json['id'] as String,
    source: json['source'] as String? ?? '',
    system: json['system'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    transport: json['transport'] as String? ?? '',
    payload: json['payload'] as String? ?? '',
    strategy: json['strategy'] as String? ?? 'reify',
    landsAs: json['lands_as'] as String? ?? '',
    resolvesTo: (json['resolves_to'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    identity: (json['identity'] as List<dynamic>? ?? const [])
        .map((e) => IdentityBinding.fromJson(e as Map<String, dynamic>))
        .toList(),
    precedenceSource: json['precedence_source'] as String? ?? '',
    matchPolicy: json['match_policy'] as String? ?? '',
    freshness: json['freshness'] as String? ?? '',
    custody: json['custody'] as String? ?? 'copy',
    collection: json['collection'] as String? ?? '',
    escalation: json['escalation'] as String? ?? '',
    notes: json['notes'] as String? ?? '',
  );
}

class MappingRegistry {
  final List<SourceMapping> mappings;

  /// Structural problems the server found — a mapping naming a type or
  /// Collection that does not exist. Empty means the registry conforms.
  final List<String> problems;

  const MappingRegistry({required this.mappings, required this.problems});

  factory MappingRegistry.fromJson(Map<String, dynamic> json) =>
      MappingRegistry(
        mappings: (json['mappings'] as List<dynamic>? ?? const [])
            .map((e) => SourceMapping.fromJson(e as Map<String, dynamic>))
            .toList(),
        problems: (json['problems'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
      );
}
