/// The entity layer. Mirrors `server/app/graph.py` and `ontology.py`.
///
/// Three ideas the UI has to render: an Object is canonical and
/// Cumulus-identified; its `props` are *derived* from retained claims, so two
/// systems disagreeing is a normal state to show rather than an error; and a
/// Link is typed, carries a confidence, and can be withdrawn without erasing
/// that it was asserted.
library;

class GraphClaim {
  final String field;
  final dynamic value;
  final String source;
  final String sourceRecordId;
  final double confidence;
  final String atIso;
  final String note;

  /// The *act* that wrote this, as opposed to the actor `source` names. With
  /// both, "why does this say $1,425.25" resolves to a step in a journal.
  final String runId;
  final String stepId;

  const GraphClaim({
    required this.field,
    required this.value,
    required this.source,
    required this.sourceRecordId,
    required this.confidence,
    required this.atIso,
    required this.note,
    required this.runId,
    required this.stepId,
  });

  /// The precedence bucket — the part before the colon (`human`, `erp`, …).
  String get authority => source.split(':').first;

  factory GraphClaim.fromJson(Map<String, dynamic> json) => GraphClaim(
    field: json['field'] as String? ?? '',
    value: json['value'],
    source: json['source'] as String? ?? '',
    sourceRecordId: json['source_record_id'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 1).toDouble(),
    atIso: json['at_iso'] as String? ?? '',
    note: json['note'] as String? ?? '',
    runId: json['run_id'] as String? ?? '',
    stepId: json['step_id'] as String? ?? '',
  );
}

class GraphObject {
  final String id;
  final String type;
  final String label;
  final String naturalKey;
  final String status;
  final Map<String, dynamic> props;
  final int revision;
  final String updatedAtIso;
  final String ref;
  final int claimCount;
  final List<String> conflictFields;

  const GraphObject({
    required this.id,
    required this.type,
    required this.label,
    required this.naturalKey,
    required this.status,
    required this.props,
    required this.revision,
    required this.updatedAtIso,
    required this.ref,
    required this.claimCount,
    required this.conflictFields,
  });

