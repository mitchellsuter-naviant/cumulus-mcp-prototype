/// Everything the model diagram draws, in one round trip.
/// Mirrors `OntologyScreen` in `server/app/ui_api.py`.
///
/// A second view of the model beside [Ontology] in `graph.dart`, and
/// deliberately so. That one is the declaration as the graph service
/// publishes it. This one is what a *diagram* needs: the Collection
/// clustering it groups by, the flow list it can overlay, and — when a flow
/// is named — that flow's observed footprint beside its declared one.
library;

class OntologyField {
  final String name;
  final String kind;
  final String note;

  const OntologyField({
    required this.name,
    required this.kind,
    required this.note,
  });

  factory OntologyField.fromJson(Map<String, dynamic> json) => OntologyField(
    name: json['name'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    note: json['note'] as String? ?? '',
  );
}

class OntologyType {
  final String name;
  final String label;
  final String summary;
  final String naturalKey;
  final List<OntologyField> fields;
  final List<String> tags;
  final List<String> competencyQuestions;
  final int instances;
  final int conflicted;
  final String collection;

  /// Derived from the composition links, never declared: a part is identified
  /// through its owner, which is what gives the diagram its ranks.
  final String ownerType;
  final List<String> partTypes;

  const OntologyType({
    required this.name,
    required this.label,
    required this.summary,
    required this.naturalKey,
    required this.fields,
    required this.tags,
    required this.competencyQuestions,
    required this.instances,
    required this.conflicted,
    required this.collection,
    required this.ownerType,
    required this.partTypes,
  });

  bool get isPart => ownerType.isNotEmpty;

