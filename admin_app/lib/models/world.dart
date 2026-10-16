/// Authored ABoxes. Mirrors `server/app/worlds.py`.
///
/// The ontology is a TBox and the console draws it. A good deal of *ABox* is
/// authored too — every eval fixture, and the demo seed's feed deliveries — and
/// was only ever observable through its effects. These are those fragments,
/// materialized: run into a throwaway world so what you see is the consequence
/// (which vendor the sync resolved to, at what score, what it escalated) rather
/// than a static reading of the payload.
library;

class AuthoredWorld {
  final String id;
  final String name;
  final String summary;

  /// `eval-fixture` or `seed-world`.
  final String kind;
  final String declaredIn;
  final String owner;
  final int deliveries;
  final int directObjects;

  const AuthoredWorld({
    required this.id,
    required this.name,
    required this.summary,
    required this.kind,
    required this.declaredIn,
    required this.owner,
    required this.deliveries,
    required this.directObjects,
  });

  bool get isSeed => kind == 'seed-world';

  factory AuthoredWorld.fromJson(Map<String, dynamic> json) => AuthoredWorld(
    id: json['id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    declaredIn: json['declared_in'] as String? ?? '',
    owner: json['owner'] as String? ?? '',
    deliveries: json['deliveries'] as int? ?? 0,
    directObjects: json['direct_objects'] as int? ?? 0,
  );
}

class WorldClaim {
  final String field;
  final dynamic value;
  final String source;
  final double confidence;

  /// True when this is the claim that won under precedence. Marked by the
  /// server against the resolved value, so the view cannot disagree with it.
  final bool resolves;

  const WorldClaim({
    required this.field,
    required this.value,
    required this.source,
    required this.confidence,
    required this.resolves,
  });

  String get authority => source.split(':').first;

  factory WorldClaim.fromJson(Map<String, dynamic> json) => WorldClaim(
    field: json['field'] as String? ?? '',
    value: json['value'],
    source: json['source'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 1).toDouble(),
    resolves: json['resolves'] as bool? ?? false,
  );
}

/// One instance. Carries both halves of the question a natural key raises:
/// what it reads as, and what it resolves to.
class WorldObject {
  final String id;
  final String type;
  final String naturalKey;
  final String label;
  final String status;
  final Map<String, dynamic> props;
  final List<WorldClaim> claims;
  final String ref;
  final List<String> conflictFields;

  const WorldObject({
    required this.id,
    required this.type,
    required this.naturalKey,
    required this.label,
    required this.status,
    required this.props,
    required this.claims,
    required this.ref,
    required this.conflictFields,
  });

  factory WorldObject.fromJson(Map<String, dynamic> json) => WorldObject(
    id: json['id'] as String? ?? '',
    type: json['type'] as String? ?? '',
    naturalKey: json['natural_key'] as String? ?? '',
    label: json['label'] as String? ?? '',
    status: json['status'] as String? ?? '',
    props: (json['props'] as Map<String, dynamic>?) ?? const {},
    claims: (json['claims'] as List<dynamic>? ?? const [])
        .map((e) => WorldClaim.fromJson(e as Map<String, dynamic>))
        .toList(),
    ref: json['ref'] as String? ?? '',
    conflictFields: (json['conflict_fields'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );
}

class WorldLink {
  final String id;
  final String type;
  final String fromId;
  final String toId;
  final String fromLabel;
  final String toLabel;
  final String method;
  final double confidence;
  final Map<String, dynamic> props;
  final bool live;

  const WorldLink({
    required this.id,
    required this.type,
    required this.fromId,
    required this.toId,
    required this.fromLabel,
    required this.toLabel,
    required this.method,
    required this.confidence,
    required this.props,
    required this.live,
  });

  factory WorldLink.fromJson(Map<String, dynamic> json) => WorldLink(
    id: json['id'] as String? ?? '',
    type: json['type'] as String? ?? '',
    fromId: json['from_id'] as String? ?? '',
    toId: json['to_id'] as String? ?? '',
    fromLabel: json['from_label'] as String? ?? '',
    toLabel: json['to_label'] as String? ?? '',
    method: json['method'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 1).toDouble(),
    props: (json['props'] as Map<String, dynamic>?) ?? const {},
    live: json['live'] as bool? ?? true,
  );
}

class WorldRun {
  final String id;
  final String flowId;
  final String status;
  final int steps;
  final Map<String, dynamic> outputs;

  const WorldRun({
    required this.id,
    required this.flowId,
    required this.status,
    required this.steps,
    required this.outputs,
  });

  factory WorldRun.fromJson(Map<String, dynamic> json) => WorldRun(
    id: json['id'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    status: json['status'] as String? ?? '',
    steps: json['steps'] as int? ?? 0,
    outputs: (json['outputs'] as Map<String, dynamic>?) ?? const {},
  );
}

class WorldTask {
  final String id;
  final String kind;
  final String title;
  final String queue;

  const WorldTask({
    required this.id,
    required this.kind,
    required this.title,
    required this.queue,
  });

  factory WorldTask.fromJson(Map<String, dynamic> json) => WorldTask(
    id: json['id'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    title: json['title'] as String? ?? '',
    queue: json['queue'] as String? ?? '',
  );
}

class MaterializedWorld {
  final AuthoredWorld world;
  final List<WorldObject> objects;
  final List<WorldLink> links;
  final List<WorldRun> runs;
  final List<WorldTask> tasks;
  final Map<String, int> byType;
  final Map<String, int> byLinkType;
  final List<String> violations;

  /// True when a cap dropped instances — said out loud, because a view that
  /// silently shows part of a graph reads as showing all of it.
  final bool truncated;
  final String error;

  const MaterializedWorld({
    required this.world,
    required this.objects,
    required this.links,
    required this.runs,
    required this.tasks,
    required this.byType,
    required this.byLinkType,
    required this.violations,
    required this.truncated,
    required this.error,
  });

  factory MaterializedWorld.fromJson(Map<String, dynamic> json) =>
      MaterializedWorld(
        world: AuthoredWorld.fromJson(
          (json['world'] as Map<String, dynamic>?) ?? const {},
        ),
        objects: (json['objects'] as List<dynamic>? ?? const [])
            .map((e) => WorldObject.fromJson(e as Map<String, dynamic>))
            .toList(),
        links: (json['links'] as List<dynamic>? ?? const [])
            .map((e) => WorldLink.fromJson(e as Map<String, dynamic>))
            .toList(),
        runs: (json['runs'] as List<dynamic>? ?? const [])
            .map((e) => WorldRun.fromJson(e as Map<String, dynamic>))
            .toList(),
        tasks: (json['tasks'] as List<dynamic>? ?? const [])
            .map((e) => WorldTask.fromJson(e as Map<String, dynamic>))
            .toList(),
        // Tolerant like every other field here: a count that arrives as a
        // JSON double — which is what a Firestore round trip can make of an
        // integer — took the whole world down on a hard `as int`.
        byType: (json['by_type'] as Map<String, dynamic>? ?? const {})
            .map((k, v) => MapEntry(k, (v as num?)?.toInt() ?? 0)),
        byLinkType: (json['by_link_type'] as Map<String, dynamic>? ?? const {})
            .map((k, v) => MapEntry(k, (v as num?)?.toInt() ?? 0)),
        violations: (json['violations'] as List<dynamic>? ?? const [])
            .map((e) {
              final v = e as Map<String, dynamic>;
              return '[${v['kind']}] ${v['subject']}: ${v['detail']}';
            })
            .toList(),
        truncated: json['truncated'] as bool? ?? false,
        error: json['error'] as String? ?? '',
      );
}