  factory GraphObject.fromJson(Map<String, dynamic> json) => GraphObject(
    id: json['id'] as String? ?? '',
    type: json['type'] as String? ?? '',
    label: json['label'] as String? ?? '',
    naturalKey: json['natural_key'] as String? ?? '',
    status: json['status'] as String? ?? '',
    props: (json['props'] as Map<String, dynamic>?) ?? const {},
    revision: json['revision'] as int? ?? 1,
    updatedAtIso: json['updated_at_iso'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
    claimCount: json['claim_count'] as int? ?? 0,
    conflictFields: (json['conflict_fields'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
  );
}

class GraphEdge {
  final String linkId;
  final String type;
  final String direction; // out | in
  final double confidence;
  final String method;
  final Map<String, dynamic> props;
  final bool live;
  final GraphObject other;

  const GraphEdge({
    required this.linkId,
    required this.type,
    required this.direction,
    required this.confidence,
    required this.method,
    required this.props,
    required this.live,
    required this.other,
  });

  factory GraphEdge.fromJson(Map<String, dynamic> json) => GraphEdge(
    linkId: json['link_id'] as String? ?? '',
    type: json['type'] as String? ?? '',
    direction: json['direction'] as String? ?? 'out',
    confidence: (json['confidence'] as num? ?? 1).toDouble(),
    method: json['method'] as String? ?? '',
    props: (json['props'] as Map<String, dynamic>?) ?? const {},
    live: json['live'] as bool? ?? true,
    other: GraphObject.fromJson(
      (json['other'] as Map<String, dynamic>?) ?? const {},
    ),
  );
}

class GraphObjectDetail {
  final GraphObject object;
  final List<GraphClaim> claims;
  final Map<String, List<GraphClaim>> conflicts;
  final List<GraphEdge> neighbors;

  const GraphObjectDetail({
    required this.object,
    required this.claims,
    required this.conflicts,
    required this.neighbors,
  });

  factory GraphObjectDetail.fromJson(Map<String, dynamic> json) =>
      GraphObjectDetail(
        object: GraphObject.fromJson(
          (json['object'] as Map<String, dynamic>?) ?? const {},
        ),
        claims: (json['claims'] as List<dynamic>? ?? const [])
            .map((e) => GraphClaim.fromJson(e as Map<String, dynamic>))
            .toList(),
        conflicts:
            (json['conflicts'] as Map<String, dynamic>? ?? const {}).map(
          (k, v) => MapEntry(
            k,
            (v as List<dynamic>)
                .map((e) => GraphClaim.fromJson(e as Map<String, dynamic>))
                .toList(),
          ),
        ),
        neighbors: (json['neighbors'] as List<dynamic>? ?? const [])
            .map((e) => GraphEdge.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class OntologyObjectType {
  final String name;
  final String label;
  final String summary;
  final String naturalKey;
  final bool authoritative;
  final String collection;
  final List<Map<String, dynamic>> fields;
  final int instances;

  /// Derived from compositional links, never declared: non-empty means this
  /// type is a *part*, identified through its owner, and folds under it.
  final String ownerType;
  final List<String> partTypes;

  /// Effective filter facets — a part inherits its owner's.
  final List<String> tags;

  /// The questions this type exists to answer; `{label}` substitutes the
  /// selected instance's label.
  final List<String> competencyQuestions;

  const OntologyObjectType({
    required this.name,
    required this.label,
    required this.summary,
    required this.naturalKey,
    required this.authoritative,
    required this.collection,
    required this.fields,
    required this.instances,
    this.ownerType = '',
    this.partTypes = const [],
    this.tags = const [],
    this.competencyQuestions = const [],
  });

  bool get isPart => ownerType.isNotEmpty;

  factory OntologyObjectType.fromJson(Map<String, dynamic> json) =>
      OntologyObjectType(
        name: json['name'] as String? ?? '',
        label: json['label'] as String? ?? '',
        summary: json['summary'] as String? ?? '',
        naturalKey: json['natural_key'] as String? ?? '',
        authoritative: json['authoritative'] as bool? ?? false,
        collection: json['collection'] as String? ?? '',
        fields: (json['fields'] as List<dynamic>? ?? const [])
            .map((e) => e as Map<String, dynamic>)
            .toList(),
        instances: json['instances'] as int? ?? 0,
        ownerType: json['owner_type'] as String? ?? '',
        partTypes: (json['part_types'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
        tags: (json['tags'] as List<dynamic>? ?? const [])
            .map((e) => '$e')
            .toList(),
        competencyQuestions:
            (json['competency_questions'] as List<dynamic>? ?? const [])
                .map((e) => '$e')
                .toList(),
      );
}

class OntologyLinkType {
  final String name;

  /// association | composition | identity — one discriminating field.
  final String nature;
  final String fromType;
  final String toType;
  final String cardinality;
  final String summary;
  final bool identity;
  final int instances;

  /// True when the cardinality is constrained on write. `many-to-many` is
  /// declared deliberately where a second edge is legitimate, so an
  /// unenforced link is a decision rather than an omission.
  final bool enforced;

  const OntologyLinkType({
    required this.name,
    required this.fromType,
    required this.toType,
    required this.cardinality,
    required this.summary,
    this.nature = 'association',
    required this.identity,
    required this.instances,
    required this.enforced,
  });

  factory OntologyLinkType.fromJson(Map<String, dynamic> json) =>
      OntologyLinkType(
        name: json['name'] as String? ?? '',
        fromType: json['from_type'] as String? ?? '',
        toType: json['to_type'] as String? ?? '',
        cardinality: json['cardinality'] as String? ?? '',
        summary: json['summary'] as String? ?? '',
        nature: json['nature'] as String? ?? 'association',
        identity: json['identity'] as bool? ?? false,
        instances: json['instances'] as int? ?? 0,
        enforced: json['enforced'] as bool? ?? false,
      );
}

class Ontology {
  final List<OntologyObjectType> objects;
  final List<OntologyLinkType> links;
  final List<String> precedence;
  final String assetId;

  const Ontology({
    required this.objects,
    required this.links,
    required this.precedence,
    required this.assetId,
  });

  factory Ontology.fromJson(Map<String, dynamic> json) => Ontology(
    objects: (json['objects'] as List<dynamic>? ?? const [])
        .map((e) => OntologyObjectType.fromJson(e as Map<String, dynamic>))
        .toList(),
    links: (json['links'] as List<dynamic>? ?? const [])
        .map((e) => OntologyLinkType.fromJson(e as Map<String, dynamic>))
        .toList(),
    precedence: (json['precedence'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    assetId: json['asset_id'] as String? ?? '',
  );
}

/// One way the graph disagrees with the ontology that declares it.
class TBoxViolation {
  final String kind;
  final String subject;
  final String detail;

  const TBoxViolation({
    required this.kind,
    required this.subject,
    required this.detail,
  });

  factory TBoxViolation.fromJson(Map<String, dynamic> json) => TBoxViolation(
    kind: json['kind'] as String? ?? '',
    subject: json['subject'] as String? ?? '',
    detail: json['detail'] as String? ?? '',
  );
}

/// TBox conformance over the whole graph — the tier of testing that needs no
/// per-case labels, because these hold for every object and edge.
class InvariantReport {
  final bool ok;
  final List<TBoxViolation> violations;
  final int objectsChecked;
  final int linksChecked;
  final Map<String, int> byKind;
  final bool strictWrites;

  const InvariantReport({
    required this.ok,
    required this.violations,
    required this.objectsChecked,
    required this.linksChecked,
    required this.byKind,
    required this.strictWrites,
  });

  static const unknown = InvariantReport(
    ok: true, violations: [], objectsChecked: 0, linksChecked: 0,
    byKind: {}, strictWrites: true,
  );

  factory InvariantReport.fromJson(Map<String, dynamic> json) => InvariantReport(
    ok: json['ok'] as bool? ?? true,
    violations: (json['violations'] as List<dynamic>? ?? const [])
        .map((e) => TBoxViolation.fromJson(e as Map<String, dynamic>))
        .toList(),
    objectsChecked: json['objects_checked'] as int? ?? 0,
    linksChecked: json['links_checked'] as int? ?? 0,
    byKind: (json['by_kind'] as Map<String, dynamic>? ?? const {})
        .map((k, v) => MapEntry(k, v as int)),
    strictWrites: json['strict_writes'] as bool? ?? true,
  );
}

/// One external system's id for a canonical entity.
class VendorSourceRecord {
  final String source;
  final String externalId;
  final double confidence;
  final String method;
  final bool live;
  final String linkId;

  const VendorSourceRecord({
    required this.source,
    required this.externalId,
    required this.confidence,
    required this.method,
    required this.live,
    required this.linkId,
  });

  factory VendorSourceRecord.fromJson(Map<String, dynamic> json) =>
      VendorSourceRecord(
        source: json['source'] as String? ?? '',
        externalId: json['external_id'] as String? ?? '',
        confidence: (json['confidence'] as num? ?? 1).toDouble(),
        method: json['method'] as String? ?? '',
        live: json['live'] as bool? ?? true,
        linkId: json['link_id'] as String? ?? '',
      );
}

/// The vendor master as the AP workspace needs it.
class VendorRecord {
  final String id;
  final String label;
  final String slug;
  final String status;
  final String taxId;
  final String paymentTerms;
  final List<String> aliases;
  final List<VendorSourceRecord> sourceRecords;
  final int invoices;
  final double invoicedUsd;
  final int purchaseOrders;
  final bool hasExemplar;
  final String exemplarRef;
  final List<String> conflictFields;
  final int revision;
  final String ref;

  const VendorRecord({
    required this.id,
    required this.label,
    required this.slug,
    required this.status,
    required this.taxId,
    required this.paymentTerms,
    required this.aliases,
    required this.sourceRecords,
    required this.invoices,
    required this.invoicedUsd,
    required this.purchaseOrders,
    required this.hasExemplar,
    required this.exemplarRef,
    required this.conflictFields,
    required this.revision,
    required this.ref,
  });

  factory VendorRecord.fromJson(Map<String, dynamic> json) => VendorRecord(
    id: json['id'] as String? ?? '',
    label: json['label'] as String? ?? '',
    slug: json['slug'] as String? ?? '',
    status: json['status'] as String? ?? '',
    taxId: json['tax_id'] as String? ?? '',
    paymentTerms: json['payment_terms'] as String? ?? '',
    aliases: (json['aliases'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    sourceRecords: (json['source_records'] as List<dynamic>? ?? const [])
        .map((e) => VendorSourceRecord.fromJson(e as Map<String, dynamic>))
        .toList(),
    invoices: json['invoices'] as int? ?? 0,
    invoicedUsd: (json['invoiced_usd'] as num? ?? 0).toDouble(),
    purchaseOrders: json['purchase_orders'] as int? ?? 0,
    hasExemplar: json['has_exemplar'] as bool? ?? false,
    exemplarRef: json['exemplar_ref'] as String? ?? '',
    conflictFields: (json['conflict_fields'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    revision: json['revision'] as int? ?? 1,
    ref: json['ref'] as String? ?? '',
  );
}

/// The analytical answer, with the path that produced it — the thing no
/// document contains and no search would find.
class ExpenseRollup {
  final String employee;
  final String employeeObjectId;
  final int windowDays;
  final int requests;
  final int lines;
  final double requestedUsd;
  final double approvedUsd;
  final Map<String, double> byCategory;
  final List<String> traversal;
  final List<Map<String, dynamic>> detail;

  const ExpenseRollup({
    required this.employee,
    required this.employeeObjectId,
    required this.windowDays,
    required this.requests,
    required this.lines,
    required this.requestedUsd,
    required this.approvedUsd,
    required this.byCategory,
    required this.traversal,
    required this.detail,
  });

  factory ExpenseRollup.fromJson(Map<String, dynamic> json) => ExpenseRollup(
    employee: json['employee'] as String? ?? '',
    employeeObjectId: json['employee_object_id'] as String? ?? '',
    windowDays: json['window_days'] as int? ?? 0,
    requests: json['requests'] as int? ?? 0,
    lines: json['lines'] as int? ?? 0,
    requestedUsd: (json['requested_usd'] as num? ?? 0).toDouble(),
    approvedUsd: (json['approved_usd'] as num? ?? 0).toDouble(),
    byCategory: (json['by_category'] as Map<String, dynamic>? ?? const {})
        .map((k, v) => MapEntry(k, (v as num).toDouble())),
    traversal: (json['traversal'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    detail: (json['detail'] as List<dynamic>? ?? const [])
        .map((e) => e as Map<String, dynamic>)
        .toList(),
  );
}

/// One entity-search result. `match` names which handle the query caught;
/// `via` is set when the hit was reached through an identity Link — typing an
/// ERP id finds the canonical entity it means here.
class EntityHit {
  final GraphObject entity;
  final String match;
  final String via;

  const EntityHit({required this.entity, required this.match, required this.via});

  factory EntityHit.fromJson(Map<String, dynamic> json) => EntityHit(
    entity: GraphObject.fromJson(
      (json['entity'] as Map<String, dynamic>?) ?? const {},
    ),
    match: json['match'] as String? ?? '',
    via: json['via'] as String? ?? '',
  );
}

class LinkEndpoint {
  final String id;
  final String type;
  final String label;
  final String ref;

  const LinkEndpoint({
    required this.id,
    required this.type,
    required this.label,
    required this.ref,
  });

  factory LinkEndpoint.fromJson(Map<String, dynamic> json) => LinkEndpoint(
    id: json['id'] as String? ?? '',
    type: json['type'] as String? ?? '',
    label: json['label'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
  );
}

/// One Link as an auditable row: both endpoints, the identity metadata, and
/// the run/step that wrote it.
class LinkRow {
  final String id;
  final String type;
  final LinkEndpoint fromEntity;
  final LinkEndpoint toEntity;
  final double confidence;
  final String method;
  final Map<String, dynamic> props;
  final bool live;
  final String reversalReason;
  final String runId;
  final String stepId;

  const LinkRow({
    required this.id,
    required this.type,
    required this.fromEntity,
    required this.toEntity,
    required this.confidence,
    required this.method,
    required this.props,
    required this.live,
    required this.reversalReason,
    required this.runId,
    required this.stepId,
  });

  factory LinkRow.fromJson(Map<String, dynamic> json) => LinkRow(
    id: json['id'] as String? ?? '',
    type: json['type'] as String? ?? '',
    fromEntity: LinkEndpoint.fromJson(
      (json['from_entity'] as Map<String, dynamic>?) ?? const {},
    ),
    toEntity: LinkEndpoint.fromJson(
      (json['to_entity'] as Map<String, dynamic>?) ?? const {},
    ),
    confidence: (json['confidence'] as num? ?? 1).toDouble(),
    method: json['method'] as String? ?? '',
    props: (json['props'] as Map<String, dynamic>?) ?? const {},
    live: json['live'] as bool? ?? true,
    reversalReason: json['reversal_reason'] as String? ?? '',
    runId: json['run_id'] as String? ?? '',
    stepId: json['step_id'] as String? ?? '',
  );
}