  factory OntologyType.fromJson(Map<String, dynamic> json) => OntologyType(
    name: json['name'] as String? ?? '',
    label: json['label'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    naturalKey: json['natural_key'] as String? ?? '',
    fields: (json['fields'] as List<dynamic>? ?? const [])
        .map((e) => OntologyField.fromJson(e as Map<String, dynamic>))
        .toList(),
    tags: (json['tags'] as List<dynamic>? ?? const []).map((e) => '$e').toList(),
    competencyQuestions:
        (json['competency_questions'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
    instances: json['instances'] as int? ?? 0,
    conflicted: json['conflicted'] as int? ?? 0,
    collection: json['collection'] as String? ?? '',
    ownerType: json['owner_type'] as String? ?? '',
    partTypes: (json['part_types'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );
}

class OntologyLink {
  final String name;
  final String fromType;
  final String toType;

  /// association | composition | identity.
  final String nature;
  final String cardinality;
  final bool enforced;
  final int instances;

  const OntologyLink({
    required this.name,
    required this.fromType,
    required this.toType,
    required this.nature,
    required this.cardinality,
    required this.enforced,
    required this.instances,
  });

  /// `Employee reports_to Employee`. Never routed as a line — a line from a
  /// box back to itself is the worst artifact in a schema diagram and says
  /// nothing a glyph on the box does not.
  bool get isSelfLoop => fromType == toType;

  factory OntologyLink.fromJson(Map<String, dynamic> json) => OntologyLink(
    name: json['name'] as String? ?? '',
    fromType: json['from_type'] as String? ?? '',
    toType: json['to_type'] as String? ?? '',
    nature: json['nature'] as String? ?? 'association',
    cardinality: json['cardinality'] as String? ?? '',
    enforced: json['enforced'] as bool? ?? false,
    instances: json['instances'] as int? ?? 0,
  );
}

/// A Collection, as the thing the diagram groups by. Retention, custody and
/// authoritativeness are properties of a set, so this is the only grouping
/// that groups by something which governs anything.
class OntologyCluster {
  final String id;
  final String label;
  final String role;
  final String origin;
  final List<String> typeNames;

  const OntologyCluster({
    required this.id,
    required this.label,
    required this.role,
    required this.origin,
    required this.typeNames,
  });

  factory OntologyCluster.fromJson(Map<String, dynamic> json) =>
      OntologyCluster(
        id: json['id'] as String? ?? '',
        label: json['label'] as String? ?? '',
        role: json['role'] as String? ?? '',
        origin: json['origin'] as String? ?? '',
        typeNames: (json['type_names'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
      );
}

/// What one flow does to one type on one axis (read, or write).
///
/// Four states rather than two booleans because the *diff* is the finding.
/// [undeclared] is a governance gap — the flow touched something its contract
/// never named. [stale] is a declaration nothing has kept: either a path no
/// run has exercised, or a line left in the contract after the code moved on.
enum Footprint { absent, matched, undeclared, stale }

class OntologyOverlay {
  final String flowId;

  /// never_ran | ran_but_wrote_nothing | observed. Carried whole rather than
  /// collapsed into an empty [writes]: a flow that never ran and a flow that
  /// ran and wrote nothing produce the same empty set and are opposite facts.
  final String state;
  final int scannedRuns;
  final int totalRuns;
  final List<String> reads;
  final List<String> writes;
  final List<String> establishes;
  final List<String> knowledge;
  final List<String> declaredReads;
  final List<String> declaredWrites;

  const OntologyOverlay({
    required this.flowId,
    required this.state,
    required this.scannedRuns,
    required this.totalRuns,
    required this.reads,
    required this.writes,
    required this.establishes,
    required this.knowledge,
    required this.declaredReads,
    required this.declaredWrites,
  });

  bool get everRan => state != 'never_ran';

  static Footprint _diff(bool observed, bool declared) => switch ((
    observed,
    declared,
  )) {
    (true, true) => Footprint.matched,
    (true, false) => Footprint.undeclared,
    (false, true) => Footprint.stale,
    (false, false) => Footprint.absent,
  };

  Footprint read(String type) =>
      _diff(reads.contains(type), declaredReads.contains(type));

  Footprint write(String type) =>
      _diff(writes.contains(type), declaredWrites.contains(type));

  bool touches(String type) =>
      read(type) != Footprint.absent || write(type) != Footprint.absent;

  /// Observed but never declared, on either axis — the governance gaps, which
  /// is the one part of this payload someone has to act on.
  List<String> get gaps => [
    for (final t in {...reads, ...writes}.toList()..sort())
      if (read(t) == Footprint.undeclared || write(t) == Footprint.undeclared) t,
  ];

  factory OntologyOverlay.fromJson(Map<String, dynamic> json) {
    List<String> strings(String key) =>
        (json[key] as List<dynamic>? ?? const []).map((e) => '$e').toList();
    return OntologyOverlay(
      flowId: json['flow_id'] as String? ?? '',
      state: json['state'] as String? ?? 'never_ran',
      scannedRuns: json['scanned_runs'] as int? ?? 0,
      totalRuns: json['total_runs'] as int? ?? 0,
      reads: strings('reads'),
      writes: strings('writes'),
      establishes: strings('establishes'),
      knowledge: strings('knowledge'),
      declaredReads: strings('declared_reads'),
      declaredWrites: strings('declared_writes'),
    );
  }
}

class OntologyFlowOption {
  final String id;
  final String label;

  const OntologyFlowOption({required this.id, required this.label});

  factory OntologyFlowOption.fromJson(Map<String, dynamic> json) =>
      OntologyFlowOption(
        id: json['id'] as String? ?? '',
        label: json['label'] as String? ?? '',
      );
}

class OntologyScreen {
  final List<OntologyType> types;
  final List<OntologyLink> links;
  final List<OntologyCluster> clusters;

  /// Present only when the request named a flow.
  final OntologyOverlay? overlay;
  final List<OntologyFlowOption> flows;

  /// TBox violations over the whole graph — `null`, deliberately not zero,
  /// for a persona that could take no action on one.
  final int? conformance;

  const OntologyScreen({
    required this.types,
    required this.links,
    required this.clusters,
    required this.overlay,
    required this.flows,
    required this.conformance,
  });

  factory OntologyScreen.fromJson(Map<String, dynamic> json) => OntologyScreen(
    types: (json['types'] as List<dynamic>? ?? const [])
        .map((e) => OntologyType.fromJson(e as Map<String, dynamic>))
        .toList(),
    links: (json['links'] as List<dynamic>? ?? const [])
        .map((e) => OntologyLink.fromJson(e as Map<String, dynamic>))
        .toList(),
    clusters: (json['collections'] as List<dynamic>? ?? const [])
        .map((e) => OntologyCluster.fromJson(e as Map<String, dynamic>))
        .toList(),
    overlay: json['overlay'] == null
        ? null
        : OntologyOverlay.fromJson(json['overlay'] as Map<String, dynamic>),
    flows: (json['flows'] as List<dynamic>? ?? const [])
        .map((e) => OntologyFlowOption.fromJson(e as Map<String, dynamic>))
        .toList(),
    conformance: json['conformance'] as int?,
  );
}
